import json
import sqlite3
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from journal_recurrence import Rule,civil_timestamp
from journal_store import JournalStore


class Clock:
    def __init__(self):self.now=civil_timestamp(datetime(2026,9,16,10), 'Asia/Shanghai')
    def __call__(self):return self.now

@pytest.fixture
def journal(tmp_path):
    clock=Clock();s=JournalStore(tmp_path/'data',create=True,clock=clock)
    yield s,clock
    s.close()

def iso(stamp):return datetime.fromtimestamp(stamp,ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')[:19]
def dates(rule):return [datetime.fromtimestamp(t,ZoneInfo(rule.zone)).strftime('%Y-%m-%d %H:%M:%S') for _,t in rule.preview()]

def test_month_end_and_skip():
    assert dates(Rule('2026-01-31T09:00:00',period='monthly'))[1].startswith('2026-02-28')
    assert dates(Rule('2026-01-31T09:00:00',period='monthly',missing='skip'))[1].startswith('2026-03-31')
    assert dates(Rule('2024-02-29T09:00:00',period='yearly'))[1].startswith('2025-02-28')

def test_dst_first_fold_and_gap():
    stamp=civil_timestamp(datetime(2026,3,8,2,30),'America/New_York')
    assert datetime.fromtimestamp(stamp,ZoneInfo('America/New_York')).hour==3
    stamp=civil_timestamp(datetime(2026,11,1,1,30),'America/New_York')
    assert datetime.fromtimestamp(stamp,ZoneInfo('America/New_York')).fold==0

def test_lunar_new_year_and_leap():
    assert dates(Rule('2025-01-29T09:00:00',period='yearly',calendar='lunar'))[1].startswith('2026-02-17')
    from lunar_python import Lunar,Solar
    lunar=Lunar.fromYmd(2025,-6,1).getSolar()
    start=f'{lunar.getYear():04}-{lunar.getMonth():02}-{lunar.getDay():02}T09:00:00'
    result=Rule(start,period='yearly',calendar='lunar').preview()
    next_date=datetime.fromtimestamp(result[1][1],ZoneInfo('Asia/Shanghai'))
    assert Solar.fromYmd(next_date.year,next_date.month,next_date.day).getLunar().getMonth()==6

@pytest.mark.parametrize('period',['once','hourly','daily','weekly','monthly','yearly'])
def test_finite_periods(period):
    r=Rule('2026-01-01T09:00:00',period=period,count=2)
    assert len(r.preview(count=5))==(1 if period=='once' else 2)

def test_due_persists_and_double_completion(journal):
    s,c=journal;s.save_event('hello','todo','',Rule(iso(c.now)))
    s.tick();assert len(s.pending())==1
    identity=s.pending()[0]['id'];assert s.respond(identity,'done');assert not s.respond(identity,'done')
    s.tick();assert not s.pending();assert s.events(archived=True)[0]['title']=='hello'

def test_advance_ack_preserves_later_stages(journal):
    s,c=journal;due=c.now+7200;s.save_event('future','schedule','',Rule(iso(due),advances=(3600,)))
    s.tick();assert not s.pending();c.now+=3601;s.tick();alert=s.pending()[0];assert alert['stage']==3600
    s.respond(alert['id'],'ack');assert not s.pending();c.now=due;s.tick();assert s.pending()[0]['stage']==0

def test_early_complete_cancels_due(journal):
    s,c=journal;due=c.now+7200;s.save_event('future','todo','',Rule(iso(due),advances=(3600,)))
    s.tick();c.now+=3601;s.tick();s.respond(s.pending()[0]['id'],'done');c.now=due;s.tick();assert not s.pending()

def test_snooze_coalesces_due(journal):
    s,c=journal;due=c.now+7200;s.save_event('future','todo','',Rule(iso(due),advances=(3600,)))
    s.tick();c.now+=3601;s.tick();s.respond(s.pending()[0]['id'],'snooze',7200);c.now=due;s.tick();assert len(s.pending())==1;assert s.pending()[0]['stage']==0

def test_recurring_missed_are_not_completed(journal):
    s,c=journal;s.save_event('repeat','todo','',Rule(iso(c.now),period='hourly'))
    s.tick();c.now+=4*3600;s.tick();assert len(s.pending())==1
    alert=s.pending()[0];assert alert['missed']==4;s.respond(alert['id'],'done')
    assert len(s.rows("SELECT * FROM occurrences WHERE state='completed'"))==1
    assert len(s.rows("SELECT * FROM occurrences WHERE state='missed'"))==4

def test_health_response_and_correction(journal):
    s,c=journal;s.set_habit('water',True,1)
    for _ in range(61):c.now+=1;s.tick()
    assert len(s.pending())==1;identity=s.pending()[0]['id']
    for _ in range(120):c.now+=1;s.tick()
    assert len(s.pending())==1;s.respond(identity,'no');assert not s.pending()
    s.correct_habit(identity,True);rows=s.statistics('2026-09-16','2026-09-16');assert rows[0]['done']==1
    assert s.rows("SELECT * FROM audit WHERE action='correction'")

def test_sleep_does_not_accumulate_health(journal):
    s,c=journal;s.set_habit('water',True,1);c.now+=86400;s.tick();assert not s.pending()

def test_library_lock_and_backup(journal,tmp_path):
    s,c=journal
    with pytest.raises(ValueError):JournalStore(s.root)
    n=s.save_note('a','# hello');source=tmp_path/'sound.wav';source.write_bytes(b'example');row=s.attach(n,source)
    s.trash_note(n);assert not s.notes();s.trash_note(n,True);assert s.notes()[0]['title']=='a'
    target=tmp_path/'backup.zip';s.export_backup(target)
    import zipfile
    with zipfile.ZipFile(target) as z:z.extractall(tmp_path/'restored')
    other=JournalStore(tmp_path/'restored');assert other.notes()[0]['body']=='# hello';assert other.attachment_path(row['relative']).read_bytes()==b'example';other.close()

def test_path_escape_rejected(journal):
    s,_=journal
    with pytest.raises(ValueError):s.attachment_path('../outside')

def test_calendar_seeks_long_hourly_rule():
    r=Rule('1900-01-01T09:00:00',period='hourly')
    start=civil_timestamp(datetime(2026,9,1),'Asia/Shanghai');end=start+30*86400
    assert len(list(r.between(start,end-.001)))==720
