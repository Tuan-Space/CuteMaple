from __future__ import annotations
import json
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from PySide6.QtCore import Qt,QDateTime,QTimeZone,QTimer,QUrl,Signal
from PySide6.QtGui import QDesktopServices,QTextDocument,QImage
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,
    QTextBrowser,QComboBox,QDateTimeEdit,QSpinBox,QCheckBox,QPushButton,QLabel,QDialogButtonBox,
    QMessageBox,QListWidget,QListWidgetItem,QSplitter,QWidget,QFileDialog,QStackedWidget,QScrollArea)
from journal_recurrence import Rule,MilestoneRule,parse_rule,ADVANCES,PERIODS
from monitor_ui import ThemeBinding
from monitor_ui import ThemedComboBox as QComboBox
from monitor_ui import ThemedSpinBox as QSpinBox,ThemedCheckBox as QCheckBox
from PySide6.QtCore import QTime


def local_zone():
    name=bytes(QTimeZone.systemTimeZoneId()).decode()
    try:ZoneInfo(name);return name
    except Exception:return 'Asia/Shanghai'


class EventEditor(QDialog):
    def __init__(self,store,event=None,parent=None,initial_kind='todo'):
        super().__init__(parent);self.store=store;self.event_record=event;self.theme=ThemeBinding(self,'journal');self.resize(560,620)
        self.setWindowTitle('查看提醒' if event and event['archived'] else '编辑提醒' if event else '新建提醒')
        area=self.screen().availableGeometry();self.resize(min(560,area.width()-40),min(620,area.height()-48))
        existing=parse_rule(event['rule']) if event else None;self._legacy=bool(event and event['kind']=='anniversary' and not isinstance(existing,MilestoneRule))
        outer=QVBoxLayout(self);outer.setContentsMargins(22,18,22,18);outer.setSpacing(14);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.viewport().setObjectName('journalViewport');content=QWidget();content.setObjectName('journalPage');layout=QVBoxLayout(content);layout.setContentsMargins(0,0,8,0);layout.setSpacing(14);self.form=QFormLayout();self.form.setSpacing(12)
        self.title=QLineEdit();self.title.setPlaceholderText('想记住什么？');self.title.setMaxLength(200)
        self.kind=QComboBox();self.kind.addItems(['待办','日程','纪念日']);self.kind.setCurrentIndex(['todo','schedule','anniversary'].index(event['kind'] if event else initial_kind))
        self.start=QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600));self.start.setCalendarPopup(True);self.start.setDisplayFormat('yyyy-MM-dd  HH:mm:ss')
        if self.kind.currentIndex()==2:self.start.setTime(QTime(9,0))
        self.period=QComboBox();self.period.addItems(['不重复','每小时','每天','每周','每月','每年'])
        self.calendar=QComboBox();self.calendar.addItems(['公历','农历']);self.lunar_input=QPushButton('按农历填写');self.lunar_input.setObjectName('quiet');self.lunar_input.clicked.connect(self.pick_lunar)
        for name,w in [('名称',self.title),('类型',self.kind),('时间',self.start),('重复',self.period),('日期',self.calendar),('',self.lunar_input)]:self.form.addRow(name,w)
        layout.addLayout(self.form)
        self.milestone_box=QWidget();ml=QVBoxLayout(self.milestone_box);ml.setContentsMargins(0,0,0,0);ml.setSpacing(12);ml.addWidget(QLabel('想在哪些日子提醒？'))
        row=QHBoxLayout();self.yearly=QCheckBox('每周年');self.yearly.setChecked(True);self.hundreds=QCheckBox('每 100 天');self.day520=QCheckBox('520 天');self.day1314=QCheckBox('1314 天')
        for w in (self.yearly,self.hundreds,self.day520,self.day1314):row.addWidget(w)
        ml.addLayout(row);self.custom_days=QLineEdit();self.custom_days.setPlaceholderText('其他天数，例如 30、1000');ml.addWidget(self.custom_days);layout.addWidget(self.milestone_box)
        more=QPushButton('更多设置');more.setObjectName('quiet');more.setCheckable(True);layout.addWidget(more);self.advanced=QWidget();self.advanced.setVisible(False);more.toggled.connect(self.advanced.setVisible);advanced=QVBoxLayout(self.advanced);advanced.setContentsMargins(0,0,0,0);self.extra=QFormLayout();self.extra.setSpacing(10)
        self.body=QPlainTextEdit();self.body.setPlaceholderText('补充说明（可选）');self.body.setMaximumHeight(80);self.zone=QLineEdit(local_zone());self.zone.setReadOnly(True)
        self.missing=QComboBox();self.missing.addItems(['使用当月最后一天','跳过不存在的日期']);self.leap=QCheckBox('月度提醒包含闰月');self.leap.setChecked(True);self.strict=QCheckBox('只在对应闰月提醒')
        self.end_mode=QComboBox();self.end_mode.addItems(['不设结束时间','截至某天','指定次数']);self.end=QDateTimeEdit(QDateTime.currentDateTime().addYears(1));self.end.setCalendarPopup(True);self.end.setDisplayFormat('yyyy-MM-dd HH:mm:ss');self.count=QSpinBox();self.count.setRange(1,100000);self.count.setValue(10)
        for name,w in [('说明',self.body),('时区',self.zone),('缺少日期',self.missing),('',self.leap),('',self.strict),('结束',self.end_mode),('截至',self.end),('次数',self.count)]:self.extra.addRow(name,w)
        advanced.addLayout(self.extra);advanced.addWidget(QLabel('提前提醒'));row=QHBoxLayout();self.advances=[]
        for text in ['1 周','3 天','1 天','5 小时','3 小时','1 小时']:
            c=QCheckBox(text);row.addWidget(c);self.advances.append(c)
        advanced.addLayout(row);layout.addWidget(self.advanced);self.preview=QLabel();self.preview.setObjectName('status');self.preview.setWordWrap(True);layout.addWidget(self.preview);layout.addStretch();scroll.setWidget(content);outer.addWidget(scroll,1)
        self.buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);self.buttons.button(QDialogButtonBox.Save).setText('保存');self.buttons.button(QDialogButtonBox.Save).setObjectName('primary');self.buttons.button(QDialogButtonBox.Cancel).setText('取消');self.buttons.accepted.connect(self.save);self.buttons.rejected.connect(self.reject);outer.addWidget(self.buttons)
        if existing:
            r=existing;self.title.setText(event['title']);self.body.setPlainText(event['body']);self.start.setDateTime(QDateTime.fromString(r.start,'yyyy-MM-ddTHH:mm:ss'));self.zone.setText(r.zone);self.calendar.setCurrentIndex(int(r.calendar=='lunar'));self.missing.setCurrentIndex(int(r.missing=='skip'));self.strict.setChecked(r.strict_leap)
            if isinstance(r,MilestoneRule):
                self.yearly.setChecked(r.yearly);self.hundreds.setChecked(r.hundreds);self.day520.setChecked(520 in r.days);self.day1314.setChecked(1314 in r.days);self.custom_days.setText('、'.join(str(n) for n in r.days if n not in (520,1314)))
            else:
                self.period.setCurrentIndex(PERIODS.index(r.period));self.leap.setChecked(r.include_leap)
                if r.count:self.end_mode.setCurrentIndex(2);self.count.setValue(r.count)
                elif r.end:self.end_mode.setCurrentIndex(1);self.end.setDateTime(QDateTime.fromString(r.end,'yyyy-MM-ddTHH:mm:ss'))
            for c,a in zip(self.advances,ADVANCES):c.setChecked(a in r.advances)
        for w in (self.kind,self.period,self.calendar,self.missing,self.end_mode):w.currentIndexChanged.connect(self.update_preview)
        self.start.dateTimeChanged.connect(self.update_preview);self.end.dateTimeChanged.connect(self.update_preview);self.count.valueChanged.connect(self.update_preview);self.custom_days.textChanged.connect(self.update_preview)
        for w in (self.leap,self.strict,self.yearly,self.hundreds,self.day520,self.day1314,*self.advances):w.toggled.connect(self.update_preview)
        self.kind.currentIndexChanged.connect(self.kind_changed);self.update_preview()
        if event and event['archived']:self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        if event:
            if parent and hasattr(parent,'event_ledger'):
                records=self.buttons.addButton('发生记录',QDialogButtonBox.ActionRole);records.setObjectName('quiet');records.clicked.connect(lambda:parent.event_ledger(event))
            if not event['archived']:
                delete=self.buttons.addButton('删除计划',QDialogButtonBox.ActionRole);delete.setObjectName('quiet');delete.clicked.connect(self.delete_plan)

    def delete_plan(self):
        if QMessageBox.question(self,'删除计划','停止未来提醒，并将已有记录保留到历史？')==QMessageBox.Yes:
            self.store.archive_event(self.event_record['id']);self.accept()

    def kind_changed(self,index):
        if index==2 and not self.event_record:self.start.setTime(QTime(9,0))
    def is_milestone(self):return self.kind.currentIndex()==2 and not self._legacy
    def rule(self):
        shared=dict(start=self.start.dateTime().toString('yyyy-MM-ddTHH:mm:ss'),zone=self.zone.text().strip(),calendar='lunar' if self.calendar.currentIndex() and (self.is_milestone() or self.period.currentIndex()>=4) else 'solar',missing='skip' if self.missing.currentIndex() else 'last',strict_leap=self.strict.isChecked(),advances=tuple(a for c,a in zip(self.advances,ADVANCES) if c.isChecked()))
        if self.is_milestone():
            import re
            tokens=[v for v in re.split(r'[、，,;；\s]+',self.custom_days.text().strip()) if v]
            if any(not v.isdigit() for v in tokens):raise ValueError('特殊天数用正整数填写，多个天数用逗号分隔')
            days=[int(v) for v in tokens]+([520] if self.day520.isChecked() else [])+([1314] if self.day1314.isChecked() else [])
            return MilestoneRule(**shared,yearly=self.yearly.isChecked(),hundreds=self.hundreds.isChecked(),days=tuple(days))
        return Rule(**shared,period=PERIODS[self.period.currentIndex()],include_leap=self.leap.isChecked(),end=self.end.dateTime().toString('yyyy-MM-ddTHH:mm:ss') if self.end_mode.currentIndex()==1 else None,count=self.count.value() if self.end_mode.currentIndex()==2 else None)
    def update_preview(self,*_):
        milestone=self.is_milestone();monthly=self.period.currentIndex()>=4;lunar=self.calendar.currentIndex()==1
        self.milestone_box.setVisible(milestone);self.form.labelForField(self.start).setText('起始日期 / 提醒时间' if milestone else '时间')
        for w,visible in ((self.period,not milestone),(self.calendar,milestone or monthly),(self.lunar_input,milestone or monthly)):self.form.setRowVisible(w,visible)
        for w,visible in ((self.missing,milestone or monthly),(self.leap,not milestone and lunar and self.period.currentIndex()==4),(self.strict,lunar and (milestone or monthly)),(self.end_mode,not milestone and self.period.currentIndex()>0),(self.end,not milestone and self.period.currentIndex()>0 and self.end_mode.currentIndex()==1),(self.count,not milestone and self.period.currentIndex()>0 and self.end_mode.currentIndex()==2)):self.extra.setRowVisible(w,visible)
        try:
            rule=self.rule();dates=rule.preview(after=self.store.clock() if milestone else None,count=3);lines=[]
            for _,stamp in dates:
                text=datetime.fromtimestamp(stamp,ZoneInfo(rule.zone)).strftime('%Y年%m月%d日 %H:%M')
                if milestone:text+=' · '+' / '.join(rule.labels(stamp))
                lines.append(text)
            self.preview.setText('接下来的提醒\n'+'\n'.join(lines) if lines else '所选提醒日期均已过去')
        except Exception as error:self.preview.setText(str(error))
    def save(self):
        try:self.store.save_event(self.title.text(),['todo','schedule','anniversary'][self.kind.currentIndex()],self.body.toPlainText(),self.rule(),self.event_record['id'] if self.event_record else None);self.accept()
        except Exception as error:QMessageBox.warning(self,'未能保存',str(error))

    def pick_lunar(self):
        from lunar_python import Lunar,Solar
        now=self.start.dateTime().toPython();lunar=Solar.fromYmd(now.year,now.month,now.day).getLunar()
        dialog=QDialog(self);dialog.setWindowTitle('选择农历日期');layout=QFormLayout(dialog)
        y=QSpinBox();y.setRange(1900,2199);y.setValue(lunar.getYear());m=QSpinBox();m.setRange(1,12);m.setValue(abs(lunar.getMonth()));d=QSpinBox();d.setRange(1,30);d.setValue(lunar.getDay());leap=QCheckBox('闰月');leap.setChecked(lunar.getMonth()<0)
        for label,w in [('年',y),('月',m),('日',d),('',leap)]:layout.addRow(label,w)
        error=QLabel();layout.addRow(error);buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);layout.addRow(buttons)
        buttons.button(QDialogButtonBox.Ok).setText('确定');buttons.button(QDialogButtonBox.Cancel).setText('取消')
        def accept():
            try:
                solar=Lunar.fromYmd(y.value(),-m.value() if leap.isChecked() else m.value(),d.value()).getSolar()
                self.start.setDateTime(QDateTime.fromString(f'{solar.getYear():04}-{solar.getMonth():02}-{solar.getDay():02}T{now:%H:%M:%S}','yyyy-MM-ddTHH:mm:ss'))
                if self.period.currentIndex()<4:self.period.setCurrentIndex(5)
                self.calendar.setCurrentIndex(1);dialog.accept()
            except Exception:error.setText('这个农历日期不存在，请核对闰月和天数。')
        buttons.accepted.connect(accept);buttons.rejected.connect(dialog.reject);dialog.exec()


