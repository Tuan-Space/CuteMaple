"""A quiet, readable visual system for the personal journal."""
from PySide6.QtCore import Qt,QDate,Signal,QSize,QRectF,QPointF
from PySide6.QtGui import QColor,QFont,QPainter,QPen,QPolygonF,QTextDocument,QTextOption,QAbstractTextDocumentLayout,QPalette
from PySide6.QtWidgets import QWidget,QPushButton,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,QCheckBox,QSizePolicy,QButtonGroup,QStyledItemDelegate,QStyle,QDateEdit

TYPE_NAMES={'todo':'待办','schedule':'日程','anniversary':'纪念日','note':'笔记','habit':'健康'}
TYPE_ORDER=tuple(TYPE_NAMES)

def palette(dark=False):
    if dark:
        return dict(background='#1d2835',card='#263443',inset='#202e3d',border='#415970',text='#eaf3fc',muted='#abbdd0',accent='#87bbff',button='#314357',hover='#3b5067',pressed='#46617e',disabled='#293746',focus='#87bbff',status='#2c435c',selection='#365c83',selected='#f1f7ff',soft='#314357',todo='#ffb46a',schedule='#54d6d1',anniversary='#d2a0ff',note='#73acff',habit='#9bd761',outside='#6c8298',weekend='#87bbff',maple='#f3ac69')
    return dict(background='#edf5ff',card='#ffffff',inset='#f2f7fd',border='#cbdcf0',text='#243e59',muted='#596f86',accent='#346fca',button='#e2edfb',hover='#d6e7fd',pressed='#c3dbfa',disabled='#ecf2f8',focus='#346fca',status='#e4efff',selection='#d5e7ff',selected='#194b8f',soft='#e8f1fd',todo='#ce6614',schedule='#008b8b',anniversary='#974ad2',note='#3478e7',habit='#4b8c20',outside='#a1afbe',weekend='#397bd8',maple='#c87531')

