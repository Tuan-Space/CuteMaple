from __future__ import annotations

import ctypes
import concurrent.futures
import os
import random
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (QAction, QFont, QFontDatabase, QGuiApplication, QIcon,
                           QCursor, QMouseEvent, QPixmap, QRegion, QWheelEvent)
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon, QWidget

from pet_core import (ANIMATIONS, APP_NAME, SPRITE_GROUPS, PetSettings,
                      auto_cleanup_due, choose_dialogue, choose_happy_dialogue, load_settings,
                      resource_root, save_settings, set_autostart)
from memory_cleaner import (TASK_SCHEMA_VERSION, current_executable_path, is_process_elevated,
                            queue_cleanup, read_result, request_elevated_install, task_is_valid)
from monitor_ui import AutoCleanDialog, DetailsPanel, MonitorButton, MonitorCapsule, clamp_rect
from resource_monitor import NetworkSampler, format_bytes, memory_snapshot

DEFAULT_WINDOW_SIZE = 220
DRAG_THRESHOLD = 6
EDGE_SNAP_PX = 28
CLICK_DELAY_MS = 250
MIN_SCALE, MAX_SCALE = 0.6, 1.8
SLEEP_AFTER_SECONDS, SLEEP_FOR_SECONDS = 60.0, 240.0


