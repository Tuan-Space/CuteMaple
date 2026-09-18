from journal_test_support import settle_queries
import json
import os
from datetime import datetime

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtCore import QDate,QDateTime,QPoint,QTime,Qt
from PySide6.QtWidgets import (QApplication,QComboBox,QDialogButtonBox,QLineEdit,
                              QPlainTextEdit,QSpinBox)

from journal_event_editor import EventEditor
from journal_recurrence import Rule,MilestoneRule,civil_timestamp,parse_rule
from journal_store import JournalStore


@pytest.fixture
def environment(tmp_path):
    app=QApplication.instance() or QApplication([])
    now=civil_timestamp(datetime(2026,9,17,9),'Asia/Shanghai')
    store=JournalStore(tmp_path/'library',create=True,clock=lambda:now)
    yield app,store
    for widget in app.topLevelWidgets():
        if isinstance(widget,EventEditor):widget.close()
    store.close()


def open_editor(environment,**kwargs):
    app,store=environment;dialog=EventEditor(store,**kwargs)
    dialog.resize(560,620);dialog.show();app.processEvents()
    return app,store,dialog


def test_switching_kind_keeps_type_specific_controls_and_rule_values(environment):
    app,store,dialog=open_editor(environment)
    settle_queries();assert not dialog.milestone_box.isVisible()
    dialog.kind.setCurrentIndex(2);app.processEvents()
    settle_queries();assert dialog.milestone_box.isVisible()
    settle_queries();assert dialog._fields[dialog.period].isHidden()
    settle_queries();assert dialog.start.time()==QTime(10,0)
    dialog.hundreds.setChecked(True);dialog.day520.setChecked(True)
    dialog.custom_days.setText('30、1000')
    settle_queries();assert isinstance(dialog.rule(),MilestoneRule)
    settle_queries();assert dialog.rule().days==(30,520,1000)
    dialog.kind.setCurrentIndex(1);dialog.period.setCurrentIndex(4)
    dialog.calendar.setCurrentIndex(1);dialog.more.setChecked(True);app.processEvents()
    settle_queries();assert dialog.leap.isVisible() and dialog.strict.isVisible()
    dialog.period.setCurrentIndex(2);app.processEvents()
    settle_queries();assert not dialog.leap.isVisible() and not dialog.strict.isVisible()
    dialog.kind.setCurrentIndex(2);app.processEvents()
    settle_queries();assert dialog.hundreds.isChecked() and dialog.rule().days==(30,520,1000)


def test_lunar_leap_seconds_and_advanced_values_survive_save(environment):
    app,store,dialog=open_editor(environment)
    dialog.title.setText('闰月里的日程');dialog.kind.setCurrentIndex(1)
    instant=QDateTime(QDate(2025,7,25),QTime(9,12,34))
    dialog.start.setDateTime(instant);dialog.period.setCurrentIndex(4)
    dialog.calendar.setCurrentIndex(1)
    settle_queries();assert dialog.start.dateTime()==instant and dialog.start.month.currentText()=='闰六月'
    dialog.calendar.setCurrentIndex(0);dialog.calendar.setCurrentIndex(1)
    settle_queries();assert dialog.start.dateTime()==instant
    dialog.leap.setChecked(False);dialog.strict.setChecked(True);dialog.missing.setCurrentIndex(1)
    dialog.end_mode.setCurrentIndex(2);dialog.count.setValue(12);dialog.advances[2].setChecked(True)
    settle_queries();assert dialog.save()
    stored=parse_rule(store.events()[0]['rule'])
    settle_queries();assert stored.start=='2025-07-25T09:12:34'
    settle_queries();assert (stored.period,stored.calendar,stored.include_leap,stored.strict_leap,stored.missing,stored.count,stored.advances)==('monthly','lunar',False,True,'skip',12,(86400,))


@pytest.mark.parametrize('state',['completed','deleted'])
def test_finished_records_have_selectable_summary_and_no_editable_form(environment,state):
    app,store=environment
    identity=store.save_event('保留的内容','schedule','原来的说明',Rule('2026-09-18T09:12:34'))
    event=store.events()[0];event['archived']=int(state=='completed')
    if state=='deleted':event['deleted']=store.clock()
    before=store.rows('SELECT * FROM events')
    dialog=EventEditor(store,event);dialog.show();app.processEvents()
    settle_queries();assert dialog.read_only
    settle_queries();assert not dialog.findChildren(QLineEdit)
    settle_queries();assert not dialog.findChildren(QComboBox)
    settle_queries();assert not dialog.findChildren(QSpinBox)
    settle_queries();assert not dialog.findChildren(QPlainTextEdit)
    settle_queries();assert dialog.title.text()=='保留的内容'
    settle_queries();assert dialog.title.textInteractionFlags() & Qt.TextSelectableByMouse
    settle_queries();assert '09:12:34' in dialog.summary.text()
    settle_queries();assert dialog.buttons.button(QDialogButtonBox.Save) is None
    settle_queries();assert dialog.buttons.button(QDialogButtonBox.Close).isEnabled()
    settle_queries();assert dialog.save() is False
    settle_queries();assert store.rows('SELECT * FROM events')==before


