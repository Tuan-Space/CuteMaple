"""A quiet, readable visual system for the personal journal."""
from PySide6.QtCore import Qt,QDate,Signal,QSize,QRectF,QPointF
from PySide6.QtGui import QColor,QFont,QFontDatabase,QFontMetricsF,QPainter,QPen,QPolygonF,QTextDocument,QTextOption,QAbstractTextDocumentLayout,QPalette
from PySide6.QtWidgets import QApplication,QWidget,QPushButton,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,QCheckBox,QSizePolicy,QButtonGroup,QStyledItemDelegate,QStyle,QDateEdit,QComboBox,QStyleOptionComboBox,QListWidget

TYPE_NAMES={'todo':'待办','schedule':'日程','anniversary':'纪念日','note':'笔记','habit':'健康'}
TYPE_ORDER=tuple(TYPE_NAMES)
ITEM_PRESENTATION_ROLE=int(Qt.UserRole)+1


def load_journal_fonts():
    """Share the packaged display face while retaining a readable body face."""
    import os
    from pathlib import Path
    app=QApplication.instance()
    if app is None:return {'body':'Microsoft YaHei UI','display':'Microsoft YaHei UI'}
    if not hasattr(app,'_journal_translation'):
        from PySide6.QtCore import QTranslator,QLibraryInfo
        translator=QTranslator(app)
        if translator.load('qtbase_zh_CN',QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(translator);app._journal_translation=translator
    cached=getattr(app,'_journal_fonts',None)
    if cached:return cached
    families=QFontDatabase.families()
    if 'Microsoft YaHei UI' not in families:
        # Offscreen verification must use the same Windows body font as the app.
        font_root=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
        for filename in ('msyh.ttc','msyhbd.ttc'):
            path=font_root/filename
            if path.is_file():QFontDatabase.addApplicationFont(str(path))
        families=QFontDatabase.families()
    body=next((name for name in ('Microsoft YaHei UI','Microsoft YaHei','Noto Sans CJK SC','Segoe UI') if name in families),QFontDatabase.systemFont(QFontDatabase.GeneralFont).family())
    from pet_core import resource_root
    font_id=QFontDatabase.addApplicationFont(str(resource_root()/'assets'/'fonts'/'雅痞-简+常规体.otf'))
    display=QFontDatabase.applicationFontFamilies(font_id) if font_id>=0 else []
    result={'body':body,'display':display[0] if display else body}
    if app is not None:app._journal_fonts=result
    return result


def journal_font(role='body',point_size=None):
    fonts=load_journal_fonts();display=role in ('display','title','section','nav','empty')
    font=QFont(fonts['display' if display else 'body'])
    font.setPointSizeF(point_size if point_size is not None else 11 if display else 10)
    font.setHintingPreference(QFont.PreferVerticalHinting)
    font.setStyleStrategy(QFont.PreferAntialias|QFont.PreferQuality)
    return font

def palette(dark=False):
    if dark:
        return dict(background='#202020',card='#292929',inset='#303030',border='#505050',line='#414141',subtle='#979797',secondary='#d0d0d0',text='#f0f0f0',muted='#b2b2b2',accent='#91bdff',button='#363636',hover='#424242',pressed='#4c4c4c',disabled='#303030',focus='#91bdff',status='#303030',selection='#404040',selected='#ffffff',soft='#343434',todo='#ffb369',schedule='#81b3ff',anniversary='#c2a0ff',note='#f48cba',habit='#89d098',outside='#858585',weekend='#c2c2c2',maple='#f3ac69',success='#89d098',warning='#ffbe78',danger='#ff9f9f')
    return dict(background='#f3f3f3',card='#ffffff',inset='#f6f6f6',border='#cecece',line='#e5e5e5',subtle='#777777',secondary='#525252',text='#252525',muted='#696969',accent='#326cc1',button='#eeeeee',hover='#e6e6e6',pressed='#dcdcdc',disabled='#f0f0f0',focus='#326cc1',status='#f5f5f5',selection='#e8e8e8',selected='#202020',soft='#f1f1f1',todo='#bd6518',schedule='#326ed1',anniversary='#8551bd',note='#bf4677',habit='#33854a',outside='#929292',weekend='#555555',maple='#c87531',success='#33854a',warning='#986015',danger='#b54b50')

def style(c):
    fonts=load_journal_fonts()
    return f"""
QWidget#journalWindow, QWidget#journalBubble, QWidget#journalPage, QWidget#journalViewport, QDialog {{ background:{c['background']}; color:{c['text']}; }}
QWidget {{ font-family:'{fonts['body']}'; font-size:10pt; }}
QLabel,QCheckBox {{ color:{c['text']}; background:transparent; }}
QLabel#title {{ font-family:'{fonts['display']}'; font-size:22pt; font-weight:400; }}
QLabel#section,QLabel#emptyTitle {{ font-family:'{fonts['display']}'; font-size:12pt; font-weight:400; }}
QLabel#cardTitle {{ color:{c['text']}; font-weight:600; }}
QLabel#pageDescription {{ color:{c['secondary']}; font-size:9pt; }}
QLabel#metricValue {{ color:{c['text']}; font-size:23pt; font-weight:600; }}
QWidget#journalSidebar {{ border-right:1px solid {c['line']}; }}
QProgressBar#habitProgress {{ background:{c['inset']}; border:0; border-radius:2px; }}
QProgressBar#habitProgress::chunk {{ background:{c['habit']}; border:0; border-radius:2px; }}
QLabel#metricName,QLabel#muted {{ color:{c['muted']}; font-size:9pt; }}
QLabel#status {{ color:{c['muted']}; padding:8px; background:{c['inset']}; border-radius:8px; }}
QWidget#surface,QWidget#sectionCard {{ background:{c['card']}; border:1px solid {c['line']}; border-radius:12px; }}
QWidget#formatToolbar {{ background:{c['inset']}; border:1px solid {c['line']}; border-radius:8px; }}
QWidget#mediaPanel {{ background:{c['inset']}; border:1px solid {c['border']}; border-radius:10px; }}
QLabel#mediaStatus,QLabel#noteFormatHint {{ color:{c['muted']}; font-size:9pt; }}
QPushButton,QToolButton {{ background:{c['button']}; color:{c['text']}; border:1px solid transparent; border-radius:7px; padding:7px 12px; min-height:18px; }}
QPushButton:hover,QToolButton:hover {{ background:{c['hover']}; }}
QPushButton:pressed {{ background:{c['pressed']}; }}
QPushButton:checked,QToolButton:checked {{ background:{c['selection']}; color:{c['selected']}; border-color:{c['border']}; }}
QPushButton:disabled,QToolButton:disabled {{ color:{c['muted']}; background:{c['disabled']}; }}
QPushButton:focus,QToolButton:focus {{ border-color:{c['focus']}; }}
QPushButton#primary {{ background:{c['accent']}; color:{c['background']}; font-weight:600; }}
QPushButton#quiet {{ background:transparent; color:{c['muted']}; }}
QPushButton#quiet:hover {{ background:{c['hover']}; color:{c['text']}; }}
QPushButton#quiet:checked {{ background:{c['selection']}; color:{c['selected']}; font-weight:600; }}
QPushButton#secondary,QPushButton#danger {{ background:{c['button']}; border:1px solid {c['border']}; }}
QPushButton#danger {{ color:{c['danger']}; }}
QPushButton#secondary:hover,QPushButton#danger:hover {{ background:{c['hover']}; border-color:{c['accent']}; }}
QPushButton#secondary:focus,QPushButton#danger:focus {{ border-color:{c['focus']}; }}
QPushButton#secondary:disabled,QPushButton#danger:disabled {{ color:{c['subtle']}; background:{c['disabled']}; border-color:{c['line']}; }}
QLabel#error {{ color:{c['danger']}; background:{c['inset']}; padding:8px; border-radius:6px; }}
QLabel#validationError {{ color:{c['danger']}; font-size:10pt; }}
QPushButton#nav {{ font-family:'{fonts['display']}'; font-size:12pt; background:transparent; text-align:left; padding:10px 14px; color:{c['muted']}; }}
QPushButton#nav:checked {{ background:{c['selection']}; color:{c['selected']}; font-weight:600; }}
QPushButton#filter {{ background:transparent; color:{c['muted']}; padding:6px 12px; }}
QPushButton#filter:checked {{ background:{c['selection']}; color:{c['selected']}; font-weight:600; }}
QPushButton#calendarDay {{ padding:0; min-width:36px; min-height:58px; border:0; }}
QSpinBox::up-button,QTimeEdit::up-button,QDateTimeEdit::up-button {{ width:18px; border:0; background:transparent; }}
QSpinBox::down-button,QTimeEdit::down-button,QDateTimeEdit::down-button {{ width:18px; border:0; background:transparent; }}
QLineEdit,QComboBox,QSpinBox,QTimeEdit,QDateEdit,QDateTimeEdit {{ background:{c['inset']}; color:{c['text']}; border:1px solid {c['line']}; border-radius:7px; padding:7px 10px; min-height:19px; selection-background-color:{c['selection']}; selection-color:{c['selected']}; }}
QLineEdit:hover,QComboBox:hover,QSpinBox:hover,QDateEdit:hover,QTimeEdit:hover {{ border-color:{c['line']}; }}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QTimeEdit:focus,QDateEdit:focus,QDateTimeEdit:focus {{ border-color:{c['focus']}; }}
QLineEdit#noteTitle {{ background:transparent; border:0; font-size:15pt; font-weight:600; padding:6px 0; }}
QComboBox {{ padding-right:24px; }}
QComboBox::drop-down {{ border:0; width:22px; }}
QComboBox::down-arrow {{ image:none; }}
QDateEdit::drop-down {{ border:0; width:22px; }}
QDateEdit::down-arrow {{ image:none; }}
QComboBox QAbstractItemView {{ background:{c['card']}; color:{c['text']}; border:1px solid {c['border']}; selection-background-color:{c['selection']}; selection-color:{c['selected']}; padding:6px; }}
QPlainTextEdit,QTextBrowser,QTextEdit {{ background:{c['card']}; color:{c['text']}; border:1px solid {c['line']}; border-radius:8px; padding:12px; selection-background-color:{c['selection']}; selection-color:{c['selected']}; }}
QPushButton#formatButton,QToolButton#formatButton {{ padding:4px 7px; min-height:20px; border-radius:5px; background:transparent; }}
QPushButton#formatButton:hover,QToolButton#formatButton:hover {{ background:{c['hover']}; }}
QPushButton#formatButton:checked,QToolButton#formatButton:checked {{ background:{c['selection']}; color:{c['selected']}; border-color:{c['border']}; }}
QMenu {{ background:{c['card']}; color:{c['text']}; border:1px solid {c['border']}; padding:5px; }}
QMenu::item {{ padding:8px 20px; border-radius:4px; }}
QMenu::item:selected {{ background:{c['selection']}; color:{c['selected']}; }}
QListWidget,QTableWidget {{ background:{c['card']}; color:{c['text']}; border:1px solid {c['line']}; border-radius:10px; outline:0; }}
QTableWidget {{ alternate-background-color:{c['inset']}; gridline-color:{c['line']}; selection-background-color:{c['selection']}; selection-color:{c['selected']}; }}
QTableWidget::item {{ padding:6px 8px; border-bottom:1px solid {c['line']}; }}
QListWidget::item {{ padding:10px 12px; margin:0; border-bottom:1px solid {c['line']}; }}
QListWidget#attachmentList::item {{ padding:8px 10px; }}
QListWidget::item:hover {{ background:{c['inset']}; }}
QListWidget::item:selected {{ background:{c['selection']}; color:{c['selected']}; }}
QHeaderView::section {{ background:{c['inset']}; color:{c['muted']}; border:0; padding:8px; }}
QCheckBox {{ spacing:8px; }}
QCheckBox::indicator {{ width:16px; height:16px; border:1px solid {c['muted']}; border-radius:4px; background:{c['card']}; }}
QCheckBox::indicator:checked {{ background:{c['accent']}; border-color:{c['accent']}; }}
QScrollArea {{ border:0; background:transparent; }}
QScrollBar:vertical {{ width:7px; background:transparent; margin:2px; }}
QScrollBar::handle:vertical {{ background:{c['border']}; border-radius:3px; min-height:28px; }}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{ background:transparent; }}
QScrollBar:horizontal {{ height:7px; background:transparent; margin:2px; }}
QScrollBar::handle:horizontal {{ background:{c['border']}; border-radius:3px; min-width:28px; }}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {{ width:0; }}
QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {{ background:transparent; }}
QAbstractScrollArea::corner {{ background:{c['background']}; border:0; }}
QSplitter::handle {{ background:{c['background']}; width:14px; }}
QToolTip {{ background:{c['card']}; color:{c['text']}; border:1px solid {c['border']}; padding:5px; }}
"""

def colors(widget):return getattr(widget.window(),'_theme',palette())

class Segments(QWidget):
    currentIndexChanged=Signal(int)
    def __init__(self,parent=None):
        super().__init__(parent);self.buttons=[];self.index=0;self.group=QButtonGroup(self);self.group.setExclusive(True);self.row=QHBoxLayout(self);self.row.setContentsMargins(0,0,0,0);self.row.setSpacing(3)
    def addItems(self,items):
        for text in items:
            n=len(self.buttons);b=QPushButton(text);b.setObjectName('filter');b.setCheckable(True);b.setCursor(Qt.PointingHandCursor);self.group.addButton(b,n);self.row.addWidget(b);self.buttons.append(b);b.clicked.connect(lambda _,i=n:self.setCurrentIndex(i))
        self.buttons[self.index].setChecked(True)
    def currentIndex(self):return self.index
    def setCurrentIndex(self,n):
        changed=n!=self.index;self.index=n;self.buttons[n].setChecked(True)
        if changed:self.currentIndexChanged.emit(n)

class JournalDateEdit(QDateEdit):
    def paintEvent(self,event):
        super().paintEvent(event);c=colors(self);p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setPen(Qt.NoPen);p.setBrush(QColor(c['text']));x,y=self.width()-12,self.height()/2;p.drawPolygon(QPolygonF([QPointF(x-4,y-2),QPointF(x+4,y-2),QPointF(x,y+3)]))

class WrappedItem(QStyledItemDelegate):
    """Readable journal rows; presentation metadata never replaces business data."""
    def editorEvent(self,event,model,option,index):
        # Qt's default delegate has an invisible checkbox hit area on the left.
        # The list owns the single visible checkbox on the right and its scope.
        if index.data(Qt.CheckStateRole) is not None:return False
        return super().editorEvent(event,model,option,index)
    def presentation(self,index):
        data=index.data(ITEM_PRESENTATION_ROLE)
        if isinstance(data,dict):return data
        lines=str(index.data(Qt.DisplayRole) or '').split('\n',1)
        return {'title':lines[0],'subtitle':lines[1] if len(lines)>1 else '',
                'empty':not bool(index.flags() & Qt.ItemIsEnabled)}
    def document(self,index,width):
        from html import escape
        data=self.presentation(index);c=colors(self.parent());empty=data.get('empty',False)
        doc=QTextDocument();doc.setDefaultFont(journal_font('empty' if empty else 'body',12 if empty else 10));doc.setDocumentMargin(0)
        option=doc.defaultTextOption();option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere);doc.setDefaultTextOption(option)
        title=escape(str(data.get('title',''))).replace('\n','<br>')
        subtitle=escape(str(data.get('subtitle',''))).replace('\n','<br>') if not self.inline_subtitle(index,width) else ''
        body=f'<p style="margin:0; font-weight:{400 if empty else 600};">{title}</p>'
        if data.get('emphasis'):
            emphasis=f'<span style="font-size:13pt; font-weight:600; color:{c.get(data.get("emphasis_tone"),c["text"])};">{escape(data["emphasis"])}</span>'
            if width>=350:
                reserved=int(QFontMetricsF(journal_font('body',13)).horizontalAdvance(data['emphasis'])+28)
                body=f'<table width="100%" cellspacing="0" cellpadding="0"><tr><td valign="middle"><b>{title}</b></td><td width="{reserved}" align="right" valign="middle">{emphasis}</td></tr></table>'
            else:body+=f'<p style="margin:7px 0 0;">{emphasis}</p>'
        if data.get('summary'):
            # Keep previews to two actual wrapped lines, independently of metadata.
            from PySide6.QtGui import QTextLayout
            text=str(data['summary']);layout=QTextLayout(text,journal_font('body',9.5));layout.beginLayout();end=0
            for _ in range(2):
                line=layout.createLine()
                if not line.isValid():break
                line.setLineWidth(max(24,width-32));end=line.textStart()+line.textLength()
            layout.endLayout();shown=text[:end]
            if end<len(text):shown=shown.rstrip()[:-1]+'…'
            body+=f'<p style="margin:7px 0 0; font-size:9.5pt; color:{c["secondary"]};">{escape(shown)}</p>'
        if subtitle:body+=f'<p style="margin:5px 0 0; font-family:&quot;{load_journal_fonts()["body"]}&quot;; font-size:9pt; color:{c["muted"]};">{subtitle}</p>'
        if data.get('metadata') and not (data.get('emphasis') and self.inline_subtitle(index,width)):body+=f'<p style="margin:10px 0 0; font-size:8.5pt; color:{c["muted"]};">{escape(data["metadata"])}</p>'
        doc.setHtml(body);doc.setTextWidth(max(24,width-32));return doc
    def badges(self,index):
        data=self.presentation(index);c=colors(self.parent());badges=[]
        category=data.get('category','')
        if category in TYPE_NAMES:badges.append((TYPE_NAMES[category],c[category]))
        if data.get('status'):badges.append((str(data['status']),c.get(data.get('status_tone','muted'),c['muted'])))
        if data.get('pinned'):badges.append(('置顶',c['secondary']))
        return badges
    def badge_layout(self,index,width):
        metrics=QFontMetricsF(journal_font('body',8.5));x=y=0;result=[];available=max(24,width-32)
        for text,color in self.badges(index):
            text=metrics.elidedText(text,Qt.ElideRight,max(8,int(available-16)))
            length=min(available,metrics.horizontalAdvance(text)+(30 if text=='置顶' else 16))
            if x and x+length>available:x=0;y+=25
            result.append((text,color,QRectF(x,y,length,21)));x+=length+6
        return result,(y+21 if result else 0)
    def inline_subtitle(self,index,width):
        data=self.presentation(index)
        if data.get('summary') or (data.get('metadata') and not data.get('emphasis')):return None
        subtitle=str(data.get('metadata' if data.get('emphasis') else 'subtitle',''))
        if not subtitle or '\n' in subtitle:return None
        badges,height=self.badge_layout(index,width)
        if not badges or height>21:return None
        left=badges[-1][2].right()+10;available=max(24,width-32)-left
        if QFontMetricsF(journal_font('body',9)).horizontalAdvance(subtitle)<=available:
            return subtitle,left,available
        return None
    def sizeHint(self,option,index):
        width=self.parent().viewport().width();content_width=width-(36 if index.data(Qt.CheckStateRole) is not None else 0);doc=self.document(index,content_width);_,badges_height=self.badge_layout(index,content_width)
        height=doc.size().height()+28+(badges_height+8 if badges_height else 0)
        return QSize(width,max(64,int(height)))
    def paint(self,painter,option,index):
        c=colors(self.parent());selected=bool(option.state & QStyle.State_Selected)
        painter.save();painter.setClipRect(option.rect);painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen);painter.setBrush(QColor(c['selection'] if selected else c['inset'] if option.state & QStyle.State_MouseOver else c['card']))
        painter.drawRoundedRect(QRectF(option.rect).adjusted(3,2,-3,-2),7,7)
        if not self.presentation(index).get('empty'):
            painter.setPen(QPen(QColor(c['line']),1));painter.drawLine(QPointF(option.rect.left()+16,option.rect.bottom()),QPointF(option.rect.right()-16,option.rect.bottom()))
        if option.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor(c['focus']),1));painter.setBrush(Qt.NoBrush);painter.drawRoundedRect(QRectF(option.rect).adjusted(3,2,-3,-2),7,7)
        check=index.data(Qt.CheckStateRole);content_width=option.rect.width()-(36 if check is not None else 0)
        if check is not None:
            rect=self.check_rect(option.rect);on=check in (Qt.Checked,Qt.Checked.value)
            painter.setBrush(QColor(c['accent'] if on else c['card']));painter.setPen(QPen(QColor(c['accent'] if on else c['border']),1.5));painter.drawRoundedRect(rect,4,4)
            if on:
                painter.setPen(QPen(QColor(c['card']),2,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin));painter.drawPolyline(QPolygonF([QPointF(rect.left()+4,rect.top()+9),QPointF(rect.left()+8,rect.top()+13),QPointF(rect.left()+14,rect.top()+5)]))
        painter.translate(option.rect.x()+16,option.rect.y()+14)
        doc=self.document(index,content_width);ctx=QAbstractTextDocumentLayout.PaintContext();ctx.palette.setColor(QPalette.Text,QColor(c['selected'] if selected else c['text']));doc.documentLayout().draw(painter,ctx)
        badges,_=self.badge_layout(index,content_width);painter.translate(0,doc.size().height()+8);painter.setFont(journal_font('body',8.5))
        for text,color,rect in badges:
            fill=QColor(color);fill.setAlpha(28 if QColor(c['background']).lightness()<128 else 18)
            painter.setPen(Qt.NoPen);painter.setBrush(fill);painter.drawRoundedRect(rect,5,5)
            painter.setPen(QColor(color))
            if text=='置顶':
                x,y=rect.left()+10,rect.top()+5;painter.setPen(QPen(QColor(color),1.2));painter.drawPolyline(QPolygonF([QPointF(x-3,y),QPointF(x+3,y),QPointF(x+2,y+5),QPointF(x+4,y+7),QPointF(x-4,y+7),QPointF(x-2,y+5),QPointF(x-3,y)]));painter.drawLine(QPointF(x,y+7),QPointF(x,y+12));painter.drawText(rect.adjusted(14,0,0,0),Qt.AlignCenter,text)
            else:painter.drawText(rect,Qt.AlignCenter,text)
        subtitle=self.inline_subtitle(index,content_width)
        if subtitle:
            text,left,width=subtitle;painter.setFont(journal_font('body',9));painter.setPen(QColor(c['muted']))
            painter.drawText(QRectF(left,0,width,21),Qt.AlignVCenter|Qt.AlignLeft,text)
        painter.restore()

    @staticmethod
    def check_rect(rect):return QRectF(rect.right()-30,rect.center().y()-9,18,18)


