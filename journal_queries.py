"""Latest-request-wins read snapshots. SQLite connections never cross threads."""
from collections import OrderedDict
from datetime import datetime,timedelta
import sqlite3,threading,time
from PySide6.QtCore import QObject,Signal,QTimer
from journal_store import JournalStore
from journal_recurrence import parse_rule,MilestoneRule,check_cancel,QueryCancelled


def wait_for_idle(root,timeout=10000):
    """Explicit verification barrier; application event handlers never call this."""
    from PySide6.QtWidgets import QApplication
    deadline=time.monotonic()+timeout/1000
    while True:
        QApplication.processEvents()
        busy=any(r.busy for r in root.findChildren(QueryRunner))
        busy |= bool(getattr(root,'_applying',{}))
        busy |= any(t.isActive() and t.property('journalDebounce') for t in root.findChildren(QTimer))
        if not busy:return True
        if time.monotonic()>=deadline:return False
        time.sleep(.001)


def result_weight(value):
    # Conservative bounded-cache accounting without serializing a whole month
    # (pickle itself holds the GIL long enough to stall the GUI on large data).
    if isinstance(value,dict):return sum(len(v)*1024 if isinstance(v,list) else 1024 for v in value.values())
    if isinstance(value,list):return sum(1024+2*len(v.get('body','')) if isinstance(v,dict) else 256 for v in value)
    if isinstance(value,tuple):return sum(result_weight(v) for v in value)
    if isinstance(value,str):return len(value)*2+128
    return 128


class QueryRunner(QObject):
    completed=Signal(object)
    def __init__(self,path,parent=None):
        super().__init__(parent);self.path=path;self._condition=threading.Condition();self._pending=OrderedDict();self._serial={};self._callbacks={};self._keys={};self._cache=OrderedDict();self._cache_weights={};self._closed=False;self.busy=set()
        self.completed.connect(self._deliver)
        self.destroyed.connect(lambda *_:self.close())
        self._thread=threading.Thread(target=self._run,name='journal-queries',daemon=True);self._thread.start()
    def submit(self,channel,key,work,done):
        if self._closed:return
        if self._keys.get(channel)==key and channel in self.busy:return
        self.cancel(channel);serial=self._serial[channel];self._keys[channel]=key;self._callbacks[channel]=done
        cache_key=(channel,key);cached=self._cache.get(cache_key)
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key);done(cached,None);return
        self.busy.add(channel)
        with self._condition:self._pending[channel]=(serial,key,work);self._condition.notify()
    def cancel(self,channel):
        with self._condition:
            self._serial[channel]=self._serial.get(channel,0)+1;self._pending.pop(channel,None)
        self._callbacks.pop(channel,None);self.busy.discard(channel)
    def cancel_all(self):
        for channel in tuple(self._serial):self.cancel(channel)
    def invalidate(self,channel):
        self.cancel(channel)
        for key in list(self._cache):
            if key[0]==channel:self._cache.pop(key);self._cache_weights.pop(key,None)
    def close(self):
        self.cancel_all()
        with self._condition:self._closed=True;self._condition.notify()
    def _run(self):
        presentations=OrderedDict()
        while True:
            with self._condition:
                self._condition.wait_for(lambda:self._pending or self._closed)
                if self._closed:return
                channel,(serial,key,work)=self._pending.popitem(last=False)
            cancel=lambda:self._closed or self._serial.get(channel)!=serial
            result=None;error=None;weight=0
            try:
                check_cancel(cancel)
                db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2);db.row_factory=sqlite3.Row
                try:
                    db.execute('PRAGMA query_only=ON');db.set_progress_handler(lambda:int(cancel()),1000);db.execute('BEGIN')
                    reader=object.__new__(JournalStore);reader.db=db;reader.path=self.path;reader.root=self.path.parent;reader.clock=time.time;reader.read_only=True;reader.cancel=cancel;reader._presentation_cache=presentations
                    result=work(reader,cancel)
                finally:db.close()
                check_cancel(cancel)
                if channel!='schedule':weight=result_weight(result)
            except QueryCancelled:continue
            except Exception as e:
                if cancel():continue
                error=str(e)
            if not self._closed:
                try:self.completed.emit((channel,serial,key,result,error,weight))
                except RuntimeError:return  # Owner was destroyed between the cancellation check and emission.
    def _deliver(self,message):
        channel,serial,key,result,error,weight=message
        if self._closed or self._serial.get(channel)!=serial:return
        self.busy.discard(channel)
        if error is None and channel!='schedule':
            self._cache[(channel,key)]=result
            self._cache_weights[(channel,key)]=weight
            while len(self._cache)>12 or sum(self._cache_weights.values())>96*1024*1024:
                removed,_=self._cache.popitem(last=False);self._cache_weights.pop(removed,None)
        callback=self._callbacks.pop(channel,None)
        if callback:callback(result,error)