def test_single_occurrence_summary_uses_due_instead_of_parent_start(environment):
    app,store=environment
    identity=store.save_event('每日安排','todo','说明',Rule('2026-09-01T09:00:00',period='daily'))
    event=store.events()[0]
    event.update(_occurrence=True,deleted=store.clock(),due=store.clock(),snapshot=json.dumps({'state':'completed'}))
    dialog=EventEditor(store,event);dialog.show();app.processEvents()
    settle_queries();assert dialog.read_only
    settle_queries();assert '2026年09月17日 09:00:00' in dialog.summary.text()
    settle_queries();assert '2026年09月01日' not in dialog.summary.text()
    settle_queries();assert '删除前状态：已完成' in dialog.summary.text()
    settle_queries();assert '重复：' not in dialog.summary.text()


def test_reschedule_override_validates_future_time_before_restoring(environment):
    app,store=environment
    identity=store.save_event('过期提醒','todo','',Rule('2026-09-16T09:00:00'))
    store.archive_event(identity);event=store.events(status='trash')[0]
    dialog=EventEditor(store,event,read_only=False);dialog.show();app.processEvents()
    settle_queries();assert not dialog.read_only and isinstance(dialog.title,QLineEdit)
    settle_queries();assert not dialog.save();app.processEvents()
    settle_queries();assert '未来' in dialog.error_label.text() and dialog.error_label.isVisible()
    settle_queries();assert store.events(status='trash')
    dialog.start.setDateTime(QDateTime(QDate(2026,9,18),QTime(10,0,7)))
    settle_queries();assert dialog.save()
    settle_queries();assert not store.events(status='trash')
    settle_queries();assert parse_rule(store.events()[0]['rule']).start=='2026-09-18T10:00:07'


@pytest.mark.parametrize('rule',[
    Rule('2026-09-10T09:00:00',period='daily',count=2),
    Rule('2026-09-10T09:00:00',period='daily',end='2026-09-12T09:00:00'),
    MilestoneRule('2026-09-10T09:00:00',yearly=False,hundreds=False,days=(1,)),
])
def test_reschedule_finite_rules_requires_a_future_occurrence(environment,rule):
    app,store=environment
    kind='anniversary' if isinstance(rule,MilestoneRule) else 'todo'
    identity=store.save_event('已经结束的安排',kind,'',rule)
    store.archive_event(identity);event=store.events(status='trash')[0]
    dialog=EventEditor(store,event,read_only=False);dialog.show();app.processEvents()
    settle_queries();assert not dialog.save()
    settle_queries();assert '未来' in dialog.error_label.text()
    settle_queries();assert store.events(status='trash')
    dialog.start.setDateTime(QDateTime(QDate(2026,9,18),QTime(10,0)))
    if isinstance(rule,Rule) and rule.end:
        dialog.end.setDateTime(QDateTime(QDate(2026,9,20),QTime(10,0)))
    settle_queries();assert dialog.save()
    settle_queries();assert not store.events(status='trash')


def test_validation_footer_is_fixed_and_scrolls_to_invalid_field(environment):
    app,store,dialog=open_editor(environment,initial_kind='anniversary')
    dialog.resize(480,420);app.processEvents()
    dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
    settle_queries();assert not dialog.save();app.processEvents();app.processEvents()
    settle_queries();assert dialog.error_label.isVisible() and dialog.title.hasFocus()
    settle_queries();assert not dialog.scroll.isAncestorOf(dialog.error_label)
    settle_queries();assert dialog.error_label.mapTo(dialog,QPoint()).y()<dialog.buttons.mapTo(dialog,QPoint()).y()
    settle_queries();assert dialog.scroll.viewport().rect().intersects(dialog.title.rect().translated(dialog.title.mapTo(dialog.scroll.viewport(),QPoint())))
    dialog.title.setText('纪念日');dialog.custom_days.setText('不合法')
    settle_queries();assert not dialog.save();app.processEvents();app.processEvents()
    settle_queries();assert '正整数' in dialog.error_label.text()
    settle_queries();assert dialog.custom_days.hasFocus()
    settle_queries();assert dialog.scroll.viewport().rect().intersects(dialog.custom_days.rect().translated(dialog.custom_days.mapTo(dialog.scroll.viewport(),QPoint())))
    dialog.custom_days.setText('30');app.processEvents()
    settle_queries();assert not dialog.error_label.isVisible()


