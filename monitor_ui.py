from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPen, QPalette, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget, QScrollArea, QStyle, QStyleOptionButton)

from resource_monitor import MemorySnapshot, NetworkSnapshot, format_bytes


LIGHT = dict(background='#fafdff', card='#ffffff', border='#63849a', text='#273e4c', muted='#506575',
             accent='#80521e', button='#dff0fa', hover='#fff0ca', pressed='#bbd9eb', disabled='#e3e8ec',
             focus='#12699d', status='#eaf3f9', selection='#176a9b', selected='#ffffff')
DARK = dict(background='#1e252b', card='#283139', border='#839aa9', text='#eef4f8', muted='#bacad5',
            accent='#edc18b', button='#344958', hover='#405d70', pressed='#263c4b', disabled='#303940',
            focus='#84cfff', status='#273b49', selection='#a0d7f7', selected='#172730')


def system_theme():
    app = QApplication.instance()
    scheme = app.styleHints().colorScheme()
    dark = scheme == Qt.ColorScheme.Dark
    if scheme == Qt.ColorScheme.Unknown:
        dark = app.palette().color(QPalette.Window).lightnessF() < .5
    return DARK if dark else LIGHT


def panel_style(c):
    return f"""
QFrame#card, QFrame#capsule {{ background: transparent; border: none; }}
QScrollArea {{ background: transparent; border: none; }}
QWidget#monitorContent, QWidget#monitorViewport {{ background: {c['background']}; }}
QScrollBar:vertical {{ background: {c['background']}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 22px; border-radius: 4px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: {c['background']}; }}
QDialog {{ background: {c['background']}; color: {c['text']}; }}
QLabel, QCheckBox {{ color: {c['text']}; font-size: 11pt; }}
QLabel#title {{ color: {c['text']}; font-size: 16pt; }}
QLabel#section, QLabel#memory {{ color: {c['accent']}; font-size: 12pt; }}
QFrame#sectionCard {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 11px; }}
QLabel#metricName {{ color: {c['muted']}; font-size: 10pt; }}
QLabel#metricValue {{ color: {c['text']}; font-size: 11pt; }}
QLabel#status {{ background: {c['status']}; border: 1px solid {c['border']}; border-radius: 8px; color: {c['text']}; padding: 6px; }}
QPushButton, QComboBox {{ color: {c['text']}; background: {c['button']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 7px 12px; }}
QPushButton:hover, QComboBox:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['pressed']}; }}
QPushButton:focus, QComboBox:focus, QCheckBox:focus {{ border: 2px solid {c['focus']}; }}
QPushButton:disabled, QComboBox:disabled, QCheckBox:disabled {{ background: {c['disabled']}; color: {c['muted']}; }}
QPushButton#close {{ padding: 0; font-size: 18pt; }}
QComboBox {{ padding: 4px 22px 4px 8px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: {c['card']}; color: {c['text']}; selection-background-color: {c['selection']}; selection-color: {c['selected']}; border: 1px solid {c['border']}; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {c['border']}; background: {c['card']}; }}
QCheckBox::indicator:checked {{ background: {c['selection']}; border: 3px solid {c['accent']}; }}
QToolTip {{ color: {c['text']}; background: {c['card']}; border: 1px solid {c['border']}; }}
"""


PANEL_STYLE = panel_style(LIGHT)


