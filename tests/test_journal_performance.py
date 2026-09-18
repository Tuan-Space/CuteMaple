"""Performance invariants, cancellation and snapshot correctness."""
import os,time,threading
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime,timedelta
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication,QEvent
from journal_store import JournalStore
from journal_recurrence import Rule,MilestoneRule,QueryCancelled,lunar_date
from journal_queries import QueryRunner,schedule_data
from journal_test_support import settle_queries

_APP=None
@pytest.fixture
def env(tmp_path):
    global _APP
    _APP=QApplication.instance() or QApplication([]);before=set(_APP.topLevelWidgets());clock=[datetime(2026,9,18,8).timestamp()]
    store=JournalStore(tmp_path/'library',create=True,clock=lambda:clock[0])
    yield _APP,store,clock
    for widget in set(_APP.topLevelWidgets())-before:widget.close();widget.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);_APP.processEvents();store.close()

def wait(condition):
    end=time.monotonic()+5
    while not condition():
        QApplication.processEvents();time.sleep(.001)
        assert time.monotonic()<end

@pytest.mark.parametrize('day',['2025-01-29','2025-07-25','2026-02-17','2026-09-18','2027-01-01'])
def test_fast_lunar_conversion_matches_library(day):
    from lunar_python import Solar
    d=datetime.fromisoformat(day);old=Solar.fromYmd(d.year,d.month,d.day).getLunar()
    assert lunar_date(d.year,d.month,d.day)==(old.getYear(),old.getMonth(),old.getDay())

@pytest.mark.parametrize('leap',[False,True])
@pytest.mark.parametrize('missing',['skip','last'])
def test_lunar_sequence_and_finite_count_checkpoints(leap,missing):
    from lunar_python import Lunar,LunarMonth,Solar
    base='2024-02-09T10:00:00';rule=Rule(base,calendar='lunar',period='monthly',include_leap=leap,missing=missing,count=90)
    original=Solar.fromYmd(2024,2,9).getLunar();expected=[];index=0
    while len(expected)<90:
        y,m,d=original.getYear(),original.getMonth(),original.getDay()
        if leap:month=LunarMonth.fromYm(y,m).next(index);y,m=month.getYear(),month.getMonth()
        else:total=y*12+abs(m)-1+index;y,m=total//12,total%12+1;month=LunarMonth.fromYm(y,m)
        if d<=month.getDayCount() or missing=='last':
            solar=Lunar.fromYmd(y,m,min(d,month.getDayCount())).getSolar();stamp=datetime(solar.getYear(),solar.getMonth(),solar.getDay(),10).timestamp();expected.append((index,stamp))
        index+=1
    actual=list(rule.between(0,datetime(2040,1,1).timestamp()))
    assert actual==expected
    assert list(rule.between(expected[70][1],datetime(2040,1,1).timestamp()))==expected[70:]
    assert list(rule.between(expected[2][1],expected[5][1]))==expected[2:6]

def test_batched_queries_do_not_grow_with_event_count(env):
    _,s,clock=env
    with s.db:
        import json
        for n in range(250):s.db.execute('INSERT INTO events(id,title,kind,body,rule,created,updated,cursor,archived,next_check) VALUES(?,?,?,?,?,?,?,?,0,?)',(str(n),str(n),'todo','',json.dumps(Rule('2000-01-01T10:00:00',period='monthly',calendar='lunar').mapping()),1,1,1,clock[0]+999999))
    queries=[];s.db.set_trace_callback(queries.append);rows=s.events(limit=50)
    assert len(rows)==50 and len(queries)==4
    queries.clear();s.events(offset=50,limit=50);assert len(queries)==4

def test_cache_expires_on_due_clock_rewind_and_business_changes(env):
    _,s,clock=env;clock[0]=datetime(2026,9,18,9,59,59).timestamp();identity=s.save_event('安排','todo','',Rule('2026-09-18T10:00:00'))
    assert s.events()[0]['_presentation']['countdown']=='今天'
    clock[0]+=2;assert s.events()[0]['_presentation']['countdown']=='今天 · 待处理'
    clock[0]-=86400;assert s.events()[0]['_presentation']['countdown']=='还有 1 天'
    revision=s.revision('events');s.save_note('无关','正文');assert s.revision('events')==revision
    s.set_pinned('event',identity,True);assert s.revision('events')!=revision and s.events()[0]['_presentation']['pinned']

