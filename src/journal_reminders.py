"""Actionable, non-activating reminder surface. It never dismisses on a timer."""
from __future__ import annotations
import random
from datetime import datetime
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QDialog,QSpinBox,QComboBox,QDialogButtonBox,QMessageBox,QScrollArea,QSizePolicy
from monitor_ui import ThemeBinding,clamp_rect
from monitor_ui import ThemedSpinBox as QSpinBox,ThemedComboBox as QComboBox
from journal_store import HABITS

MESSAGES={
 'water':('水杯在等你，喝几口水吧。','把忙碌放一小会儿，补点水。','美腻枫提醒：该和水杯见面啦。','给自己一个喝水的小间隙。','下一件事之前，先喝口水？','忙了一阵，记得照顾自己。喝点水吧。','水杯是不是还在原来的位置？拿起来喝几口。','今天也要记得喝水呀。','让手离开键盘，端起水杯吧。','留一分钟，慢慢喝几口水。','我在这里陪你，你去喝口水。','这一小段休息，交给水杯吧。'),
 'walk':('起来走几步，再回来继续吧。','椅子替你休息一会儿，去走动走动。','换个姿势，看看窗外再回来。','给双腿一个小小的活动时间。','美腻枫邀请你散个短短的步。','下一件事之前，站起来活动一下。','久坐了一阵，走几步放松一下吧。','伸个懒腰，去门口转一圈？','离开屏幕一会儿，我帮你守着桌面。','给身体一个短暂的间奏。起来走走。','把这一刻留给自己，活动活动吧。','小小走动一下，回来接着忙。'),
 'eyes':('把视线移远一点，看看窗外吧。','屏幕先交给我，去看一会儿远处。','给眼睛换一幅远一点的风景。','抬抬头，看看房间另一端。','忙碌暂停片刻，让视线走远一点。','不妨看看远处的树或楼。','这一小会儿，不用盯着屏幕。','美腻枫提醒：看看远处，放松一下。','把视线从字里行间移开一会儿。','找一个远处的地方，安静看一看。','眼睛也想休息一下，抬头看看吧。','先看看远处，我还会在这里。')}


def snooze_seconds(parent):
    dialog=QDialog(parent); dialog.setWindowTitle('稍后提醒'); dialog.theme=ThemeBinding(dialog,'journal')
    dialog.resize(400,230);layout=QVBoxLayout(dialog);layout.setContentsMargins(20,20,20,20);layout.setSpacing(16);title=QLabel('给自己留一点时间');title.setObjectName('section');layout.addWidget(title)
    caption=QLabel('从现在开始，过多久再提醒？');caption.setObjectName('muted');layout.addWidget(caption)
    presets=QHBoxLayout(); result=[]
    for text,value in [('10 分钟',600),('1 小时',3600),('1 天',86400)]:
        button=QPushButton(text); button.clicked.connect(lambda _,v=value:(result.append(v),dialog.accept())); presets.addWidget(button)
    layout.addLayout(presets)
    row=QHBoxLayout(); amount=QSpinBox(); amount.setRange(1,366); amount.setValue(10); unit=QComboBox(); unit.addItems(['分钟','小时','天']); row.addWidget(amount); row.addWidget(unit); layout.addLayout(row)
    buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText('确定');buttons.button(QDialogButtonBox.Ok).setObjectName('primary');buttons.button(QDialogButtonBox.Cancel).setText('取消')
    buttons.accepted.connect(lambda:(result.append(amount.value()*[60,3600,86400][unit.currentIndex()]),dialog.accept())); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
    accepted=dialog.exec()==QDialog.Accepted;dialog.deleteLater()
    return result[0] if accepted else None


