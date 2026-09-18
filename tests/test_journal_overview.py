from journal_test_support import settle_queries
import os,json,sqlite3
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime
from pathlib import Path
import pytest
from PySide6.QtCore import QDateTime,QDate,QTime,Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor
from journal_store import JournalStore
from journal_recurrence import Rule,civil_timestamp
from journal_dates import DateTimeFields
from journal_holidays import Holidays,validate
from journal_ui import JournalWindow

@pytest.fixture
def ui(tmp_path):
    a=QApplication.instance() or QApplication([]);now=[civil_timestamp(datetime(2026,9,17,9),'Asia/Shanghai')]
    s=JournalStore(tmp_path/'library',create=True,clock=lambda:now[0]);w=JournalWindow(s,None);w.show();a.processEvents()
    yield a,s,w,now
    w.close();s.close()

def test_overview_segments_and_clear_trash(ui):
    a,s,w,_=ui
    settle_queries();assert w.nav[0].text()=='总览' and w.tabs.currentIndex()==0
    settle_queries();assert len(w.calendar_filter.buttons)==6 and len(w.event_kind.buttons)==4 and len(w.stat_period.buttons)==4
    w.tabs.setCurrentIndex(1);w.event_status.setCurrentIndex(2);settle_queries();assert w.page_title.text()=='提醒回收站'
    w.tabs.setCurrentIndex(2);w.trash.click();settle_queries();assert w.page_title.text()=='笔记回收站';settle_queries();assert w.trash.isChecked()
    settle_queries();assert not w.note_delete.isVisible()

def test_inline_lunar_preserves_instant_and_month_lengths(ui):
    d=DateTimeFields(QDateTime(QDate(2025,7,25),QTime(9,12,34)));before=d.dateTime()
    d.setLunar(True);settle_queries();assert d.dateTime()==before;settle_queries();assert d.month.currentText()=='闰六月';settle_queries();assert d.day.currentText()=='初一'
    settle_queries();assert d.day.count()==29;d.day.setCurrentIndex(28);settle_queries();assert d.day.currentText()=='廿九'
    d.setLunar(False);settle_queries();assert d.dateTime().time()==QTime(9,12,34)
    d.setDateTime(QDateTime(QDate(2024,2,29),QTime(12,1,1)));d.year.setValue(2025);settle_queries();assert d.dateTime().date()==QDate(2025,2,28)
    d.setDateTime(QDateTime(QDate(1900,1,1),QTime(9,0)));before=d.dateTime();d.setLunar(True);settle_queries();assert d.dateTime()==before;d.setLunar(False);settle_queries();assert d.dateTime()==before

def test_official_days_and_festival_priority(ui):
    _,s,w,_=ui;h=w.holidays
    settle_queries();assert h.info(QDate(2026,9,25))[0]=='中秋';settle_queries();assert h.info(QDate(2026,9,25))[1]['isOffDay']
    settle_queries();assert not h.info(QDate(2026,9,20))[1]['isOffDay'];settle_queries();assert h.info(QDate(2026,9,26))[0]=='十六'
    with pytest.raises(ValueError,match='尚未公布'):validate({'year':2027,'papers':[],'days':[]},2027)
    with pytest.raises(ValueError):validate({'year':2026,'papers':['https://bad.example'],'days':[{}]},2026)

def test_completed_trash_restore_and_purge(ui):
    _,s,w,now=ui
    identity=s.save_event('完成的日程','schedule','',Rule('2026-09-17T09:00:00'));s.tick();s.respond(s.pending()[0]['id'],'done');s.tick()
    settle_queries();assert len(s.events(status='completed'))==1 and not s.events(status='trash')
    s.archive_event(identity);settle_queries();assert not s.events(status='completed');settle_queries();assert len(s.events(status='trash'))==1
    s.restore_event(identity);settle_queries();assert len(s.events(status='completed'))==1
    s.archive_event(identity);s.purge_event(identity);settle_queries();assert not s.rows('SELECT * FROM events') and not s.rows('SELECT * FROM occurrences')