class CheckableJournalList(QListWidget):
    """The right checkbox changes the batch scope without changing the preview."""
    checkChanged=Signal(object,bool)
    def toggle_item(self,item):
        if item is None or item.data(Qt.CheckStateRole) is None:return
        checked=item.checkState()!=Qt.Checked;item.setCheckState(Qt.Checked if checked else Qt.Unchecked);self.checkChanged.emit(item,checked)
    def checkbox_at(self,pos):
        item=self.itemAt(pos)
        return item if item and item.data(Qt.CheckStateRole) is not None and WrappedItem.check_rect(self.visualItemRect(item)).adjusted(-7,-9,7,9).contains(pos) else None
    def mousePressEvent(self,event):
        self._check_press=self.checkbox_at(event.position().toPoint()) if event.button()==Qt.LeftButton else None
        if self._check_press is not None:event.accept();return
        super().mousePressEvent(event)
    def mouseReleaseEvent(self,event):
        pressed=getattr(self,'_check_press',None);self._check_press=None
        if pressed is not None:
            if self.checkbox_at(event.position().toPoint()) is pressed:self.toggle_item(pressed)
            event.accept();return
        super().mouseReleaseEvent(event)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Space and self.currentItem() and self.currentItem().data(Qt.CheckStateRole) is not None:
            self.toggle_item(self.currentItem());event.accept();return
        super().keyPressEvent(event)


