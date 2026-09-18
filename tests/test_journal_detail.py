"""2.2.5: stable ordering, pin lifecycle, neutral surfaces and compact tools."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime,timedelta
import pytest
from PySide6.QtCore import QTime,QDateTime,QPoint,QRect,Qt,QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication,QMenu
from journal_store import JournalStore
from journal_recurrence import Rule

_APP=None


@pytest.fixture
def env(tmp_path):
    global _APP
    app=QApplication.instance() or QApplication([]);_APP=app
    existing=set(app.topLevelWidgets())
    clock=[datetime(2026,9,18,8).timestamp()]
    store=JournalStore(tmp_path/'library',create=True,clock=lambda:clock[0])
    yield app,store,clock
    for widget in set(app.topLevelWidgets())-existing:
        widget.close();widget.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
    store.close()


def event(store,day,title='安排',**kwargs):
    return store.save_event(title,'todo','',Rule(day,**kwargs))


def test_sort_before_paging_and_pin_group(env):
    _,s,clock=env;ids=[]
    for n in range(125):
        ids.append(event(s,(datetime(2026,9,18,10)+timedelta(days=124-n)).isoformat(),str(n)))
    rows=s.events(limit=None,status='active')
    assert [r['id'] for r in rows]==ids[::-1]
    s.set_pinned('event',ids[0],True);s.set_pinned('event',ids[30],True)
    expected=[ids[30],ids[0]]+[i for i in ids[::-1] if i not in (ids[30],ids[0])]
    assert [r['id'] for r in s.events(status='active',limit=50)]==expected[:50]
    assert [r['id'] for r in s.events(status='active',offset=50,limit=50)]==expected[50:100]
    assert s.events(search='124',status='active')[0]['id']==ids[-1]


def test_effective_due_excludes_completed_deleted_and_missed(env):
    _,s,clock=env;identity=event(s,'2026-09-18T10:00:00',period='daily',advances=(86400,))
    start=datetime(2026,9,18,10).timestamp();s.trash_occurrence(identity,start)
    with s.db:
        s.db.execute("INSERT INTO occurrences VALUES(?,?,'completed',?,?)",(identity,start+86400,start,'安排'))
        s.db.execute("INSERT INTO occurrences VALUES(?,?,'missed',NULL,?)",(identity,start-86400,'安排'))
    row=s.events()[0];assert row['_presentation']['due']==start+2*86400
    assert row['_presentation']['countdown']=='还有 2 天'
    with s.db:s.db.execute("INSERT INTO occurrences VALUES(?,?,'pending',NULL,?)",(identity,start-3600,'安排'))
    clock[0]=start
    assert s.events()[0]['_presentation']['countdown']=='今天 · 待处理'


def test_snooze_and_advance_never_change_arrangement_order(env):
    _,s,clock=env;first=event(s,'2026-09-18T09:00:00',advances=(3600,));second=event(s,'2026-09-18T10:00:00')
    s.tick();alert=next(r for r in s.pending() if r['event_id']==first);s.respond(alert['id'],'snooze',86400)
    assert [r['id'] for r in s.events()]==[first,second]
    assert s.events()[0]['_presentation']['due']==datetime(2026,9,18,9).timestamp()


def test_pin_and_creation_survive_edit_trash_restore_and_reopen(env):
    _,s,clock=env;old=s.save_note('旧笔记','正文');clock[0]+=10;new=s.save_note('新笔记','正文')
    clock[0]+=10;s.save_note('修改旧笔记','修改',old)
    assert [r['id'] for r in s.notes()]==[new,old]
    s.set_pinned('note',old,True);assert s.notes()[0]['id']==old
    s.trash_note(old);assert s.notes()[0]['id']==new
    s.restore_notes([old]);assert s.notes()[0]['id']==old
    root=s.root;s.close();s.__init__(root,clock=lambda:clock[0]);assert s.notes()[0]['id']==old
    s.purge_notes([old],allow_active=True);assert old not in s.pinned_ids('note')
    identity=event(s,'2026-09-20T10:00:00');s.set_pinned('event',identity,True);s.archive_event(identity)
    s.restore_reminders([('event',identity)]);assert identity in s.pinned_ids('event')
    s.archive_event(identity);s.purge_reminders([('event',identity)]);assert identity not in s.pinned_ids('event')


@pytest.mark.parametrize('hour,minute,day',[(9,59,18),(10,0,19),(23,59,19)])
def test_ten_oclock_default_and_kind_switch(env,hour,minute,day):
    from journal_event_editor import EventEditor
    _,s,clock=env;clock[0]=datetime(2026,9,18,hour,minute).timestamp();d=EventEditor(s)
    assert d.start.dateTime().date().day()==day and d.start.time()==QTime(10,0)
    d.start.setTime(QTime(16,27,31));d.kind.setCurrentIndex(2);assert d.start.time()==QTime(16,27,31)
    d.close()


def test_existing_time_units_and_lunar_roundtrip(env):
    from journal_event_editor import EventEditor
    _,s,_=env;identity=event(s,'2027-01-03T07:08:09');d=EventEditor(s,s.events()[0]);before=d.start.dateTime()
    assert d.start.year.text()=='2027年' and d.start.day.currentText()=='3日'
    assert d.start.hour.text()=='07时' and d.start.minute.text()=='08分' and d.start.second.text()=='09秒'
    d.calendar.setCurrentIndex(1);assert d.start.dateTime()==before
    assert all(not d.start.day.itemText(i).endswith('日') for i in range(d.start.day.count()))
    d.calendar.setCurrentIndex(0);assert d.start.dateTime()==before;d.close()


def test_single_row_overflow_commands_modes_and_readonly(env):
    from journal_editors import NoteEditor
    app,s,_=env;e=NoteEditor(s);e.resize(330,600);e.show();app.processEvents();e.preview.setPlainText('测试选区');e.preview.selectAll()
    e.populate_overflow();actions=e.overflow_menu.actions();assert actions and e.mode.isVisible() and e.heading.isVisible()
    quote=next(a for a in actions if a.text()=='引用段落');quote.trigger();assert e.quote.isChecked()
    for width in (330,540,850):
        e.resize(width,600);app.processEvents()
        for b in e.format_buttons:
            if b.isVisible():assert e.format_actions.rect().contains(QRect(b.mapTo(e.format_actions,QPoint()),b.size()))
        assert e.format_actions.height()==36
    e.mode.setCurrentIndex(1);app.processEvents();e.populate_overflow();assert not any(a.text()=='引用段落' for a in e.overflow_menu.actions())
    assert e.set_read_only(True);assert not e.format_toolbar.isVisible();e.timer.stop();e.close();app.removeEventFilter(e);e.deleteLater();app.processEvents()


def test_excerpt_and_neutral_palette(env):
    from journal_ui import JournalWindow
    from journal_design import palette
    value=JournalWindow.note_excerpt('# 标题\n\n**正文**\n- 项目\n![图](attachments/a.png)\n[附件](attachments/b.txt)')
    assert value=='标题 正文 项目'
    for dark in (False,True):
        for key in ('background','card','inset','button','selection'):
            color=palette(dark)[key];assert color[1:3]==color[3:5]==color[5:7]


@pytest.mark.parametrize('zone,now,due,expected',[
    ('Asia/Shanghai','2026-12-31T23:59:00','2027-01-01T00:01:00','还有 1 天'),
    ('Asia/Shanghai','2027-01-01T00:01:00','2026-12-31T23:59:00','已过 1 天'),
    ('America/New_York','2026-03-08T00:30:00','2026-03-09T00:01:00','还有 1 天'),
    ('America/New_York','2026-11-01T00:30:00','2026-11-02T00:01:00','还有 1 天'),
])
def test_countdown_uses_local_calendar_days(env,zone,now,due,expected):
    from zoneinfo import ZoneInfo
    _,s,clock=env;clock[0]=datetime.fromisoformat(now).replace(tzinfo=ZoneInfo(zone)).timestamp()
    event(s,due,zone=zone)
    assert s.events()[0]['_presentation']['countdown']==expected


def test_many_exclusions_do_not_hide_next_valid_arrangement(env):
    _,s,_=env;identity=event(s,'2026-09-18T10:00:00',period='daily')
    start=datetime(2026,9,18,10).timestamp()
    for n in range(135):s.trash_occurrence(identity,start+n*86400)
    assert s.events()[0]['_presentation']['due']==start+135*86400


def test_pin_cleanup_is_atomic_and_database_backup_keeps_pins(env,tmp_path):
    import sqlite3
    _,s,_=env;identity=s.save_note('保留','正文');s.set_pinned('note',identity,True)
    backup=sqlite3.connect(tmp_path/'backup.sqlite3');s.db.backup(backup)
    assert backup.execute('SELECT value FROM meta WHERE key=?',('pinned:note:'+identity,)).fetchone()[0]=='true';backup.close()
    s.db.execute("CREATE TRIGGER fail_pin_cleanup BEFORE DELETE ON meta BEGIN SELECT RAISE(ABORT,'fixture rollback'); END")
    with pytest.raises(sqlite3.IntegrityError):s.purge_notes([identity],allow_active=True)
    assert s.notes()[0]['id']==identity and identity in s.pinned_ids('note')


def test_common_dialog_buttons_are_chinese(env):
    from PySide6.QtWidgets import QMessageBox
    from journal_design import load_journal_fonts
    load_journal_fonts();box=QMessageBox();box.setStandardButtons(QMessageBox.Yes|QMessageBox.No)
    assert '是' in box.button(QMessageBox.Yes).text() and '否' in box.button(QMessageBox.No).text()
