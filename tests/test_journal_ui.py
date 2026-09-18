from journal_test_support import settle_queries
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('CUTEMAPLE_DISABLE_ACTIVITY','1')
os.environ.setdefault('CUTEMAPLE_DISABLE_LIVE2D','1')
from datetime import datetime
from PySide6.QtCore import Qt,QPoint,QRect
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QMouseEvent
from journal_store import JournalStore
from journal_recurrence import Rule
from journal_ui import JournalWindow
from journal_reminders import ReminderBubble
import pet_app
from pet_core import PetSettings

def app():return QApplication.instance() or QApplication([])

def test_sleep_audio_even_when_reactions_disabled(monkeypatch):
    a=app();monkeypatch.setattr(pet_app,'save_settings',lambda *_:None)
    p=pet_app.PetWindow(a,PetSettings(autostart=False,audio_enabled=False));p.show();a.processEvents()
    p._presentation_ready=True;p.awake_cycle_started=0
    monkeypatch.setattr(pet_app,'get_system_idle_seconds',lambda:10000)
    p._audio_activity(True);p._check_system_idle();settle_queries();assert p.sleep_phase is None
    p._audio_activity(False);p._check_system_idle();settle_queries();assert p.sleep_phase is None
    p.sleep_phase='loop';p._audio_activity(True);settle_queries();assert p.sleep_phase=='exit'
    p.tray.hide();p.close()

def test_double_click_only_opens_journal(monkeypatch):
    a=app();monkeypatch.setattr(pet_app,'save_settings',lambda *_:None)
    p=pet_app.PetWindow(a,PetSettings(autostart=False));p.show();a.processEvents();p._presentation_ready=True
    opened=[];monkeypatch.setattr(p,'open_journal',lambda:opened.append(True))
    monkeypatch.setattr(p,'_start_temporary',lambda *_:(_ for _ in ()).throw(AssertionError('double click animation')))
    e=QMouseEvent(QMouseEvent.MouseButtonDblClick,QPoint(50,50),QPoint(50,50),Qt.LeftButton,Qt.LeftButton,Qt.NoModifier)
    p.mouseDoubleClickEvent(e);settle_queries();assert opened==[True];settle_queries();assert p.suppress_release_click
    p.tray.hide();p.close()

def test_note_save_preview_calendar_and_trash(tmp_path):
    a=app();s=JournalStore(tmp_path/'library',create=True);w=JournalWindow(s,None);w.show();a.processEvents()
    w.note_editor.title.setText('测试手账');w.note_editor.mode.setCurrentIndex(1);w.note_editor.edit.setPlainText('# 标题\n\n- [ ] 清单\n<script>alert(1)</script>')
    settle_queries();assert w.note_editor.save();identity=w.note_editor.identity
    settle_queries();assert s.notes()[0]['title']=='测试手账'
    w.note_editor.mode.setCurrentIndex(0);settle_queries();assert '标题' in w.note_editor.preview.toPlainText()
    w.tabs.setCurrentIndex(2);w.refresh_calendar();settle_queries();assert any(x[0]=='note' for items in w.calendar_items.values() for x in items)
    s.trash_note(identity);settle_queries();assert not s.notes();s.trash_note(identity,True);settle_queries();assert s.notes()
    w.close();s.close()

def test_bubble_persistent_hidden_and_clickable(tmp_path):
    a=app();s=JournalStore(tmp_path/'library',create=True)
    s.save_event('测试','todo','',Rule(datetime.now().isoformat(timespec='seconds'),zone='Asia/Shanghai'));s.tick()
    class Pet:
        _pet_hidden=True
        def isVisible(self):return False
        def _screen_area(self):return QRect(-1920,0,1920,1080)
    b=ReminderBubble(s,Pet());b.refresh();a.processEvents()
    settle_queries();assert b.isVisible();settle_queries();assert not b.testAttribute(Qt.WA_TransparentForMouseEvents)
    settle_queries();assert Pet()._screen_area().contains(b.geometry())
    old=s.pending()[0]['id'];b.refresh();settle_queries();assert s.pending()[0]['id']==old
    b.respond('done');settle_queries();assert not b.isVisible();b.close();s.close()

def test_library_newer_schema_rejected(tmp_path):
    a=app();s=JournalStore(tmp_path/'library',create=True);s.db.execute('PRAGMA user_version=999');s.close()
    import pytest
    with pytest.raises(ValueError,match='较新版本'):JournalStore(tmp_path/'library')

def test_event_editor_opens_and_saves(tmp_path):
    from journal_editors import EventEditor
    a=app();s=JournalStore(tmp_path/'library',create=True);dialog=EventEditor(s);dialog.show();a.processEvents()
    dialog.title.setText('新事件');dialog.save();settle_queries();assert s.events()[0]['title']=='新事件';dialog.close();s.close()


def test_dark_scroll_page_paints_dark_on_light_system_palette(tmp_path,monkeypatch):
    import monitor_ui
    from PySide6.QtGui import QColor
    a=app();monkeypatch.setattr(monitor_ui,'system_theme',lambda:monitor_ui.DARK)
    s=JournalStore(tmp_path/'library',create=True);w=JournalWindow(s,None);w.show();a.processEvents()
    page=w.tabs.widget(0).widget();image=page.grab().toImage()
    settle_queries();assert image.pixelColor(image.width()-2,2)==QColor(w._theme['background'])
    from journal_editors import EventEditor
    from PySide6.QtWidgets import QScrollArea
    dialog=EventEditor(s,parent=w);dialog.show();a.processEvents()
    image=dialog.findChild(QScrollArea).widget().grab().toImage()
    settle_queries();assert image.pixelColor(image.width()-2,2)==QColor(w._theme['background'])
    dialog.close()
    w.close();s.close()