class BatchCheckBox(QCheckBox):
    def paintEvent(self,event):
        super().paintEvent(event)
        if self.checkState()==Qt.Unchecked:return
        from PySide6.QtWidgets import QStyleOptionButton
        option=QStyleOptionButton();self.initStyleOption(option);rect=self.style().subElementRect(QStyle.SE_CheckBoxIndicator,option,self)
        c=colors(self);p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setPen(QPen(QColor(c['card'] if self.checkState()==Qt.Checked else c['text']),2,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin));x,y=rect.center().x(),rect.center().y()
        if self.checkState()==Qt.PartiallyChecked:p.drawLine(QPointF(x-4,y),QPointF(x+4,y))
        else:p.drawPolyline(QPolygonF([QPointF(x-4,y),QPointF(x-1,y+3),QPointF(x+4,y-3)]))


class TrashBar(QWidget):
    selectAllRequested=Signal(bool)
    restoreRequested=Signal()
    deleteRequested=Signal()
    attentionRequested=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName('surface');self.setAttribute(Qt.WA_StyledBackground)
        box=QVBoxLayout(self);box.setContentsMargins(12,10,12,10);box.setSpacing(8)
        row=QHBoxLayout();self.all=BatchCheckBox();self.all.clicked.connect(self.selectAllRequested);row.addWidget(self.all);row.addStretch();self.count=QLabel('已选 0 项');self.count.setObjectName('muted');row.addWidget(self.count);box.addLayout(row)
        row=QHBoxLayout();self.restore=QPushButton('恢复选中');self.delete=QPushButton('永久删除选中');self.restore.setObjectName('secondary');self.delete.setObjectName('danger')
        for button,signal in ((self.restore,self.restoreRequested),(self.delete,self.deleteRequested)):
            button.setMinimumWidth(126);button.setCursor(Qt.PointingHandCursor);button.clicked.connect(signal);row.addWidget(button)
        row.addStretch();box.addLayout(row)
        self.result=QLabel();self.result.setWordWrap(True);self.result.setObjectName('pageDescription');self.result.hide();box.addWidget(self.result)
        self.attention=QPushButton('查看待处理项目');self.attention.setObjectName('secondary');self.attention.clicked.connect(self.attentionRequested);self.attention.hide();box.addWidget(self.attention,0,Qt.AlignLeft)
        self.update_selection(0,0)
    def update_selection(self,total,selected):
        self.all.setText(f'全选当前结果（共 {total} 项）');self.all.setEnabled(total>0);self.all.setTristate(0<selected<total);self.all.setCheckState(Qt.Checked if total and selected==total else Qt.PartiallyChecked if selected else Qt.Unchecked)
        self.count.setText(f'已选 {selected} 项');self.restore.setEnabled(selected>0);self.delete.setEnabled(selected>0)
    def feedback(self,text='',pending=False):
        self.result.setText(text);self.result.setVisible(bool(text));self.attention.setVisible(pending)

