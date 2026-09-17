from __future__ import annotations
import json
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from PySide6.QtCore import Qt,QDateTime,QTimeZone,QTimer,QUrl,Signal,QEvent,QRect,QSize,QRectF
from PySide6.QtGui import QDesktopServices,QTextDocument,QImage,QTextCursor,QTextCharFormat,QFont,QTextFormat,QTextListFormat,QShortcut,QKeySequence,QIcon,QIconEngine,QPainter,QPainterPath,QPen,QPalette,QPixmap
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,
    QTextBrowser,QComboBox,QDateTimeEdit,QSpinBox,QCheckBox,QPushButton,QLabel,QDialogButtonBox,
    QMessageBox,QListWidget,QListWidgetItem,QSplitter,QWidget,QFileDialog,QStackedWidget,QScrollArea,QTextEdit,QMenu,QApplication,QLayout,QInputDialog)
from journal_recurrence import Rule,MilestoneRule,parse_rule,ADVANCES,PERIODS
from monitor_ui import ThemeBinding
from monitor_ui import ThemedComboBox as QComboBox
from monitor_ui import ThemedSpinBox as QSpinBox,ThemedCheckBox as QCheckBox
from PySide6.QtCore import QTime
from journal_dates import DateTimeFields


from journal_event_editor import EventEditor, local_zone


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
        if self.isReadOnly():return
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
        if self.isReadOnly():return
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
    def sizeHint(self):
        active=[item for item in self.items if not item.isEmpty()]
        return QSize(sum(item.sizeHint().width() for item in active)+max(0,len(active)-1)*self.spacing(),max((item.sizeHint().height() for item in active),default=0))
    def minimumSize(self):
        size=QSize()
        for item in self.items:
            if not item.isEmpty():size=size.expandedTo(item.minimumSize())
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


class NoteToolIcon(QIconEngine):
    """Small vector controls, independent of installed symbol fonts."""
    def __init__(self,kind,owner=None):
        from weakref import ref
        super().__init__();self.kind=kind;self.owner=ref(owner) if owner is not None else lambda:None
    def clone(self):return NoteToolIcon(self.kind,self.owner())
    def paint(self,painter,rect,mode,state):
        palette=QApplication.palette();group=QPalette.Disabled if mode==QIcon.Disabled else QPalette.Active
        color=palette.color(group,QPalette.ButtonText)
        # Journal themes are scoped to a window instead of the global palette.
        owner=self.owner()
        try:window=owner.window() if owner is not None else QApplication.activeWindow()
        except RuntimeError:window=None
        theme=getattr(window,'_theme',None)
        if theme:
            from PySide6.QtGui import QColor
            color=QColor(theme['muted' if mode==QIcon.Disabled else 'text'])
        painter.save();painter.setRenderHint(QPainter.Antialiasing);painter.translate(rect.x(),rect.y());painter.scale(rect.width()/24,rect.height()/24)
        painter.setPen(QPen(color,1.8,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin));painter.setBrush(Qt.NoBrush)
        path=QPainterPath()
        if self.kind=='attachment':
            path.moveTo(9,16);path.lineTo(16,9);path.cubicTo(19,6,15,2,12,5);path.lineTo(4,13);path.cubicTo(-1,18,6,25,11,20);path.lineTo(20,11);path.cubicTo(25,6,18,-1,13,4)
        else:
            if self.kind=='redo':painter.translate(24,0);painter.scale(-1,1)
            path.moveTo(5,9);path.cubicTo(12,4,21,8,20,15);path.cubicTo(20,18,17,20,14,20)
            path.moveTo(5,4);path.lineTo(5,10);path.lineTo(11,10)
        painter.drawPath(path);painter.restore()
    def pixmap(self,size,mode,state):
        pixmap=QPixmap(size);pixmap.fill(Qt.transparent);painter=QPainter(pixmap);self.paint(painter,QRect(0,0,size.width(),size.height()),mode,state);painter.end();return pixmap


