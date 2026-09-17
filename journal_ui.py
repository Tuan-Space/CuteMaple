"""The shared reminder / notes / month calendar window."""
from __future__ import annotations
import calendar
import json
import threading
import time
import urllib.request
from datetime import date,datetime,timedelta
from pathlib import Path
from PySide6.QtCore import Qt,QDate,QTimer,QUrl,Signal,QObject,QEvent
from PySide6.QtGui import QDesktopServices,QTextCharFormat,QColor,QFont
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTabWidget,
 QLineEdit,QComboBox,QListWidget,QListWidgetItem,QSplitter,QCheckBox,QSpinBox,QTimeEdit,QCalendarWidget,
 QTableWidget,QTableWidgetItem,QHeaderView,QMessageBox,QFileDialog,QScrollArea,QFormLayout,QProgressBar,QSizePolicy)
from monitor_ui import ThemeBinding,system_theme
from monitor_ui import ThemedComboBox as QComboBox
from monitor_ui import ThemedSpinBox as QSpinBox,ThemedTimeEdit as QTimeEdit
from journal_store import HABITS
from journal_recurrence import Rule,MilestoneRule,parse_rule
from zoneinfo import ZoneInfo
from journal_design import MonthCalendar,Toggle,TYPE_NAMES,TYPE_ORDER,Segments,WrappedItem,ITEM_PRESENTATION_ROLE,CheckableJournalList,TrashBar
from PySide6.QtWidgets import QStackedWidget,QButtonGroup,QMenu,QDateEdit
from journal_design import JournalDateEdit as QDateEdit
from PySide6.QtCore import QTime
from journal_editors import EventEditor,NoteEditor


def app_version():
    from pet_core import resource_root
    return (resource_root()/'VERSION').read_text(encoding='utf-8').strip()


class UpdateCheck(QObject):
    finished=Signal(object)
    def __init__(self,store,parent=None):
        super().__init__(parent);self.store=store;self.busy=False
    def check(self,force=False):
        if self.busy or (not force and time.time()-self.store.setting('update_checked',0)<86400):return
        self.busy=True;self.store.set_setting('update_checked',time.time())
        def run():
            try:
                request=urllib.request.Request('https://api.github.com/repos/Tuan-Space/CuteMaple/releases/latest',headers={'Accept':'application/vnd.github+json','User-Agent':'CuteMaple/'+app_version()})
                with urllib.request.urlopen(request,timeout=10) as response:value=json.load(response)
                tag=str(value.get('tag_name','')).lstrip('v')
                numbers=tuple(int(v) for v in tag.split('.'))
                result={'new':numbers>tuple(int(v) for v in app_version().split('.')),'version':tag,'body':str(value.get('body','')),'url':'https://github.com/Tuan-Space/CuteMaple/releases/latest'}
            except Exception as error:result={'error':'暂时无法检查更新：'+str(error)}
            self.finished.emit(result)
        threading.Thread(target=run,name='journal-update-check',daemon=True).start()