def test_single_exclusion_survives_purge_and_restart(ui):
    _,s,w,now=ui;identity=s.save_event('每日','todo','',Rule('2026-09-17T09:00:00',period='daily'));due=now[0]
    s.trash_occurrence(identity,due);s.purge_occurrence(identity,due);s.tick();settle_queries();assert not s.pending()
    w.calendar.setSelectedDate(QDate(2026,9,17));w.refresh_calendar();settle_queries();assert not w.calendar_items.get('2026-09-17')
    now[0]+=86400;s.tick();settle_queries();assert len(s.pending())==1;settle_queries();assert s.pending()[0]['due']==due+86400
    s.db.execute('UPDATE events SET cursor=?,next_check=0',(due-.001,));s.db.commit();s.tick();settle_queries();assert not s.rows('SELECT * FROM occurrences WHERE due=?',(due,))

def test_restore_recurring_ignores_deleted_interval(ui):
    _,s,w,now=ui;identity=s.save_event('重复','todo','',Rule('2026-09-17T09:00:00',period='daily'));s.tick();s.archive_event(identity)
    now[0]+=3*86400;s.restore_event(identity);s.tick();settle_queries();assert not s.pending()
    now[0]+=86400;s.tick();settle_queries();assert len(s.pending())==1

def test_expired_once_requires_new_time(ui):
    _,s,w,_=ui;identity=s.save_event('过期','todo','',Rule('2026-09-16T09:00:00'));s.archive_event(identity)
    with pytest.raises(ValueError,match='新的时间'):s.restore_event(identity)
    s.restore_event(identity,Rule('2026-09-18T09:00:00'));settle_queries();assert s.events()[0]['deleted'] is None

def test_rich_note_edits_and_original_preserved(ui):
    a,s,w,_=ui;text='# 标题\n\n- 第一项\n\n**重点**\n';identity=s.save_note('原稿',text);editor=w.note_editor;editor.load(s.notes()[0]);settle_queries();assert not editor.preview.isReadOnly();settle_queries();assert '标题' in editor.preview.toPlainText()
    editor.title.setText('改标题');editor.save();settle_queries();assert s.notes()[0]['body']==text
    editor.preview.moveCursor(__import__('PySide6.QtGui',fromlist=['QTextCursor']).QTextCursor.End);editor.preview.insertPlainText('新增内容');settle_queries();assert editor.save()
    settle_queries();assert '新增内容' in s.notes()[0]['body'];settle_queries();assert s.rows('SELECT body FROM note_originals')[0]['body']==text
    editor.mode.setCurrentIndex(1);settle_queries();assert '新增内容' in editor.edit.toPlainText();editor.edit.insertPlainText('源码');editor.mode.setCurrentIndex(0);settle_queries();assert '源码' in editor.preview.toPlainText()

def test_complex_markdown_no_silent_loss(ui):
    _,s,w,_=ui;text='# 标题\n\n<div>原样内容</div>\n\n[^a]: 注释';s.save_note('复杂',text);e=w.note_editor;e.load(s.notes()[0]);settle_queries();assert e.preview.literal
    e.title.setText('另一个标题');e.save();settle_queries();assert s.notes()[0]['body']==text

def test_migration_distinguishes_manual_archive(tmp_path):
    folder=tmp_path/'lib';s=JournalStore(folder,create=True)
    a=s.save_event('手动删除','todo','',Rule('2026-09-01T09:00:00'));b=s.save_event('自动完成','todo','',Rule('2026-09-01T09:00:00'));s.archive_event(a);s.db.execute('UPDATE events SET archived=1 WHERE id=?',(b,));s.db.commit();s.close()
    db=sqlite3.connect(folder/'journal.sqlite3');db.execute('ALTER TABLE events DROP COLUMN deleted');db.execute('ALTER TABLE events DROP COLUMN previous_archived');db.execute('PRAGMA user_version=2');db.commit();db.close()
    s=JournalStore(folder);settle_queries();assert s.events(status='trash')[0]['id']==a;settle_queries();assert s.events(status='completed')[0]['id']==b;settle_queries();assert list((folder/'backups').glob('migration-v2-to-v3-*'));s.close()

