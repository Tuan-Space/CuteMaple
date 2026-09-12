from __future__ import annotations

import ctypes
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

import desktop_activity as activity
import audio_probe


def _app():
    return QApplication.instance() or QApplication([])


class FakeUser32:
    def __init__(self):
        self.registrations = []
        self.read_commands = []
        self.device_type = activity.RIM_TYPEKEYBOARD
        self.mouse_down = False

    def RegisterRawInputDevices(self, pointer, count, size):
        device = ctypes.cast(pointer, ctypes.POINTER(activity._RawInputDevice)).contents
        self.registrations.append((device.usUsagePage, device.usUsage, device.dwFlags, device.hwndTarget))
        return True

    def GetRawInputData(self, handle, command, pointer, size, header_size):
        self.read_commands.append(command)
        header = ctypes.cast(pointer, ctypes.POINTER(activity._RawInputHeader)).contents
        header.dwType = self.device_type
        return ctypes.sizeof(activity._RawInputHeader)

    def GetAsyncKeyState(self, key):
        assert key == activity.VK_LBUTTON
        return 0x8000 if self.mouse_down else 0


def test_raw_keyboard_reads_header_only_and_never_swallows_messages():
    api = FakeUser32()
    notifications = []
    reader = activity._KeyboardFilter(api, 123, lambda: notifications.append(True))
    assert reader.start()
    message = activity._Message(hwnd=123, message=activity.WM_INPUT, lParam=99)
    assert reader.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message)) == (False, 0)
    assert notifications == [True]
    assert api.read_commands == [activity.RID_HEADER]
    api.device_type = 0  # Mouse headers cannot become keyboard activity.
    reader.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    assert notifications == [True]
    reader.stop()
    reader.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    assert notifications == [True]
    assert api.registrations == [(1, 6, activity.RIDEV_INPUTSINK, 123),
                                 (1, 6, activity.RIDEV_REMOVE, None)]


def test_offscreen_never_initializes_hardware_even_with_explicit_true(monkeypatch):
    _app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(activity, "_user32", lambda: (_ for _ in ()).throw(AssertionError("hardware touched")))
    service = activity.DesktopActivity(hardware=True)
    service.start(123)
    assert not service.hardware_available
    assert service._filter is None
    assert service._worker is None
    assert not service._mouse_timer.isActive()
    service.stop()
    service.stop()


def test_facade_independent_settings_and_suspension_discard_activity():
    _app()
    service = activity.DesktopActivity(hardware=False)
    keys, sound = [], []
    service.keyboardActivity.connect(lambda: keys.append(True))
    service.audioActivityChanged.connect(sound.append)
    service.start(123)
    service._on_keyboard()
    service._on_audio(True)
    assert keys == [True] and sound == [True]
    service.set_enabled(False, True)
    service._on_keyboard()
    assert keys == [True]
    service.set_suspended(True)
    service._on_audio(True)  # Queued pre-pause update must not re-enable it.
    assert sound == [True, False]
    service.set_suspended(False)
    assert not service._audio_active
    service.stop()
    service._on_keyboard()
    service._on_audio(True)
    assert keys == [True] and sound == [True, False]


def test_global_click_only_emits_rising_edge_and_current_position(monkeypatch):
    _app()
    service = activity.DesktopActivity(hardware=False)
    service.start(123)
    service._api = FakeUser32()
    clicks = []
    service.mouseClick.connect(lambda x, y: clicks.append((x, y)))

    class Cursor:
        @staticmethod
        def pos():
            return QPoint(-420, 180)

    monkeypatch.setattr(activity, "QCursor", Cursor)
    service._api.mouse_down = True
    service._poll_mouse()
    service._poll_mouse()
    assert clicks == [(-420, 180)]
    service._api.mouse_down = False
    service._poll_mouse()
    service._api.mouse_down = True
    service._poll_mouse()
    assert clicks == [(-420, 180), (-420, 180)]
    service.set_suspended(True)
    service._poll_mouse()
    assert len(clicks) == 2
    service.stop()


def test_endpoint_meter_reads_scalar_and_respects_mute(monkeypatch):
    meter = audio_probe._EndpointMeter.__new__(audio_probe._EndpointMeter)
    meter.volume = ctypes.c_void_p(10)
    meter.meter = ctypes.c_void_p(20)
    muted = [False]
    calls = []

    def fake_call(pointer, slot, args=(), argtypes=(), restype=None):
        calls.append((pointer.value, slot))
        if pointer.value == 10 and slot == 15:
            ctypes.cast(args[0], ctypes.POINTER(ctypes.c_int)).contents.value = int(muted[0])
        else:
            ctypes.cast(args[0], ctypes.POINTER(ctypes.c_float)).contents.value = 0.6
        return 0

    monkeypatch.setattr(audio_probe, "_com_call", fake_call)
    assert abs(meter.peak() - 0.6) < 0.0001
    assert calls == [(10, 15), (10, 9), (20, 3)]
    muted[0] = True
    calls.clear()
    assert meter.peak() == 0
    assert calls == [(10, 15), (10, 9)]


def test_obsolete_queued_audio_cannot_change_a_new_provider():
    _app()
    service = activity.DesktopActivity(hardware=False)
    service.start(123)

    class Worker:
        activity_active = True

    old, current = Worker(), Worker()
    service._worker = current
    service._on_worker_audio(old, True)
    assert not service._audio_active
    service._on_worker_audio(current, True)
    assert service._audio_active
    service._on_worker_audio(current, False)
    assert service._audio_active
    current.activity_active = False
    service._on_worker_audio(current, False)
    assert not service._audio_active
    service._worker = None
    service.stop()


def test_low_level_callback_never_dereferences_key_pointer_and_always_chains():
    calls = []
    class Api:
        def CallNextHookEx(self, handle, code, message, pointer):
            calls.append((code, message, pointer)); return 123
    hook = activity._KeyboardHook()
    hook._api = Api()
    # Address 1 is intentionally unreadable. Passing it to next hook is the only permitted use.
    assert hook._callback(0, activity.WM_KEYDOWN, 1) == 123
    assert hook.drain() and not hook.drain()
    assert hook.activity_count == 1
    assert hook._callback(-1, activity.WM_KEYDOWN, 1) == 123
    assert hook._callback(0, 0x101, 1) == 123  # Release is not another LL activity event.
    assert not hook.drain() and len(calls) == 3
    hook._stop.set()
    hook._callback(0, activity.WM_SYSKEYDOWN, 1)
    assert not hook.drain()


def test_raw_and_remote_hook_activity_are_coalesced_without_key_data(monkeypatch):
    _app()
    now = [10.0]
    monkeypatch.setattr(activity.time, 'monotonic', lambda: now[0])
    service = activity.DesktopActivity(hardware=False)
    service.start(123)
    received = []
    service.keyboardActivity.connect(lambda: received.append(True))
    service._on_keyboard()
    now[0] += .02; service._on_keyboard()
    assert len(received) == 1
    now[0] += .08; service._on_keyboard()
    assert len(received) == 2
    service.set_suspended(True)
    now[0] += 1; service._on_keyboard()
    assert len(received) == 2
    service.stop()
