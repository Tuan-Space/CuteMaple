"""Nonmodal activity diagnostics and clearly labelled effect previews."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QVBoxLayout)
from monitor_ui import PANEL_STYLE


EFFECT_CHOICES = (
    ("枫叶", "leaf"), ("音符", "note"), ("眼镜", "glasses"),
    ("星光", "star"), ("爱心", "heart"), ("气泡", "bubble"),
    ("花瓣", "petal"),
    ("打字回应", "keyboard"), ("声音回应", "audio"),
)

INTERACTION_STYLE = PANEL_STYLE + """
QDialog#interactionPanel { background: #f4faff; color: #25465b; }
QDialog#interactionPanel QLabel { color: #25465b; }
QDialog#interactionPanel QPushButton { color: #25465b; background: #dff4ff; border-color: #7baac3; }
QDialog#interactionPanel QPushButton:hover { color: #493815; background: #fff1cc; border-color: #ba8e47; }
QDialog#interactionPanel QPushButton:pressed { color: #17384c; background: #b9def0; }
QDialog#interactionPanel QPushButton:focus { border: 2px solid #377a9e; }
QDialog#interactionPanel QPushButton:disabled { color: #526371; background: #e2e8ed; border-color: #bccad3; }
QDialog#interactionPanel QComboBox { color: #25465b; background: #ffffff; selection-color: #ffffff; selection-background-color: #326f94; }
QComboBox QAbstractItemView { color: #25465b; background: #ffffff; selection-color: #ffffff; selection-background-color: #326f94; }
QDialog#interactionPanel QProgressBar { color: #17384c; background: #ffffff; border: 1px solid #93b6ca; border-radius: 6px; text-align: center; }
QDialog#interactionPanel QProgressBar::chunk { background: #93c9e6; border-radius: 5px; }
"""


class InteractionPanel(QDialog):
    endpointSelected = Signal(str)
    retryAudioRequested = Signal()
    previewRequested = Signal(str)

    def __init__(self, endpoint: str = "default") -> None:
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setObjectName("interactionPanel")
        self.setWindowTitle("互动检测与效果预览")
        self.setStyleSheet(INTERACTION_STYLE)
        self.setMinimumWidth(390)
        self._endpoint = endpoint
        self._endpoint_inventory = None
        layout = QVBoxLayout(self)
        self.keyboard = QLabel("打字：等待检测")
        self.audio = QLabel("声音：等待检测")
        self.response = QLabel("等待桌面活动")
        for label in (self.keyboard, self.audio, self.response):
            label.setWordWrap(True)
            layout.addWidget(label)
        self.endpoint = QComboBox()
        self.endpoint.addItem("跟随系统默认输出", "default")
        self.endpoint.currentIndexChanged.connect(self._select_endpoint)
        layout.addWidget(self.endpoint)
        self.level = QProgressBar()
        self.level.setRange(0, 1000)
        self.level.setValue(0)
        self.level.setFormat("输出音量 0.0%")
        layout.addWidget(self.level)
        retry = QPushButton("重新连接声音检测")
        retry.clicked.connect(self.retryAudioRequested)
        layout.addWidget(retry)
        note = QLabel("以下按钮仅预览效果，不能证明已检测到打字或声音。")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.effects = QComboBox()
        for title, name in EFFECT_CHOICES:
            self.effects.addItem(title, name)
        preview = QPushButton("预览效果")
        preview.clicked.connect(lambda: self.previewRequested.emit(self.effects.currentData()))
        row.addWidget(self.effects)
        row.addWidget(preview)
        layout.addLayout(row)

    def _select_endpoint(self) -> None:
        self._endpoint = self.endpoint.currentData() or "default"
        self.endpointSelected.emit(self._endpoint)

    def set_audio_level(self, level: float) -> None:
        import math
        value = max(0.0, min(1.0, level)) if math.isfinite(level) else 0.0
        self.level.setValue(round(value * 1000))
        self.level.setFormat(f"输出音量 {value * 100:.1f}%")

    def update_status(self, status: dict, *, keyboard_enabled: bool, audio_enabled: bool,
                      suspended: bool, blocked: bool, reaction: str | None) -> None:
        keyboard, audio = status.get("keyboard", {}), status.get("audio", {})
        if not isinstance(keyboard, dict):
            keyboard = {}
        if not isinstance(audio, dict):
            audio = {}
        descriptions = {"installed": "已连接，等待实际输入", "waiting": "未收到活动",
                        "ready": "已连接，等待实际输入", "unavailable": "检测未连接",
                        "active": "已收到活动", "received": "已收到活动", "running": "检测中",
                        "idle": "未收到活动", "starting": "正在连接", "retrying": "正在重新连接",
                        "muted": "输出已静音",
                        "offline": "所选输出设备不可用", "error": "检测错误",
                        "disabled": "已关闭", "suspended": "已暂停", "stopped": "检测已停止"}
        for label, name, item, enabled in ((self.keyboard, "打字", keyboard, keyboard_enabled),
                                           (self.audio, "声音", audio, audio_enabled)):
            state = item.get("state", "waiting")
            if name == "打字" and state == "ready" and isinstance(item.get("lastActivityAt"), (int, float)):
                import time
                if 0 <= time.monotonic() - item["lastActivityAt"] <= 3:
                    state = "received"
            text = "已关闭" if not enabled else ("已暂停" if suspended else descriptions.get(state, str(state)))
            error = item.get("error")
            if error:
                text += f"：{str(error)[:220]}"
            if name == "声音" and item.get("endpointName"):
                text += f" · {item['endpointName']}"
            label.setText(f"{name}：{text}")
        self.response.setText("回应已暂停" if suspended else "暂被当前动作抑制" if blocked else
                              {"keyboard": "正在回应打字", "audio": "正在回应声音"}.get(reaction, "未收到可回应的活动"))
        endpoints = audio.get("endpoints", [])
        inventory = [(str(item.get("id")), str(item.get("name", "输出设备")))
                     for item in endpoints if isinstance(item, dict) and item.get("id")]
        if inventory != self._endpoint_inventory:
            self._endpoint_inventory = inventory
            self.endpoint.blockSignals(True)
            self.endpoint.clear()
            self.endpoint.addItem("跟随系统默认输出", "default")
            for identifier, name in inventory:
                if identifier != "default":
                    self.endpoint.addItem(name, identifier)
            index = self.endpoint.findData(self._endpoint)
            if index < 0:
                self.endpoint.addItem("所选设备（当前离线）", self._endpoint)
                index = self.endpoint.count() - 1
            self.endpoint.setCurrentIndex(index)
            self.endpoint.blockSignals(False)
