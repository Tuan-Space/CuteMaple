"""Offline Chinese festivals and manually refreshed, data-only holiday notices."""
import json,threading,urllib.request
from datetime import date
from pathlib import Path
from PySide6.QtCore import QObject,Signal
from lunar_python import Solar


def validate(value,year):
    if not isinstance(value,dict) or value.get('year')!=year:raise ValueError('节假日数据年份不符')
    papers=value.get('papers',[]);days=value.get('days',[])
    if not papers or not days:raise ValueError(f'{year} 年放假安排尚未公布')
    if not all(isinstance(p,str) and p.startswith(('https://www.gov.cn/','http://www.gov.cn/')) for p in papers):raise ValueError('缺少国务院通知出处')
    if not isinstance(days,list) or len(days)>370:raise ValueError('节假日数据格式错误')
    for row in days:
        d=date.fromisoformat(row['date'])
        if abs(d.year-year)>1 or type(row['isOffDay']) is not bool or not isinstance(row['name'],str) or len(row['name'])>60:raise ValueError('节假日记录无效')
    return value


class Holidays(QObject):
    finished=Signal(str)
    def __init__(self,root,parent=None):
        super().__init__(parent);self.cache=Path(root)/'holiday-cache';self.busy=False;self.days={};self.years=set();self.reload()
    def reload(self):
        from pet_core import resource_root
        self.days={};self.years=set();self._info={};files={}
        for folder in (resource_root()/'assets'/'holidays',self.cache):
            for p in folder.glob('*.json'):files[p.stem]=p
        for year,p in sorted(files.items()):
            try:
                value=validate(json.loads(p.read_text(encoding='utf-8-sig')),int(year));self.years.add(int(year));self.days.update({r['date']:r for r in value['days']})
            except (ValueError,KeyError,TypeError,OSError):continue
    def update_year(self,year):
        if self.busy:return
        self.busy=True
        def run():
            results=[];errors=[]
            for y in (year-1,year,year+1):
                try:
                    req=urllib.request.Request(f'https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{y}.json',headers={'User-Agent':'CuteMaple-calendar'})
                    with urllib.request.urlopen(req,timeout=10) as response:data=response.read(256001)
                    if len(data)>256000:raise ValueError('数据过大')
                    value=validate(json.loads(data),y);self.cache.mkdir(exist_ok=True);target=self.cache/f'{y}.json';temp=target.with_suffix('.part');temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temp.replace(target);results.append(y)
                except Exception as error:
                    if y==year:errors.append(str(error))
            message=f'{year} 年节假日已更新' if year in results else ('；'.join(errors) or '未取得新的安排')+'，保留已有数据'
            self.finished.emit(message)
        threading.Thread(target=run,name='holiday-manual-update',daemon=True).start()
    def info(self,d):
        key=d.toString('yyyy-MM-dd')
        if key in self._info:return self._info[key]
        solar=Solar.fromYmd(d.year(),d.month(),d.day());lunar=solar.getLunar()
        names=lunar.getFestivals()+solar.getFestivals();term=lunar.getJieQi()
        short={'春节':'春节','元宵节':'元宵','端午节':'端午','中秋节':'中秋','重阳节':'重阳','国庆节':'国庆','劳动节':'劳动节','元旦节':'元旦','清明节':'清明','七夕节':'七夕','除夕':'除夕','教师节':'教师节','儿童节':'儿童节','妇女节':'妇女节','青年节':'青年节','情人节':'情人节','母亲节':'母亲节','父亲节':'父亲节'}
        label=next((short[n] for n in names if n in short),None) or term or (lunar.getMonthInChinese()+'月' if lunar.getDay()==1 else lunar.getDayInChinese())
        result=label,self.days.get(key),'、'.join(names+([term] if term else []));self._info[key]=result;return result
