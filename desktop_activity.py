"""Optional Windows activity signals, with no key text or audio capture.

Keyboard: background Raw Input, reading RID_HEADER only (no RAWKEYBOARD).
Mouse: left-button pressed state and the current global cursor point only.
Audio: default rendering endpoint's IAudioMeterInformation scalar peak only.

Native work is disabled automatically for Qt offscreen/minimal platforms and
when CUTEMAPLE_DISABLE_ACTIVITY=1. All providers suspend while hidden/paused.
COM objects are created, polled and released only inside the isolated audio child process.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QTimer, Signal, Qt
from PySide6.QtGui import QCursor

from audio_process import AudioProcess

WM_INPUT = 0x00FF
RID_HEADER = 0x10000005
RIM_TYPEKEYBOARD = 1
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
VK_LBUTTON = 0x01
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN, WM_QUIT = 0x100, 0x104, 0x12


class _RawInputHeader(ctypes.Structure):
    _fields_ = [("dwType", ctypes.c_uint32), ("dwSize", ctypes.c_uint32),
                ("hDevice", ctypes.c_void_p), ("wParam", ctypes.c_size_t)]


class _RawInputDevice(ctypes.Structure):
    _fields_ = [("usUsagePage", ctypes.c_ushort), ("usUsage", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint32), ("hwndTarget", ctypes.c_void_p)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _Message(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint32),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint32), ("pt", _Point),
                ("lPrivate", ctypes.c_uint32)]


def hardware_activity_allowed() -> bool:
    platform = os.environ.get("QT_QPA_PLATFORM", "").split(":", 1)[0].lower()
    disabled = any(os.environ.get(name, "").lower() in {"1", "true", "yes"}
                   for name in ("CUTEMAPLE_DISABLE_ACTIVITY", "MEINIFENG_DISABLE_ACTIVITY"))
    return os.name == "nt" and platform not in {"offscreen", "minimal"} and not disabled


def _user32():
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_RawInputDevice), ctypes.c_uint, ctypes.c_uint]
    api.RegisterRawInputDevices.restype = ctypes.c_int
    api.GetRawInputData.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_uint), ctypes.c_uint]
    api.GetRawInputData.restype = ctypes.c_uint
    api.GetAsyncKeyState.argtypes = [ctypes.c_int]
    api.GetAsyncKeyState.restype = ctypes.c_short
    return api


class _KeyboardHook:
    """Activity-only fallback for remote input, on its own short-callback message pump.

    lParam is passed unchanged to the next hook and is never dereferenced. No
    KBDLLHOOKSTRUCT, virtual key, scan code, modifiers or characters are read.
    """
    def __init__(self, api_factory=_user32):
        self._factory = api_factory
        self._thread = None
        self._thread_id = 0
        self._api = None
        self._handle = None
        self._callback_ref = None
        self._stop = threading.Event()
        self._pending = threading.Event()
        self.state = 'stopped'
        self.error = ''
        self.activity_count = 0

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def registered(self):
        return self.state == 'ready' and self._handle is not None

    def _callback(self, code, message, opaque):
        try:
            if code >= 0 and message in (WM_KEYDOWN, WM_SYSKEYDOWN) and not self._stop.is_set():
                self.activity_count += 1
                self._pending.set()
        except Exception:
            # Exceptions must never escape a native callback or swallow another app's input.
            pass
        return self._api.CallNextHookEx(None, code, message, opaque)

    def start(self):
        if self.running or self.state == 'error':
            return
        self._stop.clear(); self._pending.clear(); self.state = 'starting'
        self._thread = threading.Thread(target=self._run, name='keyboard-activity-hook', daemon=True)
        self._thread.start()

    def _run(self):
        try:
            api = self._factory()
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentThreadId.restype = ctypes.c_uint32
            kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
            api.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, ctypes.c_void_p, ctypes.c_uint32]
            api.SetWindowsHookExW.restype = ctypes.c_void_p
            api.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
            api.CallNextHookEx.restype = ctypes.c_ssize_t
            api.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            api.UnhookWindowsHookEx.restype = ctypes.c_int
            api.GetMessageW.argtypes = [ctypes.POINTER(_Message), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
            api.GetMessageW.restype = ctypes.c_int
            api.PeekMessageW.argtypes = [ctypes.POINTER(_Message), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
            api.PostThreadMessageW.argtypes = [ctypes.c_uint32, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            api.PostThreadMessageW.restype = ctypes.c_int
            self._api = api
            message = _Message()
            api.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # Create queue before publishing its ID.
            self._thread_id = kernel.GetCurrentThreadId()
            self._callback_ref = callback_type(self._callback)
            if self._stop.is_set():
                return
            self._handle = api.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback_ref, kernel.GetModuleHandleW(None), 0)
            if not self._handle:
                raise OSError(f'Keyboard activity hook: WinError {ctypes.get_last_error()}')
            self.state = 'ready'
            while not self._stop.is_set():
                result = api.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    if result < 0:
                        raise OSError('Keyboard activity message pump failed')
                    break
        except (OSError, AttributeError, RuntimeError) as error:
            self.state = 'error'; self.error = str(error)[:240]
        finally:
            if self._handle and self._api:
                if not self._api.UnhookWindowsHookEx(self._handle):
                    self.state = 'error'
                    self.error = f'Keyboard activity unhook failed: WinError {ctypes.get_last_error()}'
            self._handle = None
            self._thread_id = 0
            self._pending.clear()
            # Keep the callback object owned by this hook even after unregistration.
            if self.state != 'error':
                self.state = 'stopped'

    def drain(self):
        pending = self._pending.is_set()
        self._pending.clear()
        return pending and not self._stop.is_set()

    def stop(self):
        self._stop.set(); self._pending.clear()
        if self._api and self._thread_id:
            self._api.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


class _KeyboardFilter(QAbstractNativeEventFilter):
    def __init__(self, api, hwnd: int, callback) -> None:
        super().__init__()
        self.api, self.hwnd, self.callback = api, hwnd, callback
        self.registered = False
        self.error = ''
        self.activity_count = 0

    def start(self) -> bool:
        if self.registered:
            return True
        device = _RawInputDevice(0x01, 0x06, RIDEV_INPUTSINK, self.hwnd)
        self.registered = bool(self.api.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)))
        self.error = '' if self.registered else f'Raw Input registration failed: WinError {ctypes.get_last_error()}'
        return self.registered

    def stop(self) -> None:
        if self.registered:
            device = _RawInputDevice(0x01, 0x06, RIDEV_REMOVE, None)
            self.api.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
            self.registered = False

    def read_activity(self, handle: int) -> bool:
        header = _RawInputHeader()
        size = ctypes.c_uint(ctypes.sizeof(header))
        copied = self.api.GetRawInputData(handle, RID_HEADER, ctypes.byref(header),
                                          ctypes.byref(size), ctypes.sizeof(header))
        return copied == ctypes.sizeof(header) and header.dwType == RIM_TYPEKEYBOARD

    def nativeEventFilter(self, event_type, message):
        if (self.registered and bytes(event_type) in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}
                and message):
            msg = _Message.from_address(int(message))
            if msg.hwnd == self.hwnd and msg.message == WM_INPUT:
                if self.read_activity(msg.lParam):
                    self.activity_count += 1
                    self.callback()
        # Keep normal event delivery, including DefWindowProc's raw-input cleanup.
        return False, 0


class DesktopActivity(QObject):
    """Qt facade. Signals contain only activity booleans or one click position.

    ``start(hwnd)`` is idempotent; ``stop()`` may be followed by another start.
    Keep this object alive for the lifetime of its Qt native window.
    """

    stopped = Signal()
    keyboardActivity = Signal()
    mouseClick = Signal(int, int)
    audioActivityChanged = Signal(bool)
    audioLevelChanged = Signal(float)
    statusChanged = Signal(dict)

    def __init__(self, parent=None, *, hardware: bool | None = None) -> None:
        super().__init__(parent)
        # Explicit True never bypasses the offscreen/environment opt-out.
        self.hardware_available = hardware_activity_allowed() and hardware is not False
        self._keyboard_enabled = True
        self._audio_enabled = True
        self._suspended = False
        self._started = False
        self._hwnd = 0
        self._api = None
        self._filter = None
        self._hook = None
        self._worker = None
        self._audio_active = False
        self._mouse_down = False
        self._last_keyboard_at = -float('inf')
        self._audio_endpoint = 'default'
        self._audio_status = {'state': 'stopped', 'peak': 0.0, 'endpoints': []}
        self._last_status = None
        self._stopped_emitted = False
        self._service_timer = QTimer(self)
        self._service_timer.setInterval(30)
        self._service_timer.timeout.connect(self._poll_services)
        self._last_status_time = 0.0
        self._keyboard_error = ''
        self._merged_keyboard_count = 0
        self._mouse_timer = QTimer(self)
        self._mouse_timer.setInterval(30)
        self._mouse_timer.timeout.connect(self._poll_mouse)

    def start(self, hwnd: int) -> None:
        if self._started:
            return
        self._started = True
        self._stopped_emitted = False
        self._hwnd = int(hwnd)
        if not self.hardware_available or not self._hwnd:
            self._audio_status = {'state': 'unavailable', 'peak': 0.0, 'endpoints': [], 'error': 'Native activity providers are disabled on this platform'}
            self._publish_status()
            return
        try:
            self._api = _user32()
            self._filter = _KeyboardFilter(self._api, self._hwnd, self._on_keyboard)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
            self._hook = _KeyboardHook()
        except (OSError, AttributeError, RuntimeError) as error:
            self._api = None
            self._keyboard_error = str(error)[:240]
        try:
            self._worker = AudioProcess(self)
            worker = self._worker
            worker.activityChanged.connect(
                lambda active, source=worker: self._on_worker_audio(source, active), Qt.QueuedConnection)
            worker.statusChanged.connect(lambda status, source=worker: self._on_worker_status(source, status), Qt.QueuedConnection)
            worker.levelChanged.connect(lambda peak, source=worker: self.audioLevelChanged.emit(peak) if source is self._worker else None)
            worker.finished.connect(self._check_stopped)
            worker.set_endpoint(self._audio_endpoint)
            self._worker.start()
        except (OSError, AttributeError, RuntimeError):
            self._audio_status = {'state': 'error', 'peak': 0.0, 'error': 'Audio provider could not start', 'endpoints': []}
        self._service_timer.start()
        self._sync()

    def set_audio_endpoint(self, endpoint: str) -> None:
        if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 512 or '\0' in endpoint:
            raise ValueError('Invalid output endpoint ID')
        self._audio_endpoint = endpoint
        if self._worker:
            self._worker.set_endpoint(endpoint)

    def retry_audio(self) -> None:
        if self._worker and self._started:
            self._worker.retry()

    def _on_worker_status(self, source, status):
        if source is self._worker and status == source.status:
            self._audio_status = dict(status)
            self._publish_status()

    def _publish_status(self):
        raw_error = self._filter.error if self._filter else self._keyboard_error
        hook_error = self._hook.error if self._hook else self._keyboard_error
        keyboard = {'rawInput': bool(self._filter and self._filter.registered),
                    'lowLevelHook': bool(self._hook and self._hook.registered),
                    'state': 'disabled' if not self._keyboard_enabled else 'suspended' if self._suspended else 'ready' if self._started and
                    ((self._filter and self._filter.registered) or (self._hook and self._hook.registered)) else 'unavailable',
                    'lastActivityAt': self._last_keyboard_at if self._last_keyboard_at > -float('inf') else None,
                    'error': '; '.join(value for value in (raw_error, hook_error) if value),
                    'rawInputError': raw_error, 'lowLevelHookError': hook_error,
                    'rawActivityCount': self._filter.activity_count if self._filter else 0,
                    'hookActivityCount': self._hook.activity_count if self._hook else 0,
                    'mergedActivityCount': self._merged_keyboard_count}
        value = {'keyboard': keyboard, 'audio': dict(self._audio_status)}
        if value != self._last_status:
            self._last_status = value
            self.statusChanged.emit(value)

    def _poll_services(self):
        if (self._hook and not self._hook.running and self._hook.state == 'stopped' and self._started
                and self.hardware_available and self._keyboard_enabled and not self._suspended):
            self._hook.start()  # Resume may arrive while the preceding pump is still unhooking.
        if self._hook and self._hook.drain():
            self._on_keyboard()
        now = time.monotonic()
        if now - self._last_status_time >= .2:
            self._last_status_time = now
            self._publish_status()
        self._check_stopped()

    def _check_stopped(self):
        if not self._started and not self.stopping and not self._stopped_emitted:
            self._stopped_emitted = True
            self._service_timer.stop()
            self.stopped.emit()

    def set_enabled(self, keyboard: bool, audio: bool) -> None:
        self._keyboard_enabled, self._audio_enabled = bool(keyboard), bool(audio)
        self._sync()

    def set_suspended(self, suspended: bool) -> None:
        self._suspended = bool(suspended)
        self._sync()

    def _sync(self) -> None:
        available = self._started and self.hardware_available and not self._suspended
        if self._filter:
            if available and self._keyboard_enabled:
                self._filter.start()
            else:
                self._filter.stop()
        if self._worker:
            self._worker.set_enabled(available and self._audio_enabled)
        if self._hook:
            if available and self._keyboard_enabled:
                self._hook.start()
            else:
                self._hook.stop()
        if available and self._api:
            # Initialize the edge detector to avoid treating an already-held
            # button as a new click when the pet resumes.
            if not self._mouse_timer.isActive():
                self._mouse_down = bool(self._api.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
                self._mouse_timer.start()
        else:
            self._mouse_timer.stop()
            self._mouse_down = False
        if not available or not self._audio_enabled:
            self._on_audio(False)

    def _on_keyboard(self) -> None:
        if self._started and self._keyboard_enabled and not self._suspended:
            now = time.monotonic()
            # Raw Input and LL-hook notifications from one press become one activity pulse.
            if now - self._last_keyboard_at >= .075:
                self._last_keyboard_at = now
                self._merged_keyboard_count += 1
                self.keyboardActivity.emit()

    def _on_audio(self, active: bool) -> None:
        value = bool(active) and self._started and self._audio_enabled and not self._suspended
        if value != self._audio_active:
            self._audio_active = value
            self.audioActivityChanged.emit(value)

    def _on_worker_audio(self, source, active: bool) -> None:
        # Qt signals may be queued across a pause, restart or device change.
        # Only the current worker's latest aggregate state may affect the pet.
        if source is self._worker and bool(active) == source.activity_active:
            self._on_audio(active)

    def _poll_mouse(self) -> None:
        if not self._api or self._suspended or not self._started:
            return
        down = bool(self._api.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        if down and not self._mouse_down:
            point = QCursor.pos()  # Qt global coordinates, including monitor scaling.
            self.mouseClick.emit(point.x(), point.y())
        self._mouse_down = down

    def stop(self) -> None:
        self._started = False
        self._mouse_timer.stop()
        if self._hook:
            self._hook.stop()
        if self._filter:
            self._filter.stop()
            app = QCoreApplication.instance()
            if app:
                app.removeNativeEventFilter(self._filter)
            self._filter = None
        if self._worker:
            self._worker.stop()
        self._api = None
        self._mouse_down = False
        self._on_audio(False)
        self._check_stopped()

    def force_stop(self) -> None:
        if self._hook:
            self._hook.stop()
        if self._worker:
            self._worker.force_stop()

    @property
    def stopping(self) -> bool:
        return bool((self._worker is not None and self._worker.running) or (self._hook and self._hook.running))
