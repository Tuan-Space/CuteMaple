from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from resource_monitor import MemorySnapshot, NetworkSnapshot, format_bytes


PANEL_STYLE = """
QFrame#card { background: transparent; border: none; color: #554941; }
QLabel#title { color: #477c9d; font-size: 16pt; }
QLabel#section { color: #a9753f; font-size: 12pt; }
QFrame#sectionCard { background: #ffffff; border: 1px solid #c9e3f0; border-radius: 11px; }
QLabel#metricName { color: #6b777d; font-size: 10pt; }
QLabel#metricValue { color: #344b59; font-size: 11pt; }
QLabel#status { background: #eef8fd; border: 1px solid #d1e8f3; border-radius: 8px; color: #65747d; padding: 6px; }
QLabel { font-size: 11pt; }
QPushButton { background: #dff4ff; border: 1px solid #9bcde5; border-radius: 10px; padding: 7px 12px; }
QPushButton:hover { background: #fff1cc; border-color: #d7aa63; }
QCheckBox, QComboBox { font-size: 11pt; }
QComboBox { background: white; border: 1px solid #acd8ee; border-radius: 7px; padding: 4px 8px; }
"""


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

    def set_cleaning(self, value: bool) -> None:
        self._cleaning = value
        self.update()

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
        painter.setPen(QPen(self.palette().color(self.foregroundRole()), 2.0))
        painter.drawLine(10, 21, 10, 12); painter.drawLine(7, 15, 10, 12); painter.drawLine(13, 15, 10, 12)
        painter.drawLine(24, 12, 24, 21); painter.drawLine(21, 18, 24, 21); painter.drawLine(27, 18, 24, 21)
        if self._cleaning:
            painter.setPen(QPen(Qt.white, 3.0)); painter.drawArc(4, 4, 26, 26, 20 * 16, 285 * 16)


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

    def update_stats(self, network: NetworkSnapshot, memory: MemorySnapshot) -> None:
        self.memory.setText(f"内存 {memory.load_percent:.0f}%")
        self.download.setText(f"↓ {format_bytes(network.download_bps, True)}")
        self.upload.setText(f"↑ {format_bytes(network.upload_bps, True)}")
        metrics = QFontMetrics(self.memory.font())
        text_width = max(metrics.horizontalAdvance(label.text())
                         for label in (self.memory, self.download, self.upload))
        self.setFixedWidth(max(110, min(128, text_width + 24)))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#afd8ec"), 2))
        painter.setBrush(QColor("#fafdff"))
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
    dragStarted = Signal(QPoint); dragMoved = Signal(QPoint); dragFinished = Signal(QPoint)

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setObjectName("card"); self.setStyleSheet(PANEL_STYLE)
        self.setFixedSize(380, 510); self._dragging = False
        root = QVBoxLayout(self); root.setContentsMargins(15, 12, 15, 12); root.setSpacing(5)
        title_row = QHBoxLayout()
        title = QLabel("性能小管家"); title.setObjectName("title"); title.setAttribute(Qt.WA_TransparentForMouseEvents)
        hint = QLabel("拖动面板可移动桌宠"); hint.setObjectName("metricName"); hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        close = QPushButton("×"); close.setFixedSize(30, 30); close.clicked.connect(self.closeRequested)
        title_row.addWidget(title); title_row.addWidget(hint); title_row.addStretch(); title_row.addWidget(close); root.addLayout(title_row)

        network_card = QFrame(); network_card.setObjectName("sectionCard")
        network_card.setAttribute(Qt.WA_TransparentForMouseEvents)
        network_grid = QGridLayout(network_card); network_grid.setContentsMargins(12, 8, 12, 8)
        heading = QLabel("网络"); heading.setObjectName("section"); network_grid.addWidget(heading, 0, 0, 1, 4)
        self.net_down = QLabel(); self.net_up = QLabel(); self.net_total = QLabel(); self.adapters = QLabel(); self.adapters.setWordWrap(True)
        for widget in (heading, self.net_down, self.net_up, self.net_total, self.adapters): widget.setAttribute(Qt.WA_TransparentForMouseEvents)
        network_grid.addWidget(self.net_down, 1, 0, 1, 2); network_grid.addWidget(self.net_up, 1, 2, 1, 2)
        network_grid.addWidget(self.net_total, 2, 0, 1, 4); network_grid.addWidget(self.adapters, 3, 0, 1, 4); root.addWidget(network_card)

        self.physical = MemoryCard("物理内存"); self.virtual = MemoryCard("虚拟内存（系统提交）")
        self.working_set = MemoryCard("系统工作集")
        root.addWidget(self.physical); root.addWidget(self.virtual); root.addWidget(self.working_set)
        self.result = QLabel("尚未清理"); self.result.setObjectName("status"); self.result.setAlignment(Qt.AlignCenter)
        self.result.setAttribute(Qt.WA_TransparentForMouseEvents); root.addWidget(self.result)
        clean = QPushButton("立即清理内存"); clean.clicked.connect(self.cleanRequested); root.addWidget(clean)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#9ecfe7"), 2))
        painter.setBrush(QColor("#fafdff"))
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

    def show_result(self, result: dict) -> None:
        succeeded = sum(1 for item in result.get("steps", {}).values() if item.get("ok")); total = len(result.get("steps", {}))
        delta = int(result.get("available_increase", 0)); sign = "+" if delta >= 0 else "-"
        self.result.setText(f"{time_text(result.get('completed_at'))}　完成 {succeeded}/{total} 项　可用内存 {sign}{format_bytes(abs(delta))}")

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
        self.interval_enabled = QCheckBox("按时间间隔清理"); self.interval_enabled.setChecked(settings.auto_clean_interval_enabled)
        self.interval = QComboBox(); self.interval.addItems(["15 分钟", "30 分钟", "60 分钟", "120 分钟", "240 分钟"])
        self.interval.setCurrentText(f"{settings.auto_clean_interval_minutes} 分钟")
        row = QHBoxLayout(); row.addWidget(self.interval_enabled); row.addStretch(); row.addWidget(self.interval); layout.addLayout(row)
        self.memory_enabled = QCheckBox("内存占用达到阈值"); self.memory_enabled.setChecked(settings.auto_clean_memory_enabled)
        self.memory = QComboBox(); self.memory.addItems([f"{value}%" for value in range(60, 100, 5)])
        self.memory.setCurrentText(f"{settings.auto_clean_memory_percent}%")
        row = QHBoxLayout(); row.addWidget(self.memory_enabled); row.addStretch(); row.addWidget(self.memory); layout.addLayout(row)
        note = QLabel("两项同时开启时，满足任一条件即可触发；两次清理至少间隔 10 分钟。"); note.setWordWrap(True); layout.addWidget(note)
        buttons = QHBoxLayout(); buttons.addStretch(); cancel = QPushButton("取消"); cancel.clicked.connect(self.reject)
        save = QPushButton("保存"); save.clicked.connect(self.accept); buttons.addWidget(cancel); buttons.addWidget(save); layout.addLayout(buttons)

    def apply(self, settings) -> None:
        settings.auto_clean_interval_enabled = self.interval_enabled.isChecked()
        settings.auto_clean_interval_minutes = int(self.interval.currentText().split()[0])
        settings.auto_clean_memory_enabled = self.memory_enabled.isChecked()
        settings.auto_clean_memory_percent = int(self.memory.currentText().rstrip("%"))