class ReminderBubble(QWidget):
    changed=Signal()
    openRequested=Signal()
    def __init__(self,store,pet):
        super().__init__(None,Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.store,self.pet=store,pet; self.current=None; self.items=[]; self.text_cache={}; self.last_phrase={};self.save_errors={}
        self.setObjectName('journalBubble'); self.setFixedWidth(370);self.setAttribute(Qt.WA_StyledBackground)
        self.theme=ThemeBinding(self,'journal')
        layout=QVBoxLayout(self); layout.setContentsMargins(18,16,18,16); layout.setSpacing(12)
        header=QHBoxLayout(); self.heading=QLabel('小提醒'); self.heading.setObjectName('section'); header.addWidget(self.heading); header.addStretch()
        self.count=QLabel();self.count.setObjectName('muted'); header.addWidget(self.count); layout.addLayout(header)
        self.content_area=QScrollArea();self.content_area.setWidgetResizable(True);self.content_area.setFrameShape(QScrollArea.NoFrame);self.content_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.content_area.viewport().setObjectName('journalViewport');layout.addWidget(self.content_area)
        self.content_card=QWidget();self.content_card.setObjectName('surface');self.content_card.setAttribute(Qt.WA_StyledBackground);content=QVBoxLayout(self.content_card);content.setContentsMargins(14,12,14,12);content.setSpacing(10);self.content_area.setWidget(self.content_card)
        self.text=QLabel(); self.text.setTextFormat(Qt.PlainText); self.text.setWordWrap(True);self.text.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);self.text.setObjectName('cardTitle'); content.addWidget(self.text)
        self.meta=QLabel(); self.meta.setWordWrap(True);self.meta.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred); self.meta.setObjectName('metricName'); content.addWidget(self.meta)
        actions=QHBoxLayout(); self.ack=QPushButton('知道了'); self.done=QPushButton('已完成'); self.later=QPushButton('稍后提醒')
        self.done.setObjectName('primary')
        for widget in (self.ack,self.done,self.later): actions.addWidget(widget)
        layout.addLayout(actions)
        self.ack.clicked.connect(lambda:self.respond('ack')); self.done.clicked.connect(lambda:self.respond('done')); self.later.clicked.connect(self.defer)
        nav=QHBoxLayout(); previous=QPushButton('上一条'); following=QPushButton('下一条'); details=QPushButton('打开手账')
        self.previous,self.following=previous,following
        previous.clicked.connect(lambda:self.navigate(-1)); following.clicked.connect(lambda:self.navigate(1)); details.clicked.connect(self.openRequested)
        for widget in (previous,following,details): nav.addWidget(widget)
        for widget in (previous,following,details):widget.setObjectName('quiet')
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
        multiple=len(self.items)>1
        self.count.setVisible(multiple);self.previous.setVisible(multiple);self.following.setVisible(multiple)
        if row['habit']:
            kind=row['habit']
            self.heading.setText('照顾自己 · '+HABITS[kind][0])
            if row['id'] not in self.text_cache:
                options=[s for s in MESSAGES[kind] if s!=self.last_phrase.get(kind)]
                self.text_cache[row['id']]=self.last_phrase[kind]=random.choice(options)
            self.text.setText(self.text_cache[row['id']]); self.meta.setText('');self.meta.hide()
            self.done.setText('已喝水' if kind=='water' else '已完成'); self.later.setText('未喝水' if kind=='water' else '未完成'); self.ack.hide()
        else:
            self.heading.setText('日程提醒' if row.get('kind')=='schedule' else '小提醒')
            self.meta.show()
            due=datetime.fromtimestamp(row['due']).strftime('%Y-%m-%d %H:%M:%S')
            remaining=max(0,round((row['due']-self.store.clock())/60))
            prefix=f'提前提醒 · 约 {remaining} 分钟后到期' if row['stage'] else '到期提醒'
            self.text.setText(row['title']+'\n'+(row['body'] or '')[:500])
            self.meta.setText(f'{prefix} · {due}'+(f"\n另有 {row['missed']} 次未处理" if row['missed'] else ''))
            self.done.setText('已完成'); self.later.setText('稍后提醒'); self.ack.setVisible(row['stage']>0)
        if self.current in self.save_errors:self.meta.setText(self.save_errors[self.current]);self.meta.show()
        self.follow(); self.show()

    def navigate(self,step):
        if self.items:
            ids=[x['id'] for x in self.items]; self.current=ids[(ids.index(self.current)+step)%len(ids)]; self.refresh()

    def respond(self,action,delay=0):
        if not self.current: return
        try:
            self.store.respond(self.current,action,delay);self.save_errors.pop(self.current,None);self.refresh();self.changed.emit()
        except Exception as error:
            self.save_errors[self.current]='未能保存，请重试：'+str(error)
            self.meta.setText(self.save_errors[self.current])
            self.meta.show()

    def defer(self):
        row=next((x for x in self.items if x['id']==self.current),None)
        if not row: return
        if row['habit']: self.respond('no'); return
        seconds=snooze_seconds(self)
        if seconds: self.respond('snooze',seconds)

    def follow(self):
        area=self.pet._screen_area()
        # Measure at the real card width, reserving room for its scrollbar.
        # A long reminder scrolls inside the card; response actions stay visible.
        margins=self.layout().contentsMargins();text_width=max(40,self.width()-margins.left()-margins.right()-38)
        text_height=max(self.text.fontMetrics().height(),self.text.heightForWidth(text_width));self.text.setMinimumHeight(text_height)
        meta_height=max(self.meta.fontMetrics().height(),self.meta.heightForWidth(text_width)) if not self.meta.isHidden() else 0;self.meta.setMinimumHeight(meta_height)
        content_height=text_height+meta_height+26+(10 if meta_height else 0);self.content_card.setMinimumHeight(content_height)
        self.layout().invalidate();other_height=self.layout().minimumSize().height()-self.content_area.minimumHeight()
        self.content_area.setFixedHeight(min(content_height,max(48,area.height()-other_height-16)))
        self.adjustSize()
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
