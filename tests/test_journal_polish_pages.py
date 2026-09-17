"""Presentation stays separate from journal records and adapts to small windows."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from datetime import datetime
import pytest
from PySide6.QtCore import Qt,QDate
from PySide6.QtWidgets import QApplication,QLabel
from journal_design import ITEM_PRESENTATION_ROLE,WrappedItem
from journal_recurrence import Rule,civil_timestamp
from journal_store import JournalStore
from journal_ui import JournalWindow


@pytest.fixture
def page(tmp_path):
    app=QApplication.instance() or QApplication([])
    now=civil_timestamp(datetime(2026,9,17,9),'Asia/Shanghai')
    store=JournalStore(tmp_path/'library',create=True,clock=lambda:now)
    window=JournalWindow(store,None);window.resize(1000,700);window.show();app.processEvents()
    yield app,store,window
    window.close();store.close()


def test_structured_rows_keep_action_records(page):
    app,store,window=page
    event_id=store.save_event('明天一起散步','schedule','',Rule('2026-09-18T18:00:00'))
    note_id=store.save_note('今天的想法','一点记录')
    window.refresh_events();window.refresh_notes();window.calendar.setSelectedDate(QDate(2026,9,18));window.refresh_calendar()
    event=window.events_list.item(0);note=window.notes_list.item(0);day=window.day_items.item(0)
    assert event.data(Qt.UserRole)['id']==event_id
    assert event.data(ITEM_PRESENTATION_ROLE)['category']=='schedule'
    assert note.data(Qt.UserRole)['id']==note_id
    assert note.data(ITEM_PRESENTATION_ROLE)['title']=='今天的想法'
    assert day.data(Qt.UserRole)[1]['id']==event_id
    assert day.data(ITEM_PRESENTATION_ROLE)['title']=='明天一起散步'
    for widget in (window.events_list,window.notes_list,window.day_items,window.stat_list):
        assert isinstance(widget.itemDelegate(),WrappedItem)


def test_search_empty_state_is_not_an_actionable_record(page):
    _,store,window=page
    store.save_event('散步','todo','',Rule('2026-09-18T18:00:00'))
    window.event_search.setText('不存在的关键词');window.note_search.setText('不存在的关键词')
    for widget in (window.events_list,window.notes_list):
        item=widget.item(0)
        assert item.data(Qt.UserRole) is None
        assert item.data(ITEM_PRESENTATION_ROLE)['empty'] is True
        assert not item.flags() & Qt.ItemIsSelectable
    assert not window.note_restore.isEnabled()


def test_narrow_notes_use_full_content_width_and_restore(page):
    app,_,window=page
    window.tabs.setCurrentIndex(2);window.resize(620,420);app.processEvents()
    assert window.note_split.orientation()==Qt.Vertical
    assert window.notes_list.maximumHeight()==144
    assert window.note_editor.minimumHeight()>=window.note_editor.layout().totalMinimumHeightForWidth(window.note_editor.width())
    assert window.note_editor.pages.height()>=160
    window.resize(1000,700);app.processEvents()
    assert window.note_split.orientation()==Qt.Horizontal
    assert window.notes_list.maximumHeight()>1000


def test_settings_are_grouped_and_update_feedback_clears(page):
    _,_,window=page
    labels=window.tabs.widget(4).findChildren(QLabel)
    assert {'资料与备份','应用','录音'}<={label.text() for label in labels if label.objectName()=='section'}
    assert window.startup.text()=='开机自动启动'
    window.update_result({'new':True,'version':'99.0.0','body':'更新说明'})
    assert window.update_notes.text()=='更新说明' and not window.update_notes.isHidden()
    window.resize(620,420);QApplication.processEvents()
    assert window.nav[4].sizeHint().width()<=window.nav[4].width()
    assert '99.0.0' in window.nav[4].toolTip()
    window.update_result({'new':False})
    assert window.update_notes.isHidden() and window.nav[4].text()=='设置'


def test_unbroken_text_cannot_expand_settings_or_ledger_offscreen(page,monkeypatch):
    from PySide6.QtWidgets import QDialog
    app,store,window=page
    window.resize(620,420);window.tabs.setCurrentIndex(4)
    window.update_result({'new':True,'version':'99.0.0','body':'W'*1200});app.processEvents()
    assert window.width()==620 and window.tabs.widget(4).horizontalScrollBar().maximum()==0
    store.save_event('W'*200,'todo','',Rule('2026-09-18T18:00:00'))
    seen=[]
    def inspect(dialog):
        dialog.show();app.processEvents()
        assert dialog.width()<=dialog.screen().availableGeometry().width()
        seen.append(dialog);dialog.reject();return QDialog.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    window.event_ledger(store.events()[0]);assert len(seen)==1


def test_note_sections_never_overlap_when_toolbar_wraps(page,tmp_path):
    from PySide6.QtTest import QTest
    app,store,window=page
    identity=store.save_note('带附件的笔记','# 标题\n\n正文')
    for n in range(2):
        path=tmp_path/f'attachment-{n}.txt';path.write_text('附件内容',encoding='utf-8');store.attach(identity,path)
    editor=window.note_editor;editor.load(store.notes()[0]);window.tabs.setCurrentIndex(2)
    for width in (620,800,1000):
        window.resize(width,420)
        for mode in (0,1):
            editor.mode.setCurrentIndex(mode)
            for recording in (False,True):
                editor.show_recording() if recording else editor.media.hide()
                app.processEvents();QTest.qWait(25);app.processEvents()
                assert editor.pages.height()>=160
                assert editor.pages.geometry().bottom()<editor.files.geometry().top()
                assert editor.files.geometry().bottom()<editor.plus.mapTo(editor,editor.plus.rect().topLeft()).y()
                if recording:
                    assert editor.files.geometry().bottom()<editor.media.geometry().top()


def test_long_bubble_scrolls_without_hiding_response_actions(page):
    from PySide6.QtCore import QRect
    from journal_reminders import ReminderBubble
    app,store,_=page
    class Pet:
        _pet_hidden=True
        def isVisible(self):return False
        def _screen_area(self):return QRect(0,0,800,420)
    bubble=ReminderBubble(store,Pet());bubble.text.setText('记'*500);bubble.meta.setText('到期提醒 · 2026-09-17 09:00:00')
    bubble.follow();bubble.show();app.processEvents();bubble.follow();app.processEvents()
    assert bubble.pet._screen_area().contains(bubble.geometry())
    assert bubble.content_area.verticalScrollBar().maximum()>0
    assert bubble.text.height()>=bubble.text.heightForWidth(bubble.text.width())
    assert bubble.done.geometry().bottom()<bubble.height()
    bubble.close();bubble.deleteLater()