class Toggle(QCheckBox):
    def __init__(self,text='',parent=None):
        super().__init__(text,parent);self.setCursor(Qt.PointingHandCursor);self.setMinimumHeight(30)
    def sizeHint(self):return QSize(42+(self.fontMetrics().horizontalAdvance(self.text())+12 if self.text() else 0),30)
    def hitButton(self,pos):return self.rect().contains(pos)
    def paintEvent(self,event):
        c=colors(self);p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen);p.setBrush(QColor(c['accent'] if self.isChecked() else c['border']))
        p.drawRoundedRect(QRectF(1,5,36,20),10,10)
        p.setBrush(QColor(c['background'] if self.isChecked() else c['card']));p.drawEllipse(QRectF(19 if self.isChecked() else 4,8,14,14))
        p.setPen(QColor(c['text']));p.drawText(self.rect().adjusted(49,0,0,0),Qt.AlignVCenter,self.text())
        if self.hasFocus():p.setPen(QPen(QColor(c['focus']),1));p.setBrush(Qt.NoBrush);p.drawRoundedRect(QRectF(0,3,39,24),11,11)

class CalendarDay(QPushButton):
    def __init__(self,calendar):
        super().__init__();self.calendar=calendar;self.date=QDate();self.setObjectName('calendarDay');self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding);self.setMinimumSize(54,62);self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(lambda:self.calendar.setSelectedDate(self.date));self.setContextMenuPolicy(Qt.CustomContextMenu);self.customContextMenuRequested.connect(lambda pos:self.calendar.contextRequested.emit(self.date,self.mapToGlobal(pos)))
    def marker_rects(self):
        present=self.calendar.marks.get(self.date.toString('yyyy-MM-dd'),())
        kinds=[kind for kind in TYPE_ORDER if kind in present]
        width=len(kinds)*7+max(0,len(kinds)-1)*3
        start=(self.width()-width)/2
        return [(kind,QRectF(start+i*10,self.height()-14,7,7)) for i,kind in enumerate(kinds)]
    def paintEvent(self,event):
        c=colors(self);p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);selected=self.date==self.calendar.selectedDate();current=self.date.month()==self.calendar.monthShown() and self.date.year()==self.calendar.yearShown()
        label,holiday,detail=self.calendar.holidays.info(self.date) if self.calendar.holidays else ('',None,'')
        p.setPen(QPen(QColor(c['focus'] if selected else c['line']),1));p.setBrush(QColor(c['selection'] if selected else c['inset'] if self.underMouse() or holiday and holiday['isOffDay'] else c['card']));rect=QRectF(self.rect()).adjusted(2,2,-2,-2);p.drawRoundedRect(rect,9,9)
        ink=c['selected'] if selected else c['outside'] if not current else c['weekend'] if self.date.dayOfWeek()>5 else c['text'];p.setPen(QColor(ink));font=journal_font('body',13);font.setBold(selected or self.date==QDate.currentDate());p.setFont(font);p.drawText(QRectF(0,4,self.width(),24),Qt.AlignCenter,str(self.date.day()))
        font.setPointSize(8);font.setBold(False);p.setFont(font);p.setPen(QColor(c['selected'] if selected else c['muted'] if current else c['outside']));p.drawText(QRectF(1,29,self.width()-2,17),Qt.AlignCenter,label)
        if holiday:
            badge=QRectF(self.width()-17,3,13,13);p.setPen(Qt.NoPen);p.setBrush(QColor(c['weekend'] if holiday['isOffDay'] else c['maple']));p.drawRoundedRect(badge,4,4);font.setPointSize(6);p.setFont(font);p.setPen(QColor(c['card']));p.drawText(badge,Qt.AlignCenter,'休' if holiday['isOffDay'] else '班')
        for kind,marker in self.marker_rects():
            color=QColor(c[kind]);color.setAlpha(255 if current else 100);p.setBrush(color);p.setPen(QPen(QColor(c['card']),1));p.drawEllipse(marker)
        if self.date==QDate.currentDate() and not selected or self.hasFocus():p.setPen(QPen(QColor(c['focus']),1.5));p.setBrush(Qt.NoBrush);p.drawRoundedRect(rect,9,9)

