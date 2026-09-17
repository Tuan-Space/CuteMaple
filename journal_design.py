"""A quiet, readable visual system for the personal journal."""
from PySide6.QtCore import Qt,QDate,Signal,QSize,QRectF
from PySide6.QtGui import QColor,QFont,QPainter,QPen
from PySide6.QtWidgets import QWidget,QPushButton,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,QCheckBox,QSizePolicy

TYPE_NAMES={'todo':'待办','schedule':'日程','anniversary':'纪念日','note':'笔记','habit':'健康'}
TYPE_ORDER=tuple(TYPE_NAMES)

def palette(dark=False):
    if dark:
        return dict(background='#202326',card='#292d31',inset='#25292d',border='#454b50',text='#edf0f2',muted='#a6aeb6',accent='#e0bc83',button='#343a40',hover='#3c4248',pressed='#454c53',disabled='#292d31',focus='#e0bc83',status='#353128',selection='#454036',selected='#f3dab0',soft='#373d42',todo='#e5ab6e',schedule='#64c4c5',anniversary='#b59cde',note='#7eb7e8',habit='#8fc69b')
    return dict(background='#f7f8fa',card='#ffffff',inset='#f0f2f5',border='#d8dee5',text='#28313b',muted='#65717f',accent='#8d602a',button='#eceff3',hover='#e5e9ef',pressed='#dbe1e8',disabled='#eff1f4',focus='#9a713e',status='#f2eadc',selection='#f2eadc',selected='#704b20',soft='#e9edf1',todo='#a8662d',schedule='#217b7d',anniversary='#8151af',note='#326da6',habit='#477e54')

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
QPushButton#nav {{ background:transparent; text-align:left; padding:10px 14px; color:{c['muted']}; }}
QPushButton#nav:checked {{ background:{c['selection']}; color:{c['selected']}; font-weight:600; }}
QPushButton#filter {{ background:transparent; color:{c['muted']}; padding:6px 12px; }}
QPushButton#filter:checked {{ background:{c['card']}; color:{c['text']}; font-weight:600; }}
QPushButton#calendarDay {{ padding:0; min-width:36px; min-height:58px; border:0; }}
QSpinBox::up-button,QTimeEdit::up-button,QDateTimeEdit::up-button {{ width:18px; border:0; background:transparent; }}
QSpinBox::down-button,QTimeEdit::down-button,QDateTimeEdit::down-button {{ width:18px; border:0; background:transparent; }}
QLineEdit,QComboBox,QSpinBox,QTimeEdit,QDateEdit,QDateTimeEdit {{ background:{c['inset']}; color:{c['text']}; border:1px solid transparent; border-radius:7px; padding:7px 10px; min-height:19px; selection-background-color:{c['selection']}; selection-color:{c['selected']}; }}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QTimeEdit:focus,QDateEdit:focus,QDateTimeEdit:focus {{ border-color:{c['focus']}; }}
QLineEdit#noteTitle {{ background:{c['card']}; font-size:15pt; font-weight:600; padding:6px 0; }}
QComboBox {{ padding-right:24px; }}
QComboBox::drop-down {{ border:0; width:22px; }}
QComboBox::down-arrow {{ image:none; }}
QComboBox QAbstractItemView {{ background:{c['card']}; color:{c['text']}; border:1px solid {c['border']}; selection-background-color:{c['selection']}; selection-color:{c['selected']}; padding:6px; }}
QPlainTextEdit,QTextBrowser {{ background:{c['card']}; color:{c['text']}; border:0; border-radius:8px; padding:10px; selection-background-color:{c['selection']}; selection-color:{c['selected']}; }}
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
        super().__init__();self.calendar=calendar;self.date=QDate();self.setObjectName('calendarDay');self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding);self.setMinimumSize(38,60);self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(lambda:self.calendar.setSelectedDate(self.date))
    def paintEvent(self,event):
        from lunar_python import Solar
        c=colors(self);p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);selected=self.date==self.calendar.selectedDate()
        p.setPen(Qt.NoPen);p.setBrush(QColor(c['selection'] if selected else c['inset'] if self.underMouse() else c['card']))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(2,2,-2,-2),8,8)
        current=self.date.month()==self.calendar.monthShown()
        p.setPen(QColor(c['selected'] if selected else c['text'] if current else c['muted']))
        font=QFont(self.font());font.setPointSize(11);font.setBold(self.date==QDate.currentDate());p.setFont(font)
        p.drawText(self.rect().adjusted(0,4,0,-self.height()//2),Qt.AlignCenter,str(self.date.day()))
        lunar=Solar.fromYmd(self.date.year(),self.date.month(),self.date.day()).getLunar()
        text=lunar.getMonthInChinese()+'月' if lunar.getDay()==1 else lunar.getDayInChinese()
        font.setPointSize(8);font.setBold(False);p.setFont(font);p.setPen(QColor(c['muted']))
        p.drawText(self.rect().adjusted(0,self.height()//2-8,0,-13),Qt.AlignCenter,text)
        types=self.calendar.marks.get(self.date.toString('yyyy-MM-dd'),())
        kinds=[k for k in TYPE_ORDER if k in types];start=self.width()/2-(len(kinds)*7-3)/2
        p.setPen(Qt.NoPen)
        for i,kind in enumerate(kinds):p.setBrush(QColor(c[kind]));p.drawEllipse(QRectF(start+i*7,self.height()-11,4,4))
        if self.hasFocus():p.setPen(QPen(QColor(c['focus']),1));p.setBrush(Qt.NoBrush);p.drawRoundedRect(QRectF(self.rect()).adjusted(2,2,-2,-2),8,8)

class MonthCalendar(QWidget):
    currentPageChanged=Signal(int,int)
    selectionChanged=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self._selected=QDate.currentDate();self._month=self._selected;self.marks={};self.setObjectName('surface');self.setAttribute(Qt.WA_StyledBackground)
        outer=QVBoxLayout(self);outer.setContentsMargins(14,14,14,12);outer.setSpacing(8)
        header=QHBoxLayout();self.caption=QLabel();self.caption.setObjectName('section');header.addWidget(self.caption);header.addStretch()
        for text,action in [('‹',lambda:self.shift(-1)),('今天',lambda:self.setSelectedDate(QDate.currentDate())),('›',lambda:self.shift(1))]:
            b=QPushButton(text);b.setObjectName('quiet');b.clicked.connect(action);header.addWidget(b)
        outer.addLayout(header);grid=QGridLayout();grid.setSpacing(2);self.cells=[]
        for col,name in enumerate(['一','二','三','四','五','六','日']):
            label=QLabel(name);label.setObjectName('muted');label.setAlignment(Qt.AlignCenter);grid.addWidget(label,0,col)
        for n in range(42):
            b=CalendarDay(self);self.cells.append(b);grid.addWidget(b,n//7+1,n%7)
        for n in range(1,7):grid.setRowStretch(n,1);grid.setRowMinimumHeight(n,60)
        outer.addLayout(grid,1);self.repaint_month()
    def yearShown(self):return self._month.year()
    def monthShown(self):return self._month.month()
    def selectedDate(self):return self._selected
    def setFirstDayOfWeek(self,*_):pass
    def setVerticalHeaderFormat(self,*_):pass
    def setCurrentPage(self,year,month):
        self._month=QDate(year,month,1);self.repaint_month();self.currentPageChanged.emit(year,month)
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
        self.caption.setText(f'{self.yearShown()} 年 {self.monthShown()} 月')
        first=QDate(self.yearShown(),self.monthShown(),1);start=first.addDays(1-first.dayOfWeek())
        for n,b in enumerate(self.cells):
            b.date=start.addDays(n);names='、'.join(TYPE_NAMES[k] for k in TYPE_ORDER if k in self.marks.get(b.date.toString('yyyy-MM-dd'),()))
            b.setAccessibleName(b.date.toString('yyyy年M月d日')+('，'+names if names else ''));b.setToolTip(names);b.update()