class NoteEditor(QWidget):
    saved=Signal()
    permanentDeleteRequested=Signal(str)
    def __init__(self,store,parent=None):
        super().__init__(parent);self.store=store;self.identity=None;self.loading=False;self.dirty=False;self.read_only=False
        self.setObjectName('surface');self.setAttribute(Qt.WA_StyledBackground)
        layout=QVBoxLayout(self);layout.setContentsMargins(16,16,16,12);layout.setSpacing(12)
        self.title=QLineEdit();self.title.setObjectName('noteTitle');self.title.setPlaceholderText('无标题笔记');self.title.setMaxLength(200);layout.addWidget(self.title)
        self.format_toolbar=QWidget();self.format_toolbar.setObjectName('formatToolbar');toolbar=QVBoxLayout(self.format_toolbar);toolbar.setContentsMargins(6,6,6,6)
        self.format_actions=QWidget();self.tool_flow=FormatFlow(self.format_actions);toolbar.addWidget(self.format_actions);layout.addWidget(self.format_toolbar);self.format_buttons=[];self.rich_buttons=[];self.rich_tool_groups=[]
        def group(rich=False):
            widget=QWidget();row=QHBoxLayout(widget);row.setContentsMargins(0,0,0,0);row.setSpacing(2);self.tool_flow.addWidget(widget)
            if rich:self.rich_tool_groups.append(widget)
            return row
        def action(row,name,label,tip,callback,checked=False,icon=None,rich=True):
            button=QPushButton(label);button.setObjectName('formatButton');button.setFixedSize(max(30,button.fontMetrics().horizontalAdvance(label)+14),30);button.setFocusPolicy(Qt.NoFocus);button.setToolTip(tip);button.setAccessibleName(tip);button.setCheckable(checked);button.clicked.connect(callback)
            if icon:button.setIcon(QIcon(NoteToolIcon(icon,button)));button.setIconSize(QSize(19,19))
            row.addWidget(button);setattr(self,name,button);self.format_buttons.append(button)
            if rich:self.rich_buttons.append(button)
            return button
        heading_group=group(True);self.heading=QComboBox();self.heading.addItems(['正文','一级标题','二级标题','三级标题']);self.heading.setToolTip('段落样式');self.heading.activated.connect(self.set_heading);heading_group.addWidget(self.heading)
        emphasis=group(True)
        self.bold=action(emphasis,'bold','B','加粗 · Ctrl+B',self.toggle_bold,True);font=self.bold.font();font.setBold(True);self.bold.setFont(font)
        self.italic=action(emphasis,'italic','I','斜体 · Ctrl+I',self.toggle_italic,True);font=self.italic.font();font.setItalic(True);self.italic.setFont(font)
        self.strike=action(emphasis,'strike','S','删除线',self.toggle_strike,True);font=self.strike.font();font.setStrikeOut(True);self.strike.setFont(font)
        lists=group(True);action(lists,'bullet','•','项目列表',lambda:self.toggle_list(QTextListFormat.ListDisc),True);action(lists,'numbered','1.','编号列表',lambda:self.toggle_list(QTextListFormat.ListDecimal),True)
        action(lists,'indent','→','增加列表层级',lambda:self.indent_list(1));action(lists,'outdent','←','减少列表层级',lambda:self.indent_list(-1))
        references=group(True);action(references,'quote','引用','引用段落',self.toggle_quote,True);action(references,'link','链接','插入链接 · Ctrl+K',self.insert_link)
        common=group();action(common,'plus','','添加附件或录音',self.add_menu,icon='attachment',rich=False)
        history=group();action(history,'undo','','撤销 · Ctrl+Z',self.undo_active,icon='undo',rich=False);action(history,'redo','','重做 · Ctrl+Y',self.redo_active,icon='redo',rich=False)
        mode_group=group();self.mode=QComboBox();self.mode.addItems(['可视编辑','Markdown 源码']);self.mode.currentIndexChanged.connect(self.mode_changed);mode_group.addWidget(self.mode)
        self.format_hint=QLabel();self.format_hint.setObjectName('noteFormatHint');self.format_hint.setWordWrap(True);self.format_hint.hide();layout.addWidget(self.format_hint)
        self.pages=QStackedWidget();self.edit=MarkdownEditor();self.edit.setPlaceholderText('Markdown 源码');self.preview=RichNote(store)
        self.pages.addWidget(self.preview);self.pages.addWidget(self.edit);layout.addWidget(self.pages,1);self.raw_body='';self.original_body='';self.body_dirty=False;self.active_mode=0;self.file_job=None
        from journal_design import WrappedItem
        self.files=QListWidget();self.files.setObjectName('attachmentList');self.files.setWordWrap(True);self.files.setTextElideMode(Qt.ElideNone);self.files.setResizeMode(QListWidget.Adjust);self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.files.setItemDelegate(WrappedItem(self.files));self.files.setVerticalScrollMode(QListWidget.ScrollPerPixel);self.files.setMaximumHeight(144);self.files.itemDoubleClicked.connect(self.open_file);layout.addWidget(self.files)
        from journal_media import MediaBar
        self.media=MediaBar(store.root);self.media.recorded.connect(self.recorded);self.media.prepare_recording=self.prepare_recording;self.recording_identity=None;layout.addWidget(self.media)
        self.media.hide();self.files.hide()
        self.status=QLabel();self.status.setObjectName('metricName');self.status.setWordWrap(True);self.status.hide();layout.addWidget(self.status)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(500);self.timer.timeout.connect(self.save)
        self.title.textChanged.connect(self.mark_dirty);self.edit.textChanged.connect(self.mark_dirty);self.edit.filesDropped.connect(self.add_files);self.edit.imagePasted.connect(self.paste_image)
        self.preview.textChanged.connect(self.rich_dirty);self.preview.filesDropped.connect(self.add_files);self.preview.imagePasted.connect(self.paste_image);QApplication.instance().installEventFilter(self)
        self.preview.cursorPositionChanged.connect(self.update_format_state);self.preview.currentCharFormatChanged.connect(self.update_format_state);self.preview.selectionChanged.connect(self.update_format_state)
        self.preview.document().undoAvailable.connect(self.update_format_state);self.preview.document().redoAvailable.connect(self.update_format_state)
        self.edit.document().undoAvailable.connect(self.update_format_state);self.edit.document().redoAvailable.connect(self.update_format_state)
        for widget in (self.title,self.preview,self.edit):
            widget.setContextMenuPolicy(Qt.CustomContextMenu);widget.customContextMenuRequested.connect(lambda pos,w=widget:self.show_text_context_menu(w,pos))
        self.shortcuts=[]
        for key,callback in (('Ctrl+B',self.toggle_bold),('Ctrl+I',self.toggle_italic),('Ctrl+K',self.insert_link)):
            shortcut=QShortcut(QKeySequence(key),self.preview);shortcut.setContext(Qt.WidgetShortcut);shortcut.activated.connect(callback);self.shortcuts.append(shortcut)
        self.update_format_state()

    def can_mutate(self):return not self.read_only and self.isEnabled()

    def set_read_only(self,value):
        value=bool(value)
        if value!=self.read_only and value and not self.finish():return False
        self.read_only=value;self.title.setReadOnly(value);self.preview.setReadOnly(value);self.edit.setReadOnly(value)
        self.preview.setAcceptDrops(not value);self.edit.setAcceptDrops(not value)
        if value:
            self.timer.stop();self.mode.setCurrentIndex(0);self.media.record_panel.hide();self.media.compact_stop.hide();self.media.update_visibility();self.status.hide()
        self.update_format_state();return True

    def show_status(self,text):
        self.status.setText(text);self.status.setVisible(bool(text) and not self.read_only)

    def build_text_context_menu(self,widget):
        menu=widget.createStandardContextMenu()
        if self.identity:
            menu.addSeparator();identity=self.identity
            action=menu.addAction('永久删除当前笔记…');action.setObjectName('permanentDeleteNote')
            action.triggered.connect(lambda _=False,note_id=identity:self.permanentDeleteRequested.emit(note_id))
        return menu

    def show_text_context_menu(self,widget,pos):
        menu=self.build_text_context_menu(widget)
        try:menu.exec(widget.mapToGlobal(pos))
        finally:menu.deleteLater()

    def undo_active(self):
        if self.can_mutate():(self.edit if self.active_mode else self.preview).undo()

    def redo_active(self):
        if self.can_mutate():(self.edit if self.active_mode else self.preview).redo()

    def clear_note_identity(self):
        """Detach a successfully deleted note without calling save() or load()."""
        from PySide6.QtMultimedia import QMediaRecorder
        if self.file_job or self.media.pending_finalize or self.media.recorder.recorderState()!=QMediaRecorder.StoppedState:return False
        self.timer.stop();self.loading=True
        try:
            self.identity=None;self.recording_identity=None;self.raw_body='';self.original_body='';self.dirty=False;self.body_dirty=False
            self.title.clear();self.edit.clear();self.preview.load_markdown('');self.files.clear();self.files.hide();self.media.dismiss_playback();self.media.record_panel.hide();self.media.update_visibility();self.show_status('')
        finally:self.loading=False
        self.update_format_state();return True

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
        if not self.can_mutate():return
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
    def can_format(self):return not self.active_mode and not self.preview.literal and self.can_mutate()
    def apply_char_format(self,fmt):
        if not self.can_format():return
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
        if not self.can_format():return
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
        if '\u2029' in label:self.show_status('请选择一段文字作为链接名称。');return
        value,accepted=QInputDialog.getText(self,'插入链接','网页或邮箱地址',text=cursor.charFormat().anchorHref() or 'https://')
        if not accepted:return
        value=value.strip();url=QUrl.fromUserInput(value)
        if not url.isValid() or url.scheme() not in ('http','https','mailto') or (url.scheme() in ('http','https') and not url.host()):self.show_status('请输入有效的网页或邮箱链接。');return
        fmt=QTextCharFormat();fmt.setAnchor(True);fmt.setAnchorHref(url.toString());fmt.setFontUnderline(True);cursor.insertText(label or value,fmt);self.preview.setTextCursor(cursor);self.preview.setCurrentCharFormat(QTextCharFormat());self.preview.setFocus()
    def update_format_state(self,*_):
        if not hasattr(self,'preview'):return
        available=self.can_format();self.heading.setEnabled(available);self.format_toolbar.setVisible(not self.read_only)
        for group in self.rich_tool_groups:group.setVisible(not self.active_mode)
        for button in self.rich_buttons:button.setEnabled(available)
        self.plus.setEnabled(self.can_mutate() and not self.file_job)
        self.format_hint.setVisible(not self.read_only and not self.active_mode and self.preview.literal)
        self.format_hint.setText('此笔记含扩展语法，已按原文保留。请使用 Markdown 源码编辑格式。')
        fmt=self.preview.currentCharFormat();block=self.preview.textCursor().block();text_list=block.textList();self.bold.setChecked(fmt.fontWeight()>=QFont.Bold);self.italic.setChecked(fmt.fontItalic());self.strike.setChecked(fmt.fontStrikeOut())
        self.heading.blockSignals(True);self.heading.setCurrentIndex(min(3,block.blockFormat().headingLevel()));self.heading.blockSignals(False)
        self.bullet.setChecked(bool(text_list and text_list.format().style()==QTextListFormat.ListDisc));self.numbered.setChecked(bool(text_list and text_list.format().style()==QTextListFormat.ListDecimal));self.quote.setChecked(bool(block.blockFormat().intProperty(QTextFormat.BlockQuoteLevel)))
        blocks=self.selected_blocks();first_list=blocks[0].textList();all_lists=all(b.textList() for b in blocks);previous=blocks[0].previous()
        can_indent=all_lists and previous.isValid() and previous.textList() and previous.textList().format().indent()>=first_list.format().indent() and first_list.format().indent()<6
        self.indent.setEnabled(available and bool(can_indent));self.outdent.setEnabled(available and all_lists and first_list.format().indent()>1)
        document=(self.edit if self.active_mode else self.preview).document()
        self.undo.setEnabled(self.can_mutate() and document.isUndoAvailable());self.redo.setEnabled(self.can_mutate() and document.isRedoAvailable())
    def rich_dirty(self):
        if not self.loading and self.can_mutate():self.body_dirty=True;self.mark_dirty()
    def current_body(self):
        if self.active_mode:return self.edit.toPlainText()
        return self.preview.markdown() if self.body_dirty else self.raw_body

    def mark_dirty(self):
        if not self.loading and self.can_mutate():
            if self.sender()==self.edit:self.body_dirty=True
            self.dirty=True;self.timer.start()

    def show_recording(self):
        if not self.can_mutate():return False
        self.media.show();self.media.record_panel.show();self.media.status.show()
        return True

    def load(self,note=None):
        if not self.finish():return False
        self.media.record_panel.hide();self.media.update_visibility()
        self.loading=True;self.identity=note['id'] if note else None;self.raw_body=note['body'] if note else '';self.original_body=self.raw_body;self.body_dirty=False
        self.title.setText(note['title'] if note else '');self.edit.setPlainText(self.raw_body);self.preview.load_markdown(self.raw_body);self.preview.moveCursor(QTextCursor.Start);self.preview.verticalScrollBar().setValue(0);self.edit.moveCursor(QTextCursor.Start);self.edit.verticalScrollBar().setValue(0);self.loading=False;self.dirty=False
        self.refresh_files();self.update_format_state();self.show_status('');return True

    def save(self):
        self.timer.stop()
        if self.read_only:return not self.dirty
        if not self.dirty:return True
        try:
            body=self.current_body()
            with self.store.db:
                if self.identity and self.body_dirty and self.original_body:self.store.db.execute('INSERT OR IGNORE INTO note_originals VALUES(?,?)',(self.identity,self.original_body))
                self.identity=self.store.save_note(self.title.text(),body,self.identity)
            self.raw_body=body;self.body_dirty=False;self.dirty=False;self.show_status('');self.saved.emit();return True
        except Exception as error:self.show_status('尚未保存，请勿退出：'+str(error));return False

    def ensure_note(self):
        if not self.can_mutate():raise PermissionError('回收站笔记只读，请先恢复笔记。')
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
        if not self.can_mutate():return False
        if self.active_mode:self.edit.insertPlainText(text)
        elif self.preview.literal:self.preview.insertPlainText(text)
        else:
            cursor=self.preview.textCursor();cursor.insertMarkdown(text,QTextDocument.MarkdownDialectGitHub|QTextDocument.MarkdownNoHTML);self.preview.setTextCursor(cursor);self.preview.setCurrentCharFormat(QTextCharFormat())

    def choose_files(self):
        if not self.can_mutate():return
        paths,_=QFileDialog.getOpenFileNames(self,'添加附件');self.add_files(paths)

    def add_files(self,paths,remove_sources=False):
        if not self.can_mutate() or not paths:return
        if self.file_job:self.show_status('正在添加附件，请稍候');return
        try:
            self.ensure_note();owner=self.identity
            from journal_jobs import FileJob
            self.file_job=FileJob(self);self.show_status('正在添加附件…');self.plus.setEnabled(False)
            def work():
                results=[]
                try:
                    for path in paths:results.append(self.store.prepare_attachment(path))
                except Exception:
                    for row in results:self.store.attachment_path(row['relative']).unlink(missing_ok=True)
                    raise
                return results
            def done(rows,error):
                self.file_job.deleteLater();self.file_job=None;self.update_format_state()
                try:
                    if error:raise OSError(error)
                    for value in rows:
                        row=self.store.finish_attachment(owner,value);label=row['name'].replace('[','').replace(']','');prefix='!' if row['mime'].startswith('image/') else '';self.insert_markdown(f'\n{prefix}[{label}]({row["relative"]})\n')
                    if not self.save():raise OSError('附件已经复制，正文尚未保存，请勿退出')
                    self.refresh_files()
                    if remove_sources:
                        for path in paths:Path(path).unlink(missing_ok=True)
                except Exception as e:self.show_status('附件未保存：'+str(e))
            self.file_job.finished.connect(done);self.file_job.start(work)
        except Exception as error:QMessageBox.warning(self,'附件未保存',str(error))

    def paste_image(self,image):
        if not self.can_mutate():return
        path=self.store.root/'recordings'/('图片-'+__import__('uuid').uuid4().hex+'.png')
        if image.save(str(path),'PNG'):
            self.add_files([str(path)],remove_sources=True)

    def recorded(self,path):
        try:
            if not self.can_mutate():raise PermissionError('笔记只读，原始录音已保留')
            owner=self.recording_identity
            if owner is None:raise ValueError('找不到录音所属笔记')
            if self.identity!=owner:raise ValueError('录音所属笔记已改变，原文件已保留')
            self.add_files([path],remove_sources=True);self.media.status.setText('录音已停止 · 麦克风未使用')
        except Exception as error:self.media.status.setText('录音保留在 recordings 文件夹，未能加入笔记：'+str(error))

    def prepare_recording(self):
        if not self.can_mutate():
            self.media.record_panel.hide();self.media.update_visibility();raise PermissionError('回收站笔记只读，请先恢复笔记。')
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
        if self.file_job:self.show_status('附件正在保存，请稍候');return False
        from PySide6.QtMultimedia import QMediaRecorder
        self.media.finish()
        if self.file_job:self.show_status('附件正在保存，请稍候');return False
        if self.media.recorder.recorderState()!=QMediaRecorder.StoppedState or self.media.pending_finalize:
            self.show_status('正在结束录音并保存，请稍候。');return False
        if not self.save():return False
        self.show_status('');return True