class ThemeBinding(QObject):
    def __init__(self, widget, kind='panel'):
        super().__init__(widget)
        self.widget, self.kind = widget, kind
        app = QApplication.instance()
        app.styleHints().colorSchemeChanged.connect(self.apply)
        app.paletteChanged.connect(self.apply)
        self.apply()

    def apply(self, *_):
        w, c = self.widget, system_theme()
        w._theme = c
        palette = QPalette(QApplication.instance().palette())
        for role, key in ((QPalette.Window,'background'),(QPalette.Base,'card'),(QPalette.WindowText,'text'),
                          (QPalette.Text,'text'),(QPalette.ButtonText,'text'),(QPalette.Button,'button'),
                          (QPalette.Highlight,'selection'),(QPalette.HighlightedText,'selected')):
            palette.setColor(role,QColor(c[key]))
        w.setPalette(palette)
        style = panel_style(c)
        if self.kind == 'capsule':
            style += f"QLabel, QLabel#memory {{ font-size: 10pt; padding: 0 3px; }}"
        elif self.kind == 'button':
            style += f"QPushButton {{ border-radius: 17px; padding: 0; color: {c['accent']}; }}"
        elif self.kind == 'bubble':
            style += (f"QLabel {{ background: {c['card']}; color: {c['text']}; "
                      f"border: 2px solid {c['border']}; border-radius: 14px; "
                      "padding: 10px 14px; font-size: 11pt; }")
        elif self.kind == 'journal':
            style += f"""
QWidget#journalWindow, QWidget#journalBubble {{ background: {c['background']}; color: {c['text']}; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 10px; top: -1px; background: {c['background']}; }}
QTabBar::tab {{ background: {c['background']}; color: {c['muted']}; padding: 9px 20px; border: none; min-width: 42px; }}
QTabBar::tab:selected {{ color: {c['accent']}; background: {c['status']}; border-bottom: 2px solid {c['accent']}; }}
QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QTableWidget, QSpinBox, QTimeEdit, QDateTimeEdit {{ background: {c['card']}; color: {c['text']}; selection-background-color: {c['selection']}; selection-color: {c['selected']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 6px; font-size: 11pt; }}
QListWidget::item {{ padding: 10px 8px; border-bottom: 1px solid {c['status']}; }}
QListWidget::item:selected {{ background: {c['status']}; color: {c['text']}; border-left: 3px solid {c['accent']}; }}
QHeaderView::section {{ background: {c['status']}; color: {c['text']}; border: none; padding: 6px; }}
QCalendarWidget QWidget {{ background: {c['background']}; color: {c['text']}; }}
QCalendarWidget QAbstractItemView {{ selection-background-color: {c['selection']}; selection-color: {c['selected']}; }}
QToolButton {{ background: {c['button']}; color: {c['text']}; padding: 5px; border: none; }}
QSplitter::handle {{ background: {c['background']}; width: 10px; }}
"""
        w.setStyleSheet(style)
        w.update()
        if hasattr(w,"_fit_contents"): QTimer.singleShot(0,w._fit_contents)


def bind_theme(widget, kind='panel'):
    widget._theme_binding = ThemeBinding(widget,kind)


class ThemedComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        c = getattr(self.window(), '_theme', LIGHT)
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen); painter.setBrush(QColor(c['text' if self.isEnabled() else 'muted']))
        x, y = self.width()-12, self.height()/2
        painter.drawPolygon(QPolygonF([QPointF(x-4,y-2),QPointF(x+4,y-2),QPointF(x,y+3)]))


class ThemedCheckBox(QCheckBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked(): return
        c = getattr(self.window(), '_theme', LIGHT)
        option=QStyleOptionButton(); self.initStyleOption(option)
        rect=self.style().subElementRect(QStyle.SE_CheckBoxIndicator,option,self)
        painter=QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(c['selected']),2))
        x,y=rect.center().x(),rect.center().y()
        painter.drawPolyline(QPolygonF([QPointF(x-4,y),QPointF(x-1,y+3),QPointF(x+4,y-3)]))


CLEANUP_STEP_NAMES = {
    "empty_working_sets": "进程工作集", "process_working_sets_fallback": "进程工作集兼容清理",
    "flush_modified_pages": "已修改页面", "purge_standby_list": "待机内存",
    "system_file_cache": "文件缓存", "registry_cache": "注册表缓存", "combine_memory": "内存合并",
}


def cleanup_summary(result: dict) -> str:
    labels = {"queued": "等待清理", "starting": "正在启动清理", "running": "正在清理",
              "cancelling": "正在取消，等待后台退出", "succeeded": "清理成功", "partial": "部分项目失败",
              "failed": "清理失败", "cancelled": "本次清理已取消", "timed_out": "清理超时",
              "unresponsive": "后台暂未退出，正在核验", "authorization_required": "本次运行尚未授权", "authorizing": "等待管理员授权",
              "authorized": "本次运行已授权", "helper_missing": "清理助手缺失",
              "security_blocked": "系统防护阻止清理", "busy": "正在处理本次清理"}
    status = str(result.get("status", "failed"))
    heading = labels.get(status, "清理未完成")
    if result.get("error_code") in (225, 226):
        heading = "Windows 防护阻止清理，本次未完成"
    exited = result.get("terminal") is True and result.get("processExitVerified") is True
    if status == "succeeded" and not exited:
        heading = "清理步骤完成，正在核验后台退出"
    current_step = result.get("current_step", result.get("currentStep", result.get("step")))
    if status == "running" and current_step:
        heading += f"：{CLEANUP_STEP_NAMES.get(current_step, current_step)}"
    if exited:
        steps = [item for item in result.get("steps", {}).values() if isinstance(item, dict)]
        if steps:
            heading += f"，成功 {sum(item.get('ok') is True for item in steps)}/{len(steps)} 项"
        delta = result.get("available_increase")
        if isinstance(delta, (int, float)):
            heading += f"\n可用内存变化 {'+' if delta >= 0 else '-'}{format_bytes(abs(delta))}"
    if status not in {"running", "succeeded"} and result.get("message"):
        heading += f"\n{str(result['message'])[:160]}"
    return heading


