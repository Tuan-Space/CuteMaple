"""Actionable, non-activating reminder surface. It never dismisses on a timer."""
from __future__ import annotations
import random
from datetime import datetime
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QDialog,QSpinBox,QComboBox,QDialogButtonBox,QMessageBox
from monitor_ui import ThemeBinding,clamp_rect
from journal_store import HABITS

MESSAGES={
 'water':('水杯在等你，喝几口水吧。','把忙碌放一小会儿，补点水。','美腻枫提醒：该和水杯见面啦。','给自己一个喝水的小间隙。','下一件事之前，先喝口水？','忙了一阵，记得照顾自己。喝点水吧。','水杯是不是还在原来的位置？拿起来喝几口。','今天也要记得喝水呀。','让手离开键盘，端起水杯吧。','留一分钟，慢慢喝几口水。','我在这里陪你，你去喝口水。','这一小段休息，交给水杯吧。'),
 'walk':('起来走几步，再回来继续吧。','椅子替你休息一会儿，去走动走动。','换个姿势，看看窗外再回来。','给双腿一个小小的活动时间。','美腻枫邀请你散个短短的步。','下一件事之前，站起来活动一下。','久坐了一阵，走几步放松一下吧。','伸个懒腰，去门口转一圈？','离开屏幕一会儿，我帮你守着桌面。','给身体一个短暂的间奏。起来走走。','把这一刻留给自己，活动活动吧。','小小走动一下，回来接着忙。'),
 'eyes':('把视线移远一点，看看窗外吧。','屏幕先交给我，去看一会儿远处。','给眼睛换一幅远一点的风景。','抬抬头，看看房间另一端。','忙碌暂停片刻，让视线走远一点。','不妨看看远处的树或楼。','这一小会儿，不用盯着屏幕。','美腻枫提醒：看看远处，放松一下。','把视线从字里行间移开一会儿。','找一个远处的地方，安静看一看。','眼睛也想休息一下，抬头看看吧。','先看看远处，我还会在这里。')}


def snooze_seconds(parent):
    dialog=QDialog(parent); dialog.setWindowTitle('稍后提醒'); dialog.theme=ThemeBinding(dialog)
    layout=QVBoxLayout(dialog); layout.addWidget(QLabel('从现在开始，过多久再提醒？'))
    presets=QHBoxLayout(); result=[]
    for text,value in [('10 分钟',600),('1 小时',3600),('1 天',86400)]:
        button=QPushButton(text); button.clicked.connect(lambda _,v=value:(result.append(v),dialog.accept())); presets.addWidget(button)
    layout.addLayout(presets)
    row=QHBoxLayout(); amount=QSpinBox(); amount.setRange(1,366); amount.setValue(10); unit=QComboBox(); unit.addItems(['分钟','小时','天']); row.addWidget(amount); row.addWidget(unit); layout.addLayout(row)
    buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
    buttons.accepted.connect(lambda:(result.append(amount.value()*[60,3600,86400][unit.currentIndex()]),dialog.accept())); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
    return result[0] if dialog.exec()==QDialog.Accepted else None