class MonthCalendar(QWidget):
    currentPageChanged=Signal(int,int)
    selectionChanged=Signal()
    contextRequested=Signal(QDate,object)
    def __init__(self,parent=None):
        from monitor_ui import ThemedComboBox,ThemedSpinBox
        class MonthPicker(ThemedComboBox):
            def sizeHint(self):
                size=super().sizeHint();option=QStyleOptionComboBox();self.initStyleOption(option);option.rect.setSize(size)
                field=self.style().subControlRect(QStyle.CC_ComboBox,option,QStyle.SC_ComboBoxEditField,self)
                required=max((self.fontMetrics().horizontalAdvance(self.itemText(i)) for i in range(self.count())),default=0)
                size.setWidth(size.width()+max(0,required+8-field.width()));return size
            def minimumSizeHint(self):return self.sizeHint()
        super().__init__(parent);self._selected=QDate.currentDate();self._month=self._selected;self.marks={};self.holidays=None;self.setObjectName('surface');self.setAttribute(Qt.WA_StyledBackground)
        outer=QVBoxLayout(self);outer.setContentsMargins(12,14,12,12);outer.setSpacing(10);header=QHBoxLayout();header.setSpacing(4)
        self.year=ThemedSpinBox();self.year.setRange(1900,2199);self.year.setSuffix(' 年');self.year.setFixedWidth(108);self.month=MonthPicker();self.month.addItems([str(n)+' 月' for n in range(1,13)]);self.month.setSizeAdjustPolicy(QComboBox.AdjustToContents);self.month.setSizePolicy(QSizePolicy.Minimum,QSizePolicy.Fixed);header.addWidget(self.year);header.addWidget(self.month)
        for text,action in [('‹',lambda:self.shift(-1)),('›',lambda:self.shift(1))]:
            b=QPushButton(text);b.setObjectName('quiet');b.setFixedWidth(34);b.clicked.connect(action);header.addWidget(b)
        header.addStretch();self.today=QPushButton('今天');self.today.setToolTip('回到今天');self.today.setObjectName('secondary');self.today.setCursor(Qt.PointingHandCursor);self.today.clicked.connect(lambda:self.setSelectedDate(QDate.currentDate()));header.addWidget(self.today);outer.addLayout(header)
        self.year.valueChanged.connect(lambda y:self.setCurrentPage(y,self.monthShown()));self.month.currentIndexChanged.connect(lambda m:self.setCurrentPage(self.yearShown(),m+1));grid=QGridLayout();grid.setSpacing(2);self.cells=[]
        for col,name in enumerate(['一','二','三','四','五','六','日']):
            label=QLabel(name);label.setObjectName('muted');label.setAlignment(Qt.AlignCenter);grid.addWidget(label,0,col);grid.setColumnStretch(col,1)
        for n in range(42):
            b=CalendarDay(self);self.cells.append(b);grid.addWidget(b,n//7+1,n%7)
        for n in range(1,7):grid.setRowStretch(n,1);grid.setRowMinimumHeight(n,62)
        outer.addLayout(grid,1);self.repaint_month()
    def yearShown(self):return self._month.year()
    def monthShown(self):return self._month.month()
    def selectedDate(self):return self._selected
    def setFirstDayOfWeek(self,*_):pass
    def setVerticalHeaderFormat(self,*_):pass
    def setCurrentPage(self,year,month):
        value=QDate(year,month,1)
        if not value.isValid() or not 1900<=year<=2199:return
        selection_changed=self._selected.year()!=year or self._selected.month()!=month
        if selection_changed:self._selected=QDate(year,month,min(self._selected.day(),value.daysInMonth()))
        self._month=value;self.repaint_month();self.currentPageChanged.emit(year,month)
        if selection_changed:self.selectionChanged.emit()
    def setSelectedDate(self,value):
        if not value.isValid():return
        changed=value.year()!=self.yearShown() or value.month()!=self.monthShown();self._selected=value
        if changed:self.setCurrentPage(value.year(),value.month())
        else:self.repaint_month()
        self.selectionChanged.emit()
    def shift(self,n):
        value=self._month.addMonths(n);self.setCurrentPage(value.year(),value.month())
    def set_marks(self,marks):self.marks=marks;self.repaint_month()
    def repaint_month(self):
        self.year.blockSignals(True);self.month.blockSignals(True);self.year.setValue(self.yearShown());self.month.setCurrentIndex(self.monthShown()-1);self.year.blockSignals(False);self.month.blockSignals(False)
        first=QDate(self.yearShown(),self.monthShown(),1);start=first.addDays(1-first.dayOfWeek())
        for n,b in enumerate(self.cells):
            b.date=start.addDays(n);names='、'.join(TYPE_NAMES[k] for k in TYPE_ORDER if k in self.marks.get(b.date.toString('yyyy-MM-dd'),()));detail=self.holidays.info(b.date)[2] if self.holidays else '';b.setAccessibleName(b.date.toString('yyyy年M月d日')+' '+names+' '+detail);b.setToolTip(' · '.join(x for x in (detail,names) if x));b.update()