def test_query_latest_request_wins_and_snapshot_uses_worker_connection(env):
    app,s,_=env;runner=QueryRunner(s.path);received=[];entered=threading.Event()
    def slow(reader,cancel):
        entered.set()
        while not cancel():time.sleep(.001)
        raise QueryCancelled()
    runner.submit('page','old',slow,lambda value,error:received.append(value));wait(entered.is_set)
    runner.submit('page','new',lambda reader,cancel:(threading.get_ident(),reader.rows('SELECT COUNT(*) n FROM events')[0]['n']),lambda value,error:received.append(value))
    wait(lambda:bool(received));assert received[0][0]!=threading.get_ident() and received[0][1]==0 and len(received)==1
    for n in range(20):runner.submit('page',n,lambda reader,cancel:n,lambda *_:None);wait(lambda:not runner.busy)
    assert len(runner._cache)<=12;runner.close()

def test_cancelled_rule_never_finishes_expensive_scan():
    rule=Rule('2000-01-01T10:00:00',period='monthly',calendar='lunar')
    with pytest.raises(QueryCancelled):rule.preview(after=datetime(2026,1,1).timestamp(),cancel=lambda:True)

def test_scheduler_stale_plan_cannot_recreate_edited_reminder(env):
    _,s,clock=env;identity=s.save_event('原计划','todo','',Rule('2026-09-18T07:00:00'));plans=schedule_data(s,clock[0],lambda:False)
    s.save_event('修改后','todo','',Rule('2026-09-20T10:00:00'),identity);s.tick(prepared=plans)
    assert not s.pending()

def test_async_lists_reuse_items_and_reject_old_filter(env):
    from journal_ui import JournalWindow
    _,s,clock=env
    for n in range(10):s.save_event(str(n),'todo','',Rule('2026-09-19T10:00:00'))
    w=JournalWindow(s,None);w.show();w.tabs.setCurrentIndex(1);settle_queries();first=w.events_list.item(0);w.refresh_events();settle_queries()
    assert w.events_list.item(0) is first
    w.event_search.setText('999');settle_queries();assert w.events_list.count()==1 and w.events_list.item(0).data(256) is None
    assert w.events_list.item(0).text()=='没有找到提醒'

def test_backup_worker_has_independent_connection_and_preserves_references(env,tmp_path):
    _,s,_=env;identity=s.save_note('保留','正文');source=tmp_path/'a.txt';source.write_text('attachment');attachment=s.attach(identity,source)
    values=[];worker=threading.Thread(target=lambda:values.append(s.backup_snapshot()));worker.start();worker.join(5)
    assert not worker.is_alive() and not values[0][0]
    assert s.attachment_path(attachment['relative']).is_file()

def test_async_image_keeps_small_original_and_does_not_modify_document(env,tmp_path):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QTextEdit
    from journal_jobs import ImageLoader
    _,s,_=env;editor=QTextEdit();editor.setPlainText('图片正文');editor.document().setModified(False)
    path=tmp_path/'small.png';image=QImage(160,90,QImage.Format_RGB32);image.fill(0);image.save(str(path))
    loader=ImageLoader(editor);loader.get('small.png',path,1000);wait(lambda:not loader.pending)
    assert next(iter(loader.cache.values())).size()==image.size()
    assert not editor.document().isModified()
    loader.close()

def test_leaving_page_cancels_pending_search_debounce(env):
    from journal_ui import JournalWindow
    from PySide6.QtTest import QTest
    _,s,_=env;w=JournalWindow(s,None);w.show();w.tabs.setCurrentIndex(1);settle_queries()
    w.event_search.setText('尚未发出的查询');assert w.search_timers[0].isActive()
    w.tabs.setCurrentIndex(4);QTest.qWait(220)
    assert not w.search_timers[0].isActive() and not w.queries.busy and not w._applying
    w.tabs.setCurrentIndex(1);w.event_search.setText('关闭时也取消');w.close()
    assert not any(timer.isActive() for timer in w.search_timers)
