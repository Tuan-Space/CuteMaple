import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import datetime
from PySide6.QtCore import Qt,QDate
from PySide6.QtWidgets import QApplication,QPushButton
import pytest
from journal_ui import JournalWindow
from journal_store import JournalStore
from journal_recurrence import Rule,MilestoneRule,civil_timestamp

@pytest.fixture
def view(tmp_path):
    a=QApplication.instance() or QApplication([])
    now=civil_timestamp(datetime(2026,9,17,9),'Asia/Shanghai')
    s=JournalStore(tmp_path/'library',create=True,clock=lambda:now)
    w=JournalWindow(s,None);w.resize(1000,700);w.show();a.processEvents()
    yield a,s,w
    w.close();s.close()

@pytest.mark.parametrize('count',[0,1,50,51,101])
def test_batches_no_dead_pages(view,count):
    a,s,w=view
    for n in range(count):s.save_event(f'提醒 {n}','todo','',Rule('2026-10-01T09:00:00'));s.save_note(f'笔记 {n}','正文')
    w.refresh_events();assert w.event_more==(count>50)
    assert len([w.events_list.item(i) for i in range(w.events_list.count()) if w.events_list.item(i).data(Qt.UserRole)])==min(count,50)
    w.tabs.setCurrentIndex(2);a.processEvents();assert w.note_more==(count>50)
    w.note_editor.title.setText('没有保存的内容');w.note_editor.timer.stop()
    for _ in range(3):
        bar=w.notes_list.verticalScrollBar();bar.setValue(bar.maximum());a.processEvents()
    assert w.notes_list.count()==max(count,1)
    assert w.note_editor.title.text()=='没有保存的内容'
    assert not any(b.text() in ('上一页','下一页') for b in w.findChildren(QPushButton))
    w.note_search.setText('找不到的关键词');a.processEvents();assert w.notes_list.count()==1;assert not w.note_more
    assert not w.note_delete.isEnabled()

def test_calendar_all_types_and_preserved_history(view):
    a,s,w=view
    for kind in ('todo','schedule','anniversary'):
        s.save_event(kind,kind,'',Rule('2026-09-17T09:00:00'))
    s.save_note('日记','内容');s.save_note('另一篇','内容')
    s.set_habit('water',True,1);s.db.execute("UPDATE habits SET next_due=? WHERE kind='water'",(s.clock(),));s.db.commit();s.tick()
    todo=next(x for x in s.events() if x['kind']=='todo');alert=next(x for x in s.pending() if x['event_id']==todo['id']);s.respond(alert['id'],'done')
    s.save_event('新的名字','todo','',Rule('2026-09-18T09:00:00'),todo['id'])
    w.calendar.setSelectedDate(QDate(2026,9,17));w.refresh_calendar()
    assert w.calendar.marks['2026-09-17']=={'todo','schedule','anniversary','note','habit'}
    assert any(row['title']=='todo' for kind,row,_ in w.calendar_items['2026-09-17'] if kind=='todo')
    w.calendar_filter.setCurrentIndex(4);assert w.calendar.marks['2026-09-17']=={'note'}
    assert len(w.calendar_items['2026-09-17'])==2
    s.trash_note(s.notes()[0]['id']);w.refresh_calendar();assert len(w.calendar_items['2026-09-17'])==1

def test_theme_and_milestone_form(view,monkeypatch):
    import monitor_ui
    from journal_editors import EventEditor
    a,s,w=view;w.tabs.setCurrentIndex(2);w.refresh_calendar()
    old=w.legend.text();monkeypatch.setattr(monitor_ui,'system_theme',lambda:monitor_ui.DARK);w.theme.apply()
    assert w._theme['background']=='#1d2835';assert w.legend.text()!=old
    d=EventEditor(s,parent=w,initial_kind='anniversary');d.show();a.processEvents()
    assert d.is_milestone();assert not d.advanced.isVisible();assert d.start.time().hour()==9
    d.title.setText('相识');d.hundreds.setChecked(True);d.day520.setChecked(True);d.custom_days.setText('30、1000');d.save()
    from journal_recurrence import parse_rule
    r=parse_rule(s.events()[0]['rule']);assert isinstance(r,MilestoneRule);assert r.days==(30,520,1000)
    d.close()

def test_single_bubble_hides_navigation_and_keeps_write_error(view,monkeypatch):
    from journal_reminders import ReminderBubble
    from PySide6.QtCore import QRect
    a,s,w=view;s.set_habit('water',True,1);s.db.execute("UPDATE habits SET next_due=? WHERE kind='water'",(s.clock(),));s.db.commit();s.tick()
    class Pet:
        _pet_hidden=True
        def isVisible(self):return False
        def _screen_area(self):return QRect(0,0,1000,700)
    b=ReminderBubble(s,Pet());b.refresh();a.processEvents()
    assert not b.previous.isVisible() and not b.following.isVisible() and not b.count.isVisible()
    def fail(*args):raise OSError('模拟写入失败')
    monkeypatch.setattr(s,'respond',fail);b.respond('done')
    b.refresh();assert b.meta.isVisible() and '未能保存' in b.meta.text();assert b.isVisible();b.close()

def test_archiving_cancels_future_calendar_entry(view):
    a,s,w=view
    identity=s.save_event('明天的事','schedule','',Rule('2026-09-18T09:00:00',advances=(86400,)))
    s.tick();assert s.pending();s.archive_event(identity)
    w.calendar.setSelectedDate(QDate(2026,9,18));w.refresh_calendar()
    assert not w.calendar_items.get('2026-09-18')
