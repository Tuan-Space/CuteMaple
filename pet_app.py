from __future__ import annotations

import ctypes
import concurrent.futures
import math
import os
import random
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (QAction, QFont, QFontDatabase, QGuiApplication, QIcon,
                           QCursor, QMouseEvent, QWheelEvent)
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon, QWidget, QFrame, QVBoxLayout, QPushButton

from pet_core import (ANIMATIONS, APP_NAME, PetSettings, AnimationSpec,
                      auto_cleanup_due, choose_dialogue, choose_happy_dialogue, load_settings,
                      resource_root, save_settings, set_autostart)
from memory_cleaner import (TASK_SCHEMA_VERSION, current_executable_path, is_process_elevated,
                            begin_cleanup, read_cleanup, cancel_cleanup,
                            request_session_authorization, session_is_valid, close_cleanup_session)
from monitor_ui import AutoCleanDialog, DetailsPanel, MonitorButton, MonitorCapsule, ThemeBinding, clamp_rect, cleanup_summary
from resource_monitor import NetworkSampler, format_bytes, memory_snapshot
from locomotion import ClimbCadence, climb_spec, climb_progress

DEFAULT_WINDOW_SIZE = 220
DRAG_THRESHOLD = 6
EDGE_SNAP_PX = 28
CLICK_DELAY_MS = 250
TOP_TRANSITIONS = {"climb_to_top_left", "climb_to_top_right"}
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
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)
        self.theme = ThemeBinding(self, 'bubble')
        self._cleanup_text = ""
        self._cleanup_stamp = None
        self._cleanup_active = False
        self._cleanup_expires = 0.0
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_message(self, text: str, pet_rect: QRect, screen_rect: QRect) -> None:
        if self.cleanup_owns_bubble:
            return
        self._display(text, pet_rect, screen_rect)
        self.hide_timer.start(4200)

    @property
    def cleanup_owns_bubble(self) -> bool:
        return self._cleanup_active or time.monotonic() < self._cleanup_expires

    def show_cleanup(self, result: dict, active: bool, pet_rect: QRect,
                     screen_rect: QRect, visible: bool) -> None:
        text = cleanup_summary(result)
        stamp = (result.get('operation_id'), result.get('status'), text, active)
        if stamp != self._cleanup_stamp or not self.cleanup_owns_bubble:
            self._cleanup_stamp = stamp
            self._cleanup_text = text
            self._cleanup_active = active
            duration = 6 if result.get('status') in {'succeeded', 'authorized'} else 8
            self._cleanup_expires = 0.0 if active else time.monotonic() + duration
        self.restore_cleanup(pet_rect, screen_rect, visible)

    def restore_cleanup(self, pet_rect: QRect, screen_rect: QRect, visible: bool) -> None:
        if not self.cleanup_owns_bubble:
            return
        self.hide_timer.stop()
        if not self._cleanup_active:
            remaining = max(1, math.ceil((self._cleanup_expires - time.monotonic()) * 1000))
            self.hide_timer.start(remaining)
        if visible:
            self._display(self._cleanup_text, pet_rect, screen_rect)
        else:
            self.hide()

    def _display(self, text: str, pet_rect: QRect, screen_rect: QRect) -> None:
        if self.label.text() != text:
            self.label.setText(text)
        self.label.setFixedWidth(max(1, min(230, screen_rect.width() - 16)))
        self.label.adjustSize()
        self.resize(self.label.size())
        self.follow_pet(pet_rect, screen_rect)
        if not self.isVisible():
            self.show()
            self.raise_()

    def follow_pet(self, pet_rect: QRect, screen_rect: QRect) -> None:
        x = pet_rect.center().x() - self.width() // 2
        y = pet_rect.top() - self.height() - 8
        if y < screen_rect.top():
            y = pet_rect.bottom() + 8
        x = max(screen_rect.left(), min(x, screen_rect.right() - self.width() + 1))
        y = max(screen_rect.top(), min(y, screen_rect.bottom() - self.height() + 1))
        self.move(x, y)


