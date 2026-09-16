"""Transactional local journal. No GUI, microphone, or network side effects."""
from __future__ import annotations
import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from journal_recurrence import Rule,civil_timestamp

SCHEMA = 1
HABITS = {'water': ('喝水', 60), 'walk': ('走动', 60), 'eyes': ('看远处', 20)}


class JournalStore:
    def __init__(self, root, *, create=False, clock=time.time):
        self.root = Path(root).resolve()
        self.clock = clock
        self.closed = False
        self._event_wake = {}
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'journal.sqlite3'
        if not create and not self.path.is_file():
            raise ValueError('这个目录中没有美腻枫资料库')
        # The open handle is a cross-process OS lock; a stale PID/file is not a lock.
        self.lock_stream = (self.root / '.journal.lock').open('a+b')
        try:
            self.lock_stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                self.lock_stream.write(b'0'); self.lock_stream.flush(); self.lock_stream.seek(0)
                msvcrt.locking(self.lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_stream.close()
            raise ValueError('资料库正在被另一个美腻枫使用')
        try:
            self.db = sqlite3.connect(self.path, timeout=3)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA foreign_keys=ON')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA:
                raise ValueError('资料库来自较新版本，请先升级程序')
            if not version and self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise ValueError('不是有效的美腻枫资料库')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self._schema()
            (self.root / 'attachments').mkdir(exist_ok=True)
            (self.root / 'recordings').mkdir(exist_ok=True)
            self.backup_daily()
            self._last_tick = self.clock()
            self.resume_habits()
        except Exception:
            if hasattr(self, 'db'):
                self.db.close()
            self.lock_stream.close()
            raise

    def _schema(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
          body TEXT NOT NULL, rule TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
          cursor REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS occurrences(event_id TEXT NOT NULL REFERENCES events(id), due REAL NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', answered REAL, title TEXT NOT NULL,
          PRIMARY KEY(event_id,due));
        CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY, event_id TEXT, due REAL NOT NULL,
          stage INTEGER NOT NULL, notify REAL NOT NULL, state TEXT NOT NULL, habit TEXT,
          created REAL NOT NULL, UNIQUE(event_id,due,stage));
        CREATE INDEX IF NOT EXISTS alert_due ON alerts(state,notify);
        CREATE INDEX IF NOT EXISTS event_active ON events(archived,updated);
        CREATE TABLE IF NOT EXISTS habits(kind TEXT PRIMARY KEY, enabled INTEGER NOT NULL,
          minutes INTEGER NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, next_due REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS habit_log(id TEXT PRIMARY KEY, kind TEXT NOT NULL, due REAL NOT NULL,
          day TEXT NOT NULL, answered REAL, done INTEGER);
        CREATE INDEX IF NOT EXISTS habit_day ON habit_log(day);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, entity TEXT, at REAL NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
          created REAL NOT NULL, updated REAL NOT NULL, deleted REAL);
        CREATE INDEX IF NOT EXISTS notes_order ON notes(deleted,created DESC);
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, name TEXT NOT NULL, relative TEXT NOT NULL UNIQUE,
          mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS note_files(note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
          file_id TEXT NOT NULL REFERENCES files(id), PRIMARY KEY(note_id,file_id));
        PRAGMA user_version=1;
        ''')
        if 'next_check' not in {r[1] for r in self.db.execute('PRAGMA table_info(events)')}:
            self.db.execute('ALTER TABLE events ADD COLUMN next_check REAL NOT NULL DEFAULT 0')
        self.db.execute('CREATE INDEX IF NOT EXISTS event_next_check ON events(archived,next_check)')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('library_id',?)", (uuid.uuid4().hex,))
            for kind, (_, minutes) in HABITS.items():
                self.db.execute('INSERT OR IGNORE INTO habits VALUES(?,0,?,?,?,0)', (kind,minutes,'00:00','00:00'))

    def rows(self, sql, args=()):
        return [dict(x) for x in self.db.execute(sql, args)]

    def setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key,json.dumps(value,ensure_ascii=False)))

    def audit(self, entity, action, detail):
        self.db.execute('INSERT INTO audit(entity,at,action,detail) VALUES(?,?,?,?)',
                        (entity,self.clock(),action,json.dumps(detail,ensure_ascii=False)))

    def save_event(self, title, kind, body, rule: Rule, event_id=None):
        if not title.strip() or kind not in ('todo','schedule','anniversary'):
            raise ValueError('请填写标题并选择事件类型')
        now = self.clock()
        identity = event_id or uuid.uuid4().hex
        self._event_wake.pop(identity,None)
        first=civil_timestamp(rule.base,rule.zone)
        next_check=now if first<=now else min(when for when in (first-a for a in (*rule.advances,0)) if when>=now)
        with self.db:
            if event_id:
                self.db.execute('UPDATE events SET title=?,kind=?,body=?,rule=?,updated=?,cursor=?,archived=0,next_check=? WHERE id=?',
                    (title.strip(),kind,body,json.dumps(rule.mapping()),now,now-.001,next_check,identity))
                self.db.execute("DELETE FROM alerts WHERE event_id=? AND due IN (SELECT due FROM occurrences WHERE event_id=? AND state IN ('pending','missed'))", (identity,identity))
                self.db.execute("DELETE FROM occurrences WHERE event_id=? AND state IN ('pending','missed')", (identity,))
            else:
                self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,0,?)',
                    (identity,title.strip(),kind,body,json.dumps(rule.mapping()),now,now,now-.001,next_check))
            self.audit(identity,'event_saved',rule.mapping())
        return identity

    def archive_event(self, event_id):
        with self.db:
            self.db.execute('UPDATE events SET archived=1 WHERE id=?', (event_id,))
            self.db.execute("UPDATE alerts SET state='cancelled' WHERE event_id=? AND state='pending'", (event_id,))
            self.db.execute("UPDATE occurrences SET state='cancelled' WHERE event_id=? AND state IN ('pending','missed')", (event_id,))
            self.audit(event_id,'event_archived',{})

    def events(self, search='', archived=False, kind=None, offset=0, limit=100):
        return self.rows('SELECT * FROM events WHERE archived=? AND (title LIKE ? OR body LIKE ?)'
                         + (' AND kind=?' if kind else '') + ' ORDER BY updated DESC LIMIT ? OFFSET ?',
                         (int(archived),'%'+search+'%','%'+search+'%') + ((kind,) if kind else ()) + (limit,offset))

    def set_habit(self, kind, enabled, minutes, start='00:00', end='00:00'):
        if kind not in HABITS or not 1 <= int(minutes) <= 1440:
            raise ValueError('间隔应为 1～1440 分钟')
        for value in (start,end):
            datetime.strptime(value,'%H:%M')
        with self.db:
            self.db.execute('UPDATE habits SET enabled=?,minutes=?,start=?,end=?,next_due=? WHERE kind=?',
                (int(enabled),int(minutes),start,end,self.clock()+minutes*60,kind))
            # Turning future reminders off must not silently dismiss an existing
            # unanswered bubble or create an unanswerable statistics record.

    def resume_habits(self):
        with self.db:
            self.db.execute('UPDATE habits SET next_due=?+minutes*60', (self.clock(),))

    def tick(self, now=None):
        now = self.clock() if now is None else now
        if now-self._last_tick > 10 or now < self._last_tick:
            self.resume_habits()
        self._last_tick = now
        with self.db:
            for h in self.rows('SELECT * FROM habits WHERE enabled=1 AND next_due<=?',(now,)):
                clock_text = datetime.fromtimestamp(now).strftime('%H:%M')
                a,b = h['start'],h['end']
                allowed = a==b or (a<=clock_text<b if a<b else clock_text>=a or clock_text<b)
                if not allowed:
                    self.db.execute('UPDATE habits SET next_due=? WHERE kind=?',(now+h['minutes']*60,h['kind']))
                    continue
                if not self.db.execute("SELECT 1 FROM alerts WHERE habit=? AND state='pending'",(h['kind'],)).fetchone():
                    identity=uuid.uuid4().hex
                    self.db.execute("INSERT INTO alerts VALUES(?,NULL,?,0,?,'pending',?,?)",(identity,now,now,h['kind'],now))
                    self.db.execute('INSERT INTO habit_log VALUES(?,?,?,?,NULL,NULL)',(identity,h['kind'],now,datetime.fromtimestamp(now).date().isoformat()))
            for event in self.rows('SELECT * FROM events WHERE archived=0 AND next_check<=? ORDER BY next_check LIMIT 100',(now,)):
                cached=self._event_wake.get(event['id'])
                if cached and cached[0]==event['updated'] and now<cached[1]:
                    continue
                rule=Rule(**json.loads(event['rule']))
                if now < event['cursor']:
                    continue
                window_start = event['cursor'] - .001
                if event['cursor'] < event['updated']:
                    window_start=min(window_start,civil_timestamp(rule.base,rule.zone))
                candidates=list(rule.between(window_start,now+max(rule.advances,default=0),limit=5000))
                # Find occurrences whose current reminder stage is now due. Older
                # stages coalesce, but never change an occurrence to completed.
                processed_until=now
                if len(candidates)==5000:
                    processed_until=min(now,candidates[-1][1])
                for _,due in candidates:
                    stages=[(due-a,a) for a in rule.advances]+[(due,0)]
                    eligible=[(when,a) for when,a in stages if when>=event['created'] and when<=now and when>event['cursor']]
                    # A newly-created past event still needs its due reminder.
                    if not eligible and due<=now and event['cursor']<event['updated']:
                        eligible=[(now,0)]
                    if not eligible:
                        continue
                    when,stage=max(eligible)
                    old=self.db.execute('SELECT state FROM occurrences WHERE event_id=? AND due=?',(event['id'],due)).fetchone()
                    if old and old[0] in ('completed','cancelled'):
                        continue
                    self.db.execute('INSERT OR IGNORE INTO occurrences(event_id,due,title) VALUES(?,?,?)',(event['id'],due,event['title']))
                    identity=f"{event['id']}:{due:.0f}:{stage}"
                    self.db.execute("INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,'pending',NULL,?)",(identity,event['id'],due,stage,when,now))
                    # Same occurrence: a newer stage replaces its snoozed earlier stage.
                    self.db.execute("UPDATE alerts SET state='superseded' WHERE event_id=? AND due=? AND stage>? AND state='pending'",(event['id'],due,stage))
                self.db.execute('UPDATE events SET cursor=? WHERE id=?',(processed_until,event['id']))
                # Only the newest overdue occurrence occupies a bubble. Older
                # occurrences remain explicitly missed in the expandable ledger.
                latest=self.db.execute("SELECT MAX(due) FROM alerts WHERE event_id=? AND stage=0 AND state='pending' AND notify<=?",(event['id'],now)).fetchone()[0]
                if latest is not None:
                    self.db.execute("UPDATE occurrences SET state='missed' WHERE event_id=? AND due<? AND state='pending'",(event['id'],latest))
                    self.db.execute("UPDATE alerts SET state='superseded' WHERE event_id=? AND due<? AND state='pending'",(event['id'],latest))
                pending=self.db.execute("SELECT 1 FROM alerts WHERE event_id=? AND state='pending'",(event['id'],)).fetchone()
                if not pending and not rule.preview(after=now+.001,count=1):
                    self.db.execute('UPDATE events SET archived=1 WHERE id=?',(event['id'],))
                future=rule.preview(after=now+.001,count=2)
                wake_times=[due-a for _,due in future for a in (*rule.advances,0) if due-a>now]
                self._event_wake[event['id']]=(event['updated'],now if processed_until<now else min(wake_times,default=now+86400))
                self.db.execute('UPDATE events SET next_check=? WHERE id=?',(self._event_wake[event['id']][1],event['id']))

    def pending(self, now=None):
        now=self.clock() if now is None else now
        rows=self.rows("SELECT a.*,e.title,e.body,e.kind,(SELECT COUNT(*) FROM occurrences o WHERE o.event_id=a.event_id AND o.state='missed') missed FROM alerts a LEFT JOIN events e ON e.id=a.event_id WHERE a.state='pending' AND a.notify<=? ORDER BY a.notify,a.id",(now,))
        result=[]; seen=set()
        for row in sorted(rows,key=lambda r:(r['event_id'] or '',-r['due'],r['stage'])):
            key=row['event_id'] or row['id']
            if key not in seen:
                seen.add(key); result.append(row)
        return sorted(result,key=lambda r:r['notify'])

    def respond(self, alert_id, action, delay=0):
        now=self.clock()
        with self.db:
            row=self.db.execute("SELECT * FROM alerts WHERE id=? AND state='pending'",(alert_id,)).fetchone()
            if not row:
                return False
            r=dict(row)
            if r['event_id']:
                self._event_wake.pop(r['event_id'],None)
                self.db.execute('UPDATE events SET next_check=? WHERE id=?',(now,r['event_id']))
            if action=='snooze':
                if not 1<=delay<=366*86400:
                    raise ValueError('延期应在 1 秒至 366 天之间')
                self.db.execute('UPDATE alerts SET notify=? WHERE id=?',(now+delay,alert_id))
            elif r['habit']:
                if action not in ('done','no'):
                    raise ValueError('请选择已完成或未完成')
                self.db.execute('UPDATE habit_log SET answered=?,done=? WHERE id=?',(now,int(action=='done'),alert_id))
                self.db.execute("UPDATE alerts SET state='answered' WHERE id=?",(alert_id,))
                self.db.execute('UPDATE habits SET next_due=?+minutes*60 WHERE kind=?',(now,r['habit']))
            elif action=='done':
                self.db.execute("UPDATE occurrences SET state='completed',answered=? WHERE event_id=? AND due=?",(now,r['event_id'],r['due']))
                self.db.execute("UPDATE alerts SET state='answered' WHERE event_id=? AND due=? AND state='pending'",(r['event_id'],r['due']))
            elif action=='ack' and r['stage']>0:
                self.db.execute("UPDATE alerts SET state='answered' WHERE id=?",(alert_id,))
            else:
                raise ValueError('无效的提醒操作')
            self.audit(alert_id,action,{'delay':delay})
        return True

    def correct_habit(self, identity, done):
        with self.db:
            old=self.db.execute('SELECT * FROM habit_log WHERE id=? AND answered IS NOT NULL',(identity,)).fetchone()
            if not old:
                raise ValueError('只能纠正已回应记录')
            self.db.execute('UPDATE habit_log SET done=? WHERE id=?',(int(done),identity))
            self.audit(identity,'correction',{'before':old['done'],'after':int(done)})

    def statistics(self, start, end):
        return self.rows('SELECT kind,COUNT(*) total,SUM(done=1) done,SUM(done=0) no,SUM(done IS NULL) pending FROM habit_log WHERE day>=? AND day<=? GROUP BY kind',(start,end))

    def save_note(self, title, body, identity=None):
        identity=identity or uuid.uuid4().hex; now=self.clock()
        with self.db:
            self.db.execute('INSERT INTO notes VALUES(?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,updated=excluded.updated',
                (identity,title.strip() or '未命名笔记',body,now,now))
        return identity

    def notes(self, search='', trash=False, offset=0, limit=100):
        return self.rows('SELECT * FROM notes WHERE deleted IS '+('NOT NULL' if trash else 'NULL')+' AND (title LIKE ? OR body LIKE ?) ORDER BY created DESC LIMIT ? OFFSET ?',('%'+search+'%','%'+search+'%',limit,offset))

    def trash_note(self, identity, restore=False):
        with self.db:
            self.db.execute('UPDATE notes SET deleted=? WHERE id=?',(None if restore else self.clock(),identity))

    def purge_note(self, identity):
        # Keep content-addressed files on disk for retained database snapshots.
        # Full backup only includes referenced attachments. Explicit vacuum later
        # may reclaim objects after every referencing snapshot has expired.
        with self.db:
            self.db.execute('DELETE FROM notes WHERE id=? AND deleted IS NOT NULL',(identity,))
        self.collect_attachments()

    def collect_attachments(self):
        referenced={r[0] for r in self.db.execute('SELECT DISTINCT file_id FROM note_files')}
        for backup in (self.root/'backups').glob('*.sqlite3'):
            connection=sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)
            try:referenced.update(r[0] for r in connection.execute('SELECT DISTINCT file_id FROM note_files'))
            finally:connection.close()
        for row in self.rows('SELECT * FROM files'):
            if row['id'] not in referenced:
                self.attachment_path(row['relative']).unlink(missing_ok=True)
                with self.db:self.db.execute('DELETE FROM files WHERE id=?',(row['id'],))

    def attach(self, note_id, source):
        source=Path(source)
        if not source.is_file():
            raise ValueError('附件不是文件')
        identity=uuid.uuid4().hex
        suffix=source.suffix[:20]
        dest=self.root/'attachments'/(identity+suffix)
        temp=dest.with_suffix(dest.suffix+'.part')
        digest=hashlib.sha256()
        try:
            with source.open('rb') as inp,temp.open('xb') as out:
                for chunk in iter(lambda:inp.read(1024*1024),b''):
                    digest.update(chunk); out.write(chunk)
                out.flush(); os.fsync(out.fileno())
            temp.replace(dest)
            with self.db:
                self.db.execute('INSERT INTO files VALUES(?,?,?,?,?,?)',(identity,source.name,dest.relative_to(self.root).as_posix(),mimetypes.guess_type(source.name)[0] or 'application/octet-stream',dest.stat().st_size,digest.hexdigest()))
                self.db.execute('INSERT INTO note_files VALUES(?,?)',(note_id,identity))
            return self.rows('SELECT * FROM files WHERE id=?',(identity,))[0]
        except Exception:
            temp.unlink(missing_ok=True); dest.unlink(missing_ok=True)
            raise

    def attachments(self, note_id):
        return self.rows('SELECT f.* FROM files f JOIN note_files n ON f.id=n.file_id WHERE n.note_id=?',(note_id,))

    def attachment_path(self, relative):
        path=(self.root/relative).resolve()
        if not path.is_relative_to((self.root/'attachments').resolve()):
            raise ValueError('附件路径不在资料库内')
        return path

    def backup_daily(self):
        folder=self.root/'backups'; folder.mkdir(exist_ok=True)
        dest=folder/(datetime.fromtimestamp(self.clock()).strftime('%Y-%m-%d')+'.sqlite3')
        if not dest.exists():
            target=sqlite3.connect(dest)
            try: self.db.backup(target)
            finally: target.close()
        for old in sorted(folder.glob('*.sqlite3'))[:-7]:
            old.unlink()
        self.collect_attachments()

    def export_backup(self, destination):
        destination=Path(destination)
        temp_db=self.root/('backup-'+uuid.uuid4().hex+'.sqlite3')
        temp_zip=destination.with_suffix(destination.suffix+'.part')
        try:
            target=sqlite3.connect(temp_db)
            try: self.db.backup(target)
            finally: target.close()
            with zipfile.ZipFile(temp_zip,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(temp_db,'journal.sqlite3')
                for row in self.rows('SELECT * FROM files'):
                    path=self.attachment_path(row['relative'])
                    archive.write(path,row['relative'])
                settings=self.root/'settings.json'
                if settings.exists(): archive.write(settings,'settings.json')
                for partial in (self.root/'recordings').glob('*'):
                    if partial.is_file(): archive.write(partial,'recordings/'+partial.name)
            temp_zip.replace(destination)
        finally:
            temp_db.unlink(missing_ok=True); temp_zip.unlink(missing_ok=True)

    def close(self):
        if self.closed: return
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.db.close(); self.lock_stream.close(); self.closed=True
