"""Inline civil/lunar date entry. Calendar mode never changes the instant."""
import calendar
from PySide6.QtCore import QDateTime,QDate,QTime,Signal
from PySide6.QtWidgets import QWidget,QGridLayout,QLabel
from monitor_ui import ThemedComboBox as Combo,ThemedSpinBox as Spin
from lunar_python import Lunar,LunarYear,Solar


class TimePart(Spin):
    def textFromValue(self,value):return f'{value:02d}'


class DateTimeFields(QWidget):
    dateTimeChanged=Signal(QDateTime)
    def __init__(self,value=None,parent=None):
        super().__init__(parent);self.lunar=False;self.filling=False
        self.year=Spin();self.year.setRange(1900,2199);self.month=Combo();self.day=Combo()
        self.year.setSuffix('年')
        self.hour=TimePart();self.hour.setRange(0,23);self.minute=TimePart();self.minute.setRange(0,59);self.second=TimePart();self.second.setRange(0,59)
        for w,unit in ((self.hour,'时'),(self.minute,'分'),(self.second,'秒')):w.setSuffix(unit)
        layout=QGridLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
        for i,(name,w) in enumerate(zip(('年','月','日','时','分','秒'),(self.year,self.month,self.day,self.hour,self.minute,self.second))):
            cell=QWidget();from PySide6.QtWidgets import QVBoxLayout
            box=QVBoxLayout(cell);box.setContentsMargins(0,0,0,0);box.setSpacing(4);label=QLabel(name);label.setObjectName('muted');box.addWidget(label);box.addWidget(w);layout.addWidget(cell,i//3,i%3)
        self.year.valueChanged.connect(self.year_changed);self.month.currentIndexChanged.connect(self.month_changed);self.day.currentIndexChanged.connect(self.changed)
        for w in (self.hour,self.minute,self.second):w.valueChanged.connect(self.changed)
        self.setDateTime(value or QDateTime.currentDateTime())
    def months(self):
        if self.lunar:return [(m.getMonth(),Lunar.fromYmd(self.year.value(),m.getMonth(),1).getMonthInChinese()+'月') for m in LunarYear.fromYear(self.year.value()).getMonthsInYear()]
        return [(n,str(n)+'月') for n in range(1,13)]
    def fill_months(self,month):
        self.month.clear()
        for value,label in self.months():self.month.addItem(label,value)
        self.month.setCurrentIndex(max(0,self.month.findData(month)))
    def fill_days(self,day):
        month=self.month.currentData() or 1
        count=LunarYear.fromYear(self.year.value()).getMonth(month).getDayCount() if self.lunar else calendar.monthrange(self.year.value(),month)[1]
        self.day.clear()
        for n in range(1,count+1):self.day.addItem(Lunar.fromYmd(self.year.value(),month,n).getDayInChinese() if self.lunar else str(n)+'日',n)
        self.day.setCurrentIndex(max(0,min(day,count)-1))
    def year_changed(self,*_):
        if self.filling:return
        self.filling=True;month=self.month.currentData() or 1;day=self.day.currentData() or 1;self.fill_months(month);self.fill_days(day);self.filling=False;self.changed()
    def month_changed(self,*_):
        if self.filling:return
        self.filling=True;self.fill_days(self.day.currentData() or 1);self.filling=False;self.changed()
    def changed(self,*_):
        if not self.filling:self.dateTimeChanged.emit(self.dateTime())
    def setLunar(self,on):
        value=self.dateTime();self.lunar=bool(on);self.setDateTime(value)
    def dateTime(self):
        year,month,day=self.year.value(),self.month.currentData() or 1,self.day.currentData() or 1
        if self.lunar:
            solar=Lunar.fromYmd(year,month,day).getSolar();year,month,day=solar.getYear(),solar.getMonth(),solar.getDay()
        return QDateTime(QDate(year,month,day),self.time())
    def time(self):return QTime(self.hour.value(),self.minute.value(),self.second.value())
    def setTime(self,time):
        self.filling=True;self.hour.setValue(time.hour());self.minute.setValue(time.minute());self.second.setValue(time.second());self.filling=False;self.changed()
    def setDateTime(self,value):
        d=value.date();year,month,day=d.year(),d.month(),d.day()
        if self.lunar:
            lunar=Solar.fromYmd(year,month,day).getLunar();year,month,day=lunar.getYear(),lunar.getMonth(),lunar.getDay()
        self.filling=True;self.year.setRange(min(1900,year),max(2199,year));self.year.setValue(year);self.fill_months(month);self.fill_days(day)
        self.hour.setValue(value.time().hour());self.minute.setValue(value.time().minute());self.second.setValue(value.time().second());self.filling=False;self.changed()