def style(c):
    return f"""
QWidget#journalWindow, QWidget#journalBubble, QWidget#journalPage, QWidget#journalViewport, QDialog {{ background:{c['background']}; color:{c['text']}; }}
QWidget {{ font-family:'Microsoft YaHei UI','Segoe UI'; font-size:10pt; }}
QLabel,QCheckBox {{ color:{c['text']}; background:transparent; }}
QLabel#title {{ font-size:19pt; font-weight:600; }}
QLabel#section {{ font-size:11pt; font-weight:600; }}
QLabel#metricName,QLabel#muted {{ color:{c['muted']}; font-size:9pt; }}
QLabel#status {{ color:{c['muted']}; padding:8px; background:{c['inset']}; border-radius:8px; }}
QWidget#surface {{ background:{c['card']}; border-radius:12px; }}
QPushButton,QToolButton {{ background:{c['button']}; color:{c['text']}; border:1px solid transparent; border-radius:7px; padding:7px 12px; min-height:18px; }}
QPushButton:hover,QToolButton:hover {{ background:{c['hover']}; }}
QPushButton:pressed {{ background:{c['pressed']}; }}
QPushButton:disabled {{ color:{c['muted']}; background:{c['disabled']}; }}
QPushButton:focus,QToolButton:focus {{ border-color:{c['focus']}; }}
QPushButton#primary {{ background:{c['accent']}; color:{c['background']}; font-weight:600; }}
QPushButton#quiet {{ background:transparent; color:{c['muted']}; }}
QPushButton#quiet:hover {{ background:{c['hover']}; color:{c['text']}; }}
QPushButton#quiet:checked {{ background:{c['selection']}; color:{c['selected']}; font-weight:600; }}
QPushButton#nav {{ background:transparent; text-align:left; padding:10px 14px; color:{c['muted']}; }}
QPushButton#nav:checked {{ background:{c['selection']}; color:{c['selected']}; font-weight:600; }}
QPushButton#filter {{ background:transparent; color:{c['muted']}; padding:6px 12px; }}
QPushButton#filter:checked {{ background:{c['selection']}; color:{c['selected']}; font-weight:600; }}
QPushButton#calendarDay {{ padding:0; min-width:36px; min-height:58px; border:0; }}
QSpinBox::up-button,QTimeEdit::up-button,QDateTimeEdit::up-button {{ width:18px; border:0; background:transparent; }}
QSpinBox::down-button,QTimeEdit::down-button,QDateTimeEdit::down-button {{ width:18px; border:0; background:transparent; }}
QLineEdit,QComboBox,QSpinBox,QTimeEdit,QDateEdit,QDateTimeEdit {{ background:{c['inset']}; color:{c['text']}; border:1px solid transparent; border-radius:7px; padding:7px 10px; min-height:19px; selection-background-color:{c['selection']}; selection-color:{c['selected']}; }}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QTimeEdit:focus,QDateEdit:focus,QDateTimeEdit:focus {{ border-color:{c['focus']}; }}
QLineEdit#noteTitle {{ background:{c['card']}; font-size:15pt; font-weight:600; padding:6px 0; }}
QComboBox {{ padding-right:24px; }}
QComboBox::drop-down {{ border:0; width:22px; }}
QComboBox::down-arrow {{ image:none; }}
QDateEdit::drop-down {{ border:0; width:22px; }}
QDateEdit::down-arrow {{ image:none; }}
QComboBox QAbstractItemView {{ background:{c['card']}; color:{c['text']}; border:1px solid {c['border']}; selection-background-color:{c['selection']}; selection-color:{c['selected']}; padding:6px; }}
QPlainTextEdit,QTextBrowser,QTextEdit {{ background:{c['card']}; color:{c['text']}; border:0; border-radius:8px; padding:10px; selection-background-color:{c['selection']}; selection-color:{c['selected']}; }}
QMenu {{ background:{c['card']}; color:{c['text']}; border:1px solid {c['border']}; padding:5px; }}
QMenu::item {{ padding:8px 20px; border-radius:4px; }}
QMenu::item:selected {{ background:{c['selection']}; color:{c['selected']}; }}
QListWidget,QTableWidget {{ background:{c['card']}; color:{c['text']}; border:0; border-radius:10px; outline:0; }}
QListWidget::item {{ padding:12px 14px; margin:2px 4px; border-radius:7px; }}
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

class MapleBrand(QWidget):
    def __init__(self):super().__init__();self.setMinimumHeight(38);self.setMinimumWidth(80)
    def paintEvent(self,event):
        c=colors(self);p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setPen(Qt.NoPen);p.setBrush(QColor(c['maple']))
        points=[(15,2),(18,10),(22,7),(21,14),(28,12),(24,19),(27,21),(17,24),(16,32),(14,32),(14,24),(4,21),(7,19),(2,12),(9,14),(8,7),(12,10)]
        p.drawPolygon(QPolygonF([QPointF(x,y+2) for x,y in points]));p.setPen(QColor(c['text']));font=QFont(self.font());font.setPointSize(11);font.setBold(True);p.setFont(font);p.drawText(self.rect().adjusted(35,0,0,0),Qt.AlignVCenter,'美腻枫')

class WrappedItem(QStyledItemDelegate):
    def document(self,index,width):
        doc=QTextDocument();doc.setDefaultFont(self.parent().font());doc.setDocumentMargin(0);option=doc.defaultTextOption();option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere);doc.setDefaultTextOption(option);doc.setPlainText(index.data(Qt.DisplayRole));doc.setTextWidth(max(40,width-32));return doc
    def sizeHint(self,option,index):
        width=self.parent().viewport().width();doc=self.document(index,width);return QSize(width,int(doc.size().height())+28)
    def paint(self,painter,option,index):
        c=colors(self.parent());selected=bool(option.state & QStyle.State_Selected);painter.save();painter.setRenderHint(QPainter.Antialiasing);painter.setPen(Qt.NoPen);painter.setBrush(QColor(c['selection'] if selected else c['inset'] if option.state & QStyle.State_MouseOver else c['card']));painter.drawRoundedRect(QRectF(option.rect).adjusted(2,2,-2,-2),8,8);painter.translate(option.rect.x()+16,option.rect.y()+14)
        doc=self.document(index,option.rect.width());ctx=QAbstractTextDocumentLayout.PaintContext();ctx.palette.setColor(QPalette.Text,QColor(c['selected'] if selected else c['text']));doc.documentLayout().draw(painter,ctx);painter.restore()

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
    def paintEvent(self,event):
        c=colors(self);p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);selected=self.date==self.calendar.selectedDate();current=self.date.month()==self.calendar.monthShown() and self.date.year()==self.calendar.yearShown()
        label,holiday,detail=self.calendar.holidays.info(self.date) if self.calendar.holidays else ('',None,'')
        p.setPen(Qt.NoPen);p.setBrush(QColor(c['accent'] if selected else c['inset'] if self.underMouse() or holiday and holiday['isOffDay'] else c['card']));rect=QRectF(self.rect()).adjusted(2,2,-2,-2);p.drawRoundedRect(rect,9,9)
        ink=c['background'] if selected else c['outside'] if not current else c['weekend'] if self.date.dayOfWeek()>5 else c['text'];p.setPen(QColor(ink));font=QFont(self.font());font.setPointSize(13);font.setBold(self.date==QDate.currentDate());p.setFont(font);p.drawText(QRectF(0,4,self.width(),24),Qt.AlignCenter,str(self.date.day()))
        font.setPointSize(8);font.setBold(False);p.setFont(font);p.setPen(QColor(c['background'] if selected else c['muted'] if current else c['outside']));p.drawText(QRectF(1,29,self.width()-2,17),Qt.AlignCenter,label)
        if holiday:
            badge=QRectF(self.width()-17,3,13,13);p.setPen(Qt.NoPen);p.setBrush(QColor(c['weekend'] if holiday['isOffDay'] else c['maple']));p.drawRoundedRect(badge,4,4);font.setPointSize(6);p.setFont(font);p.setPen(QColor(c['card']));p.drawText(badge,Qt.AlignCenter,'休' if holiday['isOffDay'] else '班')
        types=self.calendar.marks.get(self.date.toString('yyyy-MM-dd'),());start=self.width()/2-24
        for i,kind in enumerate(TYPE_ORDER):
            if kind not in types:continue
            color=QColor(c[kind]);color.setAlpha(255 if current else 85);p.setBrush(color);p.setPen(QPen(QColor(c['card']),1));p.drawEllipse(QRectF(start+i*10,self.height()-11,7,7))
        if self.date==QDate.currentDate() and not selected or self.hasFocus():p.setPen(QPen(QColor(c['focus']),1.5));p.setBrush(Qt.NoBrush);p.drawRoundedRect(rect,9,9)

class MonthCalendar(QWidget):
    currentPageChanged=Signal(int,int)
    selectionChanged=Signal()
    contextRequested=Signal(QDate,object)
    def __init__(self,parent=None):
        from monitor_ui import ThemedComboBox,ThemedSpinBox
        super().__init__(parent);self._selected=QDate.currentDate();self._month=self._selected;self.marks={};self.holidays=None;self.setObjectName('surface');self.setAttribute(Qt.WA_StyledBackground)
        outer=QVBoxLayout(self);outer.setContentsMargins(12,14,12,12);outer.setSpacing(10);header=QHBoxLayout();header.setSpacing(4)
        self.year=ThemedSpinBox();self.year.setRange(1900,2199);self.year.setSuffix(' 年');self.year.setFixedWidth(108);self.month=ThemedComboBox();self.month.addItems([str(n)+' 月' for n in range(1,13)]);self.month.setFixedWidth(85);header.addWidget(self.year);header.addWidget(self.month)
        for text,action in [('‹',lambda:self.shift(-1)),('›',lambda:self.shift(1))]:
            b=QPushButton(text);b.setObjectName('quiet');b.setFixedWidth(34);b.clicked.connect(action);header.addWidget(b)
        header.addStretch();self.today=QPushButton('今天');self.today.setToolTip('回到今天');self.today.setObjectName('quiet');self.today.clicked.connect(lambda:self.setSelectedDate(QDate.currentDate()));header.addWidget(self.today);outer.addLayout(header)
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
        self._month=value;self.repaint_month();self.currentPageChanged.emit(year,month)
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