class _LastInputInfo(ctypes.Structure):
    _fields_ = (("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint))


def get_system_idle_seconds() -> float:
    if os.name != "nt":
        return 0.0
    info = _LastInputInfo(cbSize=ctypes.sizeof(_LastInputInfo))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


class SpeechBubble(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet(
            "QLabel { background: rgba(255,255,255,245); color: #58483f; "
            "border: 2px solid #acd8ee; border-radius: 14px; padding: 10px 14px; "
            "font-size: 11pt; }"
        )
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_message(self, text: str, pet_rect: QRect, screen_rect: QRect) -> None:
        self.label.setText(text)
        self.label.setFixedWidth(230)
        self.label.adjustSize()
        self.resize(self.label.size())
        x = pet_rect.center().x() - self.width() // 2
        y = pet_rect.top() - self.height() - 8
        if y < screen_rect.top():
            y = pet_rect.bottom() + 8
        x = max(screen_rect.left(), min(x, screen_rect.right() - self.width() + 1))
        y = max(screen_rect.top(), min(y, screen_rect.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        self.raise_()
        self.hide_timer.start(4200)


class PetWindow(QWidget):
    def __init__(self, app: QApplication, settings: PetSettings) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.app, self.settings = app, settings
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(resource_root() / "assets" / "icon.png")))
        self.sprite = QLabel(self)
        self.sprite.setAlignment(Qt.AlignCenter)
        self.sprite.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.frames = self._load_frames()
        self.scaled_frames: dict[str, list[QPixmap]] = {}
        self.state_content_bounds: dict[str, QRect] = {}
        self.state, self.frame_index, self.animation_cycles = "idle", 0, 0
        self.base_mode, self.resume_base, self.attachment = "ground", "ground", None
        self.motion_mode: str | None = None
        self.direction, self.climb_direction = 1, -1
        self.walk_origin_x = self.walk_target_x = 0
        self.walk_leg_start_x = self.walk_leg_end_x = 0
        self.walk_leg_started_at = 0.0
        self.walk_leg_duration_ms = 1000
        self.dragging = self.suppress_release_click = self.consume_wake_click = False
        self.press_global: QPoint | None = None
        self.press_window: QPoint | None = None
        self.last_drag_global: QPoint | None = None
        self.pet_trace: list[tuple[float, int]] = []
        self.last_petting_at = 0.0
        self.current_screen = QGuiApplication.primaryScreen()
        self.bubble = SpeechBubble()
        self.sleep_phase: str | None = None
        self.sleep_started_at: float | None = None
        self.awake_cycle_started = time.monotonic()
        self.network_sampler = NetworkSampler()
        self.latest_network = self.network_sampler.sample()
        self.latest_memory = memory_snapshot()
        self.cleanup_operation: str | None = None
        self.cleanup_started_at = 0.0
        self.cleanup_finish_pending = False
        self.cleanup_result: dict | None = None
        self.auto_clean_cycle_started = time.time()
        self.panel_pause_active = False
        self.panel_previous_paused = False
        self._panel_side: str | None = None
        self._panel_dragging = False
        self._panel_positioning = False
        self._overlay_side: str | None = None
        self._install_pending_cleanup = False
        self._install_future = None
        self._install_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="clean-auth")

        self.animation_timer = QTimer(self)
        self.animation_timer.setSingleShot(True)
        self.animation_timer.timeout.connect(self._advance_frame)
        self.motion_timer = QTimer(self)
        self.motion_timer.setInterval(35)
        self.motion_timer.timeout.connect(self._motion_step)
        self.behavior_timer = QTimer(self)
        self.behavior_timer.setSingleShot(True)
        self.behavior_timer.timeout.connect(self._choose_behavior)
        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(self._finish_temporary_state)
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self._single_click)
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(1000)
        self.idle_timer.timeout.connect(self._check_system_idle)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(2000)
        self.monitor_timer.timeout.connect(self._refresh_monitor)
        self.monitor_hide_timer = QTimer(self)
        self.monitor_hide_timer.setSingleShot(True)
        self.monitor_hide_timer.timeout.connect(self._hide_monitor_if_allowed)
        self.cleanup_poll_timer = QTimer(self)
        self.cleanup_poll_timer.setInterval(400)
        self.cleanup_poll_timer.timeout.connect(self._poll_cleanup)
        self.install_poll_timer = QTimer(self)
        self.install_poll_timer.setInterval(250)
        self.install_poll_timer.timeout.connect(self._poll_clean_task_install)

        self.tray = self._create_tray()
        self.monitor_button = MonitorButton()
        self.monitor_button.singleClicked.connect(self.open_details_panel)
        self.monitor_button.doubleClicked.connect(self.start_memory_cleanup)
        self.monitor_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.monitor_button.customContextMenuRequested.connect(self._show_monitor_menu)
        self.monitor_button.installEventFilter(self)
        self.monitor_button.hide()
        self.monitor_capsule = MonitorCapsule()
        self.monitor_capsule.installEventFilter(self)
        self.details_panel = DetailsPanel()
        self.details_panel.cleanRequested.connect(self.start_memory_cleanup)
        self.details_panel.closeRequested.connect(self.close_details_panel)
        self.details_panel.dragStarted.connect(self._panel_drag_start)
        self.details_panel.dragMoved.connect(self._panel_drag_move)
        self.details_panel.dragFinished.connect(self._panel_drag_finish)
        self._apply_size(False)
        self._restore_position()
        self.start_state("idle")
        self._schedule_behavior()
        self.idle_timer.start()
        self.monitor_timer.start()
        QTimer.singleShot(1400, self._offer_clean_task_install)

    def _load_frames(self) -> dict[str, list[QPixmap]]:
        folder = resource_root() / "assets" / "sprites_v2"
        result = {state: [QPixmap(str(folder / name)) for name in names]
                  for state, names in SPRITE_GROUPS.items()}
        if any(frame.isNull() for frames in result.values() for frame in frames):
            raise RuntimeError("无法载入桌宠动画素材")
        return result

    def _rebuild_scaled_frames(self) -> None:
        self.scaled_frames = {
            state: [frame.scaled(self.sprite.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    for frame in frames] for state, frames in self.frames.items()
        }
        self.state_content_bounds = {}
        for state, frames in self.scaled_frames.items():
            combined = QRect()
            for frame in frames:
                frame_bounds = QRegion(frame.mask()).boundingRect()
                combined = frame_bounds if combined.isNull() else combined.united(frame_bounds)
            self.state_content_bounds[state] = combined if not combined.isNull() else self.rect()

    def _apply_size(self, preserve_anchor: bool = True) -> None:
        old = self.geometry()
        side = round(DEFAULT_WINDOW_SIZE * self.settings.scale)
        self.setFixedSize(side, side)
        self.sprite.setGeometry(0, 0, side, side)
        self._rebuild_scaled_frames()
        if preserve_anchor and old.isValid():
            if self.base_mode.startswith("climb_"):
                self.move(self.x(), old.center().y() - side // 2)
                self._position_side(self.base_mode.removeprefix("climb_"))
            elif self.base_mode == "top_swing":
                self.move(old.center().x() - side // 2, self.y())
                self._position_top()
            else:
                self.move(old.center().x() - side // 2, old.bottom() - side + 1)
                self._clamp_to_current_screen()
        self._render_frame()

    def set_scale(self, scale: float) -> None:
        self.settings.scale = round(max(MIN_SCALE, min(MAX_SCALE, scale)), 2)
        self._apply_size()
        self._save_position()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.ControlModifier:
            self.set_scale(self.settings.scale + (0.1 if event.angleDelta().y() > 0 else -0.1))
            event.accept()
        else:
            super().wheelEvent(event)

    def _screen_for_point(self, p: QPoint):
        return QGuiApplication.screenAt(p) or QGuiApplication.primaryScreen()

    def _screen_for_rect(self, rect: QRect):
        def overlap_area(screen) -> int:
            overlap = screen.availableGeometry().intersected(rect)
            return max(0, overlap.width()) * max(0, overlap.height())
        return max(QGuiApplication.screens(), key=overlap_area, default=QGuiApplication.primaryScreen())

    def _restore_position(self) -> None:
        if self.settings.last_x is not None and self.settings.last_y is not None:
            proposed = QRect(self.settings.last_x, self.settings.last_y, self.width(), self.height())
            if any(s.availableGeometry().intersects(proposed) for s in QGuiApplication.screens()):
                self.move(proposed.topLeft())
                self.current_screen = self._screen_for_rect(proposed)
                self._clamp_to_current_screen()
                return
        area = self.current_screen.availableGeometry()
        self.move(area.right() - self.width() - 40, area.bottom() - self.height() + 1)

    def _create_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(QIcon(str(resource_root() / "assets" / "icon.png")), self.app)
        tray.setToolTip(APP_NAME)
        tray.setContextMenu(self._create_context_menu())
        tray.activated.connect(lambda reason: self.show_pet() if reason in
                               (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger) else None)
        tray.show()
        return tray

    def _create_context_menu(self) -> QMenu:
        menu = QMenu()
        visibility = QAction(menu)
        visibility.setObjectName("visibility_action")
        visibility.triggered.connect(self.toggle_visibility)
        menu.addAction(visibility)

        pause = QAction(menu)
        pause.setObjectName("pause_action")
        pause.triggered.connect(self.toggle_pause)
        menu.addAction(pause)
        menu.addSeparator()

        sizes = menu.addMenu("大小")
        sizes.addAction("小（165）", lambda _=False: self.set_scale(.75))
        sizes.addAction("默认（220）", lambda _=False: self.set_scale(1.0))
        sizes.addAction("大（300）", lambda _=False: self.set_scale(300 / DEFAULT_WINDOW_SIZE))
        sizes.addSeparator()
        sizes.addAction("恢复默认大小", self.restore_default_size)

        autostart = QAction("开机自动启动", menu)
        autostart.setObjectName("autostart_action")
        autostart.setCheckable(True)
        autostart.toggled.connect(self.toggle_autostart)
        menu.addAction(autostart)
        performance = menu.addMenu("性能与内存")
        always = QAction("始终显示性能监控", menu)
        always.setObjectName("monitor_always_action")
        always.setCheckable(True)
        always.toggled.connect(self.toggle_monitor_always)
        performance.addAction(always)
        details = performance.addAction("打开详细面板", self.open_details_panel)
        details.setObjectName("details_action")
        performance.addAction("立即清理内存", self.start_memory_cleanup)
        repair = performance.addAction("修复清理授权…", self.repair_clean_authorization)
        repair.setObjectName("repair_clean_action")
        performance.addAction("自动清理设置…", self.open_auto_clean_settings)
        menu.addSeparator()
        menu.addAction("退出", self.quit_app)

        menu.aboutToShow.connect(lambda current=menu: self._sync_context_menu(current))
        self._sync_context_menu(menu)
        return menu

    def _sync_context_menu(self, menu: QMenu) -> None:
        menu.findChild(QAction, "visibility_action").setText(
            "隐藏美腻枫" if self.isVisible() else "显示美腻枫"
        )
        pause = menu.findChild(QAction, "pause_action")
        pause.setText("详细面板打开（活动已暂停）" if self.panel_pause_active else
                      ("继续活动" if self.settings.paused else "暂停活动"))
        pause.setEnabled(not self.panel_pause_active)
        autostart = menu.findChild(QAction, "autostart_action")
        autostart.blockSignals(True)
        autostart.setChecked(self.settings.autostart)
        autostart.blockSignals(False)
        always = menu.findChild(QAction, "monitor_always_action")
        if always:
            always.blockSignals(True)
            always.setChecked(self.settings.monitor_always_visible)
            always.blockSignals(False)
        details = menu.findChild(QAction, "details_action")
        if details:
            shown = hasattr(self, "details_panel") and self.details_panel.isVisible()
            details.setText("关闭详细面板" if shown else "打开详细面板")
        repair = menu.findChild(QAction, "repair_clean_action")
        if repair:
            repair.setEnabled(not is_process_elevated() and self._install_future is None)

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide_pet()
        else:
            self.show_pet()

    @property
    def activity_paused(self) -> bool:
        return self.settings.paused or self.panel_pause_active

    def start_state(self, state: str, duration_ms: int = 0) -> None:
        if state not in self.frames:
            return
        self.state_timer.stop()
        self.animation_timer.stop()
        self.state, self.frame_index, self.animation_cycles = state, 0, 0
        self._overlay_side = None
        self._render_frame()
        self._arm_animation()
        if duration_ms:
            self.state_timer.start(duration_ms)

    def _arm_animation(self) -> None:
        if self.activity_paused and not self.state.startswith("clean_"):
            return
        self.animation_timer.start(ANIMATIONS[self.state].delays_ms[self.frame_index])

    def _advance_frame(self) -> None:
        spec = ANIMATIONS[self.state]
        last = self.frame_index == len(self.frames[self.state]) - 1
        if last and spec.playback == "one_shot":
            self._complete_animation()
            return
        if last:
            minimum_cycles = 2 if self.state == "clean_ground" else 1
            if self.state.startswith("clean_") and self.cleanup_finish_pending and \
                    self.animation_cycles + 1 >= minimum_cycles:
                self._finish_cleanup_animation()
                return
            self.frame_index = 0
            self.animation_cycles += 1
            if spec.playback == "counted_loop" and self.animation_cycles >= spec.cycles:
                self._complete_animation()
                return
        else:
            self.frame_index += 1
        self._render_frame()
        self._arm_animation()

    def _complete_animation(self) -> None:
        completed = self.state
        if completed == "swing_cycle":
            self.start_state("swing_idle")
        elif completed == "land":
            self._set_ground_idle()
        elif completed == "sleep_enter":
            self.sleep_phase, self.sleep_started_at = "loop", time.monotonic()
            self.start_state("sleep_loop")
        elif completed == "sleep_exit":
            self.sleep_phase, self.sleep_started_at = None, None
            self.awake_cycle_started = time.monotonic()
            self._set_ground_idle()
        elif completed in ("happy", "petting"):
            self._resume_base_state()

    def _render_frame(self) -> None:
        if self.scaled_frames and self.state in self.scaled_frames:
            self.sprite.setPixmap(self.scaled_frames[self.state][self.frame_index])
            if self.base_mode in ("climb_left", "climb_right") and self.state == self.base_mode:
                self._position_side(self.base_mode.removeprefix("climb_"))
        if hasattr(self, "monitor_button"):
            self._position_monitor_button()
            self._position_monitor_overlays()

    def _set_base(self, mode: str) -> None:
        self.base_mode = mode
        self.attachment = {"climb_left": "left", "climb_right": "right", "top_swing": "top"}.get(mode)

    def _begin_walk(self, _duration_ms: int | None = None) -> None:
        if self.activity_paused or self.dragging or self.base_mode != "ground" or self.sleep_phase:
            return
        area = self.current_screen.availableGeometry()
        low, high = area.left(), area.right() - self.width() + 1
        self.walk_origin_x = max(low, min(self.x(), high))
        distance = max(1, round(90 * self.settings.scale))
        left, right = self.walk_origin_x - low, high - self.walk_origin_x
        if max(left, right) < 2:
            return self._set_ground_idle()
        viable = [direction for direction, room in ((-1, left), (1, right)) if room >= 2]
        fully_viable = [direction for direction, room in ((-1, left), (1, right)) if room >= distance]
        self.direction = random.choice(fully_viable or viable)
        limit = high if self.direction > 0 else low
        self.walk_target_x = self.walk_origin_x + self.direction * min(distance, abs(limit - self.walk_origin_x))
        self.walk_leg_start_x = self.walk_origin_x
        self.walk_leg_end_x = self.walk_target_x
        self.walk_leg_started_at = time.monotonic()
        self.walk_leg_duration_ms = max(1, int(_duration_ms or 1000))
        self.motion_mode = "walk_out"
        self.start_state("walk_right" if self.direction > 0 else "walk_left")
        self.motion_timer.start()

    def _motion_step(self) -> None:
        if self.activity_paused or self.dragging:
            return
        area = self.current_screen.availableGeometry()
        if self.motion_mode in ("walk_out", "walk_back"):
            elapsed_ms = max(0.0, (time.monotonic() - self.walk_leg_started_at) * 1000.0)
            progress = min(1.0, elapsed_ms / self.walk_leg_duration_ms)
            x = round(self.walk_leg_start_x + (self.walk_leg_end_x - self.walk_leg_start_x) * progress)
            self.move(x, area.bottom() - self.height() + 1)
            if progress >= 1.0:
                if self.motion_mode == "walk_out":
                    self.motion_mode, self.direction = "walk_back", -self.direction
                    self.walk_leg_start_x = self.walk_target_x
                    self.walk_leg_end_x = self.walk_origin_x
                    self.walk_leg_started_at = time.monotonic()
                    self.start_state("walk_right" if self.direction > 0 else "walk_left")
                else:
                    self.motion_mode = None
                    self.motion_timer.stop()
                    self._set_ground_idle()
        elif self.motion_mode == "climb":
            y = self.y() + self.climb_direction * max(1, round(self.settings.scale * 2))
            if y <= area.top():
                return self._attach_top(True)
            floor = area.bottom() - self.height() + 1
            if y >= floor:
                y, self.climb_direction = floor, -1
            self.move(self.x(), y)
        elif self.motion_mode == "fall":
            floor = area.bottom() - self.height() + 1
            y = self.y() + max(5, round(self.settings.scale * 9))
            if y >= floor:
                self.move(self.x(), floor)
                self.motion_mode = None
                self.motion_timer.stop()
                self.start_state("land")
            else:
                self.move(self.x(), y)

    def _schedule_behavior(self) -> None:
        if not self.activity_paused and self.isVisible() and self.base_mode == "ground" and not self.sleep_phase:
            self.behavior_timer.start(random.randint(60_000, 90_000))

    def _choose_behavior(self) -> None:
        if self.activity_paused or self.dragging or not self.isVisible() or self.motion_mode or \
                self.base_mode != "ground" or self.sleep_phase:
            return
        now = time.monotonic()
        if get_system_idle_seconds() >= SLEEP_AFTER_SECONDS and \
                now - self.awake_cycle_started >= SLEEP_AFTER_SECONDS:
            self._check_system_idle()
            if not self.sleep_phase:
                self._schedule_behavior()
            return
        self._begin_walk()

    def _set_ground_idle(self) -> None:
        self._set_base("ground")
        self.resume_base = "ground"
        self.motion_mode = None
        self.motion_timer.stop()
        area = self.current_screen.availableGeometry()
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)), area.bottom() - self.height() + 1)
        self.start_state("idle")
        self._schedule_behavior()

    def _start_temporary(self, state: str, duration_ms: int = 0) -> None:
        self.resume_base = self.base_mode
        self.behavior_timer.stop()
        self.motion_timer.stop()
        self.motion_mode = None
        self.start_state(state, duration_ms)

    def _finish_temporary_state(self) -> None:
        if not self.dragging:
            self._resume_base_state()

    def _resume_base_state(self) -> None:
        self.motion_mode = None
        self.motion_timer.stop()
        if self.resume_base == "climb_left":
            self._attach_side("left")
        elif self.resume_base == "climb_right":
            self._attach_side("right")
        elif self.resume_base == "top_swing":
            self._set_base("top_swing")
            self._position_top()
            self.start_state("swing_idle")
        else:
            self._set_ground_idle()

    def say(self, text: str) -> None:
        self._start_temporary("talk", 3200)
        self.bubble.show_message(text, self.geometry(), self.current_screen.availableGeometry())

    def _single_click(self) -> None:
        if not self.activity_paused and not self.dragging and not self.sleep_phase:
            self.say(choose_dialogue())

    def _begin_wake(self) -> None:
        if self.sleep_phase in ("enter", "loop"):
            self.behavior_timer.stop()
            self.sleep_phase = "exit"
            self.start_state("sleep_exit")

    def _check_system_idle(self) -> None:
        if self.activity_paused or self.dragging or not self.isVisible():
            return
        now, idle = time.monotonic(), get_system_idle_seconds()
        if self.sleep_phase in ("enter", "loop"):
            expired = self.sleep_started_at is not None and now - self.sleep_started_at >= SLEEP_FOR_SECONDS
            if idle < 1.5 or expired:
                self._begin_wake()
            return
        if self.sleep_phase == "exit":
            return
        area = self.current_screen.availableGeometry()
        on_floor = abs(self.y() - (area.bottom() - self.height() + 1)) <= 2
        if self.base_mode == "ground" and on_floor and not self.motion_mode and \
                idle >= SLEEP_AFTER_SECONDS and now - self.awake_cycle_started >= SLEEP_AFTER_SECONDS:
            self.behavior_timer.stop()
            self.sleep_phase = "enter"
            self.start_state("sleep_enter")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.press_global = event.globalPosition().toPoint()
            self.press_window = self.pos()
            self.last_drag_global = self.press_global
            self.dragging = False
            self.consume_wake_click = self.sleep_phase is not None
            if self.consume_wake_click:
                self._begin_wake()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.press_global is not None and event.buttons() & Qt.LeftButton:
            current, delta = event.globalPosition().toPoint(), event.globalPosition().toPoint() - self.press_global
            if not self.dragging and delta.manhattanLength() >= DRAG_THRESHOLD:
                self.dragging = True
                self.click_timer.stop()
                self._detach_for_drag()
            if self.dragging and self.press_window is not None:
                dx = current.x() - (self.last_drag_global.x() if self.last_drag_global else current.x())
                if abs(dx) >= 2:
                    self._flip_panel_for_drag(dx)
                    if self.state != (wanted := "drag_right" if dx > 0 else "drag_left"):
                        self.start_state(wanted)
                self.move(self.press_window + delta)
                self.last_drag_global = current
            event.accept()
        else:
            if self._pointer_near_sprite(event.position().toPoint()):
                self._show_monitor_button()
            else:
                self.monitor_hide_timer.start(800)
            self._track_petting(event.position().toPoint(), event.buttons())
            super().mouseMoveEvent(event)

    def _detach_for_drag(self) -> None:
        self.behavior_timer.stop(); self.state_timer.stop(); self.motion_timer.stop()
        self.motion_mode = self.sleep_phase = self.sleep_started_at = None
        self.awake_cycle_started = time.monotonic()
        self._set_base("ground")

    def _track_petting(self, local: QPoint, buttons: Qt.MouseButton) -> None:
        if buttons != Qt.NoButton or local.y() > self.height() * .46 or self.activity_paused or self.sleep_phase:
            self.pet_trace.clear(); return
        now = time.monotonic()
        if now - self.last_petting_at < 6:
            return
        self.pet_trace.append((now, local.x()))
        self.pet_trace = [(t, x) for t, x in self.pet_trace if now - t <= 1.5]
        xs = [x for _, x in self.pet_trace]
        movement = sum(abs(b - a) for a, b in zip(xs, xs[1:]))
        signs = [1 if b > a else -1 for a, b in zip(xs, xs[1:]) if abs(b - a) >= 3]
        if len(xs) >= 4 and movement >= 80 * self.settings.scale and sum(a != b for a, b in zip(signs, signs[1:])) >= 2:
            self.last_petting_at = now
            self.pet_trace.clear()
            self._start_temporary("petting")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self.press_global is not None:
            was_dragging = self.dragging
            self.press_global = self.press_window = self.last_drag_global = None
            self.dragging = False
            if was_dragging:
                self.current_screen = self._screen_for_point(event.globalPosition().toPoint())
                self._settle_after_drag(); self._save_position(); self._panel_side = None
                if self.details_panel.isVisible(): self._position_details_panel()
            elif self.consume_wake_click:
                self.consume_wake_click = False
            elif self.suppress_release_click:
                self.suppress_release_click = False
            else:
                self.click_timer.start(CLICK_DELAY_MS)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _settle_after_drag(self) -> None:
        area = self.current_screen.availableGeometry()
        if self.y() <= area.top() + EDGE_SNAP_PX:
            self._attach_top(True)
        elif self.x() <= area.left() + EDGE_SNAP_PX:
            self._attach_side("left")
        elif self.x() + self.width() >= area.right() - EDGE_SNAP_PX:
            self._attach_side("right")
        elif self.y() + self.height() < area.bottom() - 3:
            self._set_base("ground")
            self.motion_mode = "fall"
            self.start_state("fall_float")
            if not self.activity_paused:
                self.motion_timer.start()
        else:
            self._set_ground_idle()

    def _position_side(self, side: str) -> None:
        area = self.current_screen.availableGeometry()
        state = f"climb_{side}"
        anchors = ANIMATIONS[state].contact_anchors
        index = self.frame_index if self.state == state else 0
        contact = round(anchors[index % len(anchors)] * self.width() / 512)
        x = area.left() - contact if side == "left" else area.right() - contact
        self.move(x, max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))

    def _attach_side(self, side: str) -> None:
        self._set_base(f"climb_{side}")
        self.resume_base, self.motion_mode = self.base_mode, "climb"
        self._position_side(side)
        self.start_state(f"climb_{side}")
        if not self.activity_paused:
            self.motion_timer.start()

    def _position_top(self) -> None:
        area = self.current_screen.availableGeometry()
        margin = round(ANIMATIONS["swing_idle"].contact_margin * self.height())
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)), area.top() - margin)

    def _attach_top(self, run_intro: bool = True) -> None:
        self._set_base("top_swing")
        self.resume_base, self.motion_mode = self.base_mode, None
        self.motion_timer.stop()
        self._position_top()
        self.start_state("swing_cycle" if run_intro else "swing_idle")

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.click_timer.stop(); self.suppress_release_click = True
            if self.activity_paused:
                pass
            elif self.sleep_phase:
                self._begin_wake()
            else:
                self._start_temporary("happy")
                self.bubble.show_message(choose_happy_dialogue(), self.geometry(), self.current_screen.availableGeometry())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = self._create_context_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()

    def leaveEvent(self, event) -> None:
        self.monitor_hide_timer.start(800)
        super().leaveEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched in (getattr(self, "monitor_button", None), getattr(self, "monitor_capsule", None)):
            if event.type() in (QEvent.Enter, QEvent.MouseMove):
                self.monitor_hide_timer.stop()
                self.monitor_button.show()
                if not self.details_panel.isVisible():
                    self.monitor_capsule.show()
                self._position_monitor_overlays()
            elif event.type() == QEvent.Leave:
                self.monitor_hide_timer.start(800)
        return super().eventFilter(watched, event)

    def _pointer_near_sprite(self, point: QPoint) -> bool:
        if not self.scaled_frames or self.state not in self.scaled_frames:
            return False
        pixmap = self.scaled_frames[self.state][self.frame_index]
        image = pixmap.toImage()
        ox, oy = (self.width() - image.width()) // 2, (self.height() - image.height()) // 2
        px, py = point.x() - ox, point.y() - oy
        radius = max(8, round(20 * self.settings.scale))
        for y in range(max(0, py - radius), min(image.height(), py + radius + 1), 4):
            for x in range(max(0, px - radius), min(image.width(), px + radius + 1), 4):
                if (x - px) ** 2 + (y - py) ** 2 <= radius ** 2 and image.pixelColor(x, y).alpha() > 24:
                    return True
        return False

    def _show_monitor_button(self) -> None:
        if not self.isVisible():
            return
        self.monitor_hide_timer.stop()
        self.monitor_button.show(); self.monitor_button.raise_()
        self._position_monitor_button()

    def _hide_monitor_if_allowed(self) -> None:
        if self.details_panel.isVisible():
            self.monitor_capsule.hide()
            return
        if self.settings.monitor_always_visible:
            self.monitor_button.hide(); self.monitor_capsule.show()
            self._position_monitor_overlays()
            return
        self.monitor_button.hide(); self.monitor_capsule.hide()

    def _position_monitor_button(self) -> None:
        self._position_monitor_overlays()

    def _content_rect_global(self) -> QRect:
        bounds = self.state_content_bounds.get(self.state, self.rect())
        return bounds.translated(self.pos())

    @staticmethod
    def _rect_inside(rect: QRect, area: QRect) -> bool:
        return (rect.left() >= area.left() and rect.right() <= area.right() and
                rect.top() >= area.top() and rect.bottom() <= area.bottom())

    def _preferred_overlay_side(self, content: QRect, area: QRect) -> str:
        if self.details_panel.isVisible() and self._panel_side:
            return "left" if self._panel_side == "right" else "right"
        with_button = self.monitor_button.isVisible()
        with_capsule = self.monitor_capsule.isVisible() and not self.details_panel.isVisible()
        _, _, right_cluster = self._side_overlay_rects("right", content, with_button, with_capsule)
        return "right" if self._rect_inside(right_cluster, area) else "left"

    def _side_overlay_rects(self, side: str, content: QRect, with_button: bool,
                            with_capsule: bool) -> tuple[QRect | None, QRect | None, QRect]:
        gap, item_gap = 6, 5
        button_size = self.monitor_button.size()
        capsule_size = self.monitor_capsule.size()
        height = max(button_size.height() if with_button else 0,
                     capsule_size.height() if with_capsule else 0)
        top = content.center().y() - height // 2
        button_rect = capsule_rect = None
        if side == "right":
            cursor = content.right() + gap + 1
            if with_button:
                button_rect = QRect(cursor, top + (height - button_size.height()) // 2,
                                    button_size.width(), button_size.height())
                cursor = button_rect.right() + item_gap + 1
            if with_capsule:
                capsule_rect = QRect(cursor, top + (height - capsule_size.height()) // 2,
                                     capsule_size.width(), capsule_size.height())
            outer_right = (capsule_rect or button_rect).right()
            cluster = QRect(content.right() + 1, top, outer_right - content.right(), height)
        else:
            cursor = content.left() - gap
            if with_button:
                button_rect = QRect(cursor - button_size.width(), top + (height - button_size.height()) // 2,
                                    button_size.width(), button_size.height())
                cursor = button_rect.left() - item_gap
            if with_capsule:
                capsule_rect = QRect(cursor - capsule_size.width(), top + (height - capsule_size.height()) // 2,
                                     capsule_size.width(), capsule_size.height())
            outer_left = (capsule_rect or button_rect).left()
            cluster = QRect(outer_left, top, content.left() - outer_left, height)
        return button_rect, capsule_rect, cluster

    def _position_monitor_overlays(self) -> None:
        if not hasattr(self, "monitor_button"):
            return
        area = self.current_screen.availableGeometry()
        content = self._content_rect_global()
        panel_visible = self.details_panel.isVisible()
        if panel_visible:
            self.monitor_capsule.hide()
        with_button = self.monitor_button.isVisible() or panel_visible
        with_capsule = self.monitor_capsule.isVisible() and not panel_visible
        if not with_button and not with_capsule:
            return
        if not panel_visible:
            button_rect = capsule_rect = None
            button_side = "right"
            if with_button:
                _, _, right_cluster = self._side_overlay_rects("right", content, True, False)
                button_side = "right" if self._rect_inside(right_cluster, area) else "left"
                button_rect, _, _ = self._side_overlay_rects(button_side, content, True, False)
            if with_capsule:
                same_side = button_side if with_button else self._preferred_overlay_side(content, area)
                same_button, same_capsule, same_cluster = self._side_overlay_rects(
                    same_side, content, with_button, True)
                if self._rect_inside(same_cluster, area):
                    button_rect, capsule_rect = same_button, same_capsule
                else:
                    other = "left" if same_side == "right" else "right"
                    _, other_capsule, other_cluster = self._side_overlay_rects(other, content, False, True)
                    capsule_rect = (other_capsule if self._rect_inside(other_cluster, area)
                                    else clamp_rect(other_capsule, area))
            self._overlay_side = button_side
            if button_rect is not None:
                self.monitor_button.setGeometry(button_rect)
            if capsule_rect is not None:
                self.monitor_capsule.setGeometry(capsule_rect)
            return
        preferred = ((self._overlay_side or self._preferred_overlay_side(content, area))
                     if panel_visible else self._preferred_overlay_side(content, area))
        sides = (preferred, "left" if preferred == "right" else "right")
        panel_rect = self.details_panel.geometry() if panel_visible else QRect()
        chosen = None
        for side in sides:
            button_rect, capsule_rect, cluster = self._side_overlay_rects(
                side, content, with_button, with_capsule)
            occupied = [rect for rect in (button_rect, capsule_rect) if rect is not None]
            if self._rect_inside(cluster, area) and not any(rect.intersects(panel_rect) for rect in occupied):
                chosen = side, button_rect, capsule_rect
                break
        if chosen is None and with_button:
            size = self.monitor_button.size(); gap = 6
            alternatives = (
                QRect(content.center().x() - size.width() // 2, content.top() - gap - size.height(),
                      size.width(), size.height()),
                QRect(content.center().x() - size.width() // 2, content.bottom() + gap + 1,
                      size.width(), size.height()),
            )
            button_rect = next((rect for rect in alternatives if self._rect_inside(rect, area) and
                                not rect.intersects(panel_rect)), clamp_rect(alternatives[0], area))
            chosen = preferred, button_rect, None
        if chosen is None:
            _, capsule_rect, _ = self._side_overlay_rects(preferred, content, False, True)
            chosen = preferred, None, clamp_rect(capsule_rect, area)
        self._overlay_side, button_rect, capsule_rect = chosen
        if button_rect is not None:
            self.monitor_button.setGeometry(button_rect)
        if capsule_rect is not None:
            self.monitor_capsule.setGeometry(capsule_rect)

    def _show_monitor_menu(self, _point) -> None:
        menu = self._create_context_menu()
        menu.exec(self.monitor_button.mapToGlobal(self.monitor_button.rect().bottomLeft()))
        menu.deleteLater()

    def toggle_monitor_always(self, enabled: bool) -> None:
        self.settings.monitor_always_visible = enabled
        if enabled:
            self._show_monitor_button()
            if not self.details_panel.isVisible(): self.monitor_capsule.show()
            self._position_monitor_overlays()
            self.monitor_hide_timer.start(800)
        else:
            self.monitor_hide_timer.start(800)
        self._save()

    def _refresh_monitor(self) -> None:
        self.latest_network = self.network_sampler.sample()
        self.latest_memory = memory_snapshot()
        self.monitor_capsule.update_stats(self.latest_network, self.latest_memory)
        self.details_panel.update_stats(self.latest_network, self.latest_memory)
        if self.settings.monitor_always_visible and self.isVisible() and not self.details_panel.isVisible():
            self.monitor_capsule.show(); self._position_monitor_overlays()
        self._check_auto_cleanup()

    def open_details_panel(self) -> None:
        if self.details_panel.isVisible():
            self.close_details_panel()
            return
        self.panel_previous_paused = self.settings.paused
        self._panel_dragging = False; self.dragging = False; self.details_panel.reset_drag()
        self.press_global = self.press_window = self.last_drag_global = None
        self.panel_pause_active = True
        self.behavior_timer.stop(); self.motion_timer.stop(); self.animation_timer.stop(); self.state_timer.stop()
        self.details_panel.update_stats(self.latest_network, self.latest_memory)
        self.monitor_capsule.hide(); self.monitor_button.show()
        self._panel_side = self._overlay_side = None
        self.details_panel.show(); self._position_details_panel(); self.details_panel.raise_()
        self.monitor_button.raise_(); self._position_monitor_overlays()
        self.monitor_timer.start(2000)

    def close_details_panel(self) -> None:
        if not self.details_panel.isVisible() and not self.panel_pause_active:
            return
        self.details_panel.reset_drag(); self._panel_dragging = False; self.dragging = False
        self.details_panel.hide(); self.panel_pause_active = False
        self._panel_side = self._overlay_side = None
        cursor = QCursor.pos()
        pointer_near = self.geometry().contains(cursor) or self.monitor_button.geometry().contains(cursor)
        if self.settings.monitor_always_visible or pointer_near:
            self.monitor_capsule.show()
        else:
            self.monitor_button.hide(); self.monitor_capsule.hide()
        self._position_monitor_overlays()
        if not self.panel_previous_paused and not self.settings.paused and not self.cleanup_operation:
            self._resume_activity()

    def _position_details_panel(self) -> None:
        if self._panel_positioning:
            return
        area = self.current_screen.availableGeometry(); gap = 10
        content = self._content_rect_global()
        right_x = content.right() + gap + 1
        left_x = content.left() - gap - self.details_panel.width()
        if self._panel_side is None or not self.dragging:
            right_overflow = max(0, right_x + self.details_panel.width() - 1 - area.right())
            left_overflow = max(0, area.left() - left_x)
            self._panel_side = "right" if right_overflow <= left_overflow else "left"
        self._panel_positioning = True
        try:
            if self._panel_side == "right" and not self.dragging:
                overflow = right_x + self.details_panel.width() - 1 - area.right()
                if overflow > 0:
                    self.move(self.x() - overflow, self.y())
            elif self._panel_side == "left" and not self.dragging:
                overflow = area.left() - left_x
                if overflow > 0:
                    self.move(self.x() + overflow, self.y())
            content = self._content_rect_global()
            right_x = content.right() + gap + 1
            left_x = content.left() - gap - self.details_panel.width()
            x = right_x if self._panel_side == "right" else left_x
            y = self.geometry().center().y() - self.details_panel.height() // 2
            target = clamp_rect(QRect(x, y, self.details_panel.width(), self.details_panel.height()), area)
            self.details_panel.setGeometry(target)
        finally:
            self._panel_positioning = False
        self._position_monitor_overlays()

    def _flip_panel_for_drag(self, dx: int) -> None:
        if self.details_panel.isVisible() and dx:
            desired = "left" if dx > 0 else "right"
            area = self.current_screen.availableGeometry(); content = self._content_rect_global(); gap = 10
            x = (content.left() - gap - self.details_panel.width() if desired == "left"
                 else content.right() + gap + 1)
            if area.left() <= x and x + self.details_panel.width() - 1 <= area.right():
                self._panel_side = desired
                self._overlay_side = None

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if hasattr(self, "monitor_button"):
            self._position_monitor_overlays()
        if hasattr(self, "details_panel") and self.details_panel.isVisible() and not self._panel_positioning:
            self._position_details_panel()

    def _panel_drag_start(self, global_pos: QPoint) -> None:
        self._panel_dragging = True
        self.press_global = global_pos; self.press_window = self.pos(); self.last_drag_global = global_pos
        self.dragging = True; self.click_timer.stop(); self._detach_for_drag()

    def _panel_drag_move(self, global_pos: QPoint) -> None:
        if not self._panel_dragging or self.press_global is None or self.press_window is None:
            return
        dx = global_pos.x() - (self.last_drag_global.x() if self.last_drag_global else global_pos.x())
        if abs(dx) >= 2:
            self._flip_panel_for_drag(dx)
            if self.state != (wanted := "drag_right" if dx > 0 else "drag_left"):
                self.start_state(wanted)
        self.move(self.press_window + (global_pos - self.press_global)); self.last_drag_global = global_pos

    def _panel_drag_finish(self, global_pos: QPoint) -> None:
        if not self._panel_dragging:
            return
        self._panel_dragging = False; self.dragging = False
        self.press_global = self.press_window = self.last_drag_global = None
        self.current_screen = self._screen_for_point(global_pos)
        self._settle_after_drag(); self._save_position(); self._panel_side = None; self._position_details_panel()
        self.details_panel.reset_drag(); self.monitor_timer.start(2000)

    def open_auto_clean_settings(self) -> None:
        dialog = AutoCleanDialog(self.settings)
        area = self.current_screen.availableGeometry()
        dialog.setGeometry(QRect(area.center().x() - 175, area.center().y() - 115, 350, 230))
        if dialog.exec():
            dialog.apply(self.settings); self._save()

    def _offer_clean_task_install(self) -> None:
        if os.environ.get("MEINIFENG_DISABLE_CLEAN_TASK") == "1" or is_process_elevated():
            return
        expected = str(current_executable_path())
        if task_is_valid():
            self.settings.clean_task_schema_version = TASK_SCHEMA_VERSION
            self.settings.clean_task_executable = expected; self._save(); return
        # v1 只记录“已询问”，却可能没有真正创建任务；新版为它自动迁移一次。
        if self.settings.clean_task_schema_version < TASK_SCHEMA_VERSION:
            self._begin_clean_task_install(False)

    def repair_clean_authorization(self) -> None:
        self._begin_clean_task_install(False)

    def _begin_clean_task_install(self, pending_cleanup: bool) -> None:
        if self._install_future is not None:
            self._install_pending_cleanup = self._install_pending_cleanup or pending_cleanup
            return
        if os.environ.get("MEINIFENG_DISABLE_CLEAN_TASK") == "1":
            return
        self._install_pending_cleanup = pending_cleanup
        operation_id = uuid.uuid4().hex
        self._install_future = self._install_executor.submit(request_elevated_install, operation_id)
        self.install_poll_timer.start()
        self.tray.showMessage(APP_NAME, "请在系统窗口中确认一次管理员授权。",
                              QSystemTrayIcon.Information, 4000)

    def _poll_clean_task_install(self) -> None:
        future = self._install_future
        if future is None or not future.done():
            return
        self.install_poll_timer.stop(); self._install_future = None
        try:
            result = future.result()
        except Exception as exc:
            result = {"ok": False, "cancelled": False, "message": str(exc)}
        valid = bool(result.get("ok") and task_is_valid())
        self.settings.clean_task_prompted = True
        self.settings.clean_task_schema_version = TASK_SCHEMA_VERSION
        self.settings.clean_task_executable = str(current_executable_path()) if valid else ""
        self._save()
        if valid:
            self.tray.showMessage(APP_NAME, "内存清理授权已完成。", QSystemTrayIcon.Information, 3500)
            pending = self._install_pending_cleanup; self._install_pending_cleanup = False
            if pending: self.start_memory_cleanup()
        else:
            self._install_pending_cleanup = False
            message = result.get("message") or "未能创建清理任务"
            self.tray.showMessage(APP_NAME, str(message), QSystemTrayIcon.Warning, 5000)

    def start_memory_cleanup(self) -> None:
        if self.cleanup_operation:
            return
        if not is_process_elevated() and not task_is_valid():
            self._begin_clean_task_install(True)
            return
        operation = queue_cleanup()
        if not operation:
            self.tray.showMessage(APP_NAME, "无法启动内存清理任务。", QSystemTrayIcon.Warning, 4000)
            return
        self.cleanup_operation, self.cleanup_started_at = operation, time.monotonic()
        self.cleanup_finish_pending = False; self.cleanup_result = None
        self.resume_base = self.base_mode
        self.behavior_timer.stop(); self.motion_timer.stop(); self.motion_mode = None
        state = {"ground": "clean_ground", "climb_left": "clean_climb_left",
                 "climb_right": "clean_climb_right", "top_swing": "clean_top"}.get(self.base_mode, "clean_ground")
        self.start_state(state)
        self.monitor_button.set_cleaning(True)
        self.cleanup_poll_timer.start()

    def _poll_cleanup(self) -> None:
        if not self.cleanup_operation:
            self.cleanup_poll_timer.stop(); return
        result = read_result(self.cleanup_operation)
        if result is None and time.monotonic() - self.cleanup_started_at < 30:
            return
        if result is None:
            result = {"completed_at": time.time(), "available_increase": 0,
                      "steps": {"timeout": {"ok": False, "message": "30 秒超时"}}}
        self.cleanup_result = result
        self.cleanup_finish_pending = True
        self.cleanup_poll_timer.stop()

    def _finish_cleanup_animation(self) -> None:
        result = self.cleanup_result or {}
        self.cleanup_operation = None; self.cleanup_finish_pending = False
        self.monitor_button.set_cleaning(False)
        self.settings.last_clean_timestamp = float(result.get("completed_at", time.time()))
        self.details_panel.show_result(result); self._save()
        delta = int(result.get("available_increase", 0))
        self.tray.showMessage(APP_NAME, f"清理完成，可用内存变化 {'+' if delta >= 0 else '-'}{format_bytes(abs(delta))}",
                              QSystemTrayIcon.Information, 4500)
        self._resume_base_state()

    def _check_auto_cleanup(self) -> None:
        if self.cleanup_operation or not (self.settings.auto_clean_interval_enabled or self.settings.auto_clean_memory_enabled):
            return
        if auto_cleanup_due(self.settings, self.latest_memory.load_percent, time.time(), self.auto_clean_cycle_started):
            self.start_memory_cleanup()

    def toggle_pause(self) -> None:
        if self.panel_pause_active:
            return
        self.settings.paused = not self.settings.paused
        self.behavior_timer.stop(); self.motion_timer.stop(); self.animation_timer.stop(); self.state_timer.stop()
        if not self.settings.paused:
            self._resume_activity()
        self._save()

    def _resume_activity(self) -> None:
        if self.activity_paused or self.cleanup_operation:
            return
        if self.motion_mode == "fall":
            self.start_state("fall_float"); self.motion_timer.start()
        elif self.base_mode.startswith("climb_"):
            self._attach_side(self.base_mode.removeprefix("climb_"))
        elif self.base_mode == "top_swing":
            self.start_state(self.state if self.state in ("swing_cycle", "swing_idle") else "swing_idle")
        else:
            self._set_ground_idle()

    def toggle_autostart(self, enabled: bool) -> None:
        self.settings.autostart = enabled; set_autostart(enabled); self._save()

    def restore_default_size(self) -> None:
        self.set_scale(1.0)

    def hide_pet(self) -> None:
        self.close_details_panel()
        self.bubble.hide(); self.monitor_button.hide(); self.monitor_capsule.hide()
        self.behavior_timer.stop(); self.motion_timer.stop(); self.hide()

    def show_pet(self) -> None:
        self.show(); self.raise_(); self.activateWindow(); self._position_for_base_or_clamp(); self._schedule_behavior()

    def _position_for_base_or_clamp(self) -> None:
        if self.base_mode.startswith("climb_"):
            self._position_side(self.base_mode.removeprefix("climb_"))
        elif self.base_mode == "top_swing": self._position_top()
        else: self._clamp_to_current_screen()

    def _position_for_attachment_or_clamp(self) -> None:
        self._position_for_base_or_clamp()

    def _clamp_to_current_screen(self) -> None:
        area = self.current_screen.availableGeometry()
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))

    def _save_position(self) -> None:
        self.settings.last_x, self.settings.last_y = self.x(), self.y(); self._save()

    def _save(self) -> None:
        try: save_settings(self.settings)
        except OSError: pass

    def quit_app(self) -> None:
        self._save_position(); self.bubble.close(); self.monitor_button.close()
        self.monitor_capsule.close(); self.details_panel.close()
        self._install_executor.shutdown(wait=False, cancel_futures=True)
        self.tray.hide(); self.app.quit()


def acquire_single_instance():
    from PySide6.QtCore import QLockFile, QStandardPaths
    lock = QLockFile(str(Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation)) /
                         "meinifeng-desktop-pet.lock"))
    lock.setStaleLockTime(5000)
    if lock.tryLock(100): return lock
    if lock.removeStaleLockFile() and lock.tryLock(100): return lock
    return None


def run() -> int:
    if os.name == "nt": os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME); app.setOrganizationName(APP_NAME); app.setQuitOnLastWindowClosed(False)
    font_id = QFontDatabase.addApplicationFont(str(resource_root() / "assets" / "fonts" / "雅痞-简+常规体.otf"))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    if families:
        font = QFont(families[0])
        font.setPointSizeF(11.0)
        font.setHintingPreference(QFont.PreferVerticalHinting)
        font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
        app.setFont(font)
    lock = acquire_single_instance()
    if lock is None: return 0
    settings = load_settings(); set_autostart(settings.autostart)
    pet = PetWindow(app, settings); pet.show()
    app._pet_window, app._instance_lock = pet, lock
    if os.environ.get("MEINIFENG_SMOKE_TEST") == "1": QTimer.singleShot(1600, app.quit)
    return app.exec()
