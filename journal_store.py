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
from collections import OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from journal_recurrence import Rule,MilestoneRule,parse_rule,civil_timestamp

SCHEMA = 3
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
            if 0 < version < SCHEMA:
                folder=self.root/'backups';folder.mkdir(exist_ok=True)
                target=sqlite3.connect(folder/(f'migration-v{version}-to-v{SCHEMA}-'+uuid.uuid4().hex+'.sqlite3'))
                try:self.db.backup(target)
                finally:target.close()
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self._schema()
            self._revisions=dict(events=0,notes=0,statistics=0,files=0)
            self._presentation_cache=OrderedDict()
            self.db.create_function('journal_changed',1,self._changed)
            for table,group,columns in [('events','events','title,kind,body,rule,archived,deleted'),('occurrences','events','state,due'),('occurrence_exclusions','events',None),('notes','notes',None),('habit_log','statistics',None),('files','files',None),('note_files','files',None),('note_originals','files',None),('meta','pins',None)]:
                for operation in ('INSERT','UPDATE','DELETE'):
                    condition=" WHEN "+('OLD' if operation=='DELETE' else 'NEW')+".key LIKE 'pinned:%'" if table=='meta' else ''
                    update=' OF '+columns if operation=='UPDATE' and columns else ''
                    self.db.execute(f"CREATE TEMP TRIGGER revision_{table}_{operation} AFTER {operation}{update} ON {table}{condition} BEGIN SELECT journal_changed('{group}'); END")
            (self.root / 'attachments').mkdir(exist_ok=True)
            (self.root / 'recordings').mkdir(exist_ok=True)
            (self.root/'backups').mkdir(exist_ok=True)
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
        CREATE TABLE IF NOT EXISTS occurrence_exclusions(event_id TEXT NOT NULL REFERENCES events(id), due REAL NOT NULL,
          deleted REAL, snapshot TEXT, PRIMARY KEY(event_id,due));
        CREATE TABLE IF NOT EXISTS note_originals(note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE, body TEXT NOT NULL);
        ''')
        if 'next_check' not in {r[1] for r in self.db.execute('PRAGMA table_info(events)')}:
            self.db.execute('ALTER TABLE events ADD COLUMN next_check REAL NOT NULL DEFAULT 0')
        if 'deleted' not in {r[1] for r in self.db.execute('PRAGMA table_info(events)')}:
            self.db.execute('ALTER TABLE events ADD COLUMN deleted REAL')
            self.db.execute('ALTER TABLE events ADD COLUMN previous_archived INTEGER NOT NULL DEFAULT 0')
            self.db.execute("UPDATE events SET deleted=updated WHERE archived=1 AND id IN (SELECT entity FROM audit WHERE action='event_archived')")
        self.db.execute('CREATE INDEX IF NOT EXISTS event_next_check ON events(archived,next_check)')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('library_id',?)", (uuid.uuid4().hex,))
            for kind, (_, minutes) in HABITS.items():
                self.db.execute('INSERT OR IGNORE INTO habits VALUES(?,0,?,?,?,0)', (kind,minutes,'00:00','00:00'))
            self.db.execute('PRAGMA user_version=3')

    def _changed(self,group):
        if group=='pins':
            self._revisions['events']+=1;self._revisions['notes']+=1
        else:self._revisions[group]+=1
        return 0

    def revision(self,*groups):return tuple(self._revisions[g] for g in groups)

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
                self.db.execute('UPDATE events SET title=?,kind=?,body=?,rule=?,updated=?,cursor=?,archived=0,next_check=?,deleted=NULL WHERE id=?',
                    (title.strip(),kind,body,json.dumps(rule.mapping()),now,now-.001,next_check,identity))
                self.db.execute("DELETE FROM alerts WHERE event_id=? AND due IN (SELECT due FROM occurrences WHERE event_id=? AND state IN ('pending','missed'))", (identity,identity))
                self.db.execute("DELETE FROM occurrences WHERE event_id=? AND state IN ('pending','missed')", (identity,))
            else:
                self.db.execute('INSERT INTO events(id,title,kind,body,rule,created,updated,cursor,archived,next_check) VALUES(?,?,?,?,?,?,?,?,0,?)',
                    (identity,title.strip(),kind,body,json.dumps(rule.mapping()),now,now,now-.001,next_check))
            self.audit(identity,'event_saved',rule.mapping())
        return identity

    def archive_event(self, event_id):
        with self.db:
            self.db.execute('UPDATE events SET previous_archived=archived,archived=1,deleted=? WHERE id=? AND deleted IS NULL', (self.clock(),event_id))
            self.db.execute("UPDATE alerts SET state='cancelled' WHERE event_id=? AND state='pending'", (event_id,))
            self.db.execute("UPDATE occurrences SET state='cancelled' WHERE event_id=? AND state IN ('pending','missed')", (event_id,))
            self.audit(event_id,'event_archived',{})

    def events(self, search='', archived=False, kind=None, offset=0, limit=100, status=None):
        clause='deleted IS NOT NULL' if status=='trash' else 'deleted IS NULL AND archived='+str(int(status=='completed' if status else archived))
        active=status=='active' or (status is None and not archived)
        args=('%'+search+'%','%'+search+'%') + ((kind,) if kind else ())
        rows=self.rows('SELECT * FROM events WHERE '+clause+' AND (title LIKE ? OR body LIKE ?)'
                         + (' AND kind=?' if kind else '') + ' ORDER BY updated DESC,id DESC'+('' if active else ' LIMIT ? OFFSET ?'),
                         args if active else args+(-1 if limit is None else limit,offset))
        if active:
            now=self.clock();pins=self.pinned_ids('event')
            ids={r['id'] for r in rows};states={};excluded={}
            for r in self.rows('SELECT event_id,due,state FROM occurrences'):
                if r['event_id'] in ids:states.setdefault(r['event_id'],{})[r['due']]=r['state']
            for r in self.rows('SELECT event_id,due FROM occurrence_exclusions'):
                if r['event_id'] in ids:excluded.setdefault(r['event_id'],set()).add(r['due'])
            if getattr(self,'read_only',False):self.db.commit()
            for row in rows:
                from journal_recurrence import check_cancel
                check_cancel(getattr(self,'cancel',None))
                row['_presentation']=self.reminder_presentation(row,now,pins,states.get(row['id'],{}),excluded.get(row['id'],set()))
            rows.sort(key=lambda r:(not r['_presentation']['pinned'],r['_presentation']['due'] if r['_presentation']['due'] is not None else float('inf'),r['id']))
            expiry=min((r['_presentation']['valid_until'] for r in rows),default=now+30)
            page=rows[offset:None if limit is None else offset+limit]
            for row in page:row['_query_valid_until']=expiry
            return page
        completed={r['event_id']:r['due'] for r in self.rows("SELECT event_id,MAX(due) due FROM occurrences WHERE state='completed' GROUP BY event_id")}
        for row in rows:row['_completed_due']=completed.get(row['id'])
        return rows

    def pinned_ids(self,kind):
        if kind not in ('event','note'):raise ValueError('无效的置顶类型')
        prefix='pinned:'+kind+':'
        return {r['key'][len(prefix):] for r in self.rows('SELECT key FROM meta WHERE key LIKE ?',(prefix+'%',))}

    def set_pinned(self,kind,identity,pinned):
        if kind not in ('event','note'):raise ValueError('无效的置顶类型')
        table='events' if kind=='event' else 'notes';key=f'pinned:{kind}:{identity}'
        with self.db:
            if not self.db.execute(f'SELECT 1 FROM {table} WHERE id=? AND deleted IS NULL'+(' AND archived=0' if kind=='event' else ''),(identity,)).fetchone():raise ValueError('只能置顶进行中的提醒或普通笔记')
            if pinned:self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,'true'))
            else:self.db.execute('DELETE FROM meta WHERE key=?',(key,))

    def reminder_presentation(self,event,now=None,pins=None,states=None,excluded=None):
        """One effective occurrence for both ordering and display; never schedules alerts."""
        now=self.clock() if now is None else now;rule=parse_rule(event['rule'])
        if states is None:states={r['due']:r['state'] for r in self.rows('SELECT due,state FROM occurrences WHERE event_id=?',(event['id'],))}
        if excluded is None:excluded={r['due'] for r in self.rows('SELECT due FROM occurrence_exclusions WHERE event_id=?',(event['id'],))}
        signature=(event['rule'],tuple(sorted(states.items())),tuple(sorted(excluded)))
        cache=self._presentation_cache;cached=cache.get(signature)
        if cached and cached[0]<=now<cached[1]:
            cache.move_to_end(signature)
            return dict(cached[2],valid_until=cached[1],pinned=event['id'] in (self.pinned_ids('event') if pins is None else pins))
        pending=[due for due,state in states.items() if state=='pending' and due not in excluded]
        overdue=[due for due in pending if due<=now]
        due=max(overdue) if overdue else min(pending,default=None)
        if due is None:
            upper=253370000000 if isinstance(rule,MilestoneRule) else min(now+366*86400*150,253370000000)
            due=next((stamp for _,stamp in rule.between(now,upper,limit=1000000,cancel=getattr(self,'cancel',None)) if stamp not in excluded and states.get(stamp) not in ('completed','cancelled')),None)
        if due is None:
            due=max((stamp for stamp,state in states.items() if state in ('pending','missed') and stamp not in excluded),default=None)
        if due is None and getattr(rule,'period',None)=='once':
            stamp=civil_timestamp(rule.base,rule.zone)
            if stamp not in excluded and states.get(stamp) not in ('completed','cancelled'):due=stamp
        countdown='待处理';date_text='暂无下一次安排';tone='muted'
        if due is not None:
            local=datetime.fromtimestamp(due,ZoneInfo(rule.zone));today=datetime.fromtimestamp(now,ZoneInfo(rule.zone)).date();days=(local.date()-today).days
            countdown=f'还有 {days} 天' if days>0 else ('今天 · 待处理' if due<now else '今天') if days==0 else f'已过 {-days} 天'
            date_text=local.strftime('%Y年%m月%d日');tone='warning' if due<now else 'text'
        repeat=rule.describe() if isinstance(rule,MilestoneRule) else {'once':'仅一次','hourly':'每小时','daily':'每天','weekly':'每周','monthly':'每月','yearly':'每年'}[rule.period]
        result=dict(due=due,countdown=countdown,date=date_text,repeat=repeat,tone=tone)
        local_now=datetime.fromtimestamp(now,ZoneInfo(rule.zone));expiry=civil_timestamp(datetime.combine(local_now.date()+timedelta(days=1),datetime.min.time()),rule.zone)
        if due is not None and due>=now:expiry=min(expiry,due+.001)
        cache[signature]=(now,expiry,result)
        if len(cache)>2048:cache.popitem(last=False)
        return dict(result,valid_until=expiry,pinned=event['id'] in (self.pinned_ids('event') if pins is None else pins))

    def reminder_trash(self, search='', kind=None, offset=0, limit=100):
        """One filtered, stable page of deleted plans and deleted occurrences.

        Child occurrences remain hidden while their whole plan is in the trash.
        ``_key`` identifies the selected entity without conflating two dates of
        the same plan; ``id`` and ``_occurrence`` retain the existing UI shape.
        """
        pattern='%'+search+'%'
        kind_clause=' AND e.kind=?' if kind else ''
        args=(pattern,pattern)+((kind,) if kind else ())
        sql=('SELECT * FROM ('
             'SELECT e.*,0 AS _occurrence,e.id AS event_id,NULL AS due,NULL AS snapshot,e.deleted AS trash_deleted '
             'FROM events e WHERE e.deleted IS NOT NULL AND (e.title LIKE ? OR e.body LIKE ?)'+kind_clause+
             ' UNION ALL '
             'SELECT e.*,1 AS _occurrence,x.event_id,x.due,x.snapshot,x.deleted AS trash_deleted '
             'FROM occurrence_exclusions x JOIN events e ON e.id=x.event_id '
             'WHERE x.deleted IS NOT NULL AND e.deleted IS NULL '
             "AND (COALESCE(json_extract(x.snapshot,'$.title'),e.title) LIKE ? OR e.body LIKE ?)"+kind_clause+
             ') ORDER BY trash_deleted DESC,id DESC,_occurrence ASC,due DESC LIMIT ? OFFSET ?')
        rows=self.rows(sql,args+args+(-1 if limit is None else max(0,int(limit)),max(0,int(offset))))
        for row in rows:
            row['_occurrence']=bool(row['_occurrence'])
            row['deleted']=row.pop('trash_deleted')
            row['_key']=('occurrence',row['id'],row['due']) if row['_occurrence'] else ('event',row['id'])
            if row['_occurrence']:
                snapshot=json.loads(row['snapshot'])
                row['title']=snapshot.get('title',row['title'])
                row['state']=snapshot.get('state','pending')
                row['answered']=snapshot.get('answered')
        return rows

    def reminder_trash_keys(self, search='', kind=None):
        return [row['_key'] for row in self.reminder_trash(search,kind,limit=None)]

    @staticmethod
    def _batch_result():
        return dict(restored=[],deleted=[],pending=[],missing=[],cleanup_warning=None)

    @staticmethod
    def _reminder_keys(keys):
        normalized=[];seen=set()
        for key in keys:
            key=tuple(key)
            if not key or key[0] not in ('event','occurrence') or len(key)!=(2 if key[0]=='event' else 3):
                raise ValueError('无效的提醒回收站标识')
            if key not in seen:normalized.append(key);seen.add(key)
        return normalized

    def restore_reminders(self, keys):
        """Atomically restore eligible entities; overdue unfinished ones stay put.

        ``pending`` requires a new date (or restoration of the parent plan).
        SQL failures roll back every restoration, including its audit entries.
        """
        keys=self._reminder_keys(keys);result=self._batch_result();now=self.clock()
        # Restoring a parent first also makes an explicitly selected child
        # eligible, independent of the selection's original ordering.
        with self.db:
            for key in sorted(keys,key=lambda key:key[0]!='event'):
                identity=key[1]
                if key[0]=='event':
                    rows=self.rows('SELECT * FROM events WHERE id=? AND deleted IS NOT NULL',(identity,))
                    if not rows:result['missing'].append(key);continue
                    event=rows[0];rule=parse_rule(event['rule'])
                    future=rule.preview(after=now+.001,count=1)
                    if not event['previous_archived'] and not future:
                        result['pending'].append(key);continue
                    archived=int(bool(event['previous_archived']) or not future)
                    self.db.execute('UPDATE events SET deleted=NULL,archived=?,cursor=?,updated=?,next_check=? WHERE id=?',(archived,now,now,now,identity))
                    self.db.execute("DELETE FROM alerts WHERE event_id=? AND state='cancelled' AND due>?",(identity,now))
                    self.db.execute("DELETE FROM occurrences WHERE event_id=? AND state='cancelled' AND due>? AND NOT EXISTS(SELECT 1 FROM occurrence_exclusions x WHERE x.event_id=occurrences.event_id AND x.due=occurrences.due)",(identity,now))
                    self.audit(identity,'event_restored',{})
                else:
                    due=key[2]
                    rows=self.rows('SELECT x.snapshot,e.deleted AS parent_deleted FROM occurrence_exclusions x JOIN events e ON e.id=x.event_id WHERE x.event_id=? AND x.due=? AND x.deleted IS NOT NULL',(identity,due))
                    if not rows:result['missing'].append(key);continue
                    snap=json.loads(rows[0]['snapshot'])
                    if rows[0]['parent_deleted'] is not None or (due<=now and snap['state'] not in ('completed','cancelled')):
                        result['pending'].append(key);continue
                    self.db.execute('DELETE FROM occurrence_exclusions WHERE event_id=? AND due=?',(identity,due))
                    self.db.execute('INSERT OR REPLACE INTO occurrences VALUES(?,?,?,?,?)',(identity,due,snap['state'],snap['answered'],snap['title']))
                    self.db.execute("DELETE FROM alerts WHERE event_id=? AND due=? AND state='cancelled'",(identity,due))
                    if snap['state'] not in ('completed','cancelled'):
                        self.db.execute("INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,'pending',NULL,?)",(f'{identity}:{due:.0f}:0',identity,due,0,due,now))
                        self.db.execute('UPDATE events SET archived=0,next_check=? WHERE id=? AND deleted IS NULL',(now,identity))
                    self.audit(f'{identity}:{due:.0f}','occurrence_restored',{})
                result['restored'].append(key)
        for key in result['restored']:self._event_wake.pop(key[1],None)
        return result

    def purge_reminders(self, keys):
        """Permanently remove selected trash rows in one transaction.

        A purged occurrence keeps a content-free exclusion tombstone, so a
        recurring rule cannot recreate the date after the next tick or restart.
        """
        keys=self._reminder_keys(keys);result=self._batch_result()
        with self.db:
            # Purge selected children before their parent removes its own rows.
            for key in sorted(keys,key=lambda key:key[0]=='event'):
                identity=key[1]
                if key[0]=='event':
                    if not self.db.execute('SELECT 1 FROM events WHERE id=? AND deleted IS NOT NULL',(identity,)).fetchone():
                        result['missing'].append(key);continue
                    for table in ('alerts','occurrences','occurrence_exclusions'):
                        self.db.execute(f'DELETE FROM {table} WHERE event_id=?',(identity,))
                    self.db.execute('DELETE FROM audit WHERE entity=? OR entity LIKE ?',(identity,identity+':%'))
                    self.db.execute('DELETE FROM events WHERE id=?',(identity,))
                    self.db.execute('DELETE FROM meta WHERE key IN (?,?)',('event_input_lunar:'+identity,'pinned:event:'+identity))
                else:
                    due=key[2]
                    changed=self.db.execute('UPDATE occurrence_exclusions SET deleted=NULL,snapshot=NULL WHERE event_id=? AND due=? AND deleted IS NOT NULL',(identity,due)).rowcount
                    if not changed:result['missing'].append(key);continue
                    self.db.execute('DELETE FROM alerts WHERE event_id=? AND due=?',(identity,due))
                    self.db.execute('DELETE FROM occurrences WHERE event_id=? AND due=?',(identity,due))
                result['deleted'].append(key)
        for key in result['deleted']:self._event_wake.pop(key[1],None)
        return result

    def restore_event(self, identity, rule=None):
        rows=self.rows('SELECT * FROM events WHERE id=? AND deleted IS NOT NULL',(identity,))
        if not rows:return False
        event=rows[0];old=parse_rule(event['rule']);now=self.clock()
        if isinstance(old,Rule) and old.period=='once' and civil_timestamp(old.base,old.zone)<=now and not event['previous_archived']:
            if rule is None or civil_timestamp(rule.base,rule.zone)<=now:raise ValueError('请为过期提醒选择新的时间')
        with self.db:
            if rule is not None:
                self.save_event(event['title'],event['kind'],event['body'],rule,identity)
            else:
                future=old.preview(after=now+.001,count=1)
                archived=int(bool(event['previous_archived']) or not future)
                self.db.execute('UPDATE events SET deleted=NULL,archived=?,cursor=?,updated=?,next_check=? WHERE id=?',(archived,now,now,now,identity))
                self.db.execute("DELETE FROM alerts WHERE event_id=? AND state='cancelled' AND due>?",(identity,now))
                self.db.execute("DELETE FROM occurrences WHERE event_id=? AND state='cancelled' AND due>? AND NOT EXISTS(SELECT 1 FROM occurrence_exclusions x WHERE x.event_id=occurrences.event_id AND x.due=occurrences.due)",(identity,now))
            self.audit(identity,'event_restored',{})
        self._event_wake.pop(identity,None);return True

    def purge_event(self, identity):
        with self.db:
            if not self.db.execute('SELECT 1 FROM events WHERE id=? AND deleted IS NOT NULL',(identity,)).fetchone():return False
            for table in ('alerts','occurrences','occurrence_exclusions'):self.db.execute(f'DELETE FROM {table} WHERE event_id=?',(identity,))
            self.db.execute('DELETE FROM audit WHERE entity=? OR entity LIKE ?',(identity,identity+':%'))
            self.db.execute('DELETE FROM events WHERE id=?',(identity,))
            self.db.execute('DELETE FROM meta WHERE key IN (?,?)',('event_input_lunar:'+identity,'pinned:event:'+identity))
        self._event_wake.pop(identity,None);return True

    def trash_occurrence(self,identity,due):
        event=self.rows('SELECT * FROM events WHERE id=? AND deleted IS NULL',(identity,))
        if not event:return False
        rows=self.rows('SELECT * FROM occurrences WHERE event_id=? AND due=?',(identity,due))
        snapshot=rows[0] if rows else dict(event_id=identity,due=due,state='pending',answered=None,title=event[0]['title'])
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO occurrence_exclusions VALUES(?,?,?,?)',(identity,due,self.clock(),json.dumps(snapshot,ensure_ascii=False)))
            self.db.execute("UPDATE alerts SET state='cancelled' WHERE event_id=? AND due=? AND state='pending'",(identity,due))
            self.db.execute('DELETE FROM occurrences WHERE event_id=? AND due=?',(identity,due))
        return True

    def restore_occurrence(self,identity,due):
        rows=self.rows('SELECT snapshot FROM occurrence_exclusions WHERE event_id=? AND due=? AND deleted IS NOT NULL',(identity,due))
        if not rows:return False
        snap=json.loads(rows[0]['snapshot']);now=self.clock()
        with self.db:
            self.db.execute('DELETE FROM occurrence_exclusions WHERE event_id=? AND due=?',(identity,due))
            self.db.execute('INSERT OR REPLACE INTO occurrences VALUES(?,?,?,?,?)',(identity,due,snap['state'],snap['answered'],snap['title']))
            self.db.execute("DELETE FROM alerts WHERE event_id=? AND due=? AND state='cancelled'",(identity,due))
            if snap['state'] not in ('completed','cancelled'):
                self.db.execute("INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,'pending',NULL,?)",(f'{identity}:{due:.0f}:0',identity,due,0,max(now,due),now))
                self.db.execute('UPDATE events SET archived=0,next_check=? WHERE id=? AND deleted IS NULL',(now,identity))
        return True

    def purge_occurrence(self,identity,due):
        with self.db:
            self.db.execute('UPDATE occurrence_exclusions SET deleted=NULL,snapshot=NULL WHERE event_id=? AND due=?',(identity,due))
            self.db.execute('DELETE FROM alerts WHERE event_id=? AND due=?',(identity,due))

    def excluded(self,identity,due):
        return self.db.execute('SELECT 1 FROM occurrence_exclusions WHERE event_id=? AND due=?',(identity,due)).fetchone() is not None

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

    def tick(self, now=None, *, prepared=None, only_ids=None, process_habits=True):
        now = self.clock() if now is None else now
        if now-self._last_tick > 10 or now < self._last_tick:
            self.resume_habits()
        self._last_tick = now
        with self.db:
            for h in (self.rows('SELECT * FROM habits WHERE enabled=1 AND next_due<=?',(now,)) if process_habits else []):
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
            restrict=' AND id IN ('+','.join('?' for _ in only_ids)+')' if only_ids else ''
            for event in self.rows('SELECT * FROM events WHERE archived=0 AND next_check<=?'+restrict+' ORDER BY next_check LIMIT 100',(now,)+tuple(only_ids or ())):
                plan=prepared.get(event['id']) if prepared is not None else None
                if prepared is not None and (plan is None or plan['signature']!=(event['rule'],event['updated'],event['cursor'])):continue
                cached=self._event_wake.get(event['id'])
                if cached and cached[0]==event['updated'] and now<cached[1]:
                    continue
                rule=parse_rule(event['rule'])
                if now < event['cursor']:
                    continue
                window_start = event['cursor'] - .001
                if event['cursor'] < event['updated'] and not isinstance(rule,MilestoneRule):
                    window_start=min(window_start,civil_timestamp(rule.base,rule.zone))
                candidates=plan['candidates'] if plan else list(rule.between(window_start,now+max(rule.advances,default=0),limit=5000))
                # Find occurrences whose current reminder stage is now due. Older
                # stages coalesce, but never change an occurrence to completed.
                processed_until=now
                if len(candidates)==5000:
                    processed_until=min(now,candidates[-1][1])
                for _,due in candidates:
                    if self.excluded(event['id'],due):continue
                    stages=[(due-a,a) for a in rule.advances]+[(due,0)]
                    eligible=[(when,a) for when,a in stages if when>=event['created'] and when<=now and when>event['cursor']]
                    # A newly-created past event still needs its due reminder.
                    if not eligible and due<=now and event['cursor']<event['updated'] and not isinstance(rule,MilestoneRule):
                        eligible=[(now,0)]
                    if not eligible:
                        continue
                    when,stage=max(eligible)
                    old=self.db.execute('SELECT state FROM occurrences WHERE event_id=? AND due=?',(event['id'],due)).fetchone()
                    if old and old[0] in ('completed','cancelled'):
                        continue
                    title=event['title']+(' · '+' / '.join(rule.labels(due)) if isinstance(rule,MilestoneRule) else '')
                    self.db.execute('INSERT OR IGNORE INTO occurrences(event_id,due,title) VALUES(?,?,?)',(event['id'],due,title))
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
                pending=pending or self.db.execute("SELECT 1 FROM occurrences WHERE event_id=? AND state IN ('pending','missed')",(event['id'],)).fetchone()
                future=plan['future'] if plan else rule.preview(after=now+.001,count=2)
                if not pending and not future:
                    self.db.execute('UPDATE events SET archived=1 WHERE id=?',(event['id'],))
                wake_times=[due-a for _,due in future for a in (*rule.advances,0) if due-a>now]
                self._event_wake[event['id']]=(event['updated'],now if processed_until<now else min(wake_times,default=now+86400))
                self.db.execute('UPDATE events SET next_check=? WHERE id=?',(self._event_wake[event['id']][1],event['id']))

    def pending(self, now=None):
        now=self.clock() if now is None else now
        rows=self.rows("SELECT a.*,COALESCE(o.title,e.title) title,e.body,e.kind,(SELECT COUNT(*) FROM occurrences x WHERE x.event_id=a.event_id AND x.state='missed') missed FROM alerts a LEFT JOIN events e ON e.id=a.event_id LEFT JOIN occurrences o ON o.event_id=a.event_id AND o.due=a.due WHERE a.state='pending' AND a.notify<=? ORDER BY a.notify,a.id",(now,))
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

    def complete_occurrence(self,event_id,due):
        """Explicitly complete only the ledger occurrence selected by the user."""
        now=self.clock()
        with self.db:
            changed=self.db.execute("UPDATE occurrences SET state='completed',answered=? WHERE event_id=? AND due=? AND state IN ('pending','missed')",(now,event_id,due)).rowcount
            if not changed:return False
            self.db.execute("UPDATE alerts SET state='answered' WHERE event_id=? AND due=? AND state='pending'",(event_id,due))
            self.db.execute('UPDATE events SET next_check=? WHERE id=?',(now,event_id))
            self.audit(f'{event_id}:{due:.0f}','complete_selected_occurrence',{})
        self._event_wake.pop(event_id,None)
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
        order='deleted DESC,id DESC' if trash else "EXISTS(SELECT 1 FROM meta WHERE key='pinned:note:'||notes.id) DESC,created DESC,id DESC"
        return self.rows('SELECT *,EXISTS(SELECT 1 FROM meta WHERE key=\'pinned:note:\'||notes.id) AS _pinned FROM notes WHERE deleted IS '+('NOT NULL' if trash else 'NULL')+' AND (title LIKE ? OR body LIKE ?) ORDER BY '+order+' LIMIT ? OFFSET ?',('%'+search+'%','%'+search+'%',-1 if limit is None else limit,offset))

    def note_ids(self, search='', trash=False):
        """All matching IDs, including records beyond the loaded UI page."""
        order='deleted DESC,id DESC' if trash else "EXISTS(SELECT 1 FROM meta WHERE key='pinned:note:'||notes.id) DESC,created DESC,id DESC"
        return [row['id'] for row in self.rows('SELECT id FROM notes WHERE deleted IS '+('NOT NULL' if trash else 'NULL')+' AND (title LIKE ? OR body LIKE ?) ORDER BY '+order,('%'+search+'%','%'+search+'%'))]

    def restore_notes(self, identities):
        result=self._batch_result()
        with self.db:
            for identity in dict.fromkeys(identities):
                changed=self.db.execute('UPDATE notes SET deleted=NULL WHERE id=? AND deleted IS NOT NULL',(identity,)).rowcount
                result['restored' if changed else 'missing'].append(identity)
        return result

    def purge_notes(self, identities, *, allow_active=False):
        """Delete atomically, then collect attachments once after the commit.

        Active-note deletion is deliberate and opt-in. Cleanup failures cannot
        undo committed note deletion and are reported separately to the UI.
        """
        result=self._batch_result()
        with self.db:
            for identity in dict.fromkeys(identities):
                changed=self.db.execute('DELETE FROM notes WHERE id=?'+('' if allow_active else ' AND deleted IS NOT NULL'),(identity,)).rowcount
                if changed:self.db.execute('DELETE FROM meta WHERE key=?',('pinned:note:'+identity,))
                result['deleted' if changed else 'missing'].append(identity)
        if result['deleted']:
            try:self.collect_attachments()
            except Exception as error:result['cleanup_warning']='记录已删除，但部分附件清理未完成：'+str(error)
        return result

    def trash_note(self, identity, restore=False):
        with self.db:
            self.db.execute('UPDATE notes SET deleted=? WHERE id=?',(None if restore else self.clock(),identity))

    def purge_note(self, identity):
        # Keep content-addressed files on disk for retained database snapshots.
        # Full backup only includes referenced attachments. Explicit vacuum later
        # may reclaim objects after every referencing snapshot has expired.
        with self.db:
            self.db.execute('DELETE FROM notes WHERE id=? AND deleted IS NOT NULL',(identity,))
            self.db.execute("DELETE FROM meta WHERE key=? AND NOT EXISTS(SELECT 1 FROM notes WHERE id=?)",('pinned:note:'+identity,identity))
        self.collect_attachments()

    def collect_attachments(self):
        if getattr(self,'background_backup',False):return
        if getattr(self,'maintenance_request',None):self.maintenance_request();return
        referenced=self._attachment_references(self.db)
        for backup in (self.root/'backups').glob('*.sqlite3'):
            connection=sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)
            try:referenced.update(self._attachment_references(connection))
            finally:connection.close()
        for row in self.rows('SELECT * FROM files'):
            if row['id'] not in referenced:
                self.attachment_path(row['relative']).unlink(missing_ok=True)
                with self.db:self.db.execute('DELETE FROM files WHERE id=?',(row['id'],))

    @staticmethod
    def _attachment_references(connection):
        referenced={r[0] for r in connection.execute('SELECT DISTINCT file_id FROM note_files')}
        # Markdown can refer to an attachment owned by a different note. Keep
        # those files, including preserved original bodies and retained backups.
        tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('notes','note_originals'):
            if table in tables:
                referenced.update(r[0] for r in connection.execute(f'SELECT id FROM files WHERE EXISTS(SELECT 1 FROM {table} WHERE instr(body,files.relative)>0)'))
        return referenced

    def attach(self, note_id, source):
        return self.finish_attachment(note_id,self.prepare_attachment(source))

    def prepare_attachment(self,source):
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
            return dict(id=identity,name=source.name,relative=dest.relative_to(self.root).as_posix(),mime=mimetypes.guess_type(source.name)[0] or 'application/octet-stream',size=dest.stat().st_size,sha256=digest.hexdigest())
        except Exception:
            temp.unlink(missing_ok=True); dest.unlink(missing_ok=True)
            raise

    def finish_attachment(self,note_id,row):
        try:
            with self.db:
                self.db.execute('INSERT INTO files VALUES(?,?,?,?,?,?)',tuple(row[k] for k in ('id','name','relative','mime','size','sha256')))
                self.db.execute('INSERT INTO note_files VALUES(?,?)',(note_id,row['id']))
            return row
        except Exception:
            self.attachment_path(row['relative']).unlink(missing_ok=True);raise

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

    def backup_snapshot(self):
        """Worker-only I/O. No use of the live connection and no attachment deletion."""
        folder=self.root/'backups';folder.mkdir(exist_ok=True)
        dest=folder/(datetime.fromtimestamp(self.clock()).strftime('%Y-%m-%d')+'.sqlite3')
        source=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            if not dest.exists():
                partial=dest.with_suffix('.sqlite3.part');target=sqlite3.connect(partial)
                try:source.backup(target,pages=256)
                finally:target.close()
                partial.replace(dest)
            for old in sorted(folder.glob('*.sqlite3'))[:-7]:old.unlink()
            source.execute('BEGIN');referenced=self._attachment_references(source);files=list(source.execute('SELECT id,relative FROM files'));source.commit()
            for backup in folder.glob('*.sqlite3'):
                connection=sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)
                try:referenced.update(self._attachment_references(connection))
                finally:connection.close()
            fingerprint=tuple(sorted((p.name,p.stat().st_mtime_ns,p.stat().st_size) for p in folder.glob('*.sqlite3')))
            return [(identity,relative) for identity,relative in files if identity not in referenced],fingerprint
        finally:source.close()

    def export_backup(self, destination):
        destination=Path(destination)
        temp_db=self.root/('backup-'+uuid.uuid4().hex+'.sqlite3')
        temp_zip=destination.with_suffix(destination.suffix+'.part')
        try:
            target=sqlite3.connect(temp_db)
            source=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)
            try: source.backup(target)
            finally: target.close();source.close()
            snapshot=sqlite3.connect(temp_db);snapshot.row_factory=sqlite3.Row
            try:files=[dict(r) for r in snapshot.execute('SELECT * FROM files')]
            finally:snapshot.close()
            with zipfile.ZipFile(temp_zip,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(temp_db,'journal.sqlite3')
                for row in files:
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