def clamp_rect(rect: QRect, area: QRect) -> QRect:
    x = max(area.left(), min(rect.x(), area.right() - rect.width() + 1))
    y = max(area.top(), min(rect.y(), area.bottom() - rect.height() + 1))
    return QRect(x, y, rect.width(), rect.height())


def _metric(percent: float | None, available: int, total: int) -> tuple[str, str, str]:
    if percent is None or total <= 0:
        return "不可用", "不可用", "不可用"
    return f"{max(0.0, min(100.0, percent)):.0f}%", format_bytes(max(0, available)), format_bytes(total)


class MonitorButton(QPushButton):
    singleClicked = Signal()
    doubleClicked = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("性能与内存")
        self.setStyleSheet(
            "QPushButton { background: #f1fbff; border: 2px solid #92cce8; border-radius: 17px; color: #c28b43; }"
            "QPushButton:hover { background: #fff4d6; border-color: #d8aa61; }"
        )
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.singleClicked)
        self._cleaning = False
        self._suppress_release = False
        bind_theme(self, "button")

    def set_cleaning(self, value: bool) -> None:
        self._cleaning = value
        self.update()

    def cancel_pending_click(self) -> None:
        self._click_timer.stop()
        self._suppress_release = False
        self.setDown(False)

    def hideEvent(self, event) -> None:
        self.cancel_pending_click()
        super().hideEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            if self._suppress_release:
                self._suppress_release = False
                event.accept(); return
            self._click_timer.start(250)
            event.accept(); return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._click_timer.stop(); self._suppress_release = True
            self.doubleClicked.emit(); event.accept(); return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(self._theme["accent"]), 2.0))
        painter.drawLine(10, 21, 10, 12); painter.drawLine(7, 15, 10, 12); painter.drawLine(13, 15, 10, 12)
        painter.drawLine(24, 12, 24, 21); painter.drawLine(21, 18, 24, 21); painter.drawLine(27, 18, 24, 21)
        if self._cleaning:
            painter.setPen(QPen(QColor(self._theme["focus"]), 3.0)); painter.drawArc(4, 4, 26, 26, 20 * 16, 285 * 16)


class MonitorCapsule(QFrame):
    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setObjectName("capsule")
        self.setStyleSheet(
            "QFrame#capsule { background: transparent; border: none; }"
            "QLabel { color: #58483f; font-size: 10pt; padding: 0 3px; } QLabel#memory { color: #a6743f; }"
        )
        layout = QVBoxLayout(self); layout.setContentsMargins(8, 6, 8, 6); layout.setSpacing(1)
        self.memory = QLabel("内存 0%"); self.memory.setObjectName("memory")
        self.download = QLabel("↓ 0 B/s"); self.upload = QLabel("↑ 0 B/s")
        for label in (self.memory, self.download, self.upload): layout.addWidget(label)
        self.setFixedSize(116, 74)
        bind_theme(self, "capsule")

    def update_stats(self, network: NetworkSnapshot, memory: MemorySnapshot) -> None:
        self.memory.setText(f"内存 {memory.load_percent:.0f}%")
        self.download.setText(f"↓ {format_bytes(network.download_bps, True)}")
        self.upload.setText(f"↑ {format_bytes(network.upload_bps, True)}")
        metrics = QFontMetrics(self.memory.font())
        text_width = max(metrics.horizontalAdvance(label.text())
                         for label in (self.memory, self.download, self.upload))
        self.setFixedWidth(max(110, text_width + 24))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(self._theme["border"]), 2))
        painter.setBrush(QColor(self._theme["background"]))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)


class MemoryCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__(); self.setObjectName("sectionCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout = QGridLayout(self); layout.setContentsMargins(12, 7, 12, 7)
        layout.setHorizontalSpacing(12); layout.setVerticalSpacing(2)
        heading = QLabel(title); heading.setObjectName("section"); heading.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(heading, 0, 0, 1, 2)
        self.values: list[QLabel] = []
        for row, name in enumerate(("已用", "可用", "总计"), 1):
            key = QLabel(name); key.setObjectName("metricName")
            value = QLabel("不可用"); value.setObjectName("metricValue"); value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            key.setAttribute(Qt.WA_TransparentForMouseEvents); value.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(key, row, 0); layout.addWidget(value, row, 1); self.values.append(value)

    def set_values(self, values: tuple[str, str, str]) -> None:
        for label, value in zip(self.values, values): label.setText(value)


class DetailsPanel(QFrame):
    cleanRequested = Signal(); closeRequested = Signal()
    cancelRequested = Signal()
    dragStarted = Signal(QPoint); dragMoved = Signal(QPoint); dragFinished = Signal(QPoint)

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setObjectName("card"); self.setStyleSheet(PANEL_STYLE)
        self.setFixedWidth(380); self.resize(380, 550); self._dragging = False
        root = QVBoxLayout(self); root.setContentsMargins(15, 12, 15, 12); root.setSpacing(5)
        title_row = QHBoxLayout()
        title = QLabel("性能小管家"); title.setObjectName("title"); title.setAttribute(Qt.WA_TransparentForMouseEvents)
        hint = QLabel("拖动面板可移动桌宠"); hint.setObjectName("metricName"); hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        close = QPushButton("×"); close.setObjectName("close"); close.setAccessibleName("关闭详细面板"); close.setFixedSize(32, 32); close.clicked.connect(self.closeRequested)
        title_row.addWidget(title); title_row.addWidget(hint); title_row.addStretch(); title_row.addWidget(close); root.addLayout(title_row)

        self.content = QWidget(); self.content.setObjectName("monitorContent")
        self.content_layout = QVBoxLayout(self.content); self.content_layout.setContentsMargins(0,0,0,0); self.content_layout.setSpacing(5)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.viewport().setObjectName("monitorViewport")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.content); root.addWidget(self.scroll,1)

        network_card = QFrame(); network_card.setObjectName("sectionCard")
        network_card.setAttribute(Qt.WA_TransparentForMouseEvents)
        network_grid = QGridLayout(network_card); network_grid.setContentsMargins(12, 8, 12, 8)
        heading = QLabel("网络"); heading.setObjectName("section"); network_grid.addWidget(heading, 0, 0, 1, 4)
        self.net_down = QLabel(); self.net_up = QLabel(); self.net_total = QLabel(); self.adapters = QLabel(); self.adapters.setWordWrap(True)
        for widget in (heading, self.net_down, self.net_up, self.net_total, self.adapters): widget.setAttribute(Qt.WA_TransparentForMouseEvents)
        network_grid.addWidget(self.net_down, 1, 0, 1, 2); network_grid.addWidget(self.net_up, 1, 2, 1, 2)
        network_grid.addWidget(self.net_total, 2, 0, 1, 4); network_grid.addWidget(self.adapters, 3, 0, 1, 4); self.content_layout.addWidget(network_card)

        self.physical = MemoryCard("物理内存"); self.virtual = MemoryCard("虚拟内存（系统提交）")
        self.working_set = MemoryCard("系统工作集")
        self.content_layout.addWidget(self.physical); self.content_layout.addWidget(self.virtual); self.content_layout.addWidget(self.working_set)
        self.result = QLabel("尚未清理"); self.result.setObjectName("status"); self.result.setAlignment(Qt.AlignCenter)
        self.result.setWordWrap(True)
        self.result.setAttribute(Qt.WA_TransparentForMouseEvents); self.content_layout.addWidget(self.result)
        clean_row = QHBoxLayout()
        self.clean_button = QPushButton("立即清理内存")
        self.clean_button.clicked.connect(self.cleanRequested)
        self.cancel_button = QPushButton("取消本次清理")
        self.cancel_button.clicked.connect(self.cancelRequested)
        self.cancel_button.hide()
        clean_row.addWidget(self.clean_button); clean_row.addWidget(self.cancel_button)
        root.addLayout(clean_row)
        bind_theme(self)

    def _fit_contents(self):
        self.content_layout.invalidate()
        body_height = max(self.content.minimumSizeHint().height(), self.content_layout.totalHeightForWidth(self.width()-42))
        screen_height = self.screen().availableGeometry().height() if self.screen() else 1080
        self.setFixedHeight(min(max(510,body_height+112),max(280,screen_height-16)))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(self._theme["border"]), 2))
        painter.setBrush(QColor(self._theme["background"]))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 17, 17)

    def reset_drag(self) -> None:
        self._dragging = False

    def update_stats(self, network: NetworkSnapshot, memory: MemorySnapshot) -> None:
        self.net_down.setText(f"下载　{format_bytes(network.download_bps, True)}")
        self.net_up.setText(f"上传　{format_bytes(network.upload_bps, True)}")
        self.net_total.setText(f"本次累计　↓ {format_bytes(network.session_download)}　↑ {format_bytes(network.session_upload)}")
        active = sorted(network.adapters.items(), key=lambda item: sum(item[1]), reverse=True)
        self.adapters.setText(f"活动网卡　{active[0][0]}" if active else "活动网卡　暂无流量")
        self.physical.set_values(_metric(memory.load_percent, memory.physical_available, memory.physical_total))
        commit_available = max(0, memory.commit_limit - memory.commit_total)
        commit_percent = memory.commit_total * 100 / memory.commit_limit if memory.commit_limit > 0 else None
        self.virtual.set_values(_metric(commit_percent, commit_available, memory.commit_limit))
        cache_total = max(memory.system_cache_peak, memory.system_cache)
        cache_available = max(0, cache_total - memory.system_cache)
        cache_percent = memory.system_cache * 100 / cache_total if cache_total > 0 else None
        self.working_set.set_values(_metric(cache_percent, cache_available, cache_total))
        self._fit_contents()

    def show_result(self, result: dict) -> None:
        self.result.setText(cleanup_summary(result))
        self._fit_contents()
        self.result.setToolTip("\n".join(
            f"{CLEANUP_STEP_NAMES.get(name, name)}：{item.get('message', item.get('status', ''))}"
            for name, item in result.get("steps", {}).items() if isinstance(item, dict)))

    def set_cleaning(self, active: bool, *, cancelling: bool = False) -> None:
        self.clean_button.setEnabled(True)
        self.clean_button.setText("查看清理进度" if active else "立即清理内存")
        self.cancel_button.setVisible(active)
        self.cancel_button.setEnabled(active and not cancelling)



    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = True; self.dragStarted.emit(event.globalPosition().toPoint()); event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.dragMoved.emit(event.globalPosition().toPoint()); event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False; self.dragFinished.emit(event.globalPosition().toPoint()); event.accept(); return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not (QApplication.mouseButtons() & Qt.LeftButton):
            self._dragging = False
        super().leaveEvent(event)


