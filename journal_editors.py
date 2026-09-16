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
from journal_recurrence import Rule,ADVANCES,PERIODS
from monitor_ui import ThemeBinding
from monitor_ui import ThemedComboBox as QComboBox
from monitor_ui import ThemedSpinBox as QSpinBox


def local_zone():
    name=bytes(QTimeZone.systemTimeZoneId()).decode()
    try:ZoneInfo(name);return name
    except Exception:return 'Asia/Shanghai'


class EventEditor(QDialog):
    def __init__(self,store,event=None,parent=None):
        super().__init__(parent);self.store=store;self.event_record=event;self.theme=ThemeBinding(self,'journal')
        self.setWindowTitle('编辑提醒' if event else '新建提醒');self.resize(630,700)
        outer=QVBoxLayout(self);scroll=QScrollArea();scroll.viewport().setObjectName('journalViewport');scroll.setWidgetResizable(True);content=QWidget();content.setObjectName('journalPage');layout=QVBoxLayout(content);form=QFormLayout();self.form=form
        self.title=QLineEdit();self.title.setMaxLength(200);self.kind=QComboBox();self.kind.addItems(['待办事项','日程','纪念日'])
        self.body=QPlainTextEdit();self.body.setMaximumHeight(95)
        self.start=QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600));self.start.setCalendarPopup(True);self.start.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.period=QComboBox();self.period.addItems(['仅一次','每小时','每天','每周','每月','每年'])
        self.calendar=QComboBox();self.calendar.addItems(['公历','农历（由所选公历日期换算）'])
        self.lunar_input=QPushButton('按农历日期填写');self.lunar_input.clicked.connect(self.pick_lunar)
        self.zone=QLineEdit(local_zone());self.zone.setReadOnly(True);self.zone.setToolTip('以创建时的系统时区安排提醒，旅行时不会意外改变原来的时间。')
        self.missing=QComboBox();self.missing.addItems(['没有这一天时，使用当月最后一天','没有这一天时，跳过'])
        self.leap=QCheckBox('每月重复包含闰月');self.leap.setChecked(True)
        self.strict=QCheckBox('闰月生日严格匹配：无对应闰月年份不提醒')
        self.end_mode=QComboBox();self.end_mode.addItems(['永不结束','截止日期','总次数'])
        self.end=QDateTimeEdit(QDateTime.currentDateTime().addYears(1));self.end.setCalendarPopup(True);self.end.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.count=QSpinBox();self.count.setRange(1,100000);self.count.setValue(10)
        for label,widget in [('标题',self.title),('类型',self.kind),('说明',self.body),('第一次提醒',self.start),('时区',self.zone),('重复',self.period),('日期体系',self.calendar),('',self.lunar_input),('缺少日期',self.missing),('',self.leap),('',self.strict),('结束条件',self.end_mode),('截止时间',self.end),('发生次数',self.count)]:form.addRow(label,widget)
        layout.addLayout(form);layout.addWidget(QLabel('提前提醒（可多选）'))
        advances=QHBoxLayout();self.advances=[]
        for text in ['1 周','3 天','1 天','5 小时','3 小时','1 小时']:
            check=QCheckBox(text);self.advances.append(check);advances.addWidget(check)
        layout.addLayout(advances);self.preview=QLabel();self.preview.setWordWrap(True);self.preview.setObjectName('status');layout.addWidget(self.preview)
        scroll.setWidget(content);outer.addWidget(scroll)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);buttons.button(QDialogButtonBox.Save).setText('保存');buttons.button(QDialogButtonBox.Cancel).setText('取消');buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);outer.addWidget(buttons)
        if event:
            r=Rule(**json.loads(event['rule']));self.title.setText(event['title']);self.body.setPlainText(event['body']);self.kind.setCurrentIndex(['todo','schedule','anniversary'].index(event['kind']))
            self.start.setDateTime(QDateTime.fromString(r.start,'yyyy-MM-ddTHH:mm:ss'));self.zone.setText(r.zone);self.period.setCurrentIndex(PERIODS.index(r.period));self.calendar.setCurrentIndex(int(r.calendar=='lunar'))
            self.missing.setCurrentIndex(int(r.missing=='skip'));self.leap.setChecked(r.include_leap);self.strict.setChecked(r.strict_leap)
            if r.count:self.end_mode.setCurrentIndex(2);self.count.setValue(r.count)
            elif r.end:self.end_mode.setCurrentIndex(1);self.end.setDateTime(QDateTime.fromString(r.end,'yyyy-MM-ddTHH:mm:ss'))
            for c,a in zip(self.advances,ADVANCES):c.setChecked(a in r.advances)
        for widget in (self.period,self.calendar,self.missing,self.end_mode):widget.currentIndexChanged.connect(self.update_preview)
        for widget in (self.start,self.end):widget.dateTimeChanged.connect(self.update_preview)
        self.zone.textChanged.connect(self.update_preview);self.count.valueChanged.connect(self.update_preview)
        for widget in (self.leap,self.strict,*self.advances):widget.toggled.connect(self.update_preview)
        self.update_preview()

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

    def rule(self):
        return Rule(start=self.start.dateTime().toString('yyyy-MM-ddTHH:mm:ss'),zone=self.zone.text().strip(),period=PERIODS[self.period.currentIndex()],
            calendar='lunar' if self.calendar.currentIndex() and self.period.currentIndex()>=4 else 'solar',missing='skip' if self.missing.currentIndex() else 'last',include_leap=self.leap.isChecked(),strict_leap=self.strict.isChecked(),
            end=self.end.dateTime().toString('yyyy-MM-ddTHH:mm:ss') if self.end_mode.currentIndex()==1 else None,count=self.count.value() if self.end_mode.currentIndex()==2 else None,
            advances=tuple(a for c,a in zip(self.advances,ADVANCES) if c.isChecked()))

    def update_preview(self,*_):
        monthly=self.period.currentIndex()>=4;lunar=monthly and self.calendar.currentIndex()==1
        self.calendar.setEnabled(monthly)
        for widget,visible in ((self.calendar,monthly),(self.missing,monthly),(self.leap,lunar and self.period.currentIndex()==4),(self.strict,lunar and self.period.currentIndex()==5),(self.end_mode,self.period.currentIndex()>0),(self.end,self.period.currentIndex()>0 and self.end_mode.currentIndex()==1),(self.count,self.period.currentIndex()>0 and self.end_mode.currentIndex()==2)):
            self.form.setRowVisible(widget,bool(visible))
        try:
            rule=self.rule();dates=rule.preview(count=3)
            self.preview.setText(rule.describe()+'\n接下来：\n'+'\n'.join(datetime.fromtimestamp(t,ZoneInfo(rule.zone)).strftime('%Y-%m-%d %H:%M:%S') for _,t in dates))
        except Exception as error:self.preview.setText('请检查设置：'+str(error))

    def save(self):
        try:
            self.store.save_event(self.title.text(),['todo','schedule','anniversary'][self.kind.currentIndex()],self.body.toPlainText(),self.rule(),self.event_record['id'] if self.event_record else None);self.accept()
        except Exception as error:QMessageBox.warning(self,'未能保存',str(error))


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
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.title=QLineEdit();self.title.setPlaceholderText('给这页手账起个名字');self.title.setMaxLength(200);layout.addWidget(self.title)
        tools=QHBoxLayout();attach=QPushButton('添加附件');attach.clicked.connect(self.choose_files);self.mode=QComboBox();self.mode.addItems(['编辑 Markdown','预览']);self.mode.currentIndexChanged.connect(self.mode_changed)
        tools.addWidget(attach);tools.addStretch();tools.addWidget(self.mode);layout.addLayout(tools)
        self.pages=QStackedWidget();self.edit=MarkdownEditor();self.edit.setPlaceholderText('记下一点什么…\n支持 Markdown、粘贴图片和拖入附件。');self.preview=SafePreview(store)
        self.pages.addWidget(self.edit);self.pages.addWidget(self.preview);layout.addWidget(self.pages,1)
        self.files=QListWidget();self.files.setMaximumHeight(105);self.files.itemDoubleClicked.connect(self.open_file);layout.addWidget(self.files)
        from journal_media import MediaBar
        self.media=MediaBar(store.root);self.media.recorded.connect(self.recorded);self.media.prepare_recording=self.prepare_recording;self.recording_identity=None;layout.addWidget(self.media)
        self.status=QLabel('自动保存到本地资料库');self.status.setObjectName('metricName');layout.addWidget(self.status)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(500);self.timer.timeout.connect(self.save)
        self.title.textChanged.connect(self.mark_dirty);self.edit.textChanged.connect(self.mark_dirty);self.edit.filesDropped.connect(self.add_files);self.edit.imagePasted.connect(self.paste_image)

    def mark_dirty(self):
        if not self.loading:self.dirty=True;self.status.setText('正在编辑…');self.timer.start()

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
        if not self.identity:return
        for row in self.store.attachments(self.identity):
            item=QListWidgetItem(f'{row["name"]} · {row["size"]/1024:.1f} KB · 双击'+('播放' if row['mime'].startswith('audio/') else '打开文件夹'))
            item.setData(Qt.UserRole,row);self.files.addItem(item)

    def open_file(self,item):
        row=item.data(Qt.UserRole)
        try:
            path=self.store.attachment_path(row['relative'])
            if row['mime'].startswith('audio/'):self.media.load(path)
            else:QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        except Exception as error:QMessageBox.warning(self,'附件无法打开',str(error))

    def finish(self):
        from PySide6.QtMultimedia import QMediaRecorder
        self.media.finish()
        if self.media.recorder.recorderState()!=QMediaRecorder.StoppedState or self.media.pending_finalize:
            self.status.setText('正在结束录音并保存，请稍候。');return False
        return self.save()