class JournalWindow(QWidget):
    visibilityChanged=Signal(bool)
    def __init__(self,store,pet):
        super().__init__(None,Qt.Window);self.store,self.pet=store,pet
        self.event_limit=self.note_limit=50;self._filling=False;self._notes_trash_state=False;self.note_checked=set();self.event_checked=set();self.pending_reminders=[];self._last_page=0
        self.setWindowTitle('美腻枫 · 手账');self.setMinimumSize(620,420)
        area=self.screen().availableGeometry();self.resize(min(1000,area.width()-32),min(700,area.height()-48))
        self.setObjectName('journalWindow');self.theme=ThemeBinding(self,'journal')
        outer=QHBoxLayout(self);outer.setContentsMargins(16,20,24,16);outer.setSpacing(24)
        self.sidebar=QWidget();self.sidebar.setObjectName('journalSidebar');self.sidebar.setFixedWidth(112);nav=QVBoxLayout(self.sidebar);nav.setContentsMargins(0,4,12,0);nav.setSpacing(6)
        self.nav=[];group=QButtonGroup(self);group.setExclusive(True)
        for i,title in enumerate(['总览','提醒','笔记','统计','设置']):
            if i==4:nav.addStretch()
            b=self.button(title,lambda _,n=i:self.tabs.setCurrentIndex(n));b.setObjectName('nav');b.setCheckable(True);group.addButton(b);nav.addWidget(b);self.nav.append(b)
        outer.addWidget(self.sidebar)
        body=QVBoxLayout();body.setSpacing(12);heading=QHBoxLayout();self.page_title=QLabel('提醒');self.page_title.setObjectName('title');heading.addWidget(self.page_title);heading.addStretch();body.addLayout(heading)
        self.tabs=QStackedWidget();body.addWidget(self.tabs,1);self.status=QLabel();self.status.setWordWrap(True);self.status.setObjectName('muted');self.status.hide();body.addWidget(self.status);outer.addLayout(body,1)
        self.make_calendar();self.make_events();self.make_notes();self.make_statistics();self.make_settings()
        self.tabs.currentChanged.connect(self.refresh_current);self.updates=UpdateCheck(store,self);self.updates.finished.connect(self.update_result);self.refresh_current()

    def button(self,text,callback,kind=''):
        b=QPushButton(text);b.clicked.connect(callback);b.setCursor(Qt.PointingHandCursor)
        if kind:b.setObjectName(kind)
        return b
    def notice(self,text):self.status.setText(text);self.status.show();QTimer.singleShot(5000,self.status.hide)
    def refresh_theme(self):
        if not hasattr(self,'calendar'):return
        c=self._theme;kind=None if self.calendar_filter.currentIndex()==0 else TYPE_ORDER[self.calendar_filter.currentIndex()-1]
        self.legend.setText(' &nbsp; '.join(f'<span style="color:{c[k]}">●</span> {TYPE_NAMES[k]}' for k in TYPE_ORDER if not kind or k==kind));self.calendar.repaint_month()
    def page(self):
        page=QWidget();page.setObjectName('journalPage');layout=QVBoxLayout(page);layout.setContentsMargins(0,0,8,0);layout.setSpacing(10)
        scroll=QScrollArea();scroll.viewport().setObjectName('journalViewport');scroll.setWidgetResizable(True);scroll.setWidget(page);self.tabs.addWidget(scroll);return layout
    def prepare_list(self,widget):
        widget.setWordWrap(True);widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);widget.setTextElideMode(Qt.ElideNone);widget.setResizeMode(QListWidget.Adjust)
        widget.setVerticalScrollMode(QListWidget.ScrollPerPixel);widget.setItemDelegate(WrappedItem(widget));widget.setMinimumHeight(120)
    def list_item(self,widget,title,subtitle='',data=None,category='',status='',status_tone=''):
        item=QListWidgetItem(title+('\n'+subtitle if subtitle else ''));item.setData(Qt.UserRole,data)
        item.setData(ITEM_PRESENTATION_ROLE,dict(title=title,subtitle=subtitle,category=category,status=status,status_tone=status_tone));widget.addItem(item);return item
    def empty(self,widget,text,hint=''):
        item=QListWidgetItem(text);item.setFlags(Qt.NoItemFlags);item.setData(ITEM_PRESENTATION_ROLE,dict(title=text,subtitle=hint,empty=True));widget.addItem(item)
    def section_card(self,layout,title,description=''):
        card=QWidget();card.setObjectName('surface');card.setAttribute(Qt.WA_StyledBackground);box=QVBoxLayout(card);box.setContentsMargins(18,16,18,16);box.setSpacing(12)
        label=QLabel(title);label.setObjectName('section');box.addWidget(label)
        if description:
            caption=QLabel(description);caption.setObjectName('pageDescription');caption.setWordWrap(True);box.addWidget(caption)
        layout.addWidget(card);return box
    def eventFilter(self,obj,event):
        if obj is getattr(self,'note_editor',None) and event.type() in (QEvent.Resize,QEvent.LayoutRequest,QEvent.Show):self.schedule_note_fit()
        return super().eventFilter(obj,event)
    def schedule_note_fit(self):
        if getattr(self,'_note_fit_pending',False):return
        self._note_fit_pending=True;QTimer.singleShot(0,self.fit_note_editor)
    def fit_note_editor(self):
        self._note_fit_pending=False;editor=self.note_editor;layout=editor.layout()
        height=max(editor.minimumSizeHint().height(),layout.totalMinimumHeightForWidth(editor.width()))
        if editor.minimumHeight()!=height:editor.setMinimumHeight(height)
    def resizeEvent(self,event):
        self.sidebar.setFixedWidth(88 if self.width()<900 else 112)
        if hasattr(self,'nav') and len(self.nav)>4:self.update_nav_status()
        if hasattr(self,'habits_row'):
            from PySide6.QtWidgets import QBoxLayout
            self.habits_row.setDirection(QBoxLayout.TopToBottom if self.width()<720 else QBoxLayout.LeftToRight)
        if hasattr(self,'stat_cards_row'):
            from PySide6.QtWidgets import QBoxLayout
            self.stat_cards_row.setDirection(QBoxLayout.TopToBottom if self.width()<720 else QBoxLayout.LeftToRight)
        if hasattr(self,'note_split'):
            narrow=self.width()<800;self.note_split.setOrientation(Qt.Vertical if narrow else Qt.Horizontal)
            self.notes_list.setMaximumHeight(144 if narrow else 16777215);self.schedule_note_fit()
        if hasattr(self,'calendar_split'):
            narrow=self.width()<940;self.calendar_split.setOrientation(Qt.Vertical if narrow else Qt.Horizontal)
            self.calendar.setMinimumHeight(460);self.day_items.setMinimumHeight(130)
        super().resizeEvent(event)
    def make_events(self):
        layout=self.page();bar=QHBoxLayout();self.event_search=QLineEdit();self.event_search.setPlaceholderText('搜索提醒');bar.addWidget(self.event_search,1)
        bar.addWidget(self.button('＋ 新建提醒',lambda:self.edit_event(), 'primary'));layout.addLayout(bar)
        self.event_status=Segments();self.event_status.addItems(['进行中','已完成','回收站']);self.event_status.currentIndexChanged.connect(self.reset_events);layout.addWidget(self.event_status)
        row=QHBoxLayout();self.event_kind=Segments();self.event_kind.addItems(['全部','待办','日程','纪念日']);row.addWidget(self.event_kind);row.addStretch();self.event_count=QLabel();self.event_count.setObjectName('muted');row.addWidget(self.event_count);layout.addLayout(row)
        self.event_bulk=TrashBar();layout.addWidget(self.event_bulk);self.event_bulk.selectAllRequested.connect(self.select_all_events);self.event_bulk.restoreRequested.connect(self.restore_checked_events);self.event_bulk.deleteRequested.connect(self.purge_checked_events);self.event_bulk.attentionRequested.connect(self.show_pending_reminders)
        self.events_list=CheckableJournalList();self.prepare_list(self.events_list);self.events_list.checkChanged.connect(self.event_check_changed);self.events_list.itemClicked.connect(self.open_event_item);self.events_list.setContextMenuPolicy(Qt.CustomContextMenu);self.events_list.customContextMenuRequested.connect(self.event_menu);layout.addWidget(self.events_list,1)
        self.events_list.verticalScrollBar().valueChanged.connect(self.more_events)
        self.health_box=QWidget();layout.addWidget(self.health_box);layout=QVBoxLayout(self.health_box);layout.setContentsMargins(0,0,0,0);layout.setSpacing(10)
        row=QHBoxLayout();label=QLabel('照顾自己');label.setObjectName('section');row.addWidget(label);row.addStretch();row.addWidget(self.button('提醒时段',self.edit_habit_times,'secondary'));layout.addLayout(row)
        habits_row=QHBoxLayout();self.habits_row=habits_row;habits_row.setSpacing(12);self.habits={}
        for h in self.store.rows('SELECT * FROM habits'):
            card=QWidget();card.setObjectName('surface');card.setAttribute(Qt.WA_StyledBackground);cl=QVBoxLayout(card);cl.setContentsMargins(14,12,14,12);top=QHBoxLayout();name=QLabel(HABITS[h['kind']][0]);name.setObjectName('cardTitle');top.addWidget(name);top.addStretch();check=Toggle();check.setChecked(bool(h['enabled']));check.setAccessibleName(HABITS[h['kind']][0]+'提醒');top.addWidget(check);cl.addLayout(top)
            minutes=QSpinBox();minutes.setRange(1,1440);minutes.setValue(h['minutes']);minutes.setSuffix(' 分钟');minutes.setPrefix('每 ');cl.addWidget(minutes)
            start=QTimeEdit();end=QTimeEdit();start.setTime(QTime.fromString(h['start'],'HH:mm'));end.setTime(QTime.fromString(h['end'],'HH:mm'));self.habits[h['kind']]=(check,minutes,start,end)
            check.toggled.connect(lambda _,k=h['kind']:self.save_habit(k));minutes.editingFinished.connect(lambda k=h['kind']:self.save_habit(k));habits_row.addWidget(card,1)
        layout.addLayout(habits_row)
        self.event_search.textChanged.connect(self.reset_events);self.event_kind.currentIndexChanged.connect(self.reset_events)
    def edit_habit_times(self):
        d=QDialog(self);d.setWindowTitle('每日提醒时段');d.theme=ThemeBinding(d,'journal');d.resize(440,300);outer=QVBoxLayout(d);outer.setContentsMargins(20,20,20,20);outer.setSpacing(16)
        title=QLabel('留一段时间照顾自己');title.setObjectName('section');outer.addWidget(title);f=QFormLayout();f.setSpacing(12);outer.addLayout(f)
        fields={}
        for kind,(check,minutes,start,end) in self.habits.items():
            row=QHBoxLayout();a=QTimeEdit(start.time());b=QTimeEdit(end.time());a.setDisplayFormat('HH:mm');b.setDisplayFormat('HH:mm');row.addWidget(a);row.addWidget(QLabel('至'));row.addWidget(b);f.addRow(HABITS[kind][0],row);fields[kind]=(a,b)
        caption=QLabel('只在设定时段内提醒；起止时间相同表示全天。');caption.setObjectName('muted');caption.setWordWrap(True);outer.addWidget(caption)
        def save():
            for kind,(a,b) in fields.items():
                self.habits[kind][2].setTime(a.time());self.habits[kind][3].setTime(b.time())
                if not self.save_habit(kind):return
            d.accept()
        actions=QHBoxLayout();actions.addStretch();actions.addWidget(self.button('取消',d.reject,'secondary'));actions.addWidget(self.button('保存',save,'primary'));outer.addLayout(actions);d.exec();d.deleteLater()
    def save_habit(self,kind):
        check,minutes,start,end=self.habits[kind]
        try:self.store.set_habit(kind,check.isChecked(),minutes.value(),start.time().toString('HH:mm'),end.time().toString('HH:mm'));return True
        except Exception as error:QMessageBox.warning(self,'未能保存',str(error));return False
    def event_menu(self,pos):
        item=self.events_list.itemAt(pos)
        if not item or not item.data(Qt.UserRole):return
        self.events_list.setCurrentItem(item);menu=QMenu(self);menu.addAction('查看发生记录',self.event_ledger)
        self.add_event_actions(menu,item.data(Qt.UserRole))
        menu.exec(self.events_list.mapToGlobal(pos))
    def reset_events(self,*_):
        self.event_checked.clear();self.pending_reminders=[];self.event_bulk.feedback();self.event_limit=50;self.events_list.setCurrentRow(-1);self.events_list.verticalScrollBar().setValue(0);self.refresh_events()
    def more_events(self,value):
        bar=self.events_list.verticalScrollBar()
        if not self._filling and self.event_more and bar.maximum()>0 and value>=bar.maximum()-20:self.event_limit+=50;self.refresh_events()
    def event_summary(self,event):
        rule=parse_rule(event['rule']);now=self.store.clock();future=rule.preview(after=now,count=1)
        parts=[]
        if isinstance(rule,MilestoneRule):
            n=(datetime.fromtimestamp(now,ZoneInfo(rule.zone)).date()-rule.base.date()).days+1
            parts.append(f'已经第 {n} 天' if n>0 else '尚未开始')
        if future:
            stamp=future[0][1];due=datetime.fromtimestamp(stamp,ZoneInfo(rule.zone));parts.append(due.strftime('%m月%d日 %H:%M'))
            if isinstance(rule,MilestoneRule):
                parts.extend(rule.labels(stamp));remaining=(due.date()-datetime.fromtimestamp(now,ZoneInfo(rule.zone)).date()).days;parts.append('今天' if remaining==0 else f'还有 {remaining} 天')
            elif rule.period!='once':parts.append({'hourly':'每小时','daily':'每天','weekly':'每周','monthly':'每月','yearly':'每年'}[rule.period])
        else:parts.append('已删除' if event.get('deleted') is not None else '已完成' if event['archived'] else '等待处理')
        return ' · '.join(parts)
    def event_key(self,event):
        return ('occurrence',event['id'],event['due']) if event.get('_occurrence') else ('event',event['id'])
    def event_check_changed(self,item,checked):
        key=self.event_key(item.data(Qt.UserRole));self.event_checked.add(key) if checked else self.event_checked.discard(key);self.update_event_bulk()
    def update_event_bulk(self):
        keys=set(self.store.reminder_trash_keys(self.event_search.text(),[None,'todo','schedule','anniversary'][self.event_kind.currentIndex()]));self.event_checked.intersection_update(keys);self.event_bulk.update_selection(len(keys),len(self.event_checked))
    def select_all_events(self,checked):
        self.event_checked=set(self.store.reminder_trash_keys(self.event_search.text(),[None,'todo','schedule','anniversary'][self.event_kind.currentIndex()])) if checked else set();self.refresh_events()
    def refresh_events(self):
        trash=self.event_status.currentIndex()==2;self.health_box.setVisible(self.event_status.currentIndex()==0);self.event_bulk.setVisible(trash)
        self._filling=True;bar=self.events_list.verticalScrollBar();value=bar.value();previous=self.selected(self.events_list);key=self.event_key(previous) if previous else None;position=self.events_list.currentRow();self.events_list.clear()
        kind=[None,'todo','schedule','anniversary'][self.event_kind.currentIndex()]
        rows=self.store.reminder_trash(self.event_search.text(),kind,0,self.event_limit+1) if trash else self.store.events(self.event_search.text(),False,kind,0,self.event_limit+1,status=['active','completed'][self.event_status.currentIndex()]);self.event_more=len(rows)>self.event_limit
        for event in rows[:self.event_limit]:
            state=['','已完成','单次提醒' if event.get('_occurrence') else '整个计划'][self.event_status.currentIndex()]
            subtitle=('删除于 '+datetime.fromtimestamp(event['deleted']).strftime('%Y年%m月%d日 %H:%M')) if trash else self.event_summary(event)
            if trash and event.get('_occurrence'):subtitle=datetime.fromtimestamp(event['due']).strftime('%m月%d日 %H:%M')+' · '+subtitle
            item=self.list_item(self.events_list,event['title'],subtitle,event,event['kind'],state,'success' if self.event_status.currentIndex()==1 else 'muted')
            if trash:item.setCheckState(Qt.Checked if self.event_key(event) in self.event_checked else Qt.Unchecked)
            if self.event_key(event)==key:self.events_list.setCurrentItem(item)
        if rows and self.events_list.currentRow()<0:self.events_list.setCurrentRow(max(0,min(position,self.events_list.count()-1)))
        if not rows:
            if self.event_search.text():self.empty(self.events_list,'没有找到提醒','试试其他关键词，或清空搜索。')
            else:self.empty(self.events_list,['还没有提醒','还没有已完成的提醒','提醒回收站是空的'][self.event_status.currentIndex()],['点击“新建提醒”，记下下一件事。','完成的安排会留在这里。','删除的提醒可在这里恢复。'][self.event_status.currentIndex()])
        if trash:self.update_event_bulk()
        self.event_count.setText(['接下来的安排','已经完成的提醒','点击条目查看 · 勾选后批量处理'][self.event_status.currentIndex()]);self.update_heading();bar.setValue(value);self._filling=False
    def batch_feedback(self,bar,result,verb):
        done=len(result['restored'] if verb=='恢复' else result['deleted']);parts=[f'已{verb} {done} 项']
        if result['missing']:parts.append(f'{len(result["missing"])} 项已不存在')
        if result['pending']:parts.append(f'{len(result["pending"])} 项需要重新安排或确认提醒时间，仍保留在回收站')
        if result['cleanup_warning']:parts.append('记录已删除，附件清理未完成：'+str(result['cleanup_warning']))
        bar.feedback('；'.join(parts)+'。',bool(result['pending']))
    def restore_checked_events(self):
        if not self.event_checked:return
        try:result=self.store.restore_reminders(list(self.event_checked))
        except Exception as error:QMessageBox.warning(self,'未能恢复',str(error));return
        self.event_checked.difference_update(result['restored']+result['missing']);self.pending_reminders=list(result['pending']);self.refresh_events();self.batch_feedback(self.event_bulk,result,'恢复')
    def purge_checked_events(self):
        if not self.event_checked:return
        plans=sum(key[0]=='event' for key in self.event_checked);singles=len(self.event_checked)-plans
        if QMessageBox.question(self,'永久删除选中提醒',f'永久删除选中的 {len(self.event_checked)} 项？\n其中 {plans} 个整计划将连同发生记录删除；{singles} 次单独提醒只删除所选日期，且不会再次生成。\n此操作不能撤销。')!=QMessageBox.Yes:return
        try:result=self.store.purge_reminders(list(self.event_checked))
        except Exception as error:QMessageBox.warning(self,'未能删除',str(error));return
        self.event_checked.difference_update(result['deleted']+result['missing']);self.pending_reminders=[key for key in self.pending_reminders if key not in result['deleted']];self.refresh_events();self.batch_feedback(self.event_bulk,result,'删除')
    def show_pending_reminders(self):
        rows=[r for r in self.store.reminder_trash(limit=None) if self.event_key(r) in self.pending_reminders]
        dialog=QDialog(self);dialog.setWindowTitle('待处理的提醒');dialog.theme=ThemeBinding(dialog,'journal');dialog.resize(560,420);layout=QVBoxLayout(dialog);layout.setContentsMargins(18,18,18,18)
        hint=QLabel('这些项目仍在回收站。选择一项后，可重新安排时间或确认恢复。');hint.setWordWrap(True);hint.setObjectName('status');layout.addWidget(hint);items=QListWidget();self.prepare_list(items);layout.addWidget(items,1)
        for row in rows:self.list_item(items,row['title'],'单次提醒' if row.get('_occurrence') else '整个计划',row,row['kind'])
        if not rows:self.empty(items,'待处理项目已处理完毕')
        actions=QHBoxLayout();restore=self.button('处理选中项目',lambda:handle(),'secondary');restore.setEnabled(bool(rows));actions.addWidget(restore);actions.addStretch();actions.addWidget(self.button('关闭',dialog.accept,'secondary'));layout.addLayout(actions)
        def handle():
            row=self.selected(items)
            if row:self.restore_event_item(row);dialog.accept()
        if rows:items.setCurrentRow(0)
        dialog.exec();dialog.deleteLater()
    def edit_event(self,event=None):
        kind=['todo','todo','schedule','anniversary'][self.event_kind.currentIndex()]
        if EventEditor(self.store,event,self,initial_kind=kind).exec():self.refresh_events()

    def make_notes(self):
        layout=self.page();bar=QHBoxLayout();self.note_search=QLineEdit();self.note_search.setPlaceholderText('搜索笔记');bar.addWidget(self.note_search,1)
        self.trash=self.button('回收站',lambda:self.reset_notes(),'secondary');self.trash.setCheckable(True);bar.addWidget(self.trash);self.note_new=self.button('＋ 写笔记',self.new_note,'primary');bar.addWidget(self.note_new);layout.addLayout(bar)
        self.note_bulk=TrashBar();layout.addWidget(self.note_bulk);self.note_bulk.selectAllRequested.connect(self.select_all_notes);self.note_bulk.restoreRequested.connect(self.restore_checked_notes);self.note_bulk.deleteRequested.connect(self.purge_checked_notes)
        self.note_restore=self.note_bulk.restore;self.note_purge=self.note_bulk.delete
        self.note_split=QSplitter();self.note_split.setHandleWidth(16);self.notes_list=CheckableJournalList();self.prepare_list(self.notes_list);self.notes_list.setMinimumWidth(145);self.note_split.addWidget(self.notes_list)
        self.note_editor=NoteEditor(self.store);self.note_editor.pages.setMinimumHeight(160);self.note_editor.installEventFilter(self);self.note_split.addWidget(self.note_editor);self.note_split.setSizes([210,540]);self.note_split.setStretchFactor(1,1);layout.addWidget(self.note_split,1)
        self.note_delete=self.button('移到回收站',self.trash_note,'secondary');self.note_delete.hide();self.note_delete.setEnabled(False)
        self.notes_list.setContextMenuPolicy(Qt.CustomContextMenu);self.notes_list.customContextMenuRequested.connect(self.note_menu);self.notes_list.itemClicked.connect(self.load_note);self.notes_list.verticalScrollBar().valueChanged.connect(self.more_notes);self.note_search.textChanged.connect(self.reset_notes);self.note_editor.saved.connect(self.refresh_notes);self.note_editor.permanentDeleteRequested.connect(self.purge_note_identity);self.notes_list.checkChanged.connect(self.note_check_changed)
    def note_actions(self):
        trash=self.trash.isChecked();self.note_bulk.setVisible(trash);self.note_new.setVisible(not trash);self.note_delete.hide();self.note_delete.setEnabled(False)
        if trash:
            keys=set(self.store.note_ids(self.note_search.text(),True));self.note_checked.intersection_update(keys);self.note_bulk.update_selection(len(keys),len(self.note_checked))
    def note_check_changed(self,item,checked):
        identity=item.data(Qt.UserRole)['id'];self.note_checked.add(identity) if checked else self.note_checked.discard(identity);self.note_actions()
    def select_all_notes(self,checked):
        self.note_checked=set(self.store.note_ids(self.note_search.text(),True)) if checked else set();self.refresh_notes()
    def reset_notes(self,*_):
        if self.trash.isChecked()!=self._notes_trash_state:
            if not self.note_editor.load():self.trash.setChecked(self._notes_trash_state);return
            if not self.note_editor.set_read_only(self.trash.isChecked()):self.trash.setChecked(self._notes_trash_state);return
            self._notes_trash_state=self.trash.isChecked()
            if self.note_split.orientation()==Qt.Horizontal:self.note_split.setSizes([300,450] if self.trash.isChecked() else [210,540])
        self.note_checked.clear();self.note_bulk.feedback();self.note_limit=50;self.notes_list.setCurrentRow(-1);self.notes_list.verticalScrollBar().setValue(0);self.refresh_notes();self.update_heading()
    def more_notes(self,value):
        bar=self.notes_list.verticalScrollBar()
        if not self._filling and self.note_more and bar.maximum()>0 and value>=bar.maximum()-20:self.note_limit+=50;self.refresh_notes()
    def refresh_notes(self):
        self._filling=True;bar=self.notes_list.verticalScrollBar();value=bar.value();previous=self.selected(self.notes_list);identity=previous['id'] if previous else self.note_editor.identity;position=self.notes_list.currentRow();self.notes_list.clear();trash=self.trash.isChecked()
        rows=self.store.notes(self.note_search.text(),trash,0,self.note_limit+1);self.note_more=len(rows)>self.note_limit
        for note in rows[:self.note_limit]:
            subtitle=datetime.fromtimestamp(note['deleted'] if trash else note['created']).strftime('%m月%d日 %H:%M')
            if trash:subtitle='删除于 '+subtitle+'\n'+' '.join(note['body'].split())[:70]
            item=self.list_item(self.notes_list,note['title'],subtitle,note,category='note')
            if trash:item.setCheckState(Qt.Checked if note['id'] in self.note_checked else Qt.Unchecked)
            if note['id']==identity:self.notes_list.setCurrentItem(item)
        if rows and self.notes_list.currentRow()<0 and (trash or getattr(self,'_note_choose_adjacent',False)):
            self.notes_list.setCurrentRow(max(0,min(position,self.notes_list.count()-1)))
        if not rows:
            if self.note_search.text():self.empty(self.notes_list,'没有找到笔记','试试其他关键词。')
            else:self.empty(self.notes_list,'回收站是空的' if trash else '写下第一篇笔记','删除的笔记会留在这里。' if trash else '点击“写笔记”，收下今天的想法。')
        if trash and self.note_editor.identity not in {note['id'] for note in rows[:self.note_limit]}:self.note_editor.clear_note_identity()
        self.note_editor.setVisible(not trash or bool(rows))
        if (trash and self.note_editor.identity is None) or getattr(self,'_note_choose_adjacent',False):
            current=self.notes_list.currentItem()
            if current and current.data(Qt.UserRole):self.load_note(current)
            elif trash:self.note_editor.load()
        self._note_choose_adjacent=False;bar.setValue(value);self._filling=False;self.note_actions()
    def new_note(self):
        if self.note_editor.load():
            self.trash.setChecked(False);self._notes_trash_state=False;self.note_checked.clear();self.update_heading();self.note_editor.set_read_only(False);self.refresh_notes();self.note_editor.title.setFocus()
    def load_note(self,item):
        note=item.data(Qt.UserRole)
        if note and self.note_editor.load(note):self.note_editor.set_read_only(self.trash.isChecked())
    def trash_note(self,note=None):
        note=note or self.selected(self.notes_list)
        if not note:return
        current=note['id']==self.note_editor.identity
        if current and not self.note_editor.finish():return
        try:self.store.trash_note(note['id'])
        except Exception as error:QMessageBox.warning(self,'未能删除',str(error));return
        if current:self.note_editor.clear_note_identity();self._note_choose_adjacent=True
        self.refresh_notes()
    def restore_note(self,note=None):
        note=note or self.selected(self.notes_list)
        if note:self.apply_note_batch([note['id']],restore=True)
    def purge_note(self,note=None):
        note=note or self.selected(self.notes_list)
        if note:self.purge_note_identity(note['id'])
    def purge_note_identity(self,identity):
        rows=self.store.rows('SELECT * FROM notes WHERE id=?',(identity,))
        if not rows:return
        if QMessageBox.question(self,'永久删除笔记','永久删除“'+rows[0]['title']+'”和不再使用的附件？此操作不能撤销。')==QMessageBox.Yes:self.apply_note_batch([identity],allow_active=True)
    def restore_checked_notes(self):
        if self.note_checked:self.apply_note_batch(list(self.note_checked),restore=True)
    def purge_checked_notes(self):
        if self.note_checked and QMessageBox.question(self,'永久删除选中笔记',f'永久删除选中的 {len(self.note_checked)} 篇笔记及不再使用的附件？此操作不能撤销。')==QMessageBox.Yes:self.apply_note_batch(list(self.note_checked))
    def apply_note_batch(self,identities,restore=False,allow_active=False):
        current=self.note_editor.identity in identities
        if current and not self.note_editor.finish():return False
        try:result=self.store.restore_notes(identities) if restore else self.store.purge_notes(identities,allow_active=allow_active)
        except Exception as error:QMessageBox.warning(self,'未能恢复' if restore else '未能删除',str(error));return False
        changed=result['restored']+result['deleted']+result['missing'];self.note_checked.difference_update(changed)
        if current and self.note_editor.identity in changed:self.note_editor.clear_note_identity();self._note_choose_adjacent=True
        self.refresh_notes();self.batch_feedback(self.note_bulk,result,'恢复' if restore else '删除')
        if not self.trash.isChecked():self.notice(self.note_bulk.result.text())
        return True

    def make_calendar(self):
        layout=self.page();row=QHBoxLayout();self.calendar_filter=Segments();self.calendar_filter.addItems(['全部',*TYPE_NAMES.values()]);row.addWidget(self.calendar_filter);row.addStretch();layout.addLayout(row)
        self.legend=QLabel('');self.legend.setTextFormat(Qt.RichText);self.legend.setWordWrap(True);layout.addWidget(self.legend)
        self.calendar_split=QSplitter();self.calendar_split.setHandleWidth(16);self.calendar_split.setChildrenCollapsible(False);self.calendar=MonthCalendar();self.calendar_split.addWidget(self.calendar);right=QWidget();detail=QVBoxLayout(right);detail.setContentsMargins(0,0,0,0);detail.setSpacing(10);self.day_heading=QLabel();self.day_heading.setObjectName('section');detail.addWidget(self.day_heading)
        self.day_caption=QLabel();self.day_caption.setObjectName('muted');detail.addWidget(self.day_caption)
        self.day_items=QListWidget();self.prepare_list(self.day_items);detail.addWidget(self.day_items,1);self.day_more=self.button('加载更多',self.more_day,'secondary');detail.addWidget(self.day_more);self.calendar_split.addWidget(right);self.calendar_split.setSizes([540,260]);layout.addWidget(self.calendar_split,1)
        from journal_holidays import Holidays
        self.holidays=Holidays(self.store.root,self);self.calendar.holidays=self.holidays;row=QHBoxLayout();self.holiday_status=QLabel();self.holiday_status.setObjectName('muted');self.holiday_status.setWordWrap(True);row.addWidget(self.holiday_status,1);self.holiday_update=self.button('更新节假日',self.update_holidays,'secondary');row.addWidget(self.holiday_update);layout.addLayout(row);self.holidays.finished.connect(self.holidays_updated)
        self.calendar.contextRequested.connect(self.calendar_menu);self.day_items.setContextMenuPolicy(Qt.CustomContextMenu);self.day_items.customContextMenuRequested.connect(self.day_menu)
        self.calendar.currentPageChanged.connect(self.refresh_calendar);self.calendar.selectionChanged.connect(self.reset_day);self.calendar_filter.currentIndexChanged.connect(self.refresh_calendar);self.day_items.itemClicked.connect(self.open_calendar_item);self.calendar_items={};self.day_limit=100
    def refresh_calendar(self,*_):
        year,month=self.calendar.yearShown(),self.calendar.monthShown();first=datetime(year,month,1);start=first-timedelta(days=first.weekday());end=start+timedelta(days=42);begin,finish=start.timestamp(),end.timestamp();self.calendar_items={};filter_kind=None if self.calendar_filter.currentIndex()==0 else TYPE_ORDER[self.calendar_filter.currentIndex()-1]
        recorded=self.store.rows('SELECT o.*,e.kind,e.rule,e.body,e.archived,e.deleted FROM occurrences o JOIN events e ON e.id=o.event_id WHERE o.due>=? AND o.due<?',(begin,finish))
        seen={(r['event_id'],r['due']) for r in recorded}
        def add(kind,row,stamp):
            if filter_kind and kind!=filter_kind:return
            day=row['day'] if kind=='habit' else datetime.fromtimestamp(stamp).date().isoformat();self.calendar_items.setdefault(day,[]).append((kind,row,stamp))
        for event in self.store.rows('SELECT * FROM events WHERE archived=0'):
            rule=parse_rule(event['rule'])
            for _,stamp in rule.between(begin,finish-.001,limit=2000):
                if (event['id'],stamp) in seen or self.store.excluded(event['id'],stamp):continue
                row=dict(event)
                if isinstance(rule,MilestoneRule):row['title']+=' · '+' / '.join(rule.labels(stamp))
                add(event['kind'],row,stamp)
        for row in recorded:
            if row['deleted'] is not None or self.store.excluded(row['event_id'],row['due']) or row['state']=='cancelled':continue
            row['id']=row['event_id'];add(row['kind'],row,row['due'])
        for note in self.store.rows('SELECT * FROM notes WHERE deleted IS NULL AND created>=? AND created<? ORDER BY created',(begin,finish)):add('note',note,note['created'])
        for row in self.store.rows('SELECT day,COUNT(*) total,SUM(done=1) done FROM habit_log WHERE day>=? AND day<? GROUP BY day',(start.date().isoformat(),end.date().isoformat())):add('habit',row,0)
        self.holiday_status.setText(f'{year} 年放假安排已加载' if year in self.holidays.years else f'{year} 年暂无官方放假数据，可点击更新');self.calendar.set_marks({day:{item[0] for item in entries} for day,entries in self.calendar_items.items()})
        c=self._theme;self.legend.setText(' &nbsp; '.join(f'<span style="color:{c[k]}">●</span> {TYPE_NAMES[k]}' for k in TYPE_ORDER if not filter_kind or k==filter_kind));self.refresh_day()
    def refresh_day(self):
        q=self.calendar.selectedDate();day=q.toString('yyyy-MM-dd');self.day_heading.setText(q.toString('M月d日'));self.day_items.clear();entries=sorted(self.calendar_items.get(day,[]),key=lambda x:(TYPE_ORDER.index(x[0]),x[2]))
        self.day_caption.setText(['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][q.dayOfWeek()-1]+f' · {len(entries)} 条记录')
        for kind,row,stamp in entries[:self.day_limit]:
            title='照顾自己的日常' if kind=='habit' else row['title'];subtitle=f'已完成 {row["done"] or 0} / {row["total"]} 次' if kind=='habit' else datetime.fromtimestamp(stamp).strftime('%H:%M')
            state={'completed':'已完成','missed':'已错过','pending':'待处理'}.get(row.get('state'),'');row=dict(row);row['_calendar_due']=stamp
            self.list_item(self.day_items,title,subtitle,(kind,row),kind,state,'success' if row.get('state')=='completed' else 'warning' if row.get('state')=='missed' else 'muted')
        if not entries:self.empty(self.day_items,'这一天还没有记录','右键日期，即可添加提醒或笔记。')
        self.day_more.setVisible(len(entries)>self.day_limit)
    def reset_day(self):self.day_limit=100;self.refresh_day()
    def more_day(self):self.day_limit+=100;self.refresh_day()
    def open_calendar_item(self,item):
        value=item.data(Qt.UserRole)
        if not value:return
        kind,row=value
        if kind=='note':
            if not self.note_editor.finish():return
            self.trash.setChecked(False);self._notes_trash_state=False;self.note_checked.clear();self.note_editor.set_read_only(False)
            if self.note_editor.load(row):
                self.notes_list.setCurrentRow(-1);self.tabs.setCurrentIndex(2);self.refresh_notes();self.update_heading()
        elif kind=='habit':self.stat_date.setDate(self.calendar.selectedDate());self.tabs.setCurrentIndex(3)
        else:
            records=self.store.rows('SELECT * FROM events WHERE id=?',(row['id'],))
            if records:self.edit_event(records[0]);self.refresh_calendar()

    def make_statistics(self):
        layout=self.page();row=QHBoxLayout();self.stat_period=Segments();self.stat_period.addItems(['日','周','月','年']);self.stat_date=QDateEdit(QDate.currentDate());self.stat_date.setCalendarPopup(True);self.stat_date.setDisplayFormat('yyyy年M月d日');row.addWidget(self.stat_period);row.addWidget(self.stat_date);row.addStretch();layout.addLayout(row)
        cards=QHBoxLayout();cards.setSpacing(12);self.stat_cards_row=cards;self.stat_cards={};self.stat_progress={}
        for kind,(name,_) in HABITS.items():
            card=QWidget();card.setObjectName('surface');card.setAttribute(Qt.WA_StyledBackground);cl=QVBoxLayout(card);cl.setContentsMargins(18,16,18,16);cl.setSpacing(8);label=QLabel(name);label.setObjectName('cardTitle');cl.addWidget(label)
            big=QLabel('0 次');big.setObjectName('metricValue');cl.addWidget(big);small=QLabel();small.setObjectName('muted');small.setWordWrap(True);cl.addWidget(small)
            progress=QProgressBar();progress.setRange(0,100);progress.setTextVisible(False);progress.setFixedHeight(5);progress.setObjectName('habitProgress');cl.addWidget(progress)
            self.stat_cards[kind]=(big,small);self.stat_progress[kind]=progress;cards.addWidget(card,1)
        layout.addLayout(cards);header=QHBoxLayout();label=QLabel('健康记录');label.setObjectName('section');header.addWidget(label);header.addStretch();self.stat_summary=QLabel();self.stat_summary.setObjectName('muted');header.addWidget(self.stat_summary);layout.addLayout(header);self.stat_list=QListWidget();self.prepare_list(self.stat_list);layout.addWidget(self.stat_list,1)
        row=QHBoxLayout();self.correct_yes=self.button('更正为已完成',lambda:self.correct_stat(True),'secondary');self.correct_no=self.button('更正为未完成',lambda:self.correct_stat(False),'secondary');row.addWidget(self.correct_yes);row.addWidget(self.correct_no);row.addStretch();layout.addLayout(row)
        self.stat_list.itemSelectionChanged.connect(self.stat_actions);self.stat_period.currentIndexChanged.connect(self.refresh_statistics);self.stat_date.dateChanged.connect(self.refresh_statistics)
    def stat_actions(self):
        row=self.selected(self.stat_list);enabled=bool(row and row['answered'] is not None);self.correct_yes.setEnabled(enabled);self.correct_no.setEnabled(enabled)
    def refresh_statistics(self,*_):
        start,end=self.stat_range();rows={r['kind']:r for r in self.store.statistics(start,end)};self.stat_summary.setText(start+' — '+end);self.stat_list.clear()
        for kind,(big,small) in self.stat_cards.items():
            r=rows.get(kind,{});done=r.get('done') or 0;no=r.get('no') or 0;pending=r.get('pending') or 0;big.setText(f'{done} 次');small.setText(('完成率 '+f'{done/(done+no):.0%}' if done+no else '还没有回应')+f' · 待回应 {pending}')
            self.stat_progress[kind].setValue(round(100*done/(done+no)) if done+no else 0);self.stat_progress[kind].setAccessibleName(HABITS[kind][0]+'完成率')
        for row in self.store.rows('SELECT * FROM habit_log WHERE day>=? AND day<=? ORDER BY due DESC LIMIT 1000',(start,end)):
            state='待回应' if row['done'] is None else '已完成' if row['done'] else '未完成';self.list_item(self.stat_list,HABITS[row['kind']][0],datetime.fromtimestamp(row['due']).strftime('%m月%d日 %H:%M'),row,'habit',state,'muted' if row['done'] is None else 'success' if row['done'] else 'warning')
        if not self.stat_list.count():self.empty(self.stat_list,'这段时间还没有健康记录','在提醒页开启喝水、走动或看远处提醒。')
        self.stat_actions()
    def make_settings(self):
        layout=self.page();layout.setSpacing(16)
        sl=self.section_card(layout,'资料与备份','笔记、提醒与附件，都保存在这个资料库。')
        path=QLabel(str(self.store.root));path.setWordWrap(True);path.setTextInteractionFlags(Qt.TextSelectableByMouse);path.setObjectName('status');path.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);sl.addWidget(path)
        row=QHBoxLayout()
        for text,action in [('打开文件夹',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))),('导出备份',self.export_backup),('切换资料库',self.switch_library)]:row.addWidget(self.button(text,action))
        row.addStretch();sl.addLayout(row);caption=QLabel('备份包含笔记和全部附件。卸载会保留资料库。');caption.setObjectName('muted');caption.setWordWrap(True);sl.addWidget(caption)
        sl=self.section_card(layout,'应用')
        self.startup=Toggle('开机自动启动');self.startup.setChecked(bool(self.pet and self.pet.settings.autostart));self.startup.setEnabled(self.pet is not None);self.startup.toggled.connect(lambda on:self.pet.toggle_autostart(on) if self.pet else None);sl.addWidget(self.startup)
        row=QHBoxLayout();self.update_label=QLabel('版本 '+app_version());self.update_label.setObjectName('muted');self.update_label.setWordWrap(True);self.update_label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);row.addWidget(self.update_label,1);row.addWidget(self.button('检查更新',self.check_updates));row.addWidget(self.button('下载页面',lambda:QDesktopServices.openUrl(QUrl('https://github.com/Tuan-Space/CuteMaple/releases/latest')),'quiet'));sl.addLayout(row)
        self.update_notes=QLabel();self.update_notes.setWordWrap(True);self.update_notes.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);self.update_notes.setTextFormat(Qt.PlainText);self.update_notes.setObjectName('muted');self.update_notes.hide();sl.addWidget(self.update_notes)
        sl=self.section_card(layout,'录音','仅在你点击开始录音后使用麦克风。中断时保留的录音可在这里找回。');row=QHBoxLayout();row.addWidget(self.button('查看保留的录音',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root/'recordings')))));row.addStretch();sl.addLayout(row);layout.addStretch()
    def check_updates(self):
        if self.updates.busy:return
        self.update_label.setText('正在检查更新…');self.update_notes.hide();self.updates.check(True)
    def update_result(self,result):
        self.updates.busy=False
        if result.get('error'):self.update_label.setText('暂时无法检查更新');self.update_notes.setText('请稍后重试，也可以打开下载页面。');self.update_notes.show();return
        self._available_update=result.get('version','') if result['new'] else '';self.update_nav_status()
        if result['new']:self.update_label.setText('发现新版本 '+result['version']);self.update_notes.setText(result['body'][:1200]);self.update_notes.setVisible(bool(result['body']))
        else:self.update_label.setText('已是最新正式版本 · '+app_version());self.update_notes.hide()
    def update_nav_status(self):
        version=getattr(self,'_available_update','');button=self.nav[4]
        button.setText('设置 ·' if version else '设置');button.setToolTip('发现新版本 '+version if version else '设置');button.setAccessibleName('设置，有新版本 '+version if version else '设置')
    def update_heading(self):
        index=self.tabs.currentIndex();title=['总览','提醒','笔记','统计','设置'][index]
        if index==1:title=['提醒','已完成的提醒','提醒回收站'][self.event_status.currentIndex()]
        if index==2 and self.trash.isChecked():title='笔记回收站';self.trash.setText('返回笔记')
        elif hasattr(self,'trash'):self.trash.setText('回收站')
        self.page_title.setText(title)
    def refresh_current(self,*_):
        index=self.tabs.currentIndex()
        if self._last_page!=index:
            self.note_checked.clear();self.event_checked.clear();self.note_actions();self.update_event_bulk()
        self._last_page=index;self.nav[index].setChecked(True);self.update_heading()
        if index==0:self.refresh_calendar()
        elif index==1:self.refresh_events()
        elif index==2:self.refresh_notes()
        elif index==3:self.refresh_statistics()

    @staticmethod
    def selected(widget):
        item=widget.currentItem();return item.data(Qt.UserRole) if item else None

    def update_holidays(self):
        self.holiday_update.setEnabled(False);self.holiday_status.setText('正在更新节假日…');self.holidays.update_year(self.calendar.yearShown())
    def holidays_updated(self,message):
        self.holidays.busy=False;self.holidays.reload();self.holiday_update.setEnabled(True);self.refresh_calendar();self.holiday_status.setText(message)
    def open_event_item(self,item):
        event=item.data(Qt.UserRole)
        if not event:return
        if event.get('_occurrence'):
            parent=self.store.rows('SELECT * FROM events WHERE id=?',(event['id'],))
            if parent:EventEditor(self.store,{**parent[0],**event},self,read_only=True).exec()
        else:self.edit_event(event)
    def add_event_actions(self,menu,event):
        if event.get('deleted') is not None:
            menu.addAction('恢复',lambda:self.restore_event_item(event));menu.addAction('永久删除',lambda:self.purge_event_item(event))
        else:menu.addAction('移到回收站',lambda:self.delete_event_item(event))
    def delete_event_item(self,event):
        if QMessageBox.question(self,'移到回收站','删除“'+event['title']+'”？可在提醒回收站恢复。')!=QMessageBox.Yes:return
        self.store.archive_event(event['id']);self.refresh_current()
    def restore_event_item(self,event):
        try:
            if event.get('_occurrence'):
                result=self.store.restore_reminders([self.event_key(event)])
                if result['pending']:
                    if QMessageBox.question(self,'确认恢复这一次','恢复后可能立即提醒。仍要恢复所选日期的这一次提醒吗？')!=QMessageBox.Yes:return
                    self.store.restore_occurrence(event['id'],event['due'])
            else:
                result=self.store.restore_reminders([self.event_key(event)])
                if result['pending']:
                    d=EventEditor(self.store,{**event,'archived':0},self,read_only=False);d.setWindowTitle('选择新的提醒时间后恢复');d.start.setDateTime(__import__('PySide6.QtCore',fromlist=['QDateTime']).QDateTime.currentDateTime().addSecs(3600))
                    if not d.exec():return
            self.refresh_current();self.notice('已恢复')
        except Exception as error:QMessageBox.warning(self,'未能恢复',str(error))
    def purge_event_item(self,event):
        if QMessageBox.question(self,'永久删除','永久删除“'+event['title']+'”'+('这一次提醒' if event.get('_occurrence') else '及其发生记录')+'？此操作不能撤销。')!=QMessageBox.Yes:return
        try:self.store.purge_reminders([self.event_key(event)])
        except Exception as error:QMessageBox.warning(self,'未能删除',str(error));return
        self.refresh_current()
    def note_menu(self,pos):
        item=self.notes_list.itemAt(pos)
        if not item or not item.data(Qt.UserRole):return
        self.notes_list.setCurrentItem(item);menu=QMenu(self)
        note=item.data(Qt.UserRole)
        if self.trash.isChecked():menu.addAction('恢复',lambda:self.restore_note(note));menu.addAction('永久删除…',lambda:self.purge_note(note))
        else:menu.addAction('移到回收站',lambda:self.trash_note(note));menu.addAction('永久删除…',lambda:self.purge_note(note))
        menu.exec(self.notes_list.mapToGlobal(pos))
    def calendar_menu(self,day,pos):
        self.calendar.setSelectedDate(day);menu=QMenu(self)
        for title,kind in [('新建待办','todo'),('新建日程','schedule'),('新建纪念日','anniversary')]:menu.addAction(title,lambda _,k=kind:self.create_on_day(day,k))
        menu.addAction('写笔记',lambda:self.create_on_day(day,'note'))
        for kind,row,stamp in self.calendar_items.get(day.toString('yyyy-MM-dd'),[]):
            sub=menu.addMenu(TYPE_NAMES[kind]+' · '+(row.get('title') or '当天汇总'));self.calendar_item_actions(sub,kind,row,stamp)
        menu.exec(pos)
    def day_menu(self,pos):
        item=self.day_items.itemAt(pos)
        if not item or not item.data(Qt.UserRole):return
        kind,row=item.data(Qt.UserRole);menu=QMenu(self);self.calendar_item_actions(menu,kind,row,row['_calendar_due']);menu.exec(self.day_items.mapToGlobal(pos))
    def calendar_item_actions(self,menu,kind,row,stamp):
        if kind=='habit':menu.addAction('查看统计',lambda:self.open_calendar_value(kind,row));return
        menu.addAction('查看详情',lambda:self.open_calendar_value(kind,row))
        if kind=='note':menu.addAction('移到回收站',lambda:self.delete_calendar_note(row));return
        event=self.store.rows('SELECT * FROM events WHERE id=?',(row['id'],))
        if not event:return
        event=event[0];rule=parse_rule(event['rule'])
        if isinstance(rule,MilestoneRule) or rule.period!='once':menu.addAction('删除这一次',lambda:self.delete_calendar_occurrence(event,stamp))
        menu.addAction('删除整个计划' if isinstance(rule,MilestoneRule) or rule.period!='once' else '移到回收站',lambda:self.delete_event_item(event))
    def open_calendar_value(self,kind,row):
        item=QListWidgetItem();item.setData(Qt.UserRole,(kind,row));self.open_calendar_item(item)
    def delete_calendar_note(self,row):
        if self.note_editor.identity==row['id'] and not self.note_editor.finish():return
        self.store.trash_note(row['id'])
        if self.note_editor.identity==row['id']:self.note_editor.clear_note_identity()
        self.refresh_calendar()
    def delete_calendar_occurrence(self,event,stamp):
        if QMessageBox.question(self,'删除这一次',datetime.fromtimestamp(stamp).strftime('%Y年%m月%d日 %H:%M')+' 这次提醒移到回收站？其他周期不受影响。')==QMessageBox.Yes:
            self.store.trash_occurrence(event['id'],stamp);self.refresh_calendar()
    def create_on_day(self,day,kind):
        from PySide6.QtCore import QDateTime,QTime
        if kind=='note':
            self.tabs.setCurrentIndex(2);self.new_note();return
        d=EventEditor(self.store,parent=self,initial_kind=kind);d.start.setDateTime(QDateTime(day,QTime(9,0)));d.exec();self.refresh_calendar()

    def archive_event(self):
        event=self.selected(self.events_list)
        if event and QMessageBox.question(self,'删除计划','将此计划移到回收站，并停止未来提醒？')==QMessageBox.Yes:
            self.store.archive_event(event['id']);self.refresh_events()

    def event_ledger(self,event=None):
        event=event or self.selected(self.events_list)
        if not event:return
        dialog=QDialog(self);dialog.setWindowTitle(event['title']+' · 发生记录');dialog.theme=ThemeBinding(dialog,'journal');area=dialog.screen().availableGeometry();dialog.resize(min(780,area.width()-32),min(460,area.height()-48));layout=QVBoxLayout(dialog);layout.setContentsMargins(20,18,20,18);layout.setSpacing(12)
        title=QLabel(event['title']);title.setObjectName('section');title.setTextFormat(Qt.PlainText);title.setWordWrap(True);title.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);layout.addWidget(title)
        caption=QLabel('每一次安排的进度，都留在这里。');caption.setObjectName('muted');layout.addWidget(caption)
        table=QTableWidget(0,4);table.setHorizontalHeaderLabels(['原到期时间','状态','回应时间','提醒／延期时间']);table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents);table.horizontalHeader().setStretchLastSection(True);table.verticalHeader().hide();table.verticalHeader().setDefaultSectionSize(42);table.setAlternatingRowColors(True);table.setShowGrid(False)
        table.setSelectionBehavior(QTableWidget.SelectRows);table.setSelectionMode(QTableWidget.SingleSelection);table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row in self.store.rows('SELECT * FROM occurrences WHERE event_id=? ORDER BY due DESC LIMIT 1000',(event['id'],)):
            pos=table.rowCount();table.insertRow(pos)
            pending=self.store.rows("SELECT notify FROM alerts WHERE event_id=? AND due=? AND state='pending'",(event['id'],row['due']))
            values=[datetime.fromtimestamp(row['due']).strftime('%Y-%m-%d %H:%M:%S'),{'pending':'待处理','missed':'错过','completed':'已完成','cancelled':'已取消'}.get(row['state'],row['state']),datetime.fromtimestamp(row['answered']).strftime('%Y-%m-%d %H:%M:%S') if row['answered'] else '—',' / '.join(datetime.fromtimestamp(x['notify']).strftime('%m-%d %H:%M:%S') for x in pending)]
            for column,value in enumerate(values):
                item=QTableWidgetItem(value);item.setToolTip(value);table.setItem(pos,column,item)
                if column==1:item.setForeground(QColor(dialog._theme['success' if row['state']=='completed' else 'warning' if row['state']=='missed' else 'muted']))
            table.item(pos,0).setData(Qt.UserRole,row['due'])
        def refresh_ledger_theme():
            for pos in range(table.rowCount()):
                state=table.item(pos,1);state.setForeground(QColor(dialog._theme['success' if state.text()=='已完成' else 'warning' if state.text()=='错过' else 'muted']))
        dialog.refresh_theme=refresh_ledger_theme
        def complete_selected():
            row=table.currentRow()
            if row<0:return
            due=table.item(row,0).data(Qt.UserRole)
            if QMessageBox.question(dialog,'完成选中的一次','仅将 '+table.item(row,0).text()+' 这次发生标记为已完成？')!=QMessageBox.Yes:return
            try:
                if self.store.complete_occurrence(event['id'],due):
                    table.item(row,1).setText('已完成');table.item(row,1).setForeground(QColor(dialog._theme['success']));table.item(row,2).setText(datetime.now().strftime('%Y-%m-%d %H:%M:%S'));table.item(row,3).setText('—')
            except Exception as error:QMessageBox.warning(dialog,'尚未保存',str(error))
        layout.addWidget(table,1)
        if not table.rowCount():
            empty=QLabel('还没有发生记录，提醒到期后会显示在这里。');empty.setObjectName('status');empty.setWordWrap(True);layout.addWidget(empty)
        actions=QHBoxLayout();complete=self.button('将选中的这一次标记为已完成',complete_selected);complete.setEnabled(False);table.itemSelectionChanged.connect(lambda:complete.setEnabled(table.currentRow()>=0));actions.addWidget(complete);actions.addStretch();actions.addWidget(self.button('关闭',dialog.accept,'secondary'));layout.addLayout(actions)
        footnote=QLabel('显示最近 1000 次；已完成与错过分别记录。');footnote.setObjectName('muted');footnote.setWordWrap(True);layout.addWidget(footnote);dialog.exec();dialog.deleteLater()

    def stat_range(self):
        q=self.stat_date.date();start=date(q.year(),q.month(),q.day());kind=self.stat_period.currentIndex()
        if kind==1:start-=timedelta(days=start.weekday());end=start+timedelta(days=6)
        elif kind==2:start=start.replace(day=1);end=start.replace(day=calendar.monthrange(start.year,start.month)[1])
        elif kind==3:start=date(start.year,1,1);end=date(start.year,12,31)
        else:end=start
        return start.isoformat(),end.isoformat()
    def correct_stat(self,done):
        row=self.selected(self.stat_list)
        if row and row['answered'] is not None:self.store.correct_habit(row['id'],done);self.refresh_statistics()

    def export_backup(self):
        if not self.note_editor.finish():return
        if getattr(self,'backup_job',None):self.notice('备份正在进行');return
        path,_=QFileDialog.getSaveFileName(self,'完整备份','美腻枫手账-'+date.today().isoformat()+'.zip','ZIP (*.zip)')
        if path:
            from journal_jobs import FileJob
            self.backup_job=FileJob(self);self.store.background_backup=True;self.notice('正在导出完整备份…')
            def done(value,error):
                self.backup_job.deleteLater();self.backup_job=None;self.store.background_backup=False;self.notice('备份未完成：'+error if error else '备份已保存')
            self.backup_job.finished.connect(done);self.backup_job.start(lambda:self.store.export_backup(path))
    def switch_library(self):
        if not self.note_editor.finish():return
        from journal_library import select_library,save_library_pointer
        other=select_library(self)
        if other:
            save_library_pointer(other.root);other.close();self.status.setText('已选择新的资料库，重新打开程序后生效。');self.pet.quit_app()
    def showEvent(self,event):
        super().showEvent(event);self.visibilityChanged.emit(True);self.refresh_current()
    def closeEvent(self,event):
        if getattr(self,'backup_job',None):self.notice('正在完成备份，请稍候');event.ignore();return
        if not self.note_editor.finish():
            event.ignore()
            if self.note_editor.media.pending_finalize:QTimer.singleShot(200,self.close)
            return
        self.visibilityChanged.emit(False);super().closeEvent(event)