class ReminderBubble(QWidget):
    changed=Signal()
    openRequested=Signal()
    def __init__(self,store,pet):
        super().__init__(None,Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.store,self.pet=store,pet; self.current=None; self.items=[]; self.text_cache={}; self.last_phrase={}
        self.setObjectName('journalBubble'); self.setFixedWidth(370)
        self.theme=ThemeBinding(self,'journal')
        layout=QVBoxLayout(self); layout.setContentsMargins(16,12,16,12); layout.setSpacing(9)
        header=QHBoxLayout(); self.heading=QLabel('美腻枫 · 小提醒'); self.heading.setObjectName('section'); header.addWidget(self.heading); header.addStretch()
        self.count=QLabel(); header.addWidget(self.count); layout.addLayout(header)
        self.text=QLabel(); self.text.setTextFormat(Qt.PlainText); self.text.setWordWrap(True); layout.addWidget(self.text)
        self.meta=QLabel(); self.meta.setWordWrap(True); self.meta.setObjectName('metricName'); layout.addWidget(self.meta)
        actions=QHBoxLayout(); self.ack=QPushButton('知道了'); self.done=QPushButton('已完成'); self.later=QPushButton('稍后提醒')
        for widget in (self.ack,self.done,self.later): actions.addWidget(widget)
        layout.addLayout(actions)
        self.ack.clicked.connect(lambda:self.respond('ack')); self.done.clicked.connect(lambda:self.respond('done')); self.later.clicked.connect(self.defer)
        nav=QHBoxLayout(); previous=QPushButton('上一条'); following=QPushButton('下一条'); details=QPushButton('打开手账')
        previous.clicked.connect(lambda:self.navigate(-1)); following.clicked.connect(lambda:self.navigate(1)); details.clicked.connect(self.openRequested)
        for widget in (previous,following,details): nav.addWidget(widget)
        layout.addLayout(nav)
        self.cleanup=QLabel(); self.cleanup.setWordWrap(True); self.cleanup.setObjectName('status'); self.cleanup.hide(); layout.addWidget(self.cleanup)

    def refresh(self):
        self.items=self.store.pending()
        if not self.items:
            self.current=None; self.hide(); return
        identities=[r['id'] for r in self.items]
        if self.current not in identities: self.current=identities[0]
        row=self.items[identities.index(self.current)]
        self.count.setText(f'{identities.index(self.current)+1} / {len(self.items)}')
        if row['habit']:
            kind=row['habit']
            if row['id'] not in self.text_cache:
                options=[s for s in MESSAGES[kind] if s!=self.last_phrase.get(kind)]
                self.text_cache[row['id']]=self.last_phrase[kind]=random.choice(options)
            self.text.setText(self.text_cache[row['id']]); self.meta.setText('请手动回应，这条提醒不会自动消失。')
            self.done.setText('已喝水' if kind=='water' else '已完成'); self.later.setText('未喝水' if kind=='water' else '未完成'); self.ack.hide()
        else:
            due=datetime.fromtimestamp(row['due']).strftime('%Y-%m-%d %H:%M:%S')
            remaining=max(0,round((row['due']-self.store.clock())/60))
            prefix=f'提前提醒 · 约 {remaining} 分钟后到期' if row['stage'] else '到期提醒'
            self.text.setText(row['title']+'\n'+(row['body'] or '')[:500])
            self.meta.setText(f'{prefix}\n原到期：{due}'+(f"\n曾错过 {row['missed']} 次，可在事件记录查看。" if row['missed'] else ''))
            self.done.setText('已完成'); self.later.setText('稍后提醒'); self.ack.setVisible(row['stage']>0)
        self.follow(); self.show()

    def navigate(self,step):
        if self.items:
            ids=[x['id'] for x in self.items]; self.current=ids[(ids.index(self.current)+step)%len(ids)]; self.refresh()

    def respond(self,action,delay=0):
        if not self.current: return
        try:
            self.store.respond(self.current,action,delay); self.refresh(); self.changed.emit()
        except Exception as error:
            self.meta.setText('未能保存，请重试：'+str(error))

    def defer(self):
        row=next((x for x in self.items if x['id']==self.current),None)
        if not row: return
        if row['habit']: self.respond('no'); return
        seconds=snooze_seconds(self)
        if seconds: self.respond('snooze',seconds)

    def follow(self):
        self.adjustSize()
        area=self.pet._screen_area()
        if self.pet.isVisible() and not self.pet._pet_hidden:
            anchor=self.pet._bubble_anchor_rect()
            x=anchor.center().x()-self.width()//2
            above=QRect(x,anchor.top()-self.height()-8,self.width(),self.height())
            below=QRect(x,anchor.bottom()+9,self.width(),self.height())
            preferred,alternate=(below,above) if self.pet.base_mode=='top_swing' else (above,below)
            rect=preferred if area.contains(preferred) else alternate if area.contains(alternate) else preferred
        else:
            rect=self.geometry(); rect.moveBottomRight(area.bottomRight())
            rect.translate(-14,-14)
        self.move(clamp_rect(rect,area).topLeft())