class SafePreview(QTextBrowser):
    def __init__(self,store,parent=None):
        super().__init__(parent);self.store=store;self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self.open_link)
    def loadResource(self,kind,url):
        if kind==QTextDocument.ImageResource:
            relative=url.toString()
            if relative.startswith('attachments/'):
                try:return QImage(str(self.store.attachment_path(relative)))
                except Exception:return QImage()
            return QImage()
        return None
    def open_link(self,url):
        if url.scheme() in ('https','http'):
            QDesktopServices.openUrl(url)
        elif url.toString().startswith('attachments/'):
            try:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.attachment_path(url.toString()).parent)))
            except Exception:pass


class MarkdownEditor(QPlainTextEdit):
    filesDropped=Signal(list)
    imagePasted=Signal(QImage)
    def insertFromMimeData(self,mime):
        if mime.hasUrls():
            local=[u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if local:self.filesDropped.emit(local);return
        if mime.hasImage():self.imagePasted.emit(QImage(mime.imageData()));return
        super().insertFromMimeData(mime)


class NoteEditor(QWidget):
    saved=Signal()
    def __init__(self,store,parent=None):
        super().__init__(parent);self.store=store;self.identity=None;self.loading=False;self.dirty=False
        self.setObjectName('surface');self.setAttribute(Qt.WA_StyledBackground)
        layout=QVBoxLayout(self);layout.setContentsMargins(16,16,16,12);layout.setSpacing(12)
        self.title=QLineEdit();self.title.setObjectName('noteTitle');self.title.setPlaceholderText('无标题笔记');self.title.setMaxLength(200);layout.addWidget(self.title)
        tools=QHBoxLayout();attach=QPushButton('附件');attach.setObjectName('quiet');attach.clicked.connect(self.choose_files);self.mode=QComboBox();self.mode.addItems(['编辑','预览']);self.mode.currentIndexChanged.connect(self.mode_changed)
        record=QPushButton('录音');record.setObjectName('quiet');record.clicked.connect(self.show_recording)
        tools.addWidget(attach);tools.addWidget(record);tools.addStretch();tools.addWidget(self.mode);layout.addLayout(tools)
        self.pages=QStackedWidget();self.edit=MarkdownEditor();self.edit.setPlaceholderText('从这里开始记录…');self.edit.setToolTip('支持 Markdown、粘贴图片和拖入附件');self.preview=SafePreview(store)
        self.pages.addWidget(self.edit);self.pages.addWidget(self.preview);layout.addWidget(self.pages,1)
        self.files=QListWidget();self.files.setMaximumHeight(105);self.files.itemDoubleClicked.connect(self.open_file);layout.addWidget(self.files)
        from journal_media import MediaBar
        self.media=MediaBar(store.root);self.media.recorded.connect(self.recorded);self.media.prepare_recording=self.prepare_recording;self.recording_identity=None;layout.addWidget(self.media)
        self.media.hide();self.files.hide()
        self.status=QLabel();self.status.setObjectName('metricName');layout.addWidget(self.status)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(500);self.timer.timeout.connect(self.save)
        self.title.textChanged.connect(self.mark_dirty);self.edit.textChanged.connect(self.mark_dirty);self.edit.filesDropped.connect(self.add_files);self.edit.imagePasted.connect(self.paste_image)

    def mark_dirty(self):
        if not self.loading:self.dirty=True;self.status.setText('正在编辑…');self.timer.start()

    def show_recording(self):
        self.media.show();self.media.record_panel.show()

    def load(self,note=None):
        if not self.save():return False
        self.media.finish();self.loading=True;self.identity=note['id'] if note else None
        self.title.setText(note['title'] if note else '');self.edit.setPlainText(note['body'] if note else '');self.loading=False;self.dirty=False
        self.refresh_files();self.mode_changed();return True

    def save(self):
        self.timer.stop()
        if not self.dirty:return True
        try:
            self.identity=self.store.save_note(self.title.text(),self.edit.toPlainText(),self.identity);self.dirty=False;self.status.setText('已保存 · '+datetime.now().strftime('%H:%M:%S'));self.saved.emit();return True
        except Exception as error:self.status.setText('尚未保存，请勿退出：'+str(error));return False

    def ensure_note(self):
        if self.identity is None:self.dirty=True
        if not self.save():raise OSError('请先恢复资料库写入')

    def mode_changed(self,*_):
        self.pages.setCurrentIndex(self.mode.currentIndex())
        if self.mode.currentIndex():self.preview.document().setMarkdown(self.edit.toPlainText(),QTextDocument.MarkdownDialectGitHub|QTextDocument.MarkdownNoHTML)

    def choose_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'添加附件');self.add_files(paths)

    def add_files(self,paths):
        try:
            self.ensure_note()
            for path in paths:
                row=self.store.attach(self.identity,path)
                label=row['name'].replace('[','').replace(']','')
                prefix='!' if row['mime'].startswith('image/') else ''
                self.edit.insertPlainText(f'\n{prefix}[{label}]({row["relative"]})\n')
            self.save();self.refresh_files()
        except Exception as error:QMessageBox.warning(self,'附件未保存',str(error))

    def paste_image(self,image):
        path=self.store.root/'recordings'/('图片-'+__import__('uuid').uuid4().hex+'.png')
        if image.save(str(path),'PNG'):
            self.add_files([str(path)]);path.unlink(missing_ok=True)

    def recorded(self,path):
        try:
            owner=self.recording_identity
            if owner is None:raise ValueError('找不到录音所属笔记')
            row=self.store.attach(owner,path);text=f'\n[录音：{row["name"]}]({row["relative"]})\n'
            if self.identity==owner:
                self.edit.insertPlainText(text)
                if not self.save():raise OSError('笔记正文未保存，原录音继续保留')
                self.refresh_files()
            else:
                note=self.store.rows('SELECT * FROM notes WHERE id=?',(owner,))[0]
                self.store.save_note(note['title'],note['body']+text,owner)
            Path(path).unlink();self.media.status.setText('录音已保存到当前笔记 · 麦克风未使用')
        except Exception as error:self.media.status.setText('录音保留在 recordings 文件夹，未能加入笔记：'+str(error))

    def prepare_recording(self):
        self.ensure_note();self.recording_identity=self.identity

    def refresh_files(self):
        self.files.clear()
        self.files.hide()
        if not self.identity:return
        for row in self.store.attachments(self.identity):
            item=QListWidgetItem(f'{row["name"]} · {row["size"]/1024:.1f} KB')
            item.setToolTip('双击播放' if row['mime'].startswith('audio/') else '双击打开所在文件夹')
            item.setData(Qt.UserRole,row);self.files.addItem(item)
        self.files.setVisible(self.files.count()>0)

    def open_file(self,item):
        row=item.data(Qt.UserRole)
        try:
            path=self.store.attachment_path(row['relative'])
            if row['mime'].startswith('audio/'):self.media.show();self.media.load(path)
            else:QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        except Exception as error:QMessageBox.warning(self,'附件无法打开',str(error))

    def finish(self):
        from PySide6.QtMultimedia import QMediaRecorder
        self.media.finish()
        if self.media.recorder.recorderState()!=QMediaRecorder.StoppedState or self.media.pending_finalize:
            self.status.setText('正在结束录音并保存，请稍候。');return False
        return self.save()
