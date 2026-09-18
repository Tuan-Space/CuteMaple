"""Reminder editing and a separate, genuinely read-only record view."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import json
import weakref

from PySide6.QtCore import Qt,QDateTime,QTimeZone,QTime,QTimer,QPointF,QSize,QRect
from PySide6.QtGui import QIcon,QIconEngine,QPainter,QPen,QPolygonF,QPalette,QPixmap
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QGridLayout,QLineEdit,
    QPlainTextEdit,QPushButton,QLabel,QDialogButtonBox,QMessageBox,QWidget,QScrollArea,
    QSizePolicy)

from journal_recurrence import Rule,MilestoneRule,parse_rule,ADVANCES,PERIODS,civil_timestamp
from journal_dates import DateTimeFields
from monitor_ui import ThemeBinding,ThemedComboBox as QComboBox
from monitor_ui import ThemedSpinBox as QSpinBox,ThemedCheckBox as QCheckBox


def local_zone():
    name=bytes(QTimeZone.systemTimeZoneId()).decode()
    try:ZoneInfo(name);return name
    except Exception:return 'Asia/Shanghai'


class DisclosureIcon(QIconEngine):
    """A font-independent chevron that follows the button's themed text color."""
    def __init__(self,button,expanded=False):
        super().__init__();self.button=weakref.ref(button);self.expanded=expanded

    def clone(self):return DisclosureIcon(self.button(),self.expanded)

    def pixmap(self,size,mode,state):
        result=QPixmap(size);result.fill(Qt.transparent)
        painter=QPainter(result)
        self.paint(painter,QRect(0,0,size.width(),size.height()),mode,state)
        painter.end()
        return result

    def paint(self,painter,rect,mode,state):
        button=self.button()
        if button is None:return
        group=QPalette.Disabled if mode==QIcon.Disabled else QPalette.Active
        painter.save();painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(button.palette().color(group,QPalette.ButtonText),1.6,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        x,y=rect.center().x(),rect.center().y();size=min(rect.width(),rect.height())*.23
        points=((-1,-.5),(0,.5),(1,-.5)) if self.expanded else ((-.5,-1),(.5,0),(-.5,1))
        painter.drawPolyline(QPolygonF([QPointF(x+dx*size,y+dy*size) for dx,dy in points]));painter.restore()


class EventEditor(QDialog):
    def __init__(self,store,event=None,parent=None,initial_kind='todo',*,read_only=None):
        super().__init__(parent)
        self.store=store;self.event_record=event
        self.read_only=bool(event and (event.get('archived') or event.get('deleted') is not None)) if read_only is None else bool(read_only)
        self.theme=ThemeBinding(self,'journal');self._fields={};self._field_labels={}
        self.setWindowTitle('查看提醒' if self.read_only else '编辑提醒' if event else '新建提醒')
        area=self.screen().availableGeometry()
        self.resize(min(580,area.width()-40),min(700,area.height()-48))
        existing=parse_rule(event['rule']) if event else None
        self._legacy=bool(event and event['kind']=='anniversary' and not isinstance(existing,MilestoneRule))
        outer=QVBoxLayout(self);outer.setContentsMargins(20,18,20,18);outer.setSpacing(12)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True)
        self.scroll.viewport().setObjectName('journalViewport')
        self.content=QWidget();self.content.setObjectName('journalPage')
        self.content_layout=QVBoxLayout(self.content);self.content_layout.setContentsMargins(0,0,8,0);self.content_layout.setSpacing(12)
        self.heading=QLabel(self.windowTitle());self.heading.setObjectName('section')
        self.content_layout.addWidget(self.heading)
        self.scroll.setWidget(self.content);outer.addWidget(self.scroll,1)
        self.error_label=QLabel();self.error_label.setObjectName('validationError')
        self.error_label.setWordWrap(True);self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.hide();outer.addWidget(self.error_label)
        if self.read_only:
            self._build_summary(existing)
            self.buttons=QDialogButtonBox(QDialogButtonBox.Close)
            self.buttons.button(QDialogButtonBox.Close).setText('关闭')
            self.buttons.button(QDialogButtonBox.Close).setObjectName('secondary')
            if parent and hasattr(parent,'event_ledger'):
                button=self.buttons.addButton('发生记录',QDialogButtonBox.ActionRole)
                button.setObjectName('secondary');button.clicked.connect(lambda:parent.event_ledger(event))
        else:
            self._build_form(initial_kind,existing,parent)
            self.buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
            self.buttons.button(QDialogButtonBox.Save).setText('保存')
            self.buttons.button(QDialogButtonBox.Save).setObjectName('primary')
            self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
            self.buttons.button(QDialogButtonBox.Cancel).setObjectName('secondary')
            self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject);outer.addWidget(self.buttons)
        self.content_layout.addStretch()

    def _card(self,title):
        card=QWidget();card.setObjectName('surface');card.setAttribute(Qt.WA_StyledBackground)
        box=QVBoxLayout(card);box.setContentsMargins(16,14,16,14);box.setSpacing(10)
        label=QLabel(title);label.setObjectName('cardTitle');box.addWidget(label)
        self.content_layout.addWidget(card)
        return card,box

    def _field(self,layout,label,widget):
        field=QWidget();box=QVBoxLayout(field);box.setContentsMargins(0,0,0,0);box.setSpacing(5)
        text=QLabel(label);text.setObjectName('muted');text.setBuddy(widget);box.addWidget(text);box.addWidget(widget)
        layout.addWidget(field);self._fields[widget]=field;self._field_labels[widget]=text
        return field

    def _text(self,layout,text,*,muted=False):
        label=QLabel(text);label.setWordWrap(True);label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        if muted:label.setObjectName('muted')
        layout.addWidget(label);return label

    def _build_summary(self,rule):
        event=self.event_record
        if event is None:raise ValueError('只读查看需要已有提醒')
        deleted=event.get('deleted') is not None;single=bool(event.get('_occurrence'))
        state='已删除' if deleted else '已完成' if event.get('archived') else '提醒详情'
        _,box=self._card(('单次提醒 · ' if single else '')+state)
        self.title=self._text(box,event['title']);self.title.setObjectName('section')
        self._text(box,{'todo':'待办','schedule':'日程','anniversary':'纪念日'}[event['kind']],muted=True)
        if event.get('body'):self._text(box,event['body'])
        _,box=self._card('这一次的时间' if single else '日期与规则')
        if single:
            due=datetime.fromtimestamp(event['due'],ZoneInfo(rule.zone)).strftime('%Y年%m月%d日 %H:%M:%S')
            lines=['发生时间：'+due,'时区：'+rule.zone]
            snapshot=event.get('snapshot')
            if isinstance(snapshot,str):snapshot=json.loads(snapshot)
            if snapshot:
                state_text={'pending':'待处理','missed':'未完成','completed':'已完成','cancelled':'已取消'}
                lines.append('删除前状态：'+state_text.get(snapshot.get('state'),snapshot.get('state','未记录')))
            lines.append('此记录只对应这一次提醒，其他周期不受影响。')
        else:
            lines=['时间：'+rule.base.strftime('%Y年%m月%d日 %H:%M:%S'),
                   '历法：'+('农历（以上为对应公历时间）' if rule.calendar=='lunar' else '公历'),
                   '时区：'+rule.zone]
            if isinstance(rule,MilestoneRule):
                options=[]
                if rule.yearly:options.append('每周年')
                if rule.hundreds:options.append('每 100 天')
                options.extend(f'第 {day} 天' for day in rule.days)
                lines.append('纪念日提醒：'+'、'.join(options))
            else:
                lines.append('重复：'+dict(zip(PERIODS,('不重复','每小时','每天','每周','每月','每年')))[rule.period])
                if rule.end:lines.append('截至：'+rule.end.replace('T',' '))
                if rule.count:lines.append('总次数：'+str(rule.count))
                if rule.calendar=='lunar' and rule.period=='monthly':lines.append('月度提醒：'+('包含闰月' if rule.include_leap else '不包含闰月'))
            if isinstance(rule,MilestoneRule) or getattr(rule,'period','once') in ('monthly','yearly'):
                lines.append('缺少日期：'+('跳过不存在的日期' if rule.missing=='skip' else '使用当月最后一天'))
            if rule.calendar=='lunar':lines.append('闰月：'+('只在对应闰月提醒' if rule.strict_leap else '按规则兼容非闰月'))
            advance_names=dict(zip(ADVANCES,('1 周','3 天','1 天','5 小时','3 小时','1 小时')))
            lines.append('提前提醒：'+('、'.join(advance_names[n] for n in rule.advances) if rule.advances else '无'))
        self.summary=self._text(box,'\n'.join(lines))
        self._text(self.content_layout,'这条提醒位于回收站，可从回收站恢复。' if deleted else '内容以原记录保留，可查看发生记录。',muted=True)

    def _build_form(self,initial_kind,existing,parent):
        event=self.event_record;self.zone_name=local_zone()
        self.content_card,box=self._card('提醒内容')
        self.title=QLineEdit();self.title.setPlaceholderText('想记住什么？');self.title.setMaxLength(200)
        self._field(box,'名称',self.title)
        self.kind=QComboBox();self.kind.addItems(['待办','日程','纪念日'])
        self.kind.setCurrentIndex(['todo','schedule','anniversary'].index(event['kind'] if event else initial_kind))
        self._field(box,'类型',self.kind)
        self.time_card,box=self._card('日期与时间')
        self.calendar=QComboBox();self.calendar.addItems(['公历','农历']);self._field(box,'历法',self.calendar)
        now=QDateTime.fromSecsSinceEpoch(int(self.store.clock()));default=QDateTime(now.date(),QTime(10,0))
        if default<=now:default=default.addDays(1)
        self.start=DateTimeFields(default)
        self._field(box,'提醒时间',self.start);self.calendar.currentIndexChanged.connect(self.start.setLunar)
        self.rule_card,box=self._card('重复与提前提醒')
        self.period=QComboBox();self.period.addItems(['不重复','每小时','每天','每周','每月','每年']);self._field(box,'重复',self.period)
        self.milestone_box=QWidget();ml=QVBoxLayout(self.milestone_box);ml.setContentsMargins(0,0,0,0);ml.setSpacing(10)
        ml.addWidget(QLabel('想在哪些日子提醒？'))
        self.milestone_grid=QGridLayout();self.milestone_grid.setSpacing(10)
        self.yearly=QCheckBox('每周年');self.yearly.setChecked(True)
        self.hundreds=QCheckBox('每 100 天');self.day520=QCheckBox('520 天');self.day1314=QCheckBox('1314 天')
        for i,w in enumerate((self.yearly,self.hundreds,self.day520,self.day1314)):self.milestone_grid.addWidget(w,i//2,i%2)
        ml.addLayout(self.milestone_grid)
        self.custom_days=QLineEdit();self.custom_days.setPlaceholderText('例如 30、1000')
        self._field(ml,'其他纪念天数（可选）',self.custom_days);box.addWidget(self.milestone_box)
        box.addWidget(QLabel('提前提醒（可多选）'));self.advance_grid=QGridLayout();self.advance_grid.setSpacing(10);self.advances=[]
        for text in ('1 周','3 天','1 天','5 小时','3 小时','1 小时'):self.advances.append(QCheckBox(text))
        box.addLayout(self.advance_grid);self._advance_columns=None;self._layout_advances()
        self.more=QPushButton('更多设置');self.more.setObjectName('secondary');self.more.setCheckable(True)
        self.more.setIcon(QIcon(DisclosureIcon(self.more)));self.more.setIconSize(QSize(14,14))
        self.more.setCursor(Qt.PointingHandCursor);box.addWidget(self.more)
        self.advanced,box=self._card('更多设置');self.advanced.hide();self.more.toggled.connect(self._toggle_advanced)
        self.body=QPlainTextEdit();self.body.setPlaceholderText('补充说明（可选）');self.body.setFixedHeight(86)
        self._field(box,'说明',self.body)
        self.missing=QComboBox();self.missing.addItems(['使用当月最后一天','跳过不存在的日期']);self._field(box,'缺少日期时',self.missing)
        self.leap=QCheckBox('月度提醒包含闰月');self.leap.setChecked(True);box.addWidget(self.leap)
        self.strict=QCheckBox('只在对应闰月提醒');box.addWidget(self.strict)
        self.end_mode=QComboBox();self.end_mode.addItems(['不设结束时间','截至某天','指定次数']);self._field(box,'结束条件',self.end_mode)
        self.end=DateTimeFields(QDateTime.currentDateTime().addYears(1));self._field(box,'截至时间',self.end)
        self.count=QSpinBox();self.count.setRange(1,100000);self.count.setValue(10);self._field(box,'总次数',self.count)
        if event:
            actions=QHBoxLayout();box.addLayout(actions)
            if parent and hasattr(parent,'event_ledger'):
                self.records_button=QPushButton('发生记录');self.records_button.setObjectName('secondary')
                self.records_button.clicked.connect(lambda:parent.event_ledger(event));actions.addWidget(self.records_button)
            if event.get('deleted') is None:
                self.delete_button=QPushButton('移到回收站');self.delete_button.setObjectName('danger')
                self.delete_button.clicked.connect(self.delete_plan);actions.addWidget(self.delete_button)
            actions.addStretch()
        self.preview_card,box=self._card('接下来的提醒')
        self.preview=QLabel();self.preview.setWordWrap(True);self.preview.setTextFormat(Qt.PlainText)
        self.preview.setObjectName('pageDescription');box.addWidget(self.preview)
        if existing:self._load_rule(existing)
        for w in (self.kind,self.period,self.calendar,self.missing,self.end_mode):w.currentIndexChanged.connect(self.update_preview)
        self.start.dateTimeChanged.connect(self.update_preview);self.end.dateTimeChanged.connect(self.update_preview)
        self.count.valueChanged.connect(self.update_preview);self.custom_days.textChanged.connect(self.update_preview)
        self.title.textChanged.connect(self._title_changed)
        for w in (self.leap,self.strict,self.yearly,self.hundreds,self.day520,self.day1314,*self.advances):w.toggled.connect(self.update_preview)
        self.kind.currentIndexChanged.connect(self.kind_changed);self.update_preview()

    def _load_rule(self,r):
        event=self.event_record
        self.title.setText(event['title']);self.body.setPlainText(event['body'])
        self.start.setDateTime(QDateTime.fromString(r.start,'yyyy-MM-ddTHH:mm:ss'));self.zone_name=r.zone
        self.calendar.setCurrentIndex(int(r.calendar=='lunar'));self.missing.setCurrentIndex(int(r.missing=='skip'));self.strict.setChecked(r.strict_leap)
        if isinstance(r,MilestoneRule):
            self.yearly.setChecked(r.yearly);self.hundreds.setChecked(r.hundreds)
            self.day520.setChecked(520 in r.days);self.day1314.setChecked(1314 in r.days)
            self.custom_days.setText('、'.join(str(n) for n in r.days if n not in (520,1314)))
        else:
            self.period.setCurrentIndex(PERIODS.index(r.period));self.leap.setChecked(r.include_leap)
            if r.count:self.end_mode.setCurrentIndex(2);self.count.setValue(r.count)
            elif r.end:self.end_mode.setCurrentIndex(1);self.end.setDateTime(QDateTime.fromString(r.end,'yyyy-MM-ddTHH:mm:ss'))
        for c,a in zip(self.advances,ADVANCES):c.setChecked(a in r.advances)
        self.calendar.setCurrentIndex(int(self.store.setting('event_input_lunar:'+event['id'],r.calendar=='lunar')))

    def _layout_advances(self):
        if not hasattr(self,'advance_grid'):return
        columns=2 if self.width()<520 else 3
        if columns==self._advance_columns:return
        self._advance_columns=columns
        for w in self.advances:self.advance_grid.removeWidget(w)
        for i,w in enumerate(self.advances):self.advance_grid.addWidget(w,i//columns,i%columns)

    def resizeEvent(self,event):
        self._layout_advances();super().resizeEvent(event)

    def _toggle_advanced(self,checked):
        self.more.setText('收起更多设置' if checked else '更多设置')
        self.more.setIcon(QIcon(DisclosureIcon(self.more,checked)));self.advanced.setVisible(checked)
        if checked:QTimer.singleShot(0,lambda:self.scroll.ensureWidgetVisible(self.advanced,0,12))

    def _title_changed(self):
        if getattr(self,'_error_target',None) is self.title and self.title.text().strip():self._clear_error()

    def _clear_error(self):
        self.error_label.clear();self.error_label.hide();self._error_target=None

    def _show_error(self,message,target=None,focus=False):
        self.error_label.setText(message);self.error_label.show();self._error_target=target
        if focus and target is not None:
            if self.advanced.isAncestorOf(target):self.more.setChecked(True)
            focus_widget=target.year if isinstance(target,DateTimeFields) else target
            focus_widget.setFocus(Qt.OtherFocusReason)
            QTimer.singleShot(0,lambda:self.scroll.ensureWidgetVisible(target,0,20))

    def _rule_error_target(self):
        if self.is_milestone():return self.custom_days
        return self.end if self.end_mode.currentIndex()==1 else self.start

    def delete_plan(self):
        if self.read_only:return
        if QMessageBox.question(self,'移到回收站','将此计划移到回收站，并停止未来提醒？')==QMessageBox.Yes:
            self.store.archive_event(self.event_record['id']);self.accept()

    def kind_changed(self,index):
        self.update_preview()

    def is_milestone(self):return self.kind.currentIndex()==2 and not self._legacy

    def rule(self):
        if self.read_only:return parse_rule(self.event_record['rule'])
        shared=dict(start=self.start.dateTime().toString('yyyy-MM-ddTHH:mm:ss'),zone=self.zone_name,
            calendar='lunar' if self.calendar.currentIndex() and (self.is_milestone() or self.period.currentIndex()>=4) else 'solar',
            missing='skip' if self.missing.currentIndex() else 'last',strict_leap=self.strict.isChecked(),
            advances=tuple(a for c,a in zip(self.advances,ADVANCES) if c.isChecked()))
        if self.is_milestone():
            import re
            tokens=[v for v in re.split(r'[、，,;；\s]+',self.custom_days.text().strip()) if v]
            if any(not v.isdigit() for v in tokens):raise ValueError('特殊天数用正整数填写，多个天数用逗号分隔')
            days=[int(v) for v in tokens]+([520] if self.day520.isChecked() else [])+([1314] if self.day1314.isChecked() else [])
            return MilestoneRule(**shared,yearly=self.yearly.isChecked(),hundreds=self.hundreds.isChecked(),days=tuple(days))
        repeating=self.period.currentIndex()>0
        return Rule(**shared,period=PERIODS[self.period.currentIndex()],include_leap=self.leap.isChecked(),
            end=self.end.dateTime().toString('yyyy-MM-ddTHH:mm:ss') if repeating and self.end_mode.currentIndex()==1 else None,
            count=self.count.value() if repeating and self.end_mode.currentIndex()==2 else None)

    def update_preview(self,*_):
        if self.read_only:return
        milestone=self.is_milestone();monthly=self.period.currentIndex()>=4;lunar=self.calendar.currentIndex()==1
        self.milestone_box.setVisible(milestone);self._fields[self.period].setVisible(not milestone)
        self._field_labels[self.start].setText('起始日期与提醒时间' if milestone else '提醒时间')
        for w,visible in ((self.missing,milestone or monthly),(self.leap,not milestone and lunar and self.period.currentIndex()==4),
            (self.strict,lunar and (milestone or monthly)),(self.end_mode,not milestone and self.period.currentIndex()>0),
            (self.end,not milestone and self.period.currentIndex()>0 and self.end_mode.currentIndex()==1),
            (self.count,not milestone and self.period.currentIndex()>0 and self.end_mode.currentIndex()==2)):
            self._fields.get(w,w).setVisible(visible)
        try:
            rule=self.rule();dates=rule.preview(after=self.store.clock() if milestone else None,count=3);lines=[]
            for _,stamp in dates:
                text=datetime.fromtimestamp(stamp,ZoneInfo(rule.zone)).strftime('%Y年%m月%d日 %H:%M:%S')
                if milestone:text+=' · '+' / '.join(rule.labels(stamp))
                lines.append(text)
            self.preview.setText('\n'.join(lines) if lines else '所选提醒日期均已过去')
            if getattr(self,'_error_target',None) is not self.title:self._clear_error()
        except Exception as error:
            self.preview.setText('请检查提醒设置。');self._show_error(str(error),self._rule_error_target())

    def save(self):
        if self.read_only:return False
        if not self.title.text().strip():
            self._show_error('请填写提醒名称。',self.title,True);return False
        try:rule=self.rule()
        except Exception as error:
            self._show_error(str(error),self._rule_error_target(),True);return False
        if self.event_record and self.event_record.get('deleted') is not None and not self.event_record.get('previous_archived'):
            if not rule.preview(after=self.store.clock()+.001,count=1):
                self._show_error('请选择未来的提醒时间。',self.start,True);return False
        try:
            identity=self.store.save_event(self.title.text(),['todo','schedule','anniversary'][self.kind.currentIndex()],
                self.body.toPlainText(),rule,self.event_record['id'] if self.event_record else None)
            self.store.set_setting('event_input_lunar:'+identity,bool(self.calendar.currentIndex()))
        except Exception as error:
            self._show_error('未能保存：'+str(error));return False
        self._clear_error();self.accept();return True



