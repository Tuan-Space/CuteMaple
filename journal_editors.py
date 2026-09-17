from __future__ import annotations
import json
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from PySide6.QtCore import Qt,QDateTime,QTimeZone,QTimer,QUrl,Signal,QEvent,QRect,QSize
from PySide6.QtGui import QDesktopServices,QTextDocument,QImage,QTextCursor,QTextCharFormat,QFont,QTextFormat,QTextListFormat,QShortcut,QKeySequence
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,
    QTextBrowser,QComboBox,QDateTimeEdit,QSpinBox,QCheckBox,QPushButton,QLabel,QDialogButtonBox,
    QMessageBox,QListWidget,QListWidgetItem,QSplitter,QWidget,QFileDialog,QStackedWidget,QScrollArea,QTextEdit,QMenu,QApplication,QLayout,QInputDialog)
from journal_recurrence import Rule,MilestoneRule,parse_rule,ADVANCES,PERIODS
from monitor_ui import ThemeBinding
from monitor_ui import ThemedComboBox as QComboBox
from monitor_ui import ThemedSpinBox as QSpinBox,ThemedCheckBox as QCheckBox
from PySide6.QtCore import QTime
from journal_dates import DateTimeFields


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
        self.start=DateTimeFields(QDateTime.currentDateTime().addSecs(3600))
        if self.kind.currentIndex()==2:self.start.setTime(QTime(9,0))
        self.period=QComboBox();self.period.addItems(['不重复','每小时','每天','每周','每月','每年'])
        self.calendar=QComboBox();self.calendar.addItems(['公历','农历']);self.calendar.currentIndexChanged.connect(self.start.setLunar)
        for name,w in [('名称',self.title),('类型',self.kind),('历法',self.calendar),('时间',self.start),('重复',self.period)]:self.form.addRow(name,w)
        layout.addLayout(self.form)
        self.milestone_box=QWidget();ml=QVBoxLayout(self.milestone_box);ml.setContentsMargins(0,0,0,0);ml.setSpacing(12);ml.addWidget(QLabel('想在哪些日子提醒？'))
        row=QHBoxLayout();self.yearly=QCheckBox('每周年');self.yearly.setChecked(True);self.hundreds=QCheckBox('每 100 天');self.day520=QCheckBox('520 天');self.day1314=QCheckBox('1314 天')
        for w in (self.yearly,self.hundreds,self.day520,self.day1314):row.addWidget(w)
        ml.addLayout(row);self.custom_days=QLineEdit();self.custom_days.setPlaceholderText('其他天数，例如 30、1000');ml.addWidget(self.custom_days);layout.addWidget(self.milestone_box)
        more=QPushButton('更多设置');more.setObjectName('quiet');more.setCheckable(True);layout.addWidget(more);self.advanced=QWidget();self.advanced.setVisible(False);more.toggled.connect(self.advanced.setVisible);advanced=QVBoxLayout(self.advanced);advanced.setContentsMargins(0,0,0,0);self.extra=QFormLayout();self.extra.setSpacing(10)
        self.body=QPlainTextEdit();self.body.setPlaceholderText('补充说明（可选）');self.body.setMaximumHeight(80);self.zone_name=local_zone()
        self.missing=QComboBox();self.missing.addItems(['使用当月最后一天','跳过不存在的日期']);self.leap=QCheckBox('月度提醒包含闰月');self.leap.setChecked(True);self.strict=QCheckBox('只在对应闰月提醒')
        self.end_mode=QComboBox();self.end_mode.addItems(['不设结束时间','截至某天','指定次数']);self.end=DateTimeFields(QDateTime.currentDateTime().addYears(1));self.count=QSpinBox();self.count.setRange(1,100000);self.count.setValue(10)
        for name,w in [('说明',self.body),('缺少日期',self.missing),('',self.leap),('',self.strict),('结束',self.end_mode),('截至',self.end),('次数',self.count)]:self.extra.addRow(name,w)
        advanced.addLayout(self.extra);advanced.addWidget(QLabel('提前提醒'));row=QHBoxLayout();self.advances=[]
        for text in ['1 周','3 天','1 天','5 小时','3 小时','1 小时']:
            c=QCheckBox(text);row.addWidget(c);self.advances.append(c)
        advanced.addLayout(row);layout.addWidget(self.advanced);self.preview=QLabel();self.preview.setObjectName('status');self.preview.setWordWrap(True);layout.addWidget(self.preview);layout.addStretch();scroll.setWidget(content);outer.addWidget(scroll,1)
        self.buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);self.buttons.button(QDialogButtonBox.Save).setText('保存');self.buttons.button(QDialogButtonBox.Save).setObjectName('primary');self.buttons.button(QDialogButtonBox.Cancel).setText('取消');self.buttons.accepted.connect(self.save);self.buttons.rejected.connect(self.reject);outer.addWidget(self.buttons)
        if existing:
            r=existing;self.title.setText(event['title']);self.body.setPlainText(event['body']);self.start.setDateTime(QDateTime.fromString(r.start,'yyyy-MM-ddTHH:mm:ss'));self.zone_name=r.zone;self.calendar.setCurrentIndex(int(r.calendar=='lunar'));self.missing.setCurrentIndex(int(r.missing=='skip'));self.strict.setChecked(r.strict_leap)
            if isinstance(r,MilestoneRule):
                self.yearly.setChecked(r.yearly);self.hundreds.setChecked(r.hundreds);self.day520.setChecked(520 in r.days);self.day1314.setChecked(1314 in r.days);self.custom_days.setText('、'.join(str(n) for n in r.days if n not in (520,1314)))
            else:
                self.period.setCurrentIndex(PERIODS.index(r.period));self.leap.setChecked(r.include_leap)
                if r.count:self.end_mode.setCurrentIndex(2);self.count.setValue(r.count)
                elif r.end:self.end_mode.setCurrentIndex(1);self.end.setDateTime(QDateTime.fromString(r.end,'yyyy-MM-ddTHH:mm:ss'))
            for c,a in zip(self.advances,ADVANCES):c.setChecked(a in r.advances)
            self.calendar.setCurrentIndex(int(store.setting('event_input_lunar:'+event['id'],r.calendar=='lunar')))
        for w in (self.kind,self.period,self.calendar,self.missing,self.end_mode):w.currentIndexChanged.connect(self.update_preview)
        self.start.dateTimeChanged.connect(self.update_preview);self.end.dateTimeChanged.connect(self.update_preview);self.count.valueChanged.connect(self.update_preview);self.custom_days.textChanged.connect(self.update_preview)
        for w in (self.leap,self.strict,self.yearly,self.hundreds,self.day520,self.day1314,*self.advances):w.toggled.connect(self.update_preview)
        self.kind.currentIndexChanged.connect(self.kind_changed);self.update_preview()
        if event and event['archived']:self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        if event:
            if parent and hasattr(parent,'event_ledger'):
                records=self.buttons.addButton('发生记录',QDialogButtonBox.ActionRole);records.setObjectName('quiet');records.clicked.connect(lambda:parent.event_ledger(event))
            if not event.get('deleted'):
                delete=self.buttons.addButton('删除计划',QDialogButtonBox.ActionRole);delete.setObjectName('quiet');delete.clicked.connect(self.delete_plan)

    def delete_plan(self):
        if QMessageBox.question(self,'删除计划','将此计划移到回收站，并停止未来提醒？')==QMessageBox.Yes:
            self.store.archive_event(self.event_record['id']);self.accept()

    def kind_changed(self,index):
        if index==2 and not self.event_record:self.start.setTime(QTime(9,0))
    def is_milestone(self):return self.kind.currentIndex()==2 and not self._legacy
    def rule(self):
        shared=dict(start=self.start.dateTime().toString('yyyy-MM-ddTHH:mm:ss'),zone=self.zone_name,calendar='lunar' if self.calendar.currentIndex() and (self.is_milestone() or self.period.currentIndex()>=4) else 'solar',missing='skip' if self.missing.currentIndex() else 'last',strict_leap=self.strict.isChecked(),advances=tuple(a for c,a in zip(self.advances,ADVANCES) if c.isChecked()))
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
        self.form.setRowVisible(self.period,not milestone)
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
        try:
            rule=self.rule()
            if self.event_record and self.event_record.get('deleted') is not None and isinstance(rule,Rule) and rule.period=='once':
                from journal_recurrence import civil_timestamp
                if civil_timestamp(rule.base,rule.zone)<=self.store.clock():raise ValueError('请选择未来的提醒时间')
            identity=self.store.save_event(self.title.text(),['todo','schedule','anniversary'][self.kind.currentIndex()],self.body.toPlainText(),rule,self.event_record['id'] if self.event_record else None)
            self.store.set_setting('event_input_lunar:'+identity,bool(self.calendar.currentIndex()));self.accept()
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


