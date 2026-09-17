import sqlite3
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from journal_recurrence import MilestoneRule,Rule,parse_rule,civil_timestamp
from journal_store import JournalStore

def stamp(value,zone='Asia/Shanghai'):return civil_timestamp(datetime.fromisoformat(value),zone)
def local_dates(rule,count=20):return [datetime.fromtimestamp(t,ZoneInfo(rule.zone)).date().isoformat() for _,t in rule.preview(count=count)]

@pytest.mark.parametrize('day',[1,100,200,520,1314,1000])
def test_inclusive_day(day):
    r=MilestoneRule('2026-01-01T09:00:00',yearly=False,days=(day,))
    assert local_dates(r)==[(r.base+timedelta(days=day-1)).date().isoformat()]
    assert r.labels(r.preview()[0][1])==[f'第 {day} 天']

def test_merged_and_bounded_month_query():
    r=MilestoneRule('2025-01-01T09:00:00',days=(366,520),hundreds=True)
    due=stamp('2026-01-01T09:00:00')
    assert len(list(r.between(due,due)))==1
    assert r.labels(due)==['1 周年','第 366 天']
    assert len(list(r.between(stamp('2060-01-01'),stamp('2060-02-01'))))<3
    assert parse_rule(r.mapping()).mapping()==r.mapping()

def test_february_and_dst_calendar_days():
    assert local_dates(MilestoneRule('2024-02-29T09:00:00'),1)==['2025-02-28']
    assert local_dates(MilestoneRule('2024-02-29T09:00:00',missing='skip'),1)==['2028-02-29']
    r=MilestoneRule('2026-03-07T09:00:00',zone='America/New_York',yearly=False,days=(1,2,3))
    due=[v for _,v in r.preview()]
    assert due[1]-due[0]==23*3600
    assert local_dates(r)==['2026-03-07','2026-03-08','2026-03-09']

def test_lunar_year_and_leap_fallback():
    from lunar_python import Lunar,Solar
    assert local_dates(MilestoneRule('2025-01-29T09:00:00',calendar='lunar'),1)==['2026-02-17']
    day=Lunar.fromYmd(2025,-6,1).getSolar()
    r=MilestoneRule(day.toYmd()+'T09:00:00',calendar='lunar')
    next_day=datetime.fromtimestamp(r.preview()[0][1],ZoneInfo(r.zone))
    assert Solar.fromYmd(next_day.year,next_day.month,next_day.day).getLunar().getMonth()==6
    strict=MilestoneRule(r.start,calendar='lunar',strict_leap=True)
    d=datetime.fromtimestamp(strict.preview(count=1)[0][1],ZoneInfo(r.zone))
    assert Solar.fromYmd(d.year,d.month,d.day).getLunar().getMonth()==-6

@pytest.mark.parametrize('days',[(-1,),(0,),(1.5,)])
def test_invalid_days(days):
    with pytest.raises(ValueError):MilestoneRule('2026-01-01',yearly=False,days=days)

def test_new_past_not_backfilled_and_restart_completion(tmp_path):
    now=[stamp('2026-04-10T08:59:00')]
    root=tmp_path/'library';s=JournalStore(root,create=True,clock=lambda:now[0])
    r=MilestoneRule('2026-01-01T09:00:00',yearly=False,hundreds=True,days=(1,100,200))
    identity=s.save_event('相识','anniversary','',r);s.tick();assert not s.pending()
    now[0]+=60;s.tick();assert len(s.pending())==1
    assert '第 100 天' in s.pending()[0]['title']
    assert s.respond(s.pending()[0]['id'],'done');s.tick();assert len(s.events())==1
    s.close();now[0]=stamp('2026-07-19T09:01:00')
    s=JournalStore(root,clock=lambda:now[0]);s.tick();assert len(s.pending())==1
    assert '第 200 天' in s.pending()[0]['title']
    s.tick();assert len(s.pending())==1
    assert len(s.rows('SELECT * FROM occurrences WHERE event_id=?',(identity,)))==2;s.close()

def test_advance_snooze_and_completion_remain_per_occurrence(tmp_path):
    now=[stamp('2026-01-01T07:00:00')];s=JournalStore(tmp_path/'data',create=True,clock=lambda:now[0])
    s.save_event('纪念日','anniversary','',MilestoneRule('2026-01-01T09:00:00',yearly=False,days=(1,2),advances=(3600,)))
    now[0]+=3600;s.tick();assert s.pending()[0]['stage']==3600
    s.respond(s.pending()[0]['id'],'snooze',7200);now[0]+=3600;s.tick();assert s.pending()[0]['stage']==0
    s.respond(s.pending()[0]['id'],'done');now[0]+=86400;s.tick();assert '第 2 天' in s.pending()[0]['title'];s.close()

def test_v1_migration_backup_and_legacy_rules(tmp_path):
    root=tmp_path/'library';s=JournalStore(root,create=True)
    n=s.save_note('保留笔记','正文');r=Rule('2026-01-01T09:00:00',period='yearly')
    s.save_event('原纪念日','anniversary','',r);s.db.execute('PRAGMA user_version=1');s.close()
    s=JournalStore(root);assert s.notes()[0]['id']==n;assert type(parse_rule(s.events()[0]['rule'])) is Rule
    backups=list((root/'backups').glob('migration-v1-to-v2-*.sqlite3'));assert len(backups)==1
    old=sqlite3.connect(backups[0]);assert old.execute('PRAGMA user_version').fetchone()[0]==1;assert old.execute('SELECT body FROM notes').fetchone()[0]=='正文';old.close();s.close()
