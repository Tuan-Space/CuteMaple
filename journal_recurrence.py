"""Civil-time recurrence, independent of Qt and the scheduler clock."""
from __future__ import annotations
import calendar
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ADVANCES = (604800, 259200, 86400, 18000, 10800, 3600)
PERIODS = ('once', 'hourly', 'daily', 'weekly', 'monthly', 'yearly')


def civil_timestamp(value: datetime, zone: str) -> float:
    """First fold; move a non-existent clock reading to the next valid instant."""
    tz = ZoneInfo(zone)
    naive = value.replace(tzinfo=None)
    first = naive.replace(tzinfo=tz, fold=0)
    back = datetime.fromtimestamp(first.timestamp(), tz).replace(tzinfo=None)
    if back == naive:
        return first.timestamp()
    # Gaps are rare. Find the first valid civil minute, preserving seconds only
    # when the original clock reading exists. This also handles 30-minute gaps.
    probe = naive.replace(second=0, microsecond=0)
    for _ in range(2881):
        stamp = probe.replace(tzinfo=tz, fold=0).timestamp()
        if datetime.fromtimestamp(stamp, tz).replace(tzinfo=None) == probe and probe >= naive:
            return stamp
        probe += timedelta(minutes=1)
    raise ValueError('时区中没有可用的日期')