class RichNote(QTextEdit):
    filesDropped=Signal(list)
    imagePasted=Signal(QImage)
    loadResource=SafePreview.loadResource
    insertFromMimeData=MarkdownEditor.insertFromMimeData
    def __init__(self,store):
        super().__init__();self.store=store;self.literal=False;self.setAcceptRichText(False);self.setPlaceholderText('从这里开始记录…')
    def insertFromMimeData(self,mime):
        if mime.hasUrls():
            paths=[u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if paths:self.filesDropped.emit(paths);return
        if mime.hasImage():self.imagePasted.emit(QImage(mime.imageData()));return
        self.insertPlainText(mime.text())
    def load_markdown(self,text):
        import re
        # Qt cannot round-trip arbitrary HTML, footnotes or extension directives.
        # Preserve those documents literally rather than silently losing syntax.
        self.literal=bool(re.search(r'<[A-Za-z/!]|^\[\^|^\[[^\]]+\]:|^:::|\$\$',text,re.M))
        if self.literal:self.setPlainText(text)
        else:self.document().setMarkdown(text,QTextDocument.MarkdownDialectGitHub|QTextDocument.MarkdownNoHTML)
        self.document().setModified(False)
    def markdown(self):
        value=self.toPlainText() if self.literal else self.document().toMarkdown(QTextDocument.MarkdownDialectGitHub)
        if self.toPlainText().strip() and not value.strip():raise ValueError('正文转换失败，内容仍保留在编辑器中')
        return value


class FormatFlow(QLayout):
    """Keep every formatting action reachable when the editor becomes narrow."""
    def __init__(self,parent=None):
        super().__init__(parent);self.items=[];self.setContentsMargins(0,0,0,0);self.setSpacing(4)
    def addItem(self,item):self.items.append(item)
    def count(self):return len(self.items)
    def itemAt(self,index):return self.items[index] if 0<=index<len(self.items) else None
    def takeAt(self,index):return self.items.pop(index) if 0<=index<len(self.items) else None
    def expandingDirections(self):return Qt.Orientations()
    def hasHeightForWidth(self):return True
    def heightForWidth(self,width):return self.arrange(QRect(0,0,width,0),False)
    def setGeometry(self,rect):super().setGeometry(rect);self.arrange(rect,True)
    def sizeHint(self):return self.minimumSize()
    def minimumSize(self):
        size=QSize()
        for item in self.items:size=size.expandedTo(item.minimumSize())
        return size
    def arrange(self,rect,apply):
        x=rect.x();y=rect.y();height=0
        for item in self.items:
            if item.isEmpty():continue
            size=item.sizeHint()
            if x>rect.x() and x+size.width()>rect.right()+1:x=rect.x();y+=height+self.spacing();height=0
            if apply:item.setGeometry(QRect(x,y,size.width(),size.height()))
            x+=size.width()+self.spacing();height=max(height,size.height())
        return y+height-rect.y()


class NoteEditor(QWidget):
    saved=Signal()
    def __init__(self,store,parent=None):
        super().__init__(parent);self.store=store;self.identity=None;self.loading=False;self.dirty=False
        self.setObjectName('surface');self.setAttribute(Qt.WA_StyledBackground)
        layout=QVBoxLayout(self);layout.setContentsMargins(16,16,16,12);layout.setSpacing(12)
        self.title=QLineEdit();self.title.setObjectName('noteTitle');self.title.setPlaceholderText('无标题笔记');self.title.setMaxLength(200);layout.addWidget(self.title)
        self.format_toolbar=QWidget();self.format_toolbar.setObjectName('formatToolbar');toolbar=QVBoxLayout(self.format_toolbar);toolbar.setContentsMargins(8,8,8,8);toolbar.setSpacing(6)
        tools=QHBoxLayout();self.heading=QComboBox();self.heading.addItems(['正文','一级标题','二级标题','三级标题']);self.heading.setToolTip('段落样式');self.heading.activated.connect(self.set_heading);tools.addWidget(self.heading)
        self.mode=QComboBox();self.mode.addItems(['可视编辑','Markdown 源码']);self.mode.currentIndexChanged.connect(self.mode_changed);tools.addStretch();tools.addWidget(self.mode);toolbar.addLayout(tools)
        self.format_actions=QWidget();actions=FormatFlow(self.format_actions);toolbar.addWidget(self.format_actions);layout.addWidget(self.format_toolbar);self.format_buttons=[]
        def action(name,label,tip,callback,checked=False):
            button=QPushButton(label);button.setObjectName('formatButton');button.setFixedSize(max(34,button.fontMetrics().horizontalAdvance(label)+16),32);button.setFocusPolicy(Qt.NoFocus);button.setToolTip(tip);button.setAccessibleName(tip);button.setCheckable(checked);button.clicked.connect(callback);actions.addWidget(button);setattr(self,name,button);self.format_buttons.append(button);return button
        self.bold=action('bold','B','加粗 · Ctrl+B',self.toggle_bold,True);font=self.bold.font();font.setBold(True);self.bold.setFont(font)
        self.italic=action('italic','I','斜体 · Ctrl+I',self.toggle_italic,True);font=self.italic.font();font.setItalic(True);self.italic.setFont(font)
        self.strike=action('strike','S','删除线',self.toggle_strike,True);font=self.strike.font();font.setStrikeOut(True);self.strike.setFont(font)
        action('bullet','•','项目列表',lambda:self.toggle_list(QTextListFormat.ListDisc),True)
        action('numbered','1.','编号列表',lambda:self.toggle_list(QTextListFormat.ListDecimal),True)
        action('indent','→','增加列表层级',lambda:self.indent_list(1));action('outdent','←','减少列表层级',lambda:self.indent_list(-1))
        action('quote','引用','引用段落',self.toggle_quote,True);action('link','链接','插入链接 · Ctrl+K',self.insert_link)
        action('undo','撤销','撤销 · Ctrl+Z',lambda:self.preview.undo());action('redo','重做','重做 · Ctrl+Y',lambda:self.preview.redo())
        self.format_hint=QLabel();self.format_hint.setObjectName('noteFormatHint');self.format_hint.setWordWrap(True);self.format_hint.hide();layout.addWidget(self.format_hint)
        self.pages=QStackedWidget();self.edit=MarkdownEditor();self.edit.setPlaceholderText('Markdown 源码');self.preview=RichNote(store)
        self.pages.addWidget(self.preview);self.pages.addWidget(self.edit);layout.addWidget(self.pages,1);self.raw_body='';self.original_body='';self.body_dirty=False;self.active_mode=0;self.file_job=None
        from journal_design import WrappedItem
        self.files=QListWidget();self.files.setObjectName('attachmentList');self.files.setWordWrap(True);self.files.setTextElideMode(Qt.ElideNone);self.files.setResizeMode(QListWidget.Adjust);self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.files.setItemDelegate(WrappedItem(self.files));self.files.setVerticalScrollMode(QListWidget.ScrollPerPixel);self.files.setMaximumHeight(144);self.files.itemDoubleClicked.connect(self.open_file);layout.addWidget(self.files)
        from journal_media import MediaBar
        self.media=MediaBar(store.root);self.media.recorded.connect(self.recorded);self.media.prepare_recording=self.prepare_recording;self.recording_identity=None;layout.addWidget(self.media)
        self.media.hide();self.files.hide()
        bottom=QHBoxLayout();self.status=QLabel();self.status.setObjectName('metricName');self.status.setWordWrap(True);bottom.addWidget(self.status,1)
        self.plus=QPushButton('＋');self.plus.setFixedSize(36,36);self.plus.setToolTip('添加附件或录音');self.plus.clicked.connect(self.add_menu);bottom.addWidget(self.plus);layout.addLayout(bottom)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(500);self.timer.timeout.connect(self.save)
        self.title.textChanged.connect(self.mark_dirty);self.edit.textChanged.connect(self.mark_dirty);self.edit.filesDropped.connect(self.add_files);self.edit.imagePasted.connect(self.paste_image)
        self.preview.textChanged.connect(self.rich_dirty);self.preview.filesDropped.connect(self.add_files);self.preview.imagePasted.connect(self.paste_image);QApplication.instance().installEventFilter(self)
        self.preview.cursorPositionChanged.connect(self.update_format_state);self.preview.currentCharFormatChanged.connect(self.update_format_state);self.preview.selectionChanged.connect(self.update_format_state)
        self.preview.document().undoAvailable.connect(self.update_format_state);self.preview.document().redoAvailable.connect(self.update_format_state)
        self.shortcuts=[]
        for key,callback in (('Ctrl+B',self.toggle_bold),('Ctrl+I',self.toggle_italic),('Ctrl+K',self.insert_link)):
            shortcut=QShortcut(QKeySequence(key),self.preview);shortcut.setContext(Qt.WidgetShortcut);shortcut.activated.connect(callback);self.shortcuts.append(shortcut)
        self.update_format_state()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.EnabledChange and hasattr(self,'active_mode'):self.update_format_state()

    def eventFilter(self,obj,event):
        click=event.type()==QEvent.MouseButtonPress and isinstance(obj,QWidget)
        # Mouse presses on labels can propagate through parent widgets; use the
        # actual click position so an inside click never becomes an outside one.
        def inside(widget):
            if obj==widget or widget.isAncestorOf(obj):return True
            return obj.isAncestorOf(widget) and widget.rect().contains(widget.mapFromGlobal(event.globalPosition().toPoint()))
        if click and self.media.record_panel.isVisible() and not inside(self.plus) and not inside(self.media.record_panel):
            self.media.record_panel.hide();self.media.update_visibility()
        if self.media.playback_completed:
            outside=click and not inside(self.media.play_panel)
            deactivated=event.type()==QEvent.WindowDeactivate and obj==self.window()
            if outside or deactivated:self.media.dismiss_playback()
        return super().eventFilter(obj,event)
    def add_menu(self):
        menu=QMenu(self);menu.addAction('添加附件',self.choose_files);menu.addAction('录音',self.show_recording);menu.exec(self.plus.mapToGlobal(self.plus.rect().topLeft()))
    def toggle_bold(self):
        if not self.can_format():return
        fmt=QTextCharFormat();fmt.setFontWeight(QFont.Normal if self.preview.fontWeight()>=QFont.Bold else QFont.Bold);self.apply_char_format(fmt)
    def toggle_italic(self):
        if not self.can_format():return
        fmt=QTextCharFormat();fmt.setFontItalic(not self.preview.fontItalic());self.apply_char_format(fmt)
    def toggle_strike(self):
        if not self.can_format():return
        fmt=QTextCharFormat();fmt.setFontStrikeOut(not self.preview.currentCharFormat().fontStrikeOut());self.apply_char_format(fmt)
    def can_format(self):return not self.active_mode and not self.preview.literal and self.isEnabled()
    def apply_char_format(self,fmt):
        self.preview.mergeCurrentCharFormat(fmt);self.preview.setFocus();self.update_format_state()
    def selected_blocks(self):
        cursor=self.preview.textCursor();first=cursor.selectionStart();last=cursor.selectionEnd();block=self.preview.document().findBlock(first);blocks=[]
        while block.isValid() and (not blocks or block.position()<last):blocks.append(block);block=block.next()
        return blocks
    def edit_blocks(self,callback):
        if not self.can_format():return
        cursor=self.preview.textCursor();cursor.beginEditBlock()
        try:callback(self.selected_blocks())
        finally:cursor.endEditBlock()
        self.preview.setTextCursor(cursor);self.preview.setFocus();self.update_format_state()
    def set_heading(self,level):
        def change(blocks):
            for block in blocks:
                cursor=QTextCursor(block);fmt=block.blockFormat();fmt.setHeadingLevel(level);cursor.setBlockFormat(fmt)
                cursor.select(QTextCursor.BlockUnderCursor);char=QTextCharFormat();char.setFontWeight(QFont.Bold if level else QFont.Normal);char.setProperty(QTextFormat.FontSizeAdjustment,4-level if level else 0);cursor.mergeCharFormat(char)
        self.edit_blocks(change)
    def assign_list(self,blocks,style,indent):
        fmt=QTextListFormat();fmt.setStyle(style);fmt.setIndent(indent)
        previous=blocks[0].previous();text_list=previous.textList() if previous.isValid() else None
        if not text_list or text_list.format().style()!=style or text_list.format().indent()!=indent:text_list=QTextCursor(blocks[0]).createList(fmt)
        for block in blocks:
            text_list.add(block);cursor=QTextCursor(block);block_fmt=block.blockFormat();block_fmt.setIndent(0);cursor.setBlockFormat(block_fmt)
    def toggle_list(self,style):
        def change(blocks):
            remove=all(b.textList() and b.textList().format().style()==style for b in blocks)
            if remove:
                for block in blocks:
                    block.textList().remove(block);cursor=QTextCursor(block);fmt=block.blockFormat();fmt.setIndent(0);cursor.setBlockFormat(fmt)
            else:self.assign_list(blocks,style,blocks[0].textList().format().indent() if blocks[0].textList() else 1)
        self.edit_blocks(change)
    def indent_list(self,delta):
        def change(blocks):
            if not all(block.textList() for block in blocks):return
            fmt=blocks[0].textList().format();indent=max(1,min(6,fmt.indent()+delta))
            previous=blocks[0].previous()
            if delta>0 and (not previous.isValid() or not previous.textList() or previous.textList().format().indent()<fmt.indent()):return
            if indent!=fmt.indent():self.assign_list(blocks,fmt.style(),indent)
        self.edit_blocks(change)
    def toggle_quote(self):
        def change(blocks):
            level=0 if all(b.blockFormat().intProperty(QTextFormat.BlockQuoteLevel) for b in blocks) else 1
            for block in blocks:
                cursor=QTextCursor(block);fmt=block.blockFormat();fmt.setProperty(QTextFormat.BlockQuoteLevel,level);fmt.setLeftMargin(18 if level else 0);cursor.setBlockFormat(fmt)
        self.edit_blocks(change)
    def insert_link(self):
        if not self.can_format():return
        cursor=self.preview.textCursor();label=cursor.selectedText()
        if '\u2029' in label:self.status.setText('请选择一段文字作为链接名称。');return
        value,accepted=QInputDialog.getText(self,'插入链接','网页或邮箱地址',text=cursor.charFormat().anchorHref() or 'https://')
        if not accepted:return
        value=value.strip();url=QUrl.fromUserInput(value)
        if not url.isValid() or url.scheme() not in ('http','https','mailto') or (url.scheme() in ('http','https') and not url.host()):self.status.setText('请输入有效的网页或邮箱链接。');return
        fmt=QTextCharFormat();fmt.setAnchor(True);fmt.setAnchorHref(url.toString());fmt.setFontUnderline(True);cursor.insertText(label or value,fmt);self.preview.setTextCursor(cursor);self.preview.setCurrentCharFormat(QTextCharFormat());self.preview.setFocus()
    def update_format_state(self,*_):
        if not hasattr(self,'preview'):return
        available=self.can_format();self.heading.setEnabled(available);self.format_actions.setVisible(not self.active_mode);self.heading.setVisible(not self.active_mode)
        for button in self.format_buttons:button.setEnabled(available)
        self.format_hint.setVisible(not self.active_mode and self.preview.literal)
        self.format_hint.setText('此笔记含扩展语法，已按原文保留。请使用 Markdown 源码编辑格式。')
        fmt=self.preview.currentCharFormat();block=self.preview.textCursor().block();text_list=block.textList();self.bold.setChecked(fmt.fontWeight()>=QFont.Bold);self.italic.setChecked(fmt.fontItalic());self.strike.setChecked(fmt.fontStrikeOut())
        self.heading.blockSignals(True);self.heading.setCurrentIndex(min(3,block.blockFormat().headingLevel()));self.heading.blockSignals(False)
        self.bullet.setChecked(bool(text_list and text_list.format().style()==QTextListFormat.ListDisc));self.numbered.setChecked(bool(text_list and text_list.format().style()==QTextListFormat.ListDecimal));self.quote.setChecked(bool(block.blockFormat().intProperty(QTextFormat.BlockQuoteLevel)))
        blocks=self.selected_blocks();first_list=blocks[0].textList();all_lists=all(b.textList() for b in blocks);previous=blocks[0].previous()
        can_indent=all_lists and previous.isValid() and previous.textList() and previous.textList().format().indent()>=first_list.format().indent() and first_list.format().indent()<6
        self.indent.setEnabled(available and bool(can_indent));self.outdent.setEnabled(available and all_lists and first_list.format().indent()>1)
        self.undo.setEnabled(available and self.preview.document().isUndoAvailable());self.redo.setEnabled(available and self.preview.document().isRedoAvailable())
    def rich_dirty(self):
        if not self.loading:self.body_dirty=True;self.mark_dirty()
    def current_body(self):
        if self.active_mode:return self.edit.toPlainText()
        return self.preview.markdown() if self.body_dirty else self.raw_body

    def mark_dirty(self):
        if not self.loading:
            if self.sender()==self.edit:self.body_dirty=True
            self.dirty=True;self.status.setText('正在编辑…');self.timer.start()

    def show_recording(self):
        self.media.show();self.media.record_panel.show();self.media.status.show()

    def load(self,note=None):
        if not self.finish():return False
        self.media.record_panel.hide();self.media.update_visibility()
        self.loading=True;self.identity=note['id'] if note else None;self.raw_body=note['body'] if note else '';self.original_body=self.raw_body;self.body_dirty=False
        self.title.setText(note['title'] if note else '');self.edit.setPlainText(self.raw_body);self.preview.load_markdown(self.raw_body);self.preview.moveCursor(QTextCursor.Start);self.preview.verticalScrollBar().setValue(0);self.edit.moveCursor(QTextCursor.Start);self.edit.verticalScrollBar().setValue(0);self.loading=False;self.dirty=False
        self.refresh_files();self.update_format_state();self.status.setText('复杂格式按原文保留，可直接编辑' if self.preview.literal else '');return True

    def save(self):
        self.timer.stop()
        if not self.dirty:return True
        try:
            body=self.current_body()
            with self.store.db:
                if self.identity and self.body_dirty and self.original_body:self.store.db.execute('INSERT OR IGNORE INTO note_originals VALUES(?,?)',(self.identity,self.original_body))
                self.identity=self.store.save_note(self.title.text(),body,self.identity)
            self.raw_body=body;self.body_dirty=False;self.dirty=False;self.status.setText('已保存 · '+datetime.now().strftime('%H:%M:%S'));self.saved.emit();return True
        except Exception as error:self.status.setText('尚未保存，请勿退出：'+str(error));return False

    def ensure_note(self):
        if self.identity is None:self.dirty=True
        if not self.save():raise OSError('请先恢复资料库写入')

    def mode_changed(self,*_):
        if not hasattr(self,'preview'):return
        # Changing the view must not clear an unsaved body edit: save() still
        # needs that flag to preserve the original before the first rewrite.
        body=self.current_body();self.loading=True;self.raw_body=body
        if self.mode.currentIndex():self.edit.setPlainText(body)
        else:self.preview.load_markdown(body)
        self.active_mode=self.mode.currentIndex();self.pages.setCurrentIndex(self.active_mode);self.loading=False;self.update_format_state()

    def insert_markdown(self,text):
        if self.active_mode:self.edit.insertPlainText(text)
        elif self.preview.literal:self.preview.insertPlainText(text)
        else:
            cursor=self.preview.textCursor();cursor.insertMarkdown(text,QTextDocument.MarkdownDialectGitHub|QTextDocument.MarkdownNoHTML);self.preview.setTextCursor(cursor);self.preview.setCurrentCharFormat(QTextCharFormat())

    def choose_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'添加附件');self.add_files(paths)

    def add_files(self,paths,remove_sources=False):
        if not paths:return
        if self.file_job:self.status.setText('正在添加附件，请稍候');return
        try:
            self.ensure_note();owner=self.identity
            from journal_jobs import FileJob
            self.file_job=FileJob(self);self.status.setText('正在添加附件…');self.plus.setEnabled(False)
            def work():
                results=[]
                try:
                    for path in paths:results.append(self.store.prepare_attachment(path))
                except Exception:
                    for row in results:self.store.attachment_path(row['relative']).unlink(missing_ok=True)
                    raise
                return results
            def done(rows,error):
                self.file_job.deleteLater();self.file_job=None;self.plus.setEnabled(True)
                try:
                    if error:raise OSError(error)
                    for value in rows:
                        row=self.store.finish_attachment(owner,value);label=row['name'].replace('[','').replace(']','');prefix='!' if row['mime'].startswith('image/') else '';self.insert_markdown(f'\n{prefix}[{label}]({row["relative"]})\n')
                    if not self.save():raise OSError('附件已经复制，正文尚未保存，请勿退出')
                    self.refresh_files()
                    if remove_sources:
                        for path in paths:Path(path).unlink(missing_ok=True)
                except Exception as e:self.status.setText('附件未保存：'+str(e))
            self.file_job.finished.connect(done);self.file_job.start(work)
        except Exception as error:QMessageBox.warning(self,'附件未保存',str(error))

    def paste_image(self,image):
        path=self.store.root/'recordings'/('图片-'+__import__('uuid').uuid4().hex+'.png')
        if image.save(str(path),'PNG'):
            self.add_files([str(path)],remove_sources=True)

    def recorded(self,path):
        try:
            owner=self.recording_identity
            if owner is None:raise ValueError('找不到录音所属笔记')
            if self.identity!=owner:raise ValueError('录音所属笔记已改变，原文件已保留')
            self.add_files([path],remove_sources=True);self.media.status.setText('录音已停止 · 麦克风未使用')
        except Exception as error:self.media.status.setText('录音保留在 recordings 文件夹，未能加入笔记：'+str(error))

    def prepare_recording(self):
        self.ensure_note();self.recording_identity=self.identity

    def refresh_files(self):
        from journal_design import ITEM_PRESENTATION_ROLE
        self.files.clear()
        self.files.hide()
        if not self.identity:return
        for row in self.store.attachments(self.identity):
            audio=row['mime'].startswith('audio/');hint='双击播放' if audio else '双击打开所在文件夹'
            item=QListWidgetItem(f'{row["name"]}\n{row["size"]/1024:.1f} KB · {hint}')
            item.setData(ITEM_PRESENTATION_ROLE,{'title':row['name'],'subtitle':f'{row["size"]/1024:.1f} KB · {hint}'})
            item.setToolTip(hint)
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
        if self.file_job:self.status.setText('附件正在保存，请稍候');return False
        from PySide6.QtMultimedia import QMediaRecorder
        self.media.finish()
        if self.media.recorder.recorderState()!=QMediaRecorder.StoppedState or self.media.pending_finalize:
            self.status.setText('正在结束录音并保存，请稍候。');return False
        return self.save()
