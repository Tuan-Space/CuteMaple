"""The shared reminder / notes / month calendar window."""
from __future__ import annotations
import calendar
import json
import threading
import time
import urllib.request
from datetime import date,datetime,timedelta
from pathlib import Path
from PySide6.QtCore import Qt,QDate,QTimer,QUrl,Signal,QObject
from PySide6.QtGui import QDesktopServices,QTextCharFormat,QColor,QFont
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTabWidget,
 QLineEdit,QComboBox,QListWidget,QListWidgetItem,QSplitter,QCheckBox,QSpinBox,QTimeEdit,QCalendarWidget,
 QTableWidget,QTableWidgetItem,QHeaderView,QMessageBox,QFileDialog,QScrollArea,QFormLayout)
from monitor_ui import ThemeBinding,system_theme
from monitor_ui import ThemedComboBox as QComboBox
from journal_store import HABITS
from journal_recurrence import Rule
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


class MonthCalendar(QCalendarWidget):
    def paintCell(self,painter,rect,value):
        from lunar_python import Solar
        c=system_theme();selected=value==self.selectedDate();current=value.month()==self.monthShown()
        painter.save();painter.fillRect(rect,QColor(c['selection'] if selected else c['background']))
        painter.setPen(QColor(c['selected'] if selected else c['text'] if current else c['muted']))
        font=painter.font();font.setPointSize(10);painter.setFont(font)
        painter.drawText(rect.adjusted(0,1,0,-rect.height()//2),Qt.AlignCenter,str(value.day()))
        lunar=Solar.fromYmd(value.year(),value.month(),value.day()).getLunar()
        text=(lunar.getMonthInChinese()+'月') if lunar.getDay()==1 else lunar.getDayInChinese()
        font.setPointSize(8);painter.setFont(font)
        painter.drawText(rect.adjusted(0,rect.height()//2-1,0,-2),Qt.AlignCenter,text)
        if self.dateTextFormat(value).fontWeight()>=700:
            painter.setBrush(QColor(c['selected'] if selected else c['accent']));painter.setPen(Qt.NoPen);painter.drawEllipse(rect.right()-7,rect.top()+5,4,4)
        painter.restore()


class JournalWindow(QWidget):
    visibilityChanged=Signal(bool)
    def __init__(self,store,pet):
        super().__init__(None,Qt.Window);self.store,self.pet=store,pet;self.event_offset=0;self.note_offset=0
        self.setWindowTitle('美腻枫 · 枫叶手账');self.resize(900,650);self.setMinimumSize(620,420)
        self.setObjectName('journalWindow');self.theme=ThemeBinding(self,'journal')
        outer=QVBoxLayout(self);outer.setContentsMargins(20,16,20,16);outer.setSpacing(12)
        heading=QHBoxLayout();title=QLabel('枫叶手账');title.setObjectName('title');heading.addWidget(title);heading.addStretch();self.today=QLabel();self.today.setObjectName('metricName');heading.addWidget(self.today);outer.addLayout(heading)
        self.tabs=QTabWidget();outer.addWidget(self.tabs,1)
        self.make_events();self.make_notes();self.make_calendar();self.make_statistics();self.make_settings()
        self.status=QLabel('把要记得的事放在这里，剩下的慢慢来。');self.status.setObjectName('metricName');outer.addWidget(self.status)
        self.tabs.currentChanged.connect(self.refresh_current)
        self.updates=UpdateCheck(store,self);self.updates.finished.connect(self.update_result)
        self.refresh_current()

    def button(self,text,callback):
        button=QPushButton(text);button.clicked.connect(callback);return button

    def page(self,title):
        page=QWidget();page.setObjectName('journalPage');layout=QVBoxLayout(page);layout.setContentsMargins(8,12,8,8);layout.setSpacing(10)
        scroll=QScrollArea();scroll.viewport().setObjectName('journalViewport');scroll.setWidgetResizable(True);scroll.setWidget(page);self.tabs.addTab(scroll,title);return layout

    def make_events(self):
        layout=self.page('提醒');bar=QHBoxLayout();self.event_search=QLineEdit();self.event_search.setPlaceholderText('搜索标题或说明')
        self.event_kind=QComboBox();self.event_kind.addItems(['全部类型','待办','日程','纪念日']);self.history=QCheckBox('历史事件')
        bar.addWidget(self.event_search,1);bar.addWidget(self.event_kind);bar.addWidget(self.history);bar.addWidget(self.button('＋ 新提醒',lambda:self.edit_event()));layout.addLayout(bar)
        self.events_list=QListWidget();self.events_list.setWordWrap(True);self.events_list.itemDoubleClicked.connect(lambda _:self.edit_event(self.selected(self.events_list)));layout.addWidget(self.events_list,1)
        row=QHBoxLayout();row.addWidget(self.button('编辑',lambda:self.edit_event(self.selected(self.events_list))));row.addWidget(self.button('发生记录',self.event_ledger));row.addWidget(self.button('删除计划并归档',self.archive_event));row.addStretch();row.addWidget(self.button('上一页',lambda:self.event_page(-1)));row.addWidget(self.button('下一页',lambda:self.event_page(1)));layout.addLayout(row)
        layout.addWidget(QLabel('健康小提醒 · 默认关闭 · 回应后重新计时'))
        self.habits={}
        for h in self.store.rows('SELECT * FROM habits'):
            row=QHBoxLayout();check=QCheckBox(HABITS[h['kind']][0]);check.setChecked(bool(h['enabled']));minutes=QSpinBox();minutes.setRange(1,1440);minutes.setValue(h['minutes']);minutes.setSuffix(' 分钟')
            start=QTimeEdit();end=QTimeEdit();from PySide6.QtCore import QTime
            start.setTime(QTime.fromString(h['start'],'HH:mm'));end.setTime(QTime.fromString(h['end'],'HH:mm'));start.setDisplayFormat('HH:mm');end.setDisplayFormat('HH:mm')
            row.addWidget(check);row.addWidget(minutes);row.addWidget(QLabel('时段'));row.addWidget(start);row.addWidget(QLabel('至'));row.addWidget(end);row.addStretch()
            row.addWidget(self.button('保存',lambda _,k=h['kind']:self.save_habit(k)));layout.addLayout(row);self.habits[h['kind']]=(check,minutes,start,end)
        label=QLabel('起止时间相同表示全天；未回应时不反复催促。');label.setObjectName('metricName');layout.addWidget(label)
        for signal in (self.event_search.textChanged,self.event_kind.currentIndexChanged,self.history.toggled):signal.connect(self.reset_events)

    @staticmethod
    def selected(widget):
        item=widget.currentItem();return item.data(Qt.UserRole) if item else None

    def reset_events(self,*_):self.event_offset=0;self.refresh_events()
    def event_page(self,step):self.event_offset=max(0,self.event_offset+step*50);self.refresh_events()
    def refresh_events(self):
        self.events_list.clear();kind=[None,'todo','schedule','anniversary'][self.event_kind.currentIndex()]
        for event in self.store.events(self.event_search.text(),self.history.isChecked(),kind,self.event_offset,50):
            rule=Rule(**json.loads(event['rule']));item=QListWidgetItem(event['title']+'\n'+rule.describe());item.setData(Qt.UserRole,event);self.events_list.addItem(item)
        if self.events_list.count()==0:self.events_list.addItem('这一页还没有事件。点击“新提醒”记下第一件事。')

    def edit_event(self,event=None):
        if EventEditor(self.store,event,self).exec():self.refresh_events()

    def archive_event(self):
        event=self.selected(self.events_list)
        if event and QMessageBox.question(self,'删除计划','停止此计划的未来提醒，并保留发生记录到历史？')==QMessageBox.Yes:
            self.store.archive_event(event['id']);self.refresh_events()

    def event_ledger(self):
        event=self.selected(self.events_list)
        if not event:return
        dialog=QDialog(self);dialog.setWindowTitle(event['title']+' · 发生记录');dialog.resize(650,420);layout=QVBoxLayout(dialog)
        table=QTableWidget(0,4);table.setHorizontalHeaderLabels(['原到期时间','状态','回应时间','提醒／延期时间']);table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.setSelectionBehavior(QTableWidget.SelectRows);table.setSelectionMode(QTableWidget.SingleSelection);table.setEditTriggers(QTableWidget.NoEditTriggers)
        for row in self.store.rows('SELECT * FROM occurrences WHERE event_id=? ORDER BY due DESC LIMIT 1000',(event['id'],)):
            pos=table.rowCount();table.insertRow(pos)
            pending=self.store.rows("SELECT notify FROM alerts WHERE event_id=? AND due=? AND state='pending'",(event['id'],row['due']))
            values=[datetime.fromtimestamp(row['due']).strftime('%Y-%m-%d %H:%M:%S'),{'pending':'待处理','missed':'错过','completed':'已完成','cancelled':'已取消'}.get(row['state'],row['state']),datetime.fromtimestamp(row['answered']).strftime('%Y-%m-%d %H:%M:%S') if row['answered'] else '—',' / '.join(datetime.fromtimestamp(x['notify']).strftime('%m-%d %H:%M:%S') for x in pending)]
            for column,value in enumerate(values):table.setItem(pos,column,QTableWidgetItem(value))
            table.item(pos,0).setData(Qt.UserRole,row['due'])
        def complete_selected():
            row=table.currentRow()
            if row<0:return
            due=table.item(row,0).data(Qt.UserRole)
            if QMessageBox.question(dialog,'完成选中的一次','仅将 '+table.item(row,0).text()+' 这次发生标记为已完成？')!=QMessageBox.Yes:return
            try:
                if self.store.complete_occurrence(event['id'],due):
                    table.item(row,1).setText('已完成');table.item(row,2).setText(datetime.now().strftime('%Y-%m-%d %H:%M:%S'));table.item(row,3).setText('—')
            except Exception as error:QMessageBox.warning(dialog,'尚未保存',str(error))
        layout.addWidget(table);layout.addWidget(self.button('将选中的这一次标记为已完成',complete_selected));layout.addWidget(QLabel('显示最近 1000 次；已完成与错过分别记录，不会把错过的事件算作完成。'));dialog.exec()

    def save_habit(self,kind):
        check,minutes,start,end=self.habits[kind]
        self.store.set_habit(kind,check.isChecked(),minutes.value(),start.time().toString('HH:mm'),end.time().toString('HH:mm'));self.status.setText(HABITS[kind][0]+'提醒已保存')

    def make_notes(self):
        layout=self.page('笔记');bar=QHBoxLayout();self.note_search=QLineEdit();self.note_search.setPlaceholderText('搜索笔记');self.trash=QCheckBox('回收站')
        bar.addWidget(self.note_search,1);bar.addWidget(self.trash);bar.addWidget(self.button('＋ 新笔记',self.new_note));layout.addLayout(bar)
        split=QSplitter();left=QWidget();ll=QVBoxLayout(left);ll.setContentsMargins(0,0,0,0);self.notes_list=QListWidget();self.notes_list.setWordWrap(True);ll.addWidget(self.notes_list,1)
        row=QHBoxLayout();row.addWidget(self.button('上一页',lambda:self.note_page(-1)));row.addWidget(self.button('下一页',lambda:self.note_page(1)));ll.addLayout(row)
        for text,action in [('移到回收站',self.trash_note),('恢复',self.restore_note),('永久删除',self.purge_note)]:ll.addWidget(self.button(text,action))
        split.addWidget(left);self.note_editor=NoteEditor(self.store);split.addWidget(self.note_editor);split.setStretchFactor(1,3);split.setSizes([230,600]);layout.addWidget(split,1)
        self.notes_list.itemClicked.connect(self.load_note);self.note_search.textChanged.connect(self.reset_notes);self.trash.toggled.connect(self.reset_notes)
        self.note_editor.saved.connect(self.refresh_notes)

    def reset_notes(self,*_):self.note_offset=0;self.refresh_notes()
    def note_page(self,step):self.note_offset=max(0,self.note_offset+step*50);self.refresh_notes()
    def refresh_notes(self):
        self.notes_list.clear()
        for note in self.store.notes(self.note_search.text(),self.trash.isChecked(),self.note_offset,50):
            item=QListWidgetItem(note['title']+'\n'+datetime.fromtimestamp(note['created']).strftime('%Y-%m-%d %H:%M'));item.setData(Qt.UserRole,note);self.notes_list.addItem(item)
    def new_note(self):self.trash.setChecked(False);self.note_editor.setEnabled(True);self.note_editor.load()
    def load_note(self,item):
        note=item.data(Qt.UserRole)
        if note:self.note_editor.load(note);self.note_editor.setEnabled(not self.trash.isChecked())
    def trash_note(self):
        note=self.selected(self.notes_list)
        if note and self.note_editor.finish():self.store.trash_note(note['id']);self.note_editor.load();self.refresh_notes()
    def restore_note(self):
        note=self.selected(self.notes_list)
        if note:self.store.trash_note(note['id'],True);self.refresh_notes()
    def purge_note(self):
        note=self.selected(self.notes_list)
        if note and note['deleted'] and QMessageBox.question(self,'永久删除','永久删除这篇笔记？此操作不能从回收站恢复。')==QMessageBox.Yes:
            self.store.purge_note(note['id']);self.note_editor.load();self.refresh_notes()

    def make_calendar(self):
        layout=self.page('月历');bar=QHBoxLayout();bar.addWidget(QLabel('每一页日子，都有一点值得记住的事。'));bar.addStretch();self.calendar_filter=QComboBox();self.calendar_filter.addItems(['全部','事件','笔记','健康统计']);bar.addWidget(self.calendar_filter);layout.addLayout(bar)
        split=QSplitter();self.calendar=MonthCalendar();self.calendar.setFirstDayOfWeek(Qt.Monday);self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader);split.addWidget(self.calendar)
        self.day_items=QListWidget();self.day_items.setWordWrap(True);split.addWidget(self.day_items);split.setSizes([530,300]);layout.addWidget(split,1)
        self.calendar.currentPageChanged.connect(self.refresh_calendar);self.calendar.selectionChanged.connect(self.reset_day);self.calendar_filter.currentIndexChanged.connect(self.refresh_calendar);self.day_items.itemDoubleClicked.connect(self.open_calendar_item)
        self.calendar_items={};self.calendar_marks=[];self.day_limit=100
        layout.addWidget(self.button('显示这一天的更多记录',self.more_day))

    def refresh_calendar(self,*_):
        from lunar_python import Solar
        year,month=self.calendar.yearShown(),self.calendar.monthShown();start=datetime(year,month,1);end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
        begin,finish=start.timestamp(),end.timestamp();self.calendar_items={};selected=self.calendar_filter.currentIndex()
        if selected in (0,1):
            for event in self.store.rows('SELECT * FROM events WHERE archived=0'):
                rule=Rule(**json.loads(event['rule']))
                for _,stamp in rule.between(begin,finish-.001,limit=1000):
                    day=datetime.fromtimestamp(stamp).date().isoformat();self.calendar_items.setdefault(day,[]).append(('event',event,stamp))
        if selected in (0,2):
            for note in self.store.rows('SELECT * FROM notes WHERE deleted IS NULL AND created>=? AND created<? ORDER BY created',(begin,finish)):
                day=datetime.fromtimestamp(note['created']).date().isoformat();self.calendar_items.setdefault(day,[]).append(('note',note,note['created']))
        if selected in (0,3):
            for row in self.store.rows('SELECT day,COUNT(*) total,SUM(done=1) done FROM habit_log WHERE day>=? AND day<? GROUP BY day',(start.date().isoformat(),end.date().isoformat())):
                self.calendar_items.setdefault(row['day'],[]).append(('habit',row,0))
        for qdate in self.calendar_marks:self.calendar.setDateTextFormat(qdate,QTextCharFormat())
        self.calendar_marks=[]
        colors=system_theme()
        for day in range(1,calendar.monthrange(year,month)[1]+1):
            qdate=QDate(year,month,day);key=qdate.toString('yyyy-MM-dd');fmt=QTextCharFormat();lunar=Solar.fromYmd(year,month,day).getLunar()
            fmt.setToolTip(lunar.getMonthInChinese()+'月'+lunar.getDayInChinese()+f' · {len(self.calendar_items.get(key,[]))} 条')
            if key in self.calendar_items:fmt.setBackground(QColor(colors['status']));fmt.setForeground(QColor(colors['accent']));fmt.setFontWeight(700)
            self.calendar.setDateTextFormat(qdate,fmt);self.calendar_marks.append(qdate)
        self.refresh_day()

    def refresh_day(self):
        from lunar_python import Solar
        selected=self.calendar.selectedDate();day=selected.toString('yyyy-MM-dd');lunar=Solar.fromYmd(selected.year(),selected.month(),selected.day()).getLunar();self.day_items.clear()
        heading=QListWidgetItem(day+' · 农历'+lunar.getMonthInChinese()+'月'+lunar.getDayInChinese());self.day_items.addItem(heading)
        entries=sorted(self.calendar_items.get(day,[]),key=lambda v:v[2])
        for kind,row,stamp in entries[:self.day_limit]:
            text=f'健康提醒 · 已完成 {row["done"] or 0} / {row["total"]}' if kind=='habit' else datetime.fromtimestamp(stamp).strftime('%H:%M')+' · '+('笔记 · ' if kind=='note' else '')+row['title']
            item=QListWidgetItem(text);item.setData(Qt.UserRole,(kind,row));self.day_items.addItem(item)
        if len(entries)>self.day_limit:self.day_items.addItem(f'另有 {len(entries)-self.day_limit} 条，点击下方按钮继续查看。')
    def reset_day(self):self.day_limit=100;self.refresh_day()
    def more_day(self):self.day_limit+=100;self.refresh_day()
    def open_calendar_item(self,item):
        data=item.data(Qt.UserRole)
        if not data:return
        kind,row=data
        if kind=='note':self.tabs.setCurrentIndex(1);self.note_editor.setEnabled(True);self.note_editor.load(row)
        elif kind=='event':self.edit_event(row);self.refresh_calendar()
        else:self.tabs.setCurrentIndex(3)

    def make_statistics(self):
        layout=self.page('统计');bar=QHBoxLayout();self.stat_period=QComboBox();self.stat_period.addItems(['日','周','月','年']);self.stat_date=QCalendarWidget();self.stat_date.setMaximumHeight(210)
        bar.addWidget(QLabel('统计范围'));bar.addWidget(self.stat_period);bar.addStretch();layout.addLayout(bar);layout.addWidget(self.stat_date)
        self.stat_summary=QLabel();self.stat_summary.setWordWrap(True);self.stat_summary.setObjectName('status');layout.addWidget(self.stat_summary)
        self.stat_list=QListWidget();layout.addWidget(self.stat_list,1);bar=QHBoxLayout();bar.addWidget(self.button('更正为已完成',lambda:self.correct_stat(True)));bar.addWidget(self.button('更正为未完成',lambda:self.correct_stat(False)));bar.addStretch();layout.addLayout(bar)
        self.stat_period.currentIndexChanged.connect(self.refresh_statistics);self.stat_date.selectionChanged.connect(self.refresh_statistics)
    def stat_range(self):
        q=self.stat_date.selectedDate();start=date(q.year(),q.month(),q.day());kind=self.stat_period.currentIndex()
        if kind==1:start-=timedelta(days=start.weekday());end=start+timedelta(days=6)
        elif kind==2:start=start.replace(day=1);end=start.replace(day=calendar.monthrange(start.year,start.month)[1])
        elif kind==3:start=date(start.year,1,1);end=date(start.year,12,31)
        else:end=start
        return start.isoformat(),end.isoformat()
    def refresh_statistics(self,*_):
        start,end=self.stat_range();rows={r['kind']:r for r in self.store.statistics(start,end)};lines=[f'{start} — {end}']
        for kind,(name,_) in HABITS.items():
            r=rows.get(kind,{});done=r.get('done') or 0;no=r.get('no') or 0;rate=f'{done/(done+no):.0%}' if done+no else '—'
            lines.append(f'{name}：已完成 {done} · 未完成 {no} · 待回应 {r.get("pending") or 0} · 完成率 {rate}')
        self.stat_summary.setText('\n'.join(lines));self.stat_list.clear()
        for row in self.store.rows('SELECT * FROM habit_log WHERE day>=? AND day<=? ORDER BY due DESC LIMIT 1000',(start,end)):
            state='待回应' if row['done'] is None else '已完成' if row['done'] else '未完成';item=QListWidgetItem(datetime.fromtimestamp(row['due']).strftime('%Y-%m-%d %H:%M')+' · '+HABITS[row['kind']][0]+' · '+state);item.setData(Qt.UserRole,row);self.stat_list.addItem(item)
    def correct_stat(self,done):
        row=self.selected(self.stat_list)
        if row and row['answered'] is not None:self.store.correct_habit(row['id'],done);self.refresh_statistics()

    def make_settings(self):
        layout=self.page('设置');title=QLabel('你的资料，只留在你选择的地方');title.setObjectName('section');layout.addWidget(title)
        path=QLabel(str(self.store.root));path.setTextInteractionFlags(Qt.TextSelectableByMouse);path.setWordWrap(True);layout.addWidget(path)
        for text,callback in [('打开资料库文件夹',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))),('查看中断时保留的录音',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root/'recordings')))),('导出完整备份',self.export_backup),('选择其他资料库（下次启动生效）',self.switch_library),('检查更新',lambda:self.updates.check(True)),('打开 GitHub 下载页面',lambda:QDesktopServices.openUrl(QUrl('https://github.com/Tuan-Space/CuteMaple/releases/latest')))]:layout.addWidget(self.button(text,callback))
        self.update_label=QLabel('当前版本 '+app_version()+' · 发现更新后由你手动下载安装包');self.update_label.setWordWrap(True);layout.addWidget(self.update_label)
        self.update_notes=QLabel();self.update_notes.setWordWrap(True);layout.addWidget(self.update_notes);layout.addStretch()
        privacy=QLabel('录音只在你主动开始后使用麦克风。升级和卸载不会删除资料库。\n回收站不会自动清空；建议定期导出包含附件的完整备份。');privacy.setWordWrap(True);privacy.setObjectName('metricName');layout.addWidget(privacy)
    def export_backup(self):
        if not self.note_editor.finish():return
        path,_=QFileDialog.getSaveFileName(self,'完整备份','美腻枫手账-'+date.today().isoformat()+'.zip','ZIP (*.zip)')
        if path:
            try:self.store.export_backup(path);self.status.setText('完整备份已保存，解压后可作为已有资料库打开。')
            except Exception as error:QMessageBox.warning(self,'备份未完成',str(error))
    def switch_library(self):
        if not self.note_editor.finish():return
        from journal_library import select_library,save_library_pointer
        other=select_library(self)
        if other:
            save_library_pointer(other.root);other.close();self.status.setText('已选择新的资料库，重新打开程序后生效。');self.pet.quit_app()
    def update_result(self,result):
        self.updates.busy=False
        if result.get('error'):self.update_label.setText(result['error']);return
        if result['new']:
            self.tabs.setTabText(4,'设置 · 有更新')
            self.update_label.setText('发现新版 '+result['version']+'，点击下载页面查看并手动安装。');self.update_notes.setText(result['body'][:1200])
        else:self.update_label.setText('当前已是最新正式版本 · '+app_version())
    def refresh_current(self,*_):
        self.today.setText(datetime.now().strftime('%Y 年 %m 月 %d 日'))
        index=self.tabs.currentIndex()
        if index==0:self.refresh_events()
        elif index==1:self.refresh_notes()
        elif index==2:self.refresh_calendar()
        elif index==3:self.refresh_statistics()
    def showEvent(self,event):
        super().showEvent(event);self.visibilityChanged.emit(True);self.refresh_current()
    def closeEvent(self,event):
        if not self.note_editor.finish():
            event.ignore()
            if self.note_editor.media.pending_finalize:QTimer.singleShot(200,self.close)
            return
        self.visibilityChanged.emit(False);super().closeEvent(event)