@dataclass
class Rule:
    start: str
    zone: str = 'Asia/Shanghai'
    period: str = 'once'
    calendar: str = 'solar'
    missing: str = 'last'
    include_leap: bool = True
    strict_leap: bool = False
    end: str | None = None
    count: int | None = None
    advances: tuple[int, ...] = ()

    def __post_init__(self):
        self.advances = tuple(sorted(set(self.advances), reverse=True))
        self.base = datetime.fromisoformat(self.start).replace(tzinfo=None, microsecond=0)
        ZoneInfo(self.zone)
        if self.period not in PERIODS or self.calendar not in ('solar', 'lunar'):
            raise ValueError('无效的重复规则')
        if self.missing not in ('last', 'skip') or any(x not in ADVANCES for x in self.advances):
            raise ValueError('无效的日期或提前提醒规则')
        if self.count is not None and not 1 <= self.count <= 100000:
            raise ValueError('总次数应为 1～100000')
        if self.end and datetime.fromisoformat(self.end) < self.base:
            raise ValueError('截止时间不能早于首次时间')
        self._lunar = None
        if self.calendar == 'lunar':
            if self.period not in ('monthly', 'yearly'):
                raise ValueError('农历只用于每月或每年重复')
            from lunar_python import Solar
            self._lunar = Solar.fromYmd(self.base.year, self.base.month, self.base.day).getLunar()

    def mapping(self):
        return asdict(self)

    def _candidate(self, index: int) -> datetime | None:
        b = self.base
        if index == 0:
            return b
        if self.period == 'once':
            return None
        if self.period in ('hourly', 'daily', 'weekly'):
            return b + timedelta(seconds=index * {'hourly':3600, 'daily':86400, 'weekly':604800}[self.period])
        if self.calendar == 'lunar':
            from lunar_python import Lunar, LunarMonth
            original = self._lunar
            y, m, d = original.getYear(), original.getMonth(), original.getDay()
            if self.period == 'monthly':
                if self.include_leap:
                    month = LunarMonth.fromYm(y, m).next(index)
                    y, m = month.getYear(), month.getMonth()
                else:
                    total = y * 12 + abs(m) - 1 + index
                    y, m = total // 12, total % 12 + 1
            else:
                y += index
                if m < 0 and LunarMonth.fromYm(y, m) is None:
                    if self.strict_leap:
                        return None
                    m = abs(m)
            month = LunarMonth.fromYm(y, m)
            if month is None:
                return None
            if d > month.getDayCount():
                if self.missing == 'skip':
                    return None
                d = month.getDayCount()
            solar = Lunar.fromYmd(y, m, d).getSolar()
            return b.replace(year=solar.getYear(), month=solar.getMonth(), day=solar.getDay())
        total = b.year * 12 + b.month - 1 + (index if self.period == 'monthly' else index * 12)
        y, m = total // 12, total % 12 + 1
        if y > 9999:
            return None
        last = calendar.monthrange(y, m)[1]
        if b.day > last and self.missing == 'skip':
            return None
        return b.replace(year=y, month=m, day=min(b.day, last))

    def between(self, lower: float, upper: float, limit: int = 10000):
        """Yield (civil index, UTC timestamp); calculate only the requested window.

        A finite count counts actual occurrences, including after skipped dates.
        Unbounded short-period schedules seek directly to the visible window.
        """
        if upper < lower:
            return
        start_index = 0
        if self.count is None and self.calendar == 'solar':
            near = datetime.fromtimestamp(lower, ZoneInfo(self.zone)).replace(tzinfo=None)
            seconds = (near - self.base).total_seconds()
            if self.period in ('hourly', 'daily', 'weekly'):
                start_index = max(0, int(seconds // {'hourly':3600,'daily':86400,'weekly':604800}[self.period]) - 2)
            elif self.period == 'monthly':
                start_index = max(0, (near.year-self.base.year)*12+near.month-self.base.month-2)
            elif self.period == 'yearly':
                start_index = max(0, near.year-self.base.year-2)
        emitted, ordinal, index = 0, 0, start_index
        end_stamp = civil_timestamp(datetime.fromisoformat(self.end), self.zone) if self.end else float('inf')
        upper = min(upper, end_stamp)
        while index - start_index < 1000000:
            try:
                candidate = self._candidate(index)
            except (OverflowError, ValueError):
                return
            current = index
            index += 1
            if candidate is None:
                if self.period == 'once' or self.base.year + (current // 12 if self.period == 'monthly' else current) > 9999:
                    return
                continue
            stamp = civil_timestamp(candidate, self.zone)
            ordinal += 1
            if self.count is not None and ordinal > self.count:
                return
            if stamp > upper:
                return
            if stamp >= lower:
                yield current, stamp
                emitted += 1
                if emitted >= limit:
                    return

    def preview(self, after: float | None = None, count: int = 3):
        lower = civil_timestamp(self.base, self.zone) if after is None else after
        upper = min(lower + 366 * 86400 * 150, 253370000000)
        return list(self.between(lower, upper, count))

    def describe(self):
        period = dict(zip(PERIODS, ('仅一次','每小时','每天','每周','每月','每年')))[self.period]
        text = f'{period} · {self.base:%Y-%m-%d %H:%M:%S} · {self.zone}'
        if self.calendar == 'lunar':
            text += ' · 农历' + self._lunar.toString()
        if self.period in ('monthly', 'yearly'):
            text += ' · 缺日' + ('顺延至月底' if self.missing == 'last' else '跳过')
        if self.count:
            text += f' · 共 {self.count} 次'
        elif self.end:
            text += ' · 截至 ' + self.end
        return text


@dataclass
class MilestoneRule:
    start: str
    zone: str = 'Asia/Shanghai'
    calendar: str = 'solar'
    yearly: bool = True
    hundreds: bool = False
    days: tuple[int, ...] = ()
    missing: str = 'last'
    strict_leap: bool = False
    advances: tuple[int, ...] = ()
    schedule_type: str = 'milestone'

    def __post_init__(self):
        self.base=datetime.fromisoformat(self.start).replace(tzinfo=None,microsecond=0)
        self.days=tuple(sorted(set(self.days)))
        if any(type(n) is not int or not 1<=n<=3652059 for n in self.days):raise ValueError('特殊天数请填写正整数')
        if not (self.yearly or self.hundreds or self.days):raise ValueError('至少选择一种提醒')
        self.annual=Rule(self.start,zone=self.zone,period='yearly',calendar=self.calendar,missing=self.missing,strict_leap=self.strict_leap,advances=self.advances)
        self.advances=self.annual.advances

    def mapping(self):return asdict(self)

    def between(self,lower,upper,limit=10000):
        import heapq
        if upper<lower:return
        def annual():
            if self.yearly:
                for index,stamp in self.annual.between(lower,upper,limit+1):
                    if index>0:yield stamp
        def day_stamp(n):
            try:return civil_timestamp(self.base+timedelta(days=n-1),self.zone)
            except (OverflowError,ValueError):return None
        def hundreds():
            if not self.hundreds:return
            local=datetime.fromtimestamp(lower,ZoneInfo(self.zone)).date();elapsed=(local-self.base.date()).days+1
            n=max(100,((elapsed+99)//100)*100)
            while True:
                stamp=day_stamp(n)
                if stamp is None or stamp>upper:return
                if stamp>=lower:yield stamp
                n+=100
        def special():
            for n in self.days:
                stamp=day_stamp(n)
                if stamp is not None and lower<=stamp<=upper:yield stamp
        last=None;count=0
        for stamp in heapq.merge(annual(),hundreds(),special()):
            if stamp==last:continue
            last=stamp;yield count,stamp;count+=1
            if count>=limit:return

    def preview(self,after=None,count=3):
        lower=civil_timestamp(self.base,self.zone) if after is None else after
        return list(self.between(lower,253370000000,count))

    def labels(self,stamp):
        local=datetime.fromtimestamp(stamp,ZoneInfo(self.zone));n=(local.date()-self.base.date()).days+1;result=[]
        if (self.hundreds and n>0 and n%100==0) or n in self.days:result.append(f'第 {n} 天')
        if self.yearly:
            for index,due in self.annual.between(stamp,stamp,1):
                if index>0:result.insert(0,f'{index} 周年')
        return result

    def describe(self):
        parts=[]
        if self.yearly:parts.append('每周年')
        if self.hundreds:parts.append('每 100 天')
        parts.extend(f'{n} 天' for n in self.days)
        return '、'.join(parts)


def parse_rule(value):
    import json
    data=json.loads(value) if isinstance(value,str) else dict(value)
    return MilestoneRule(**data) if data.get('schedule_type')=='milestone' else Rule(**data)