def test_invalid_advanced_time_expands_group_and_preserves_database(environment):
    app,store,dialog=open_editor(environment)
    dialog.title.setText('结束时间错误');dialog.period.setCurrentIndex(2)
    dialog.start.setDateTime(QDateTime(QDate(2026,10,2),QTime(9,0)))
    dialog.end_mode.setCurrentIndex(1)
    dialog.end.setDateTime(QDateTime(QDate(2026,10,1),QTime(9,0)))
    settle_queries();assert not dialog.more.isChecked()
    settle_queries();assert not dialog.save();app.processEvents();app.processEvents()
    settle_queries();assert dialog.more.isChecked() and dialog.advanced.isVisible()
    settle_queries();assert '截止时间' in dialog.error_label.text()
    settle_queries();assert not store.events()


@pytest.mark.parametrize('width,columns',[(560,3),(480,2),(420,2)])
def test_short_window_layout_has_no_horizontal_overflow_and_fixed_actions(environment,width,columns):
    app,store,dialog=open_editor(environment,initial_kind='anniversary')
    dialog.resize(width,420);dialog.more.setChecked(True);app.processEvents();app.processEvents()
    settle_queries();assert dialog._advance_columns==columns
    settle_queries();assert dialog.advance_grid.getItemPosition(dialog.advance_grid.indexOf(dialog.advances[-1]))[:2]==(5//columns,5%columns)
    settle_queries();assert dialog.milestone_grid.getItemPosition(dialog.milestone_grid.indexOf(dialog.day1314))[:2]==(1,1)
    settle_queries();assert dialog.scroll.horizontalScrollBar().maximum()==0
    settle_queries();assert dialog.scroll.verticalScrollBar().maximum()>0
    for role in (QDialogButtonBox.Save,QDialogButtonBox.Cancel):
        button=dialog.buttons.button(role)
        settle_queries();assert dialog.rect().contains(button.rect().translated(button.mapTo(dialog,QPoint())))
    dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum());app.processEvents()
    for role in (QDialogButtonBox.Save,QDialogButtonBox.Cancel):settle_queries();assert dialog.buttons.button(role).isVisible()


def test_short_form_date_fields_fit_long_lunar_month_and_preserve_seconds(environment):
    from PySide6.QtWidgets import QStyle,QStyleOptionComboBox
    app,store,dialog=open_editor(environment)
    dialog.resize(420,420)
    dialog.start.setDateTime(QDateTime(QDate(2025,7,25),QTime(23,59,58)))
    dialog.calendar.setCurrentIndex(1);app.processEvents()
    for combo in (dialog.start.month,dialog.start.day):
        option=QStyleOptionComboBox();combo.initStyleOption(option)
        rect=combo.style().subControlRect(QStyle.CC_ComboBox,option,QStyle.SC_ComboBoxEditField,combo)
        settle_queries();assert rect.width()>=combo.fontMetrics().horizontalAdvance(combo.currentText())
    settle_queries();assert dialog.start.time()==QTime(23,59,58)
    settle_queries();assert dialog.scroll.horizontalScrollBar().maximum()==0


def test_changing_to_non_repeating_drops_hidden_end_condition(environment):
    app,store,dialog=open_editor(environment)
    dialog.period.setCurrentIndex(2);dialog.end_mode.setCurrentIndex(2);dialog.count.setValue(5)
    settle_queries();assert dialog.rule().count==5
    dialog.period.setCurrentIndex(0)
    settle_queries();assert dialog.rule().period=='once' and dialog.rule().count is None and dialog.rule().end is None


@pytest.mark.parametrize('size',[14,18,21,28])
def test_advanced_chevron_is_drawn_in_both_states_without_font_glyphs(environment,size):
    app,store,dialog=open_editor(environment)
    images=[]
    for expanded,text in ((False,'更多设置'),(True,'收起更多设置')):
        dialog.more.setChecked(expanded);app.processEvents()
        settle_queries();assert dialog.more.text()==text
        settle_queries();assert not dialog.more.icon().isNull()
        image=dialog.more.icon().pixmap(size,size).toImage()
        settle_queries();assert not image.isNull()
        visible=[image.pixelColor(x,y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x,y).alpha()>0]
        settle_queries();assert 0<len(visible)<size*size//2
        settle_queries();assert all(image.pixelColor(x,y).alpha()==0 for y in (0,size-1) for x in range(size))
        settle_queries();assert all(image.pixelColor(x,y).alpha()==0 for x in (0,size-1) for y in range(size))
        settle_queries();assert image==dialog.more.icon().pixmap(size,size).toImage()
        images.append(image)
    settle_queries();assert images[0]!=images[1]


def test_write_failure_stays_visible_without_accepting_or_losing_content(environment,monkeypatch):
    app,store,dialog=open_editor(environment)
    dialog.title.setText('尚未保存的安排')
    def fail(*args,**kwargs):raise OSError('资料库暂时不可写')
    monkeypatch.setattr(store,'save_event',fail)
    settle_queries();assert not dialog.save();app.processEvents()
    settle_queries();assert dialog.isVisible() and dialog.title.text()=='尚未保存的安排'
    settle_queries();assert dialog.error_label.isVisible() and '不可写' in dialog.error_label.text()