def test_journal_has_no_pet_pause_connection():
    root=Path(__file__).resolve().parents[1]
    settle_queries();assert 'journal_pause_active' not in (root/'pet_app.py').read_text(encoding='utf-8')
    settle_queries();assert 'visibilityChanged.connect(pet' not in (root/'journal_service.py').read_text(encoding='utf-8')

def test_background_attachment_preserves_owner_and_responsive_ui(ui,tmp_path):
    from PySide6.QtTest import QTest
    _,s,w,_=ui;e=w.note_editor;e.title.setText('附件笔记');e.preview.insertPlainText('正文');e.save();owner=e.identity
    path=tmp_path/'图片之外.txt';path.write_text('附件内容',encoding='utf-8');e.add_files([str(path)])
    assert not e.load()  # Switching is deferred until the owner attachment commits.
    for _ in range(100):
        if not e.file_job:break
        QTest.qWait(10)
    settle_queries();assert not e.file_job;settle_queries();assert s.attachments(owner)[0]['name']==path.name;settle_queries();assert 'attachments/' in s.notes()[0]['body'];settle_queries();assert path.exists()

def test_trash_switch_cannot_disable_pending_recording_controls(ui,monkeypatch):
    _,s,w,_=ui;w.tabs.setCurrentIndex(2);monkeypatch.setattr(w.note_editor,'finish',lambda:False)
    w.trash.click();settle_queries();assert not w.trash.isChecked();settle_queries();assert w.note_editor.isEnabled();settle_queries();assert w.page_title.text()=='笔记'

def test_rich_text_markdown_features_survive_edit(ui):
    _,s,w,_=ui;text='# 标题\n\n- [x] 完成\n- [ ] 未完成\n\n| 甲 | 乙 |\n| --- | --- |\n| 一 | 二 |\n\n```python\nprint(1)\n```\n\n[网页](https://example.org)\n\n![图](attachments/a.png)'
    s.save_note('格式',text);e=w.note_editor;e.load(s.notes()[0]);e.preview.moveCursor(QTextCursor.End);e.preview.insertPlainText('新增');e.save();body=s.notes()[0]['body']
    for token in ('标题','[x]','[ ]','甲','乙','print(1)','https://example.org','attachments/a.png','新增'):settle_queries();assert token in body

def test_pet_fall_and_wall_clock_continue_with_journal(tmp_path,monkeypatch):
    from journal_service import JournalService
    from test_live2d_integration import activate
    from test_live2d_only import frame
    import pet_app
    from pet_core import PetSettings
    a=QApplication.instance() or QApplication([]);monkeypatch.setattr(pet_app,'save_settings',lambda *_:None)
    p=pet_app.PetWindow(a,PetSettings(autostart=False));p.show();activate(p)
    s=JournalStore(tmp_path/'pet-library',create=True);j=JournalService(s,p);p.journal=j;j.window.updates.check=lambda *_:None
    p._detach_for_drag();p.move(300,100);p.motion_mode='fall';p._motion_y=100;p._motion_updated_at=pet_app.time.monotonic()-.1;frame(p);p.motion_timer.start()
    j.open();a.processEvents();settle_queries();assert not p.movement_paused;settle_queries();assert p.motion_timer.isActive();p._motion_step();settle_queries();assert p.y()>100
    p._attach_side('left');frame(p);p._climb_cadence.start();remaining=p._climb_cadence.remaining;p._climb_cadence.tick(1,blocked=p.movement_paused);settle_queries();assert p._climb_cadence.remaining==remaining-1
    j.window.close();settle_queries();assert not p.movement_paused
    j.close();p.journal=None
    for timer in p.findChildren(pet_app.QTimer):timer.stop()
    p.tray.hide();p.close();p._install_executor.shutdown(wait=False,cancel_futures=True)
