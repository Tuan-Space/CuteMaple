from journal_test_support import settle_queries
"""Batch scope, preview identity and deletion lifecycle are independent."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMessageBox
from journal_design import WrappedItem
from journal_recurrence import Rule
from journal_store import JournalStore
from journal_ui import JournalWindow


@pytest.fixture
def page(tmp_path):
    app=QApplication.instance() or QApplication([])
    store=JournalStore(tmp_path/'library',create=True,clock=lambda:datetime(2026,9,17,12).timestamp())
    w=JournalWindow(store,None);w.show();app.processEvents()
    yield app,store,w
    w.note_editor.timer.stop();w.close();app.removeEventFilter(w.note_editor);w.deleteLater();app.processEvents();store.close()


def trash_notes(store,w,count=105):
    ids=[]
    for n in range(count):
        identity=store.save_note(f'记录 {n:03}','摘要内容');store.trash_note(identity);ids.append(identity)
    w.tabs.setCurrentIndex(2);w.trash.setChecked(True);w.reset_notes();settle_queries();QApplication.processEvents()
    return ids


def test_right_checks_do_not_change_preview_and_space_toggles(page):
    app,store,w=page;trash_notes(store,w,3)
    first=w.notes_list.item(0);second=w.notes_list.item(1);w.load_note(first);identity=w.note_editor.identity
    pos=WrappedItem.check_rect(w.notes_list.visualItemRect(second)).center().toPoint()
    QTest.mouseClick(w.notes_list.viewport(),Qt.LeftButton,pos=pos)
    settle_queries();assert w.note_editor.identity==identity
    settle_queries();assert w.note_checked=={second.data(Qt.UserRole)['id']}
    w.notes_list.setCurrentItem(second);QTest.keyClick(w.notes_list,Qt.Key_Space)
    settle_queries();assert not w.note_checked and w.note_editor.identity==identity
    QTest.mouseClick(w.notes_list.viewport(),Qt.LeftButton,pos=w.notes_list.visualItemRect(second).topLeft()+__import__('PySide6.QtCore',fromlist=['QPoint']).QPoint(30,20))
    settle_queries();assert w.note_editor.identity==second.data(Qt.UserRole)['id'] and not w.note_checked
    settle_queries();assert w.note_editor.read_only and w.note_editor.isEnabled()


def test_all_results_include_unloaded_pages_and_filter_or_exit_clears(page):
    _,store,w=page;ids=trash_notes(store,w)
    settle_queries();assert w.notes_list.count()==50
    w.select_all_notes(True);settle_queries();assert w.note_checked==set(ids)
    w.note_limit=100;w.refresh_notes();settle_queries();assert len(w.note_checked)==105
    settle_queries();assert all(w.notes_list.item(n).checkState()==Qt.Checked for n in range(100))
    w.note_search.setText('记录 00');settle_queries();assert not w.note_checked
    w.select_all_notes(True);settle_queries();assert len(w.note_checked)==10
    w.tabs.setCurrentIndex(0);settle_queries();assert not w.note_checked
    w.tabs.setCurrentIndex(2);settle_queries();assert not w.note_restore.isEnabled()


def test_empty_trash_has_no_checkboxes_or_mutation_tools(page):
    _,_,w=page;w.tabs.setCurrentIndex(2);w.trash.setChecked(True);w.reset_notes()
    settle_queries();assert w.notes_list.item(0).data(Qt.CheckStateRole) is None
    settle_queries();assert not w.note_restore.isEnabled() and not w.note_purge.isEnabled()
    settle_queries();assert w.note_new.isHidden() and w.note_editor.format_toolbar.isHidden()


def test_unchecked_preview_never_becomes_bulk_target_and_cancel_preserves_checks(page,monkeypatch):
    _,store,w=page;ids=trash_notes(store,w,3)
    w.restore_checked_notes();settle_queries();assert len(store.notes(trash=True))==3
    w.select_all_notes(True);monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.No)
    w.purge_checked_notes();settle_queries();assert w.note_checked==set(ids) and len(store.notes(trash=True))==3
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    w.purge_note(w.notes_list.item(1).data(Qt.UserRole))
    settle_queries();assert len(store.notes(trash=True))==2 and len(w.note_checked)==2


def test_delete_current_flushes_then_clears_identity_and_cannot_recreate(page,monkeypatch):
    _,store,w=page;identity=store.save_note('当前','原文');w.note_editor.load(store.notes()[0]);w.note_editor.title.setText('刚刚修改')
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    w.purge_note_identity(identity);w.note_editor.timer.timeout.emit()
    settle_queries();assert w.note_editor.identity is None and not store.rows('SELECT * FROM notes WHERE id=?',(identity,))
    settle_queries();assert not w.note_editor.timer.isActive()
    # SQLite's committed result survives an independent connection/restart.
    import sqlite3
    with sqlite3.connect(store.root/'journal.sqlite3') as db:
        settle_queries();assert db.execute('SELECT COUNT(*) FROM notes WHERE id=?',(identity,)).fetchone()[0]==0


def test_delete_other_note_does_not_finish_current_edit_and_failure_blocks_delete(page,monkeypatch):
    _,store,w=page;current=store.save_note('编辑中','正文');other=store.save_note('另一篇','正文');w.note_editor.load(store.rows('SELECT * FROM notes WHERE id=?',(current,))[0])
    calls=[];monkeypatch.setattr(w.note_editor,'finish',lambda:calls.append(True) or False);monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    w.purge_note_identity(other);settle_queries();assert not calls and w.note_editor.identity==current
    w.purge_note_identity(current);settle_queries();assert calls and store.rows('SELECT id FROM notes WHERE id=?',(current,))


def test_reminder_bulk_retains_expired_and_uses_full_selection(page):
    _,store,w=page
    future=store.save_event('明天','todo','',Rule('2026-09-18T09:00:00'));expired=store.save_event('过期','todo','',Rule('2026-09-16T09:00:00'))
    for identity in (future,expired):store.archive_event(identity)
    w.tabs.setCurrentIndex(1);w.event_status.setCurrentIndex(2);w.select_all_events(True);w.restore_checked_events()
    settle_queries();assert w.pending_reminders==[('event',expired)]
    settle_queries();assert w.event_checked=={('event',expired)}
    settle_queries();assert not w.event_bulk.attention.isHidden()
    settle_queries();assert store.rows('SELECT deleted FROM events WHERE id=?',(expired,))[0]['deleted'] is not None
    settle_queries();assert store.rows('SELECT deleted FROM events WHERE id=?',(future,))[0]['deleted'] is None
    w.event_search.setText('过期');settle_queries();assert not w.event_checked


def test_health_success_is_silent_and_today_visibly_actionable(page):
    _,_,w=page;w.status.hide();settle_queries();assert w.save_habit('water')
    settle_queries();assert w.status.isHidden() and w.calendar.today.objectName()=='secondary'


def test_calendar_open_from_trash_loads_requested_active_note_without_unlocking_trash(page):
    _,store,w=page
    identity=store.save_note('总览目标','正常正文');trash_notes(store,w,2)
    settle_queries();assert w.note_editor.read_only
    w.tabs.setCurrentIndex(0);row=store.rows('SELECT * FROM notes WHERE id=?',(identity,))[0]
    w.open_calendar_value('note',row)
    settle_queries();assert w.note_editor.identity==identity and w.note_editor.title.text()=='总览目标'
    settle_queries();assert w.note_editor.preview.toPlainText()=='正常正文'
    settle_queries();assert not w.note_editor.read_only and not w.trash.isChecked() and not w._notes_trash_state
    settle_queries();assert w.page_title.text()=='笔记' and not w.note_checked


def test_body_has_no_hidden_native_checkbox_hitbox(page):
    from PySide6.QtWidgets import QStyleOptionViewItem,QStyle
    _,store,w=page;trash_notes(store,w,3);item=w.notes_list.item(1);index=w.notes_list.indexFromItem(item)
    option=QStyleOptionViewItem();option.rect=w.notes_list.visualItemRect(item);option.widget=w.notes_list
    w.notes_list.itemDelegate().initStyleOption(option,index)
    rect=w.notes_list.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator,option,w.notes_list)
    QTest.mouseClick(w.notes_list.viewport(),Qt.LeftButton,pos=rect.center())
    settle_queries();assert item.checkState()==Qt.Unchecked and not w.note_checked
    settle_queries();assert w.note_editor.identity==item.data(Qt.UserRole)['id']


def test_pending_finite_reminder_entry_requires_rescheduling(page,monkeypatch):
    import journal_ui
    from PySide6.QtCore import QDateTime
    _,store,w=page
    identity=store.save_event('已过期有限重复','todo','',Rule('2026-09-15T09:00:00',period='daily',count=1));store.archive_event(identity)
    seen=[]
    class Edit:
        def __init__(self,*args,**kwargs):
            seen.append(kwargs);self.start=type('Field',(),{'setDateTime':lambda self,value:None})()
        def setWindowTitle(self,title):pass
        def exec(self):return 0
    monkeypatch.setattr(journal_ui,'EventEditor',Edit)
    w.restore_event_item(store.reminder_trash()[0])
    settle_queries();assert seen and seen[0]['read_only'] is False
    settle_queries();assert store.rows('SELECT deleted FROM events WHERE id=?',(identity,))[0]['deleted'] is not None