def calendar_data(reader,year,month,cancel):
    first=datetime(year,month,1);start=first-timedelta(days=first.weekday());end=start+timedelta(days=42);begin,finish=start.timestamp(),end.timestamp()
    recorded=reader.rows('SELECT o.*,e.kind,e.rule,e.body,e.archived,e.deleted FROM occurrences o JOIN events e ON e.id=o.event_id WHERE o.due>=? AND o.due<?',(begin,finish))
    events=reader.rows('SELECT * FROM events WHERE archived=0 AND deleted IS NULL')
    excluded={(r['event_id'],r['due']) for r in reader.rows('SELECT event_id,due FROM occurrence_exclusions WHERE due>=? AND due<?',(begin,finish))}
    notes=reader.rows('SELECT * FROM notes WHERE deleted IS NULL AND created>=? AND created<? ORDER BY created',(begin,finish))
    habits=reader.rows('SELECT day,COUNT(*) total,SUM(done=1) done FROM habit_log WHERE day>=? AND day<? GROUP BY day',(start.date().isoformat(),end.date().isoformat()))
    reader.db.commit();seen={(r['event_id'],r['due']) for r in recorded};items={};generated={}
    def add(kind,row,stamp):
        day=row['day'] if kind=='habit' else datetime.fromtimestamp(stamp).date().isoformat();items.setdefault(day,[]).append((kind,row,stamp))
    for event in events:
        check_cancel(cancel);rule=parse_rule(event['rule'])
        if event['rule'] not in generated:generated[event['rule']]=list(rule.between(begin,finish-.001,limit=2000,cancel=cancel))
        for _,stamp in generated[event['rule']]:
            if (event['id'],stamp) in seen or (event['id'],stamp) in excluded:continue
            row=dict(event)
            if isinstance(rule,MilestoneRule):row['title']+=' · '+' / '.join(rule.labels(stamp))
            add(event['kind'],row,stamp)
    for row in recorded:
        if row['deleted'] is not None or (row['event_id'],row['due']) in excluded or row['state']=='cancelled':continue
        row['id']=row['event_id'];add(row['kind'],row,row['due'])
    for row in notes:add('note',row,row['created'])
    for row in habits:add('habit',row,0)
    return items


def schedule_data(reader,now,cancel):
    events=reader.rows('SELECT * FROM events WHERE archived=0 AND next_check<=? ORDER BY next_check LIMIT 100',(now,));reader.db.commit();result={}
    for event in events:
        check_cancel(cancel);rule=parse_rule(event['rule']);lower=event['cursor']-.001
        if event['cursor']<event['updated'] and not isinstance(rule,MilestoneRule):
            from journal_recurrence import civil_timestamp
            lower=min(lower,civil_timestamp(rule.base,rule.zone))
        candidates=list(rule.between(lower,now+max(rule.advances,default=0),limit=5000,cancel=cancel))
        result[event['id']]=dict(signature=(event['rule'],event['updated'],event['cursor']),candidates=candidates,future=rule.preview(after=now+.001,count=2,cancel=cancel))
    return result