class PetWindow(QWidget):
    def __init__(self, app: QApplication, settings: PetSettings) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.app, self.settings = app, settings
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(resource_root() / "assets" / "icon.png")))
        self.state, self.animation_cycles = "idle", 0
        self._presentation_ready = False
        self._renderer_generation = None
        self._pending_presentation = None
        self.loading_panel = QFrame(self)
        self.loading_panel.setObjectName("sectionCard")
        layout = QVBoxLayout(self.loading_panel)
        self.loading_label = QLabel("正在加载桌宠…", self.loading_panel)
        self.loading_label.setWordWrap(True)
        self.loading_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.loading_label)
        self.retry_button = QPushButton("重试", self.loading_panel)
        self.retry_button.clicked.connect(self.retry_live2d)
        self.retry_button.hide()
        layout.addWidget(self.retry_button)
        self.loading_exit = QPushButton("退出", self.loading_panel)
        self.loading_exit.clicked.connect(self.quit_app)
        layout.addWidget(self.loading_exit)
        self.loading_theme = ThemeBinding(self.loading_panel)
        self.live2d_host = None
        self.live2d_states: set[str] = set()
        self._live2d_active = False
        self._renderer_token = 0
        self._renderer_geometry: dict = {}
        self.renderer_status = "正在加载桌宠…"
        self.desktop_activity = self.reactions = None
        self._active_reaction: str | None = None
        self._reaction_expressions: set[str] = set()
        self._requested_expressions: set[str] = set()
        self._applied_expressions: set[str] = set()
        self._last_desktop_click = QCursor.pos()
        self._drag_effect_until = 0.0
        self._explicit_effect: str | None = None
        self._explicit_effect_until = 0.0
        self._last_particle_at = 0.0
        self._particle_times: dict[str, float] = {}
        self._activity_status: dict = {}
        self.interaction_panel = None
        self._reaction_blocked = False
        self._motion_updated_at = time.monotonic()
        self._motion_y = 0.0
        self._fall_velocity = 0.0
        self._motion_velocity = (0.0, 0.0)
        self._paused_drag_contact: str | None = None
        self._climb_spec: dict | None = None
        self._climb_cadence = ClimbCadence()
        self._climb_clock = time.monotonic()
        self._climb_control_stamp = None
        self._climb_progress = 0.0
        self._climb_endpoint_supported = False
        self._motion_polish_supported = False
        self._climb_endpoint_request = None
        self._climb_endpoint_serial = 0
        self._state_remaining_ms = -1
        self._runtime_suspended = False
        self._suspended_at = 0.0
        self._runtime_stopped = False
        self._quitting = False
        self._top_transition = None
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
        self._removed_screen_ids = {}
        self._tracked_screen_ids = set()
        self.current_screen = QGuiApplication.primaryScreen()
        self.app.screenRemoved.connect(self._screen_removed)
        self.app.screenAdded.connect(self._screens_changed)
        self.app.primaryScreenChanged.connect(self._screens_changed)
        for screen in QGuiApplication.screens():
            self._track_screen(screen)
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
        self._cleanup_future = None
        self._cleanup_manual = True
        self._cleanup_cancel_requested = False
        self._cleanup_unresponsive = False
        self._cleanup_feedback = {"status": "idle"}
        self._cleanup_feedback_active = False
        self._cleanup_feedback_stamp = None
        self._auto_authorization_notice = False
        self.auto_clean_cycle_started = time.time()
        self.panel_pause_active = False
        self.panel_previous_paused = False
        self._panel_side: str | None = None
        self._panel_dragging = False
        self._panel_positioning = False
        self._overlay_side: str | None = None
        self._install_pending_cleanup = False
        self._install_future = None
        self._authorization_probe_future = None
        self._install_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="clean-auth")

        self.motion_timer = QTimer(self)
        self.motion_timer.setInterval(16)
        self.motion_timer.setTimerType(Qt.PreciseTimer)
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
        self.presentation_timer = QTimer(self)
        self.presentation_timer.setInterval(16)
        self.presentation_timer.timeout.connect(self._present_current_frame)
        self.runtime_timer = QTimer(self)
        self.runtime_timer.setInterval(30)
        self.runtime_timer.timeout.connect(self._update_runtime)
        self.app.aboutToQuit.connect(self._finalize_runtime)

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
        self.details_panel.cancelRequested.connect(self.cancel_memory_cleanup)
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
        QTimer.singleShot(0, self._initialize_runtime)



    def _initialize_runtime(self) -> None:
        if self._runtime_stopped:
            return
        # Unit tests inject their renderer through a test-only fixture.
        if os.environ.get("MEINIFENG_DISABLE_RUNTIME") == "1":
            return
        self.retry_live2d()
        try:
            from desktop_activity import DesktopActivity
            from pet_reactions import ReactionController
            self.reactions = ReactionController()
            self.desktop_activity = DesktopActivity(self)
            self.desktop_activity.keyboardActivity.connect(self._keyboard_activity)
            self.desktop_activity.mouseClick.connect(self._desktop_click)
            self.desktop_activity.audioActivityChanged.connect(self._audio_activity)
            self.desktop_activity.statusChanged.connect(self._activity_status_changed)
            self.desktop_activity.audioLevelChanged.connect(self._audio_level_changed)
            self._configure_activity()
            self.desktop_activity.start(int(self.winId()))
        except (ImportError, RuntimeError, OSError, AttributeError) as exc:
            # Provider absence must never prevent the pet/monitor from running.
            self.tray.setToolTip(f"{APP_NAME}（桌面感知暂不可用：{exc}）")
        self.runtime_timer.start()
        self._sync_runtime_pause()

    def _finalize_runtime(self) -> None:
        self._stop_runtime()
        if self.desktop_activity is not None:
            self.desktop_activity.force_stop()

    def _track_screen(self, screen) -> None:
        if id(screen) not in self._tracked_screen_ids:
            self._tracked_screen_ids.add(id(screen))
            screen.availableGeometryChanged.connect(self._screens_changed)
            screen.geometryChanged.connect(self._screens_changed)
            screen.logicalDotsPerInchChanged.connect(self._screens_changed)

    def _screen_removed(self, removed) -> None:
        self._removed_screen_ids[id(removed)] = removed
        if self.current_screen is removed:
            self.current_screen = next((screen for screen in QGuiApplication.screens() if screen is not removed), None)
        self._screens_changed()
        QTimer.singleShot(0, self._screens_changed)

    def _screens_changed(self, *_args) -> None:
        if self._runtime_stopped:
            return
        screens = [screen for screen in QGuiApplication.screens() if id(screen) not in self._removed_screen_ids]
        if self.current_screen not in screens:
            primary = QGuiApplication.primaryScreen()
            self.current_screen = primary if primary in screens else (screens[0] if screens else None)
        for screen in screens:
            self._track_screen(screen)
        if not screens:
            self.motion_timer.stop()
            return
        if self.current_screen is not None:
            if self._top_transition:
                self._top_transition = None
                self._set_ground_idle()
            self._position_for_base_or_clamp()

    def _screen_area(self) -> QRect:
        screens = [screen for screen in QGuiApplication.screens() if id(screen) not in self._removed_screen_ids]
        if self.current_screen not in screens:
            primary = QGuiApplication.primaryScreen()
            self.current_screen = primary if primary in screens else (screens[0] if screens else None)
        if self.current_screen is None:
            return QRect(0, 0, max(1, self.width()), max(1, self.height()))
        try:
            return self.current_screen.availableGeometry()
        except RuntimeError:
            self.current_screen = None
            return QRect(0, 0, max(1, self.width()), max(1, self.height()))

    def _stop_runtime(self) -> None:
        if self._runtime_stopped:
            return
        self._runtime_stopped = True
        if self.reactions is not None:
            self.reactions.clear()
        self._sync_ambient_reactions()
        if self._authorization_probe_future is not None:
            self._authorization_probe_future.cancel()
            self._authorization_probe_future = None
        for timer in self.findChildren(QTimer):
            timer.stop()
        for component in (self.desktop_activity, self.live2d_host):
            if component is not None:
                component.stop()
        if self.interaction_panel is not None:
            self.interaction_panel.close()

    def _configure_activity(self) -> None:
        for component in (self.desktop_activity, self.reactions):
            if component is not None:
                component.set_enabled(keyboard=self.settings.keyboard_enabled, audio=self.settings.audio_enabled)
        if self.desktop_activity is not None:
            self.desktop_activity.set_audio_endpoint(self.settings.audio_endpoint)

    def _send_renderer(self, kind: str, **values) -> None:
        # The frontend cannot consume animation commands before its model is
        # ready. In particular, do not queue cursor samples after a load error.
        if self.live2d_host is not None and self.live2d_host.ready:
            self.live2d_host.send(kind, **values)

    def _set_expression(self, name: str, active: bool) -> None:
        if active:
            self._requested_expressions.add(name)
        else:
            self._requested_expressions.discard(name)
        self._sync_expressions()

    def _has_support_pose(self) -> bool:
        return self.base_mode in {"climb_left", "climb_right", "top_swing"} or self._top_transition is not None

    def _sync_expressions(self) -> None:
        # Keyboard/listening overlays move arms; attached hands must retain the
        # authored support pose. Merge sources so expiring a click expression
        # cannot also clear a still-active ambient reaction using the same face.
        safe = {"keyboard": "keyboard", "audio": "audio", "smile": "smile", "blush": "blush", "maple": "maple"}
        if self._motion_polish_supported and self.base_mode == "top_swing" and not self._top_transition:
            safe.update({name: name for name in ("swing_petting_left", "swing_petting_right")})
        requested = self._requested_expressions | self._reaction_expressions
        wanted = {safe[name] for name in requested if name in safe} if self._has_support_pose() else requested
        if self.live2d_host is None or not self.live2d_host.ready:
            return
        for name in sorted(self._applied_expressions - wanted):
            self._send_renderer("expression", name=name, active=False, intensity=1.0, token=self._renderer_token)
        for name in sorted(wanted - self._applied_expressions):
            self._send_renderer("expression", name=name, active=True, intensity=1.0, token=self._renderer_token)
        self._applied_expressions = wanted

    def _show_effect(self, name: str, duration_ms: int = 5000) -> None:
        if self._explicit_effect:
            self._set_expression(self._explicit_effect, False)
        self._explicit_effect = name
        self._explicit_effect_until = time.monotonic() + duration_ms / 1000
        self._set_expression(name, True)

    def _render_state_name(self) -> str:
        if self._top_transition:
            return self._top_transition['name']
        return self.state

    def _activate_state_renderer(self) -> None:
        self._cancel_swing_petting()
        name = self._render_state_name()
        self._renderer_token += 1
        self._climb_progress = 0.0
        self._climb_endpoint_request = None
        if self.live2d_host is None or not self.live2d_host.ready:
            return
        if name not in self.live2d_states:
            self._renderer_failed(f"模型缺少必要动作：{name}")
            return
        self._live2d_active = True
        spec = AnimationSpec(1800, "one_shot") if self._top_transition else ANIMATIONS[self.state]
        self._send_renderer("resize", width=self.width(), height=self.height(), scale=self.settings.scale)
        self._send_renderer("pause", paused=self._animation_paused())
        self._send_renderer("play", name=name, token=self._renderer_token,
                            playback=spec.playback, cycles=spec.cycles,
                            durationMs=(round(self._climb_spec["cycleDuration"] * 1000)
                                        if name in {"climb_left", "climb_right"} and self._climb_spec
                                        else spec.duration_ms), fade=0.25)
        self._send_motion_context()
        self._sync_climb_control(force=True)
        self._sync_ambient_reactions()

    def _on_renderer_event(self, event: dict) -> None:
        if self._runtime_stopped:
            return
        if event.get("generation") != self._renderer_generation:
            return
        kind = event.get("type")
        if kind == "ready":
            self._motion_polish_supported = "motion-polish-v1" in event.get("capabilities", [])
            self._applied_expressions.clear()
            self._climb_spec = climb_spec(event.get("locomotion"))
            locomotion = event.get("locomotion")
            climb = locomotion.get("climb", {}) if isinstance(locomotion, dict) else {}
            self._climb_endpoint_supported = (
                "climb-endpoint-v1" in event.get("capabilities", []) and
                isinstance(climb, dict) and climb.get("refinementParameter") == "ParamClimbRefine")
            states = event.get("states", [])
            self.live2d_states = {name for name in states if isinstance(name, str) and
                                  (name in ANIMATIONS or name in TOP_TRANSITIONS)}
            required = set(ANIMATIONS) | TOP_TRANSITIONS
            if self.live2d_states != required or self._climb_spec is None:
                self._renderer_failed("模型动作或攀爬数据不完整")
                return
            self._activate_state_renderer()
            for name in (self._explicit_effect,
                         "dizzy" if self.dragging or self._drag_effect_until > time.monotonic() else None):
                if name:
                    self._set_expression(name, True)
            self._sync_expressions()
            return
        if kind == "error":
            self._renderer_failed(str(event.get("message", "模型载入失败")))
            return
        if not self._live2d_active or event.get("token") != self._renderer_token or event.get("name") != self._render_state_name():
            return
        if kind in {"geometry", "frame-ready"}:
            bounds = event.get("bounds")
            if (isinstance(bounds, list) and len(bounds) == 4 and
                    all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds) and
                    bounds[2] > 0 and bounds[3] > 0):
                self._renderer_geometry = event
                if self._top_transition:
                    self._position_top_transition(event)
                elif self.base_mode.startswith("climb_"):
                    if self._advance_native_climb(event):
                        return
                    self._position_side(self.base_mode.removeprefix("climb_"))
                elif self.base_mode == "top_swing":
                    self._position_top()
                elif (self.base_mode == "ground" and self.motion_mode != "fall"
                      and not self.dragging and self.state != "happy"):
                    self.move(self.x(), self._floor_y(self._screen_area()))
                self._position_monitor_overlays()
                if kind == "frame-ready" and not self._presentation_ready:
                    self._pending_presentation = (self._renderer_generation, self._renderer_token)
                    self.presentation_timer.start()
                    self._present_current_frame()
            return
        if kind == "climb-rest" and event.get("cycles") == 2:
            if self._climb_cadence.finished(event.get("run")):
                self._climb_clock = time.monotonic()
                self._sync_climb_control(force=True)
            return
        if self._animation_paused():
            return
        if self._top_transition:
            if kind == "marker":
                marker = event.get("marker")
                phases = {"top_grab": 1, "wall_release": 2, "settled": 3}
                if marker in phases and phases[marker] > self._top_transition['phase']:
                    self._top_transition['phase'] = phases[marker]
            elif kind == "finished":
                self._top_transition = None
                self._attach_top(True)
            return


        if kind == "cycle":
            cycle = event.get("cycle")
            if not isinstance(cycle, int) or cycle <= self.animation_cycles:
                return
            self.animation_cycles = cycle
            spec = ANIMATIONS[self.state]
            if spec.playback == "counted_loop" and cycle >= spec.cycles:
                self._complete_animation()
        elif kind == "finished" and ANIMATIONS[self.state].playback == "one_shot":
            self._complete_animation()

    def _wall_resting(self) -> bool:
        return (self._climb_cadence.active and self.motion_mode == "climb"
                and self.state in {"climb_left", "climb_right"}
                and (self._climb_cadence.waiting or self.panel_pause_active))

    def _sync_climb_control(self, *, force: bool = False) -> None:
        if not self._climb_cadence.active or self._top_transition or self.state not in {"climb_left", "climb_right"}:
            return
        resting = self._wall_resting()
        stamp = (self._renderer_token, self._climb_cadence.run, resting)
        if self._live2d_active and (force or stamp != self._climb_control_stamp):
            self._send_renderer("climb-control", name=self.state, token=self._renderer_token,
                                run=self._climb_cadence.run, resting=resting)
            self._climb_control_stamp = stamp

    def _tick_climb_cadence(self) -> None:
        now = time.monotonic()
        elapsed = min(.25, max(0.0, now - self._climb_clock))
        self._climb_clock = now
        blocked = not self._presentation_ready or self.activity_paused or self.panel_pause_active or not self.isVisible() or self.dragging or bool(self._top_transition)
        if self._climb_cadence.tick(elapsed, blocked):
            self._motion_updated_at = now
            self._motion_y = float(self.y())
            self._sync_climb_control(force=True)

    def _animation_paused(self) -> bool:
        return not self._presentation_ready or self.activity_paused or not self.isVisible()

    def _advance_native_climb(self, geometry: dict) -> bool:
        if self._climb_spec is None or self.motion_mode != "climb" or not self.state.startswith("climb_"):
            return False
        progress = climb_progress(self._climb_spec, geometry.get("climbPhase"), geometry.get("climbCycle"))
        if progress is None:
            return False
        endpoint = geometry.get("climbEndpoint")
        request = self._climb_endpoint_request
        bound_endpoint = (request and isinstance(endpoint, dict) and endpoint.get("request") == request["request"]
            and endpoint.get("cycle") == geometry.get("climbCycle")
            and geometry.get("generation") == request["generation"]
            and abs(geometry["climbPhase"] - 1) <= 1e-5)
        delta = progress - self._climb_progress
        if delta < -1e-6:
            return False  # Ignore reordered geometry without rewinding the movement baseline.
        if delta > .6:
            # A resumed clock must never teleport the pet across missed cycles.
            self._climb_progress = progress
            if not bound_endpoint:
                return False
            # Still consume the one-shot held endpoint, otherwise the native
            # renderer would wait forever after a dropped geometry interval.
            delta = 0.0
        self._climb_progress = progress
        if self.movement_paused or self.dragging or not self.isVisible():
            self._cancel_climb_endpoint()
            return False
        self._motion_y += self.climb_direction * delta * self._climb_spec["risePerCycle"] * self.height()
        area = self._screen_area()
        anchor = self._model_anchor("left" if self.base_mode == "climb_left" else "right")
        ceiling = area.top() - round(anchor[1] * self.height()) if anchor else area.top()
        y = round(self._motion_y)
        refined = self._climb_endpoint_supported and "climb_to_top_" + self.base_mode.removeprefix("climb_") in self.live2d_states
        if refined:
            floor = self._floor_y(area)
            if y >= floor:
                y, self._motion_y, self.climb_direction = floor, float(floor), -1
            self.move(self.x(), y)
            # Let the native phase own Y all the way to the endpoint. Clamping
            # the window at the roof while the arms keep moving slides the grip.
            self._position_side(self.base_mode.removeprefix("climb_"), clamp_ceiling=False)
            if bound_endpoint:
                self._begin_top_transition(endpoint)
                return True

            if request is None and self.climb_direction < 0:
                side = self.base_mode.removeprefix("climb_")
                material_grip = self._model_anchor("nearGrip")
                grips = ([material_grip] if material_grip is not None else
                         [self._model_anchor(key) for key in (side, "grip" + side.title())])
                grip_y = min((point[1] for point in grips if point), default=None)
                if grip_y is not None:
                    clearance = self.y() + grip_y * self.height() - area.top()
                    if clearance <= self._climb_spec["risePerCycle"] * self.height() + 2:
                        self._request_climb_endpoint("top")
            self._position_monitor_overlays()
            return True
        if y <= ceiling:
            self.move(self.x(), ceiling)
            self._begin_top_transition()
            return True
        floor = self._floor_y(area)
        if y >= floor:
            y, self._motion_y, self.climb_direction = floor, float(floor), -1
        self.move(self.x(), y)
        return False

    def _request_climb_endpoint(self, purpose: str, cleanup_state: str | None = None) -> None:
        previous = self._climb_endpoint_request
        if (previous and previous.get("purpose") == purpose and
                previous.get("operation") == (self.cleanup_operation if purpose == "cleanup" else None)):
            return
        self._cancel_climb_endpoint()
        self._climb_endpoint_serial += 1
        self._climb_endpoint_request = {"request": self._climb_endpoint_serial,
            "name": self._render_state_name(), "token": self._renderer_token,
            "generation": self._renderer_geometry.get("generation"), "purpose": purpose,
            "cleanup_state": cleanup_state,
            "operation": self.cleanup_operation if purpose == "cleanup" else None}
        self._send_renderer("climb-endpoint", name=self._render_state_name(), token=self._renderer_token,
                            request=self._climb_endpoint_serial, enabled=True)

    def _cancel_climb_endpoint(self) -> None:
        request = self._climb_endpoint_request
        self._climb_endpoint_request = None
        if request:
            self._send_renderer("climb-endpoint", name=request["name"], token=request["token"],
                                request=request["request"], enabled=False)

    def _sync_runtime_pause(self) -> None:
        self._climb_clock = time.monotonic()
        self._sync_climb_control(force=True)
        suspended = not self._presentation_ready or self.activity_paused or not self.isVisible()
        if suspended:
            self._cancel_swing_petting()
        if suspended or self.movement_paused:
            self._cancel_climb_endpoint()

        if suspended and not self._runtime_suspended:
            self._suspended_at = time.monotonic()
            self._state_remaining_ms = self.state_timer.remainingTime()
            self.state_timer.stop()
        elif not suspended and self._runtime_suspended:
            if self.motion_mode in {"walk_out", "walk_back"}:
                self.walk_leg_started_at += max(0.0, time.monotonic() - self._suspended_at)
            if self._state_remaining_ms >= 0:
                self.state_timer.start(max(1, self._state_remaining_ms))
                self._state_remaining_ms = -1
            self._motion_updated_at = time.monotonic()
            self._motion_y = float(self.y())
        self._runtime_suspended = suspended
        if self.desktop_activity is not None:
            self.desktop_activity.set_suspended(suspended)
        self._send_renderer("pause", paused=self._animation_paused())
        self._send_motion_context()
        if suspended:
            if self.reactions is not None:
                self.reactions.clear()
            self._send_renderer("gaze", x=0.0, y=0.0, enabled=False)
        self._sync_ambient_reactions()

    def _desktop_click(self, x: int, y: int) -> None:
        self._last_desktop_click = QPoint(x, y)

    def _keyboard_activity(self) -> None:
        if (self.settings.keyboard_enabled and self.reactions is not None and not self._runtime_stopped
                and not self.activity_paused and self.isVisible()):
            self.reactions.note_keyboard(time.monotonic())

    def _audio_activity(self, active: bool) -> None:
        if self.reactions is not None:
            self.reactions.set_audio(active and self.settings.audio_enabled and not self._runtime_stopped
                                     and not self.activity_paused and self.isVisible(), time.monotonic())

    def _sync_ambient_reactions(self, now: float | None = None) -> tuple[str, ...]:
        now = time.monotonic() if now is None else now
        blocked = (self._runtime_stopped or self.activity_paused or not self.isVisible() or self.dragging or
                   self.sleep_phase is not None or
                   (not self._has_support_pose() and self.state not in {"idle", "walk_left", "walk_right"}))
        self._reaction_blocked = blocked
        self._active_reaction = self.reactions.update(now, blocked=blocked) if self.reactions is not None else None
        effects = self.reactions.active_effects(now, blocked=blocked) if self.reactions is not None else ()
        # The primary reaction still chooses gaze/status priority. Independent
        # expression targets preserve real listening while typing owns that role.
        self._reaction_expressions = set(effects)
        self._sync_expressions()
        return effects

    def _update_runtime(self) -> None:
        if self._runtime_stopped:
            return
        self._poll_authorization_probe()
        self._tick_climb_cadence()
        self._send_motion_context()
        if not self._presentation_ready or self.activity_paused or not self.isVisible():
            self._update_interaction_panel()
            return
        now = time.monotonic()
        effects = self._sync_ambient_reactions(now)
        wanted = self._active_reaction
        if self._explicit_effect and now >= self._explicit_effect_until:
            self._set_expression(self._explicit_effect, False)
            self._explicit_effect = None
        if self._drag_effect_until and now >= self._drag_effect_until and not self.dragging:
            self._set_expression("dizzy", False)
            self._drag_effect_until = 0.0
        gaze_allowed = (self.settings.gaze_enabled and not self.activity_paused and self.isVisible() and
                        not self.sleep_phase and not self.dragging and not self._drag_effect_until and
                        self.state not in {"petting", "happy"})
        target = self._last_desktop_click if wanted == "keyboard" else QCursor.pos()
        point = self.mapFromGlobal(target)
        head = self._model_anchor("head") or (0.5, 0.32)
        x = max(-1.0, min(1.0, (point.x() - head[0] * self.width()) / max(120.0, self.width() * 1.5)))
        y = max(-1.0, min(1.0, (head[1] * self.height() - point.y()) / max(120.0, self.height())))
        self._send_renderer("gaze", x=x if gaze_allowed else 0.0, y=y if gaze_allowed else 0.0, enabled=gaze_allowed)
        for effect in effects:
            self._emit_effect(effect)
        self._update_interaction_panel()

    def _send_motion_context(self) -> None:
        area = self._screen_area().intersected(self.geometry()).translated(-self.pos())
        width, height = max(1, self.width()), max(1, self.height())
        # Wind comes only from the physical fall integrator. A later attachment,
        # scale change or screen clamp cannot reuse a fall's cached velocity.
        moving = not self.movement_paused and not self.dragging and self.motion_mode == "fall"
        vx, vy = self._motion_velocity if moving else (0.0, 0.0)
        self._send_renderer("context", token=self._renderer_token,
                            grounded=self.base_mode == "ground" and self.motion_mode != "fall" and not self.dragging,
                            attached=self._has_support_pose(), dragging=self.dragging,
                            falling=self.motion_mode == "fall", vx=vx / width, vy=vy / height,
                            visibleRect=[area.x() / width, area.y() / height,
                                         max(0, area.width()) / width, max(0, area.height()) / height],
                            effectsEnabled=self.settings.particles_enabled and self.isVisible() and not self.activity_paused)

    def _emit_effect(self, name: str, *, interval: float = .3) -> None:
        if name not in {"keyboard", "audio", "happy", "leaf", "note", "star", "heart", "bubble", "petal", "sleep"}:
            return
        if not self.settings.particles_enabled or self.activity_paused or not self.isVisible():
            return
        now = time.monotonic()
        if now - self._particle_times.get(name, -100.0) < interval:
            return
        self._particle_times[name] = now
        if self._live2d_active:
            self._send_renderer("effect", name=name, token=self._renderer_token, intensity=1.0)

    def _activity_status_changed(self, status: dict) -> None:
        self._activity_status = status
        self._update_interaction_panel()

    def _audio_level_changed(self, level: float) -> None:
        if self.interaction_panel is not None:
            self.interaction_panel.set_audio_level(level)

    def _update_interaction_panel(self) -> None:
        if self.interaction_panel is not None and self.interaction_panel.isVisible():
            self.interaction_panel.update_status(self._activity_status,
                keyboard_enabled=self.settings.keyboard_enabled, audio_enabled=self.settings.audio_enabled,
                suspended=self.activity_paused or not self.isVisible(),
                blocked=self._reaction_blocked, reaction=self._active_reaction)

    def open_interaction_panel(self) -> None:
        if self.interaction_panel is None:
            from interaction_ui import InteractionPanel
            self.interaction_panel = InteractionPanel(self.settings.audio_endpoint)
            self.interaction_panel.endpointSelected.connect(self._select_audio_endpoint)
            self.interaction_panel.retryAudioRequested.connect(self._retry_audio)
            self.interaction_panel.previewRequested.connect(self._preview_effect)
        self.interaction_panel.show()
        self.interaction_panel.setGeometry(clamp_rect(self.interaction_panel.geometry(), self._screen_area()))
        self.interaction_panel.raise_()
        self._update_interaction_panel()

    def _select_audio_endpoint(self, endpoint: str) -> None:
        self.settings.audio_endpoint = endpoint
        if self.desktop_activity is not None:
            self.desktop_activity.set_audio_endpoint(endpoint)
        self._save()

    def _retry_audio(self) -> None:
        if self.desktop_activity is not None:
            self.desktop_activity.retry_audio()

    def _preview_effect(self, name: str) -> None:
        if self.activity_paused or not self.isVisible():
            self.bubble.show_message("预览效果需要先显示桌宠并继续活动。", self.geometry(), self._screen_area())
            return
        if name != "glasses" and not self.settings.particles_enabled:
            self.bubble.show_message("请先在互动菜单开启装饰特效，再预览。", self.geometry(), self._screen_area())
            return
        if name == "glasses":
            if self._has_support_pose():
                self.bubble.show_message("眼镜预览可在地面姿势下查看。", self.geometry(), self._screen_area())
                return
            self._show_effect("glasses", 3500)
        else:
            self._emit_effect(name, interval=0)

    def _model_anchor(self, name: str) -> tuple[float, float] | None:
        anchors = self._renderer_geometry.get("anchors", {})
        value = anchors.get(name) if isinstance(anchors, dict) else None
        if (isinstance(value, list) and len(value) == 2 and
                all(isinstance(v, (int, float)) and math.isfinite(v) for v in value)):
            return float(value[0]), float(value[1])
        return None

    def _floor_y(self, area: QRect) -> int:
        anchor = self._model_anchor("ground") if self._live2d_active else None
        return area.bottom() + 1 - round((anchor[1] if anchor else 1.0) * self.height())

    def toggle_interaction(self, name: str, enabled: bool) -> None:
        if name not in {"roaming_enabled", "gaze_enabled", "keyboard_enabled", "audio_enabled", "particles_enabled"}:
            return
        setattr(self.settings, name, bool(enabled))
        if name == "roaming_enabled":
            self.behavior_timer.stop()
            if not enabled and self.motion_mode in {"walk_out", "walk_back"}:
                self._set_ground_idle()
            elif enabled:
                self._schedule_behavior()
        self._configure_activity()
        self._sync_runtime_pause()
        self._update_runtime()
        self._save()

    def retry_live2d(self) -> None:
        if self._runtime_stopped:
            return
        if self.live2d_host is not None:
            self.live2d_host.stop()
            self.live2d_host = None
        self._pending_presentation = None
        self.presentation_timer.stop()
        self._presentation_ready = self._live2d_active = False
        self._renderer_geometry = {}
        self._renderer_token += 1
        self.loading_label.setText("正在加载桌宠…")
        self.renderer_status = "正在加载桌宠…"
        self.retry_button.hide()
        self.loading_panel.show()
        self.loading_panel.raise_()
        self._sync_runtime_pause()
        try:
            from live2d_host import Live2DHost
            self.live2d_host = Live2DHost(self, resource_root())
            host = self.live2d_host
            host.event.connect(lambda event: self._on_renderer_event(event) if self.live2d_host is host else None)
            host.view.setGeometry(self.rect())
            host.view.show()
            host.view.lower()
            self.loading_panel.raise_()
            host.load()
            self._renderer_generation = host.generation
        except (ImportError, RuntimeError, OSError, AttributeError) as exc:
            self._renderer_failed(str(exc))

    def _frame_has_pixels(self) -> bool:
        image = self.live2d_host.view.grab().toImage()
        if image.isNull():
            return False
        # Inspect the actual Qt compositor result, not merely a WebGL draw call.
        hits = 0
        for y in range(0, image.height(), max(1, image.height() // 24)):
            for x in range(0, image.width(), max(1, image.width() // 24)):
                hits += image.pixelColor(x, y).alpha() > 16
        return hits >= 12

    def _present_current_frame(self) -> None:
        if (self._runtime_stopped or not self._pending_presentation or
                self._pending_presentation != (self._renderer_generation, self._renderer_token)):
            self.presentation_timer.stop()
            return
        if not self.isVisible():
            self.live2d_host.presented(self._renderer_token)
            return  # Showing the pet still waits for a compositor frame.
        if not self._frame_has_pixels():
            return
        self.presentation_timer.stop()
        self._pending_presentation = None
        self._presentation_ready = True
        self.renderer_status = "Live2D"
        self.live2d_host.presented(self._renderer_token)
        self.loading_panel.hide()
        self.live2d_host.view.lower()
        self._sync_runtime_pause()
        self._resume_activity()

    def _renderer_failed(self, message: str) -> None:
        self._cancel_swing_petting()
        self._pending_presentation = None
        self.presentation_timer.stop()
        self._presentation_ready = self._live2d_active = False
        self._renderer_geometry = {}
        self._renderer_token += 1
        self._climb_endpoint_request = None
        if self.live2d_host is not None:
            self.live2d_host.stop()
        self.renderer_status = f"Live2D 无法显示：{message[:160]}"
        self.loading_label.setText(self.renderer_status)
        self.retry_button.show()
        self.loading_panel.show()
        self.loading_panel.raise_()
        self.motion_timer.stop()
        self.behavior_timer.stop()
        self._sync_runtime_pause()



    def _apply_size(self, preserve_anchor: bool = True) -> None:
        old = self.geometry()
        side = round(DEFAULT_WINDOW_SIZE * self.settings.scale)
        self.setFixedSize(side, side)
        self.loading_panel.setGeometry(0, 0, side, side)
        if self.live2d_host is not None:
            self.live2d_host.view.setGeometry(self.rect())
            self._send_renderer("resize", width=side, height=side, scale=self.settings.scale)
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

        # Scaling preserves the visible contact point by moving the window.
        # Integrate the next climbing/falling step from that new position.
        self._motion_y = float(self.y())
        self._motion_updated_at = time.monotonic()

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
        area = self._screen_area()
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

        interactions = menu.addMenu("互动")
        for name, title in (("gaze_enabled", "视线跟随鼠标"), ("roaming_enabled", "自主行走"),
                            ("keyboard_enabled", "打字回应"), ("audio_enabled", "系统声音回应"),
                            ("particles_enabled", "装饰特效")):
            action = QAction(title, menu)
            action.setObjectName(name)
            action.setCheckable(True)
            action.toggled.connect(lambda enabled, option=name: self.toggle_interaction(option, enabled))
            interactions.addAction(action)
        renderer = interactions.addAction("渲染状态")
        renderer.setObjectName("renderer_status")
        renderer.setEnabled(False)
        interactions.addAction("重新载入 Live2D 模型", self.retry_live2d)
        interactions.addAction("互动检测与效果预览", self.open_interaction_panel)

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
        repair = performance.addAction("授权本次运行…", self.repair_clean_authorization)
        repair.setObjectName("repair_clean_action")
        performance.addAction("自动清理设置…", self.open_auto_clean_settings)
        menu.addSeparator()
        menu.addAction("退出", self.quit_app)

        menu.aboutToShow.connect(lambda current=menu: self._sync_context_menu(current))
        self._sync_context_menu(menu)
        return menu

    def _sync_context_menu(self, menu: QMenu) -> None:
        for name in ("gaze_enabled", "roaming_enabled", "keyboard_enabled", "audio_enabled", "particles_enabled"):
            action = menu.findChild(QAction, name)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(getattr(self.settings, name))
                action.blockSignals(False)
        renderer = menu.findChild(QAction, "renderer_status")
        if renderer is not None:
            renderer.setText(f"渲染：{self.renderer_status}")
        menu.findChild(QAction, "visibility_action").setText(
            "隐藏美腻枫" if self.isVisible() else "显示美腻枫"
        )
        pause = menu.findChild(QAction, "pause_action")
        pause.setText("继续活动" if self.settings.paused else "暂停活动")
        pause.setEnabled(True)
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
        return self.settings.paused

    @property
    def movement_paused(self) -> bool:
        return not self._presentation_ready or self.settings.paused or self.panel_pause_active

    def start_state(self, state: str, duration_ms: int = 0) -> None:
        if state not in ANIMATIONS:
            return
        self._top_transition = None
        self._sync_expressions()
        self.state_timer.stop()
        self.state, self.animation_cycles = state, 0
        self._state_remaining_ms = -1
        self._overlay_side = None
        self._activate_state_renderer()
        self._render_frame()
        if duration_ms:
            self.state_timer.start(duration_ms)





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
        if hasattr(self, "monitor_button"):
            self._position_monitor_button()
            self._position_monitor_overlays()

    def _set_base(self, mode: str) -> None:
        if not mode.startswith("climb_"):
            self._climb_cadence.stop()
        self.base_mode = mode
        self.attachment = {"climb_left": "left", "climb_right": "right", "top_swing": "top"}.get(mode)
        if self._has_support_pose() and self._explicit_effect in {"glasses", "fan"}:
            self._set_expression(self._explicit_effect, False)
            self._explicit_effect = None
        self._sync_expressions()

    def _begin_walk(self, _duration_ms: int | None = None) -> None:
        if self.movement_paused or self.dragging or self.base_mode != "ground" or self.sleep_phase:
            return
        area = self._screen_area()
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
        self._motion_updated_at = self.walk_leg_started_at
        self.walk_leg_duration_ms = max(1, int(_duration_ms or 1000))
        self.motion_mode = "walk_out"
        self.start_state("walk_right" if self.direction > 0 else "walk_left")
        self.motion_timer.start()

    def _motion_step(self) -> None:
        if self.movement_paused or self.dragging or self._wall_resting():
            return
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self._motion_updated_at))
        self._motion_updated_at = now
        area = self._screen_area()
        if self.motion_mode in ("walk_out", "walk_back"):
            elapsed_ms = max(0.0, (time.monotonic() - self.walk_leg_started_at) * 1000.0)
            progress = min(1.0, elapsed_ms / self.walk_leg_duration_ms)
            x = round(self.walk_leg_start_x + (self.walk_leg_end_x - self.walk_leg_start_x) * progress)
            self.move(x, self._floor_y(area))
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
            return  # Native phase and geometry exclusively drive wall travel.
        elif self.motion_mode == "fall":
            floor = self._floor_y(area)
            previous_speed = self._fall_velocity
            self._fall_velocity = min(320.0 * self.settings.scale,
                                      previous_speed + 640.0 * self.settings.scale * dt)
            self._motion_y += (previous_speed + self._fall_velocity) * .5 * dt
            self._motion_velocity = (0.0, self._fall_velocity)
            y = round(self._motion_y)
            if y >= floor:
                self.move(self.x(), floor)
                self.motion_mode = None
                self._fall_velocity = 0.0
                self._motion_velocity = (0.0, 0.0)
                self.motion_timer.stop()
                self.start_state("land")
            else:
                self.move(self.x(), y)

    def _schedule_behavior(self) -> None:
        if self.settings.roaming_enabled and not self.movement_paused and self.isVisible() and self.base_mode == "ground" and not self.sleep_phase:
            self.behavior_timer.start(random.randint(60_000, 90_000))

    def _choose_behavior(self) -> None:
        if not self.settings.roaming_enabled or self.movement_paused or self.dragging or not self.isVisible() or self.motion_mode or \
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
        area = self._screen_area()
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)), self._floor_y(area))
        self.start_state("idle")
        self._schedule_behavior()

    def _start_temporary(self, state: str, duration_ms: int = 0) -> None:
        if (state == "petting" and self._motion_polish_supported and self._live2d_active
                and self.base_mode == "top_swing" and self.state in {"swing_cycle", "swing_idle"}
                and not self._top_transition and not self.activity_paused):
            # Snapshot stroke direction; the 3-second overlay never changes the
            # seated motion token, swing phase, or either supporting arm.
            self._show_effect("swing_petting_" + getattr(self, "_petting_side", "right"), 3000)
            return
        if self._has_support_pose() and state in {"happy", "petting", "talk"}:
            self._show_effect("blush" if state == "petting" else "smile", duration_ms or 5000)
            return
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
        self.bubble.show_message(text, self.geometry(), self._screen_area())

    def _single_click(self) -> None:
        if not self.activity_paused and not self.dragging and not self.sleep_phase:
            self.say(choose_dialogue())

    def _begin_wake(self) -> None:
        if self.sleep_phase in ("enter", "loop"):
            self.behavior_timer.stop()
            self.sleep_phase = "exit"
            self.start_state("sleep_exit")

    def _check_system_idle(self) -> None:
        if not self._presentation_ready:
            return
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
        area = self._screen_area()
        on_floor = abs(self.y() - self._floor_y(area)) <= 2
        if self.base_mode == "ground" and on_floor and not self.motion_mode and \
                idle >= SLEEP_AFTER_SECONDS and now - self.awake_cycle_started >= SLEEP_AFTER_SECONDS:
            self.behavior_timer.stop()
            self.sleep_phase = "enter"
            self.start_state("sleep_enter")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._presentation_ready:
            return
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
        if not self._presentation_ready:
            return
        if self.press_global is not None and event.buttons() & Qt.LeftButton:
            current, delta = event.globalPosition().toPoint(), event.globalPosition().toPoint() - self.press_global
            if not self.dragging and delta.manhattanLength() >= DRAG_THRESHOLD:
                self.dragging = True
                self.click_timer.stop()
                self._detach_for_drag(delta.x())
            if self.dragging and self.press_window is not None:
                dx = current.x() - (self.last_drag_global.x() if self.last_drag_global else current.x())
                if abs(dx) >= 2:
                    self._flip_panel_for_drag(dx)
                    if not self.activity_paused and self.state != (wanted := "drag_right" if dx > 0 else "drag_left"):
                        self.start_state(wanted)
                self.move(self.press_window + delta)
                self.last_drag_global = current
                self.current_screen = self._screen_for_point(current)
                self._preview_paused_drag(dx)
            event.accept()
        else:
            if self._pointer_near_model(event.position().toPoint()):
                self._show_monitor_button()
            else:
                self.monitor_hide_timer.start(800)
            self._track_petting(event.position().toPoint(), event.buttons())
            super().mouseMoveEvent(event)

    def _detach_for_drag(self, dx: int = 0) -> None:
        self._cancel_climb_endpoint()
        self.behavior_timer.stop(); self.state_timer.stop(); self.motion_timer.stop()
        self.motion_mode = self.sleep_phase = self.sleep_started_at = None
        self.awake_cycle_started = time.monotonic()
        self._set_base("ground")
        self._drag_effect_until = 0.0
        self._fall_velocity = 0.0
        self._motion_velocity = (0.0, 0.0)
        self._paused_drag_contact = None
        self._set_expression("dizzy", not self.activity_paused)
        # Detachment itself must revoke the supported motion token. Vertical
        # movement may never reach the later horizontal direction threshold.
        # start_state also cancels petting and any unfinished cleanup segment;
        # the background cleanup operation continues independently.
        self.start_state("drag_left" if dx < 0 else "drag_right")

    def _preview_paused_drag(self, dx: int = 0) -> None:
        if not self.activity_paused or not self.dragging:
            return
        area = self._screen_area()
        contact = ("top" if self.y() <= area.top() + EDGE_SNAP_PX else
                   "left" if self.x() <= area.left() + EDGE_SNAP_PX else
                   "right" if self.x() + self.width() >= area.right() - EDGE_SNAP_PX else None)
        wanted = {"top": "swing_idle", "left": "climb_left", "right": "climb_right"}.get(contact)
        if wanted is None:
            wanted = "drag_left" if dx < 0 else "drag_right"
        self._paused_drag_contact = contact
        if self.state != wanted:
            # Only sample the image while dragging. Screen contact is applied on release.
            self.start_state(wanted)

    def _track_petting(self, local: QPoint, buttons: Qt.MouseButton) -> None:
        if (buttons != Qt.NoButton or not self._inside_petting_head(local) or self.activity_paused
                or self.sleep_phase or self.dragging or not self.isVisible()):
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
            self._petting_side = "right" if xs[-1] > xs[-2] else "left"
            self.pet_trace.clear()
            self._start_temporary("petting")

    def _inside_petting_head(self, local: QPoint) -> bool:
        if not self._motion_polish_supported or not self._live2d_active:
            return local.y() <= self.height() * .46
        bounds = self._renderer_geometry.get("headBounds")
        if (not isinstance(bounds, list) or len(bounds) != 4
                or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds)
                or bounds[2] <= 0 or bounds[3] <= 0):
            return False  # A new token must first supply its actual native head.
        x, y, width, height = bounds
        return x <= local.x()/self.width() <= x+width and y <= local.y()/self.height() <= y+height

    def _cancel_swing_petting(self) -> None:
        self.pet_trace.clear()
        if self._explicit_effect in {"swing_petting_left", "swing_petting_right"}:
            self._set_expression(self._explicit_effect, False)
            self._explicit_effect = None
            self._explicit_effect_until = 0.0

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self._presentation_ready:
            return
        if event.button() == Qt.LeftButton and self.press_global is not None:
            was_dragging = self.dragging
            self.press_global = self.press_window = self.last_drag_global = None
            self.dragging = False
            if was_dragging:
                self._drag_effect_until = 0.0
                self._set_expression("dizzy", False)
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
        self._paused_drag_contact = None
        self._set_expression("dizzy", False)
        self._drag_effect_until = 0.0
        area = self._screen_area()
        if self.y() <= area.top() + EDGE_SNAP_PX:
            self._attach_top(True)
        elif self.x() <= area.left() + EDGE_SNAP_PX:
            self._attach_side("left")
        elif self.x() + self.width() >= area.right() - EDGE_SNAP_PX:
            self._attach_side("right")
        elif self.y() + self.height() < area.bottom() - 3:
            self._set_base("ground")
            self.motion_mode = "fall"
            self._fall_velocity = 0.0
            self._motion_velocity = (0.0, 0.0)
            self._motion_updated_at = time.monotonic()
            self._motion_y = float(self.y())
            self.start_state("fall_float")
            if not self.movement_paused:
                self.motion_timer.start()
        else:
            self._set_ground_idle()



    def _position_side(self, side: str, *, clamp_ceiling: bool = True) -> None:
        area = self._screen_area()
        old_y = self.y()
        model_anchor = self._model_anchor(side) if self._live2d_active else None
        ceiling = area.top() - round(model_anchor[1] * self.height()) if model_anchor else area.top()
        y = min(old_y, self._floor_y(area))
        if clamp_ceiling and not (self._climb_endpoint_supported and self._live2d_active
                and self.motion_mode == "climb" and self.state.startswith("climb_")):
            y = max(ceiling, y)
        if model_anchor is not None:
            contact = round(model_anchor[0] * self.width())
            x = area.left() - contact if side == "left" else area.right() - contact
        else:
            return  # Keep the saved contact until native geometry is available.
        self.move(x, y)
        if y != old_y:
            self._motion_y = float(y)

    def _attach_side(self, side: str) -> None:
        self._climb_cadence.start()
        self._climb_clock = time.monotonic()
        self._climb_control_stamp = None
        self._set_base(f"climb_{side}")
        self.resume_base, self.motion_mode = self.base_mode, "climb"
        self._position_side(side)
        self.start_state(f"climb_{side}")
        self._motion_updated_at = time.monotonic()
        self._motion_y = float(self.y())
        if not self.movement_paused:
            self.motion_timer.start()

    def _position_top(self) -> None:
        area = self._screen_area()
        anchor = (self._model_anchor("hang") or self._model_anchor("top")) if self._live2d_active else None
        margin = round((anchor[1] if anchor else 0.0) * self.height())
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)), area.top() - margin)

    def _begin_top_transition(self, endpoint: dict | None = None) -> None:
        side = "left" if self.base_mode == "climb_left" else "right"
        name = "climb_to_top_" + side
        if self._climb_endpoint_supported and name in self.live2d_states and endpoint is None:
            return  # Refined legs can enter the authored handoff only at phase 0/1.
        if not self._live2d_active or name not in self.live2d_states:
            return self._attach_top(True)
        # Keep the same painted point throughout transfer. Switching to a mesh
        # edge at this boundary changes the contact by its transparent padding.
        anchor_key = next((key for key in ("nearGrip", "grip" + side.title(), side)
                           if self._model_anchor(key) is not None), None)
        grip = self._model_anchor(anchor_key) if anchor_key else None
        hang = self._model_anchor("hang") or self._model_anchor("top")
        if grip is None or hang is None:
            return self._attach_top(True)
        area = self._screen_area()
        final_x = max(area.left(), min(self.x(), area.right() - self.width() + 1))
        self._top_transition = {"name": name, "side": side, "phase": 0, "progress": 0.0,
            "anchor_key": anchor_key,
            "wall": (self.x() + grip[0] * self.width(), self.y() + grip[1] * self.height()),
            "hang": (final_x + hang[0] * self.width(), area.top())}
        self.motion_mode = None
        self.motion_timer.stop()
        self._activate_state_renderer()

    def _position_top_transition(self, event: dict) -> None:
        transition = self._top_transition
        grip = self._model_anchor(transition['anchor_key'])
        hang = self._model_anchor("hang") or self._model_anchor("top")
        progress = event.get('transitionProgress')
        if grip is None or hang is None or not isinstance(progress, (int, float)) or not math.isfinite(progress):
            return
        transition['progress'] = max(transition['progress'], min(1.0, max(0.0, progress)))
        # Remain attached to the wall until the authored top-grab event. The
        # native progress parameter then migrates the contact constraint smoothly.
        t = max(0.0, (transition['progress'] - .32 / 1.8) / (1 - .32 / 1.8)) if transition['phase'] else 0.0
        blend = t * t * (3 - 2 * t)
        wall_x, wall_y = transition['wall']
        hang_x, hang_y = transition['hang']
        x = (wall_x - grip[0] * self.width()) * (1 - blend) + (hang_x - hang[0] * self.width()) * blend
        y = (wall_y - grip[1] * self.height()) * (1 - blend) + (hang_y - hang[1] * self.height()) * blend
        self.move(round(x), round(y))

    def _attach_top(self, run_intro: bool = True) -> None:
        self._set_base("top_swing")
        self.resume_base, self.motion_mode = self.base_mode, None
        self.motion_timer.stop()
        self._position_top()
        self.start_state("swing_cycle" if run_intro and not self.activity_paused else "swing_idle")

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self._presentation_ready:
            return
        if event.button() == Qt.LeftButton:
            self.click_timer.stop(); self.suppress_release_click = True
            if self.activity_paused:
                pass
            elif self.sleep_phase:
                self._begin_wake()
            else:
                self._start_temporary("happy")
                self.bubble.show_message(choose_happy_dialogue(), self.geometry(), self._screen_area())
                effects = ("smile", "blush", "maple") if self._has_support_pose() else ("smile", "blush", "glasses", "fan", "maple")
                self._show_effect(random.choice(effects))
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

    def _pointer_near_model(self, point: QPoint) -> bool:
        if not self._presentation_ready:
            return False
        bounds = self._content_rect_global().translated(-self.pos())
        radius = max(8, round(20 * self.settings.scale))
        return bounds.adjusted(-radius, -radius, radius, radius).contains(point)

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
        if self._live2d_active and self._renderer_geometry.get("bounds"):
            x, y, width, height = self._renderer_geometry["bounds"]
            return QRect(round(x * self.width()), round(y * self.height()),
                         max(1, round(width * self.width())), max(1, round(height * self.height()))).translated(self.pos())
        bounds = self.rect()
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
        area = self._screen_area()
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
        self._panel_motion_paused_at = time.monotonic()
        self._sync_runtime_pause()
        self.behavior_timer.stop(); self.motion_timer.stop()
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
        if self.motion_mode in {"walk_out", "walk_back"}:
            self.walk_leg_started_at += max(0.0, time.monotonic() - getattr(self, "_panel_motion_paused_at", time.monotonic()))
        self._sync_runtime_pause()
        self._panel_side = self._overlay_side = None
        cursor = QCursor.pos()
        pointer_near = self.geometry().contains(cursor) or self.monitor_button.geometry().contains(cursor)
        if self.settings.monitor_always_visible or pointer_near:
            self.monitor_capsule.show()
        else:
            self.monitor_button.hide(); self.monitor_capsule.hide()
        self._position_monitor_overlays()
        if not self.panel_previous_paused and not self.settings.paused:
            self._resume_activity()

    def _position_details_panel(self) -> None:
        if self._panel_positioning:
            return
        area = self._screen_area(); gap = 10
        content = self._content_rect_global()
        right_x = content.right() + gap + 1
        left_x = content.left() - gap - self.details_panel.width()
        if self._panel_side is None or not self.dragging:
            right_overflow = max(0, right_x + self.details_panel.width() - 1 - area.right())
            left_overflow = max(0, area.left() - left_x)
            self._panel_side = "right" if right_overflow <= left_overflow else "left"
        self._panel_positioning = True
        try:
            attached = self.base_mode in {"climb_left", "climb_right", "top_swing"}
            if self._panel_side == "right" and not self.dragging and not attached:
                overflow = right_x + self.details_panel.width() - 1 - area.right()
                if overflow > 0:
                    self.move(self.x() - overflow, self.y())
            elif self._panel_side == "left" and not self.dragging and not attached:
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
            area = self._screen_area(); content = self._content_rect_global(); gap = 10
            x = (content.left() - gap - self.details_panel.width() if desired == "left"
                 else content.right() + gap + 1)
            if area.left() <= x and x + self.details_panel.width() - 1 <= area.right():
                self._panel_side = desired
                self._overlay_side = None

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if hasattr(self, "bubble") and self.bubble.isVisible():
            self.bubble.follow_pet(self.geometry(), self._screen_area())
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
            if not self.activity_paused and self.state != (wanted := "drag_right" if dx > 0 else "drag_left"):
                self.start_state(wanted)
        self.move(self.press_window + (global_pos - self.press_global)); self.last_drag_global = global_pos
        self.current_screen = self._screen_for_point(global_pos)
        self._preview_paused_drag(dx)

    def _panel_drag_finish(self, global_pos: QPoint) -> None:
        if not self._panel_dragging:
            return
        self._panel_dragging = False; self.dragging = False
        self._drag_effect_until = 0.0
        self._set_expression("dizzy", False)
        self.press_global = self.press_window = self.last_drag_global = None
        self.current_screen = self._screen_for_point(global_pos)
        self._settle_after_drag(); self._save_position(); self._panel_side = None; self._position_details_panel()
        self.details_panel.reset_drag(); self.monitor_timer.start(2000)

    def open_auto_clean_settings(self) -> None:
        dialog = AutoCleanDialog(self.settings)
        area = self._screen_area()
        dialog.setGeometry(QRect(area.center().x() - 175, area.center().y() - 115, 350, 230))
        if dialog.exec():
            dialog.apply(self.settings); self._save()

    def _offer_clean_task_install(self) -> None:
        # Authorization is never installed or requested at startup.
        return

    def _poll_authorization_probe(self) -> None:
        return

    def repair_clean_authorization(self) -> None:
        self._begin_clean_task_install(False)

    def _set_cleanup_progress(self, result: dict, *, active: bool = True) -> None:
        self._cleanup_feedback_active = active
        self.details_panel.set_cleaning(active, cancelling=self._cleanup_cancel_requested)
        self.monitor_button.set_cleaning(active)
        self._show_cleanup_feedback(result)

    def _begin_clean_task_install(self, pending_cleanup: bool) -> None:
        if self._install_future is not None:
            self._install_pending_cleanup |= pending_cleanup
            self._show_cleanup_feedback(self._cleanup_feedback)
            return
        self._install_pending_cleanup = pending_cleanup
        self._set_cleanup_progress({"status": "authorizing", "message": "请确认 Windows 管理员授权，仅本次运行有效。"})
        self._show_cleanup_feedback(self._cleanup_feedback)
        self._install_future = self._install_executor.submit(request_session_authorization)
        self.install_poll_timer.start()

    def _poll_clean_task_install(self) -> None:
        future = self._install_future
        if future is None or not future.done():
            return
        self.install_poll_timer.stop(); self._install_future = None
        try: result = future.result()
        except Exception as exc: result = {"ok": False, "status": "failed", "message": str(exc)}
        pending = self._install_pending_cleanup
        self._install_pending_cleanup = False
        valid = result.get("ok") is True and result.get("valid") is True
        cancelled = self._cleanup_cancel_requested
        self._cleanup_cancel_requested = False
        self._set_cleanup_progress(result, active=False)
        self._show_cleanup_feedback(result)
        if valid:
            self._auto_authorization_notice = False
            if pending and not cancelled and not self._quitting:
                self.start_memory_cleanup()

    def start_memory_cleanup(self, *, manual: bool = True) -> None:
        from diagnostics import event
        event("cleanup_button_requested", manual=manual, operation_id=self.cleanup_operation,
              pending=self._cleanup_future is not None, authorizing=self._install_future is not None)
        if self.cleanup_operation or self._cleanup_future is not None or self._install_future is not None:
            if manual: self._show_cleanup_feedback(self._cleanup_feedback)
            return
        if not manual and not session_is_valid():
            if not self._auto_authorization_notice:
                self._auto_authorization_notice = True
                self._set_cleanup_progress({"status": "authorization_required", "message": "自动清理等待本次授权，请主动点击一次清理。"}, active=False)
            return
        self._cleanup_manual = manual
        self._cleanup_cancel_requested = False
        self.settings.last_clean_attempt_timestamp = time.time()
        self._save()
        self._set_cleanup_progress({"status": "starting", "message": "正在检查清理助手与本次授权。"})
        if manual: self._show_cleanup_feedback(self._cleanup_feedback)
        self._cleanup_future = self._install_executor.submit(begin_cleanup)
        self.cleanup_poll_timer.setInterval(400)
        self.cleanup_poll_timer.start()

    def _begin_cleanup_operation(self, operation: str) -> None:
        self.cleanup_operation, self.cleanup_started_at = operation, time.monotonic()
        self._cleanup_unresponsive = False
        self.cleanup_finish_pending = False; self.cleanup_result = None
        self._set_cleanup_progress({"status": "queued", "operation_id": operation})

    def _poll_cleanup(self) -> None:
        if self._cleanup_future is not None:
            if not self._cleanup_future.done(): return
            future, self._cleanup_future = self._cleanup_future, None
            try: start = future.result()
            except Exception as exc: start = {"ok": False, "status": "failed", "message": str(exc)}
            resuming = start.get("status") == "busy" and isinstance(start.get("operation_id"), str)
            if not start.get("ok") and not resuming:
                self.cleanup_poll_timer.stop()
                cancelled = self._cleanup_cancel_requested
                self._cleanup_cancel_requested = False
                self._set_cleanup_progress(start, active=False)
                if start.get("status") == "authorization_required" and cancelled:
                    self._show_cleanup_feedback({"status": "cancelled", "operationStarted": False, "message": "已取消本次请求。"})
                elif start.get("status") == "authorization_required" and self._cleanup_manual:
                    self._begin_clean_task_install(True)
                else: self._show_cleanup_feedback(start)
                return
            self._begin_cleanup_operation(start["operation_id"])
            if self._cleanup_cancel_requested: self.cancel_memory_cleanup()
        if not self.cleanup_operation:
            self.cleanup_poll_timer.stop(); return
        result = read_cleanup(self.cleanup_operation)
        if result is None or result.get("operation_id") != self.cleanup_operation: return
        self._set_cleanup_progress(result)
        if result.get("terminal") is True and result.get("processExitVerified") is True:
            self.cleanup_result = result
            self._finish_cleanup_operation()
        elif result.get("status") == "unresponsive" and not self._cleanup_unresponsive:
            self._cleanup_unresponsive = True
            self.cleanup_poll_timer.setInterval(2000)
            self._show_cleanup_feedback(result)

    def cancel_memory_cleanup(self) -> None:
        if self._install_future is not None:
            self._install_pending_cleanup = False
            self._cleanup_cancel_requested = True
            self._set_cleanup_progress({"status": "cancelling", "message": "本次清理已取消；若授权窗口仍在，请选择否。"})
            return
        if self._cleanup_future is not None:
            self._cleanup_cancel_requested = True
            if self._cleanup_future.cancel():
                self._cleanup_future = None
                self._cleanup_cancel_requested = False
                self.cleanup_poll_timer.stop()
                result = {"status": "cancelled", "operationStarted": False, "message": "已取消尚未开始的清理请求。"}
                self._set_cleanup_progress(result, active=False); self._show_cleanup_feedback(result)
            else:
                self._set_cleanup_progress({"status": "cancelling", "message": "等待启动响应，取得操作编号后取消本次清理。"})
            return
        if self.cleanup_operation:
            result = cancel_cleanup(self.cleanup_operation)
            self._cleanup_cancel_requested = result.get("ok") is True
            self._set_cleanup_progress(result)
            if not self._cleanup_cancel_requested: self._show_cleanup_feedback(result)

    def _show_cleanup_feedback(self, result: dict) -> None:
        from diagnostics import event
        self._cleanup_feedback = dict(result)
        text = cleanup_summary(result)
        stamp = (result.get('operation_id'), result.get('status'), text, self._cleanup_feedback_active)
        if stamp != self._cleanup_feedback_stamp:
            self._cleanup_feedback_stamp = stamp
            event("cleanup_feedback", status=result.get("status"), operation_id=result.get("operation_id"),
                  error_code=result.get("error_code"), message=result.get("message"),
                  current_step=result.get("current_step", result.get("currentStep", result.get("step"))))
        self.details_panel.show_result(result)
        self.monitor_button.setToolTip(text)
        self.bubble.show_cleanup(result, self._cleanup_feedback_active, self.geometry(),
                                 self._screen_area(), self.isVisible())

    def _finish_cleanup_operation(self) -> None:
        result = self.cleanup_result or {}
        if (result.get("operation_id") != self.cleanup_operation or
                not (result.get("terminal") is True and result.get("processExitVerified") is True)):
            return
        self.cleanup_poll_timer.stop()
        self.cleanup_operation = None; self.cleanup_finish_pending = False
        self._cleanup_cancel_requested = False; self._cleanup_unresponsive = False
        self._set_cleanup_progress(result, active=False)
        if result.get("status") == "succeeded":
            self.settings.last_clean_timestamp = float(result.get("completed_at", time.time()))
        self._show_cleanup_feedback(result)
        self._save()

    def _check_auto_cleanup(self) -> None:
        if self.cleanup_operation or self._cleanup_future is not None or self._install_future is not None or not (self.settings.auto_clean_interval_enabled or self.settings.auto_clean_memory_enabled):
            return
        if auto_cleanup_due(self.settings, self.latest_memory.load_percent, time.time(), self.auto_clean_cycle_started):
            self.start_memory_cleanup(manual=False)

    def toggle_pause(self) -> None:
        self.settings.paused = not self.settings.paused
        self._sync_runtime_pause()
        self.behavior_timer.stop(); self.motion_timer.stop()
        if not self.settings.paused:
            self._resume_activity()
        self._save()

    def _resume_activity(self) -> None:
        if self.activity_paused or not self._presentation_ready or not self.isVisible():
            return
        self._motion_updated_at = time.monotonic()
        self._motion_y = float(self.y())
        if self.motion_mode is not None and not self.movement_paused:
            self.motion_timer.start()
        self._send_renderer("pause", paused=False)
        if self.state == "idle":
            self._schedule_behavior()

    def toggle_autostart(self, enabled: bool) -> None:
        try:
            if not set_autostart(enabled):
                raise RuntimeError("系统未允许写入开机启动设置")
        except (OSError, RuntimeError, ValueError) as error:
            action = self.sender()
            if isinstance(action, QAction):
                action.blockSignals(True)
                action.setChecked(self.settings.autostart)
                action.blockSignals(False)
            self.say(f"开机启动设置失败：{error}")
            return
        self.settings.autostart = enabled
        self._save()

    def restore_default_size(self) -> None:
        self.set_scale(1.0)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if not hasattr(self, "runtime_timer"):
            return
        self.close_details_panel()
        self.bubble.hide()
        self.monitor_button.hide()
        self.monitor_capsule.hide()
        self.behavior_timer.stop()
        self.motion_timer.stop()
        self._sync_runtime_pause()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "runtime_timer"):
            self.bubble.restore_cleanup(self.geometry(), self._screen_area(), True)
            if self._presentation_ready and self.live2d_host is not None:
                self.live2d_host.view.show()
            self._sync_runtime_pause()
            self._resume_activity()

    def hide_pet(self) -> None:
        self.close_details_panel()
        self.bubble.hide(); self.monitor_button.hide(); self.monitor_capsule.hide()
        self.behavior_timer.stop(); self.motion_timer.stop(); self.hide()
        self._sync_runtime_pause()

    def show_pet(self) -> None:
        self.show(); self.raise_(); self._position_for_base_or_clamp()
        if self._presentation_ready and self.live2d_host is not None:
            self.live2d_host.view.show()
        self._sync_runtime_pause()
        self._resume_activity()

    def _position_for_base_or_clamp(self) -> None:
        if self.base_mode.startswith("climb_"):
            self._position_side(self.base_mode.removeprefix("climb_"))
        elif self.base_mode == "top_swing": self._position_top()
        else: self._clamp_to_current_screen()

    def _position_for_attachment_or_clamp(self) -> None:
        self._position_for_base_or_clamp()

    def _clamp_to_current_screen(self) -> None:
        area = self._screen_area()
        old_y = self.y()
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))
        if self.y() != old_y:
            self._motion_y = float(self.y())

    def _save_position(self) -> None:
        self.settings.last_x, self.settings.last_y = self.x(), self.y(); self._save()

    def _save(self) -> None:
        try: save_settings(self.settings)
        except OSError: pass

    def quit_app(self) -> None:
        self._save_position(); self.bubble.close(); self.monitor_button.close()
        self.monitor_capsule.close(); self.details_panel.close()
        import threading
        threading.Thread(target=close_cleanup_session, name="cleanup-session-close", daemon=True).start()
        self._install_executor.shutdown(wait=False, cancel_futures=True)
        if self._quitting:
            return
        self._quitting = True
        self.tray.hide()
        if self.desktop_activity is not None:
            self.desktop_activity.stopped.connect(self.app.quit)
        self._stop_runtime()
        if self.desktop_activity is None or not self.desktop_activity.stopping:
            self.app.quit()


def acquire_single_instance():
    from PySide6.QtCore import QLockFile, QStandardPaths
    import hashlib
    profile = os.environ.get("MEINIFENG_PROFILE_DIRECTORY", "")
    suffix = "-" + hashlib.sha256(str(Path(profile).resolve()).encode()).hexdigest()[:16] if profile else ""
    lock = QLockFile(str(Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation)) /
                         f"meinifeng-desktop-pet{suffix}.lock"))
    lock.setStaleLockTime(5000)
    if lock.tryLock(100): return lock
    if lock.removeStaleLockFile() and lock.tryLock(100): return lock
    return None


def run(*, observer=None) -> int:
    if os.name == "nt": os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    try:
        from live2d_host import register_live2d_scheme
        register_live2d_scheme()
    except (ImportError, AttributeError):
        pass
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    from diagnostics import install_qt_logging
    install_qt_logging()
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
    settings = load_settings()
    pet = PetWindow(app, settings); pet.show()
    if observer is not None:
        observer(app, pet)
    app._pet_window, app._instance_lock = pet, lock
    if os.environ.get("MEINIFENG_SMOKE_TEST") == "1": QTimer.singleShot(1600, app.quit)
    return app.exec()