def time_text(timestamp) -> str:
    from datetime import datetime
    try: return datetime.fromtimestamp(float(timestamp)).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError): return "未知时间"


class AutoCleanDialog(QDialog):
    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent); self.setWindowTitle("自动清理设置"); self.setFixedSize(350, 230); self.setStyleSheet(PANEL_STYLE)
        layout = QVBoxLayout(self); title = QLabel("定时清理内存"); title.setObjectName("title"); layout.addWidget(title)
        self.interval_enabled = ThemedCheckBox("按时间间隔清理"); self.interval_enabled.setChecked(settings.auto_clean_interval_enabled)
        self.interval = ThemedComboBox(); self.interval.addItems(["15 分钟", "30 分钟", "60 分钟", "120 分钟", "240 分钟"])
        self.interval.setCurrentText(f"{settings.auto_clean_interval_minutes} 分钟")
        row = QHBoxLayout(); row.addWidget(self.interval_enabled); row.addStretch(); row.addWidget(self.interval); layout.addLayout(row)
        self.memory_enabled = ThemedCheckBox("内存占用达到阈值"); self.memory_enabled.setChecked(settings.auto_clean_memory_enabled)
        self.memory = ThemedComboBox(); self.memory.addItems([f"{value}%" for value in range(60, 100, 5)])
        self.memory.setCurrentText(f"{settings.auto_clean_memory_percent}%")
        row = QHBoxLayout(); row.addWidget(self.memory_enabled); row.addStretch(); row.addWidget(self.memory); layout.addLayout(row)
        note = QLabel("两项同时开启时，满足任一条件即可触发；两次清理至少间隔 10 分钟。"); note.setWordWrap(True); layout.addWidget(note)
        buttons = QHBoxLayout(); buttons.addStretch(); cancel = QPushButton("取消"); cancel.clicked.connect(self.reject)
        save = QPushButton("保存"); save.clicked.connect(self.accept); buttons.addWidget(cancel); buttons.addWidget(save); layout.addLayout(buttons)
        bind_theme(self)

    def apply(self, settings) -> None:
        settings.auto_clean_interval_enabled = self.interval_enabled.isChecked()
        settings.auto_clean_interval_minutes = int(self.interval.currentText().split()[0])
        settings.auto_clean_memory_enabled = self.memory_enabled.isChecked()
        settings.auto_clean_memory_percent = int(self.memory.currentText().rstrip("%"))
