"""Isolated output-meter worker. No Qt, windows, capture endpoint or elevated actions."""
from __future__ import annotations
import ctypes
import json
import math
import os
import sys
import threading
import time
import uuid
from pet_reactions import AudioActivityGate

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


CLSID_MMDEVICE_ENUMERATOR = _GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_DEVICE_ENUMERATOR = _GUID.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_AUDIO_METER = _GUID.parse("C02216F6-8C67-4B5B-9D00-D008E73E0064")
IID_ENDPOINT_VOLUME = _GUID.parse("5CDF2C82-841E-4546-9722-0CF74078229A")


class _PropertyKey(ctypes.Structure):
    _fields_ = [('fmtid', _GUID), ('pid', ctypes.c_uint32)]


class _PropertyValue(ctypes.Union):
    _fields_ = [('pointer', ctypes.c_void_p), ('bytes', ctypes.c_ubyte * 16)]


class _PropVariant(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_ushort), ('reserved', ctypes.c_ushort * 3), ('value', _PropertyValue)]


PKEY_FRIENDLY_NAME = _PropertyKey(_GUID.parse('A45C254E-DF1C-4EFD-8020-67D146A850E0'), 14)


def _check_hresult(result: int) -> None:
    if result < 0:
        raise OSError(f"Windows audio HRESULT 0x{result & 0xFFFFFFFF:08X}")


def _com_call(pointer, slot: int, args=(), argtypes=(), restype=ctypes.c_int32):
    address = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[slot]
    method = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(address)
    return method(pointer, *args)


def _release(pointer) -> None:
    if pointer and pointer.value:
        _com_call(pointer, 2, restype=ctypes.c_uint32)
        pointer.value = None


class _EndpointMeter:
    """Small COM adapter. Construct/use/close exclusively on one worker thread."""

    def __init__(self, endpoint='default') -> None:
        self.enumerator = ctypes.c_void_p()
        self.device = ctypes.c_void_p()
        self.meter = ctypes.c_void_p()
        self.volume = ctypes.c_void_p()
        self.device_id = None
        self.initialized = False
        self.endpoint = endpoint
        self.endpoints = []
        self.device_name = ''
        self.muted = False
        self.ole = ctypes.WinDLL("ole32", use_last_error=True)
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.ole.CoInitializeEx.restype = ctypes.c_int32
        self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                                             ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.ole.CoCreateInstance.restype = ctypes.c_int32
        self.ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self.ole.CoTaskMemFree.restype = None
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None
        self.ole.PropVariantClear.argtypes = [ctypes.POINTER(_PropVariant)]
        self.ole.PropVariantClear.restype = ctypes.c_int32
        try:
            _check_hresult(self.ole.CoInitializeEx(None, 0))  # COINIT_MULTITHREADED
            self.initialized = True
            _check_hresult(self.ole.CoCreateInstance(ctypes.byref(CLSID_MMDEVICE_ENUMERATOR), None, 1,
                           ctypes.byref(IID_DEVICE_ENUMERATOR), ctypes.byref(self.enumerator)))
            self.refresh_default()
        except Exception as error:
            error.endpoints = self.endpoints
            self.close()
            raise

    def _identity(self, device):
        identity = ctypes.c_void_p()
        _check_hresult(_com_call(device, 5, (ctypes.byref(identity),), (ctypes.POINTER(ctypes.c_void_p),)))
        try:
            return ctypes.wstring_at(identity)
        finally:
            self.ole.CoTaskMemFree(identity)

    def _name(self, device, fallback):
        store = ctypes.c_void_p()
        value = _PropVariant()
        try:
            _check_hresult(_com_call(device, 4, (0, ctypes.byref(store)), (ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))))
            _check_hresult(_com_call(store, 5, (ctypes.byref(PKEY_FRIENDLY_NAME), ctypes.byref(value)),
                (ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant))))
            return ctypes.wstring_at(value.value.pointer)[:160] if value.vt == 31 and value.value.pointer else fallback[:160]
        except OSError:
            return fallback[:160]
        finally:
            self.ole.PropVariantClear(ctypes.byref(value))
            _release(store)

    def list_outputs(self):
        collection, default = ctypes.c_void_p(), ctypes.c_void_p()
        default_id = None
        try:
            result = _com_call(self.enumerator, 4, (0, 1, ctypes.byref(default)),
                (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)))
            if result >= 0:
                default_id = self._identity(default)
            # eRender and DEVICE_STATE_ACTIVE: capture endpoints are never enumerated.
            _check_hresult(_com_call(self.enumerator, 3, (0, 1, ctypes.byref(collection)),
                (ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))))
            count = ctypes.c_uint32()
            _check_hresult(_com_call(collection, 3, (ctypes.byref(count),), (ctypes.POINTER(ctypes.c_uint32),)))
            results = []
            for index in range(min(count.value, 32)):
                device = ctypes.c_void_p()
                try:
                    _check_hresult(_com_call(collection, 4, (index, ctypes.byref(device)),
                        (ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))))
                    identity = self._identity(device)
                    results.append({'id': identity, 'name': self._name(device, identity), 'isDefault': identity == default_id})
                finally:
                    _release(device)
            return results
        finally:
            _release(default)
            _release(collection)

    def _activate(self, device, iid):
        pointer = ctypes.c_void_p()
        _check_hresult(_com_call(device, 3,
            (ctypes.byref(iid), 23, None, ctypes.byref(pointer)),
            (ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))))
        return pointer

    def refresh_default(self) -> bool:
        new_device = ctypes.c_void_p()
        new_meter = ctypes.c_void_p()
        new_volume = ctypes.c_void_p()
        try:
            self.endpoints = self.list_outputs()
            # eRender=0, eMultimedia=1; never request a capture endpoint.
            if self.endpoint == 'default':
                _check_hresult(_com_call(self.enumerator, 4, (0, 1, ctypes.byref(new_device)),
                               (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))))
            else:
                if not any(item['id'] == self.endpoint for item in self.endpoints):
                    raise OSError('Selected output endpoint is unavailable')
                _check_hresult(_com_call(self.enumerator, 5, (self.endpoint, ctypes.byref(new_device)),
                    (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p))))
            device_id = self._identity(new_device)
            if device_id == self.device_id:
                return False
            new_meter = self._activate(new_device, IID_AUDIO_METER)
            new_volume = self._activate(new_device, IID_ENDPOINT_VOLUME)
            self._close_device()
            self.device, new_device = new_device, ctypes.c_void_p()
            self.meter, new_meter = new_meter, ctypes.c_void_p()
            self.volume, new_volume = new_volume, ctypes.c_void_p()
            self.device_id = device_id
            self.device_name = next((item['name'] for item in self.endpoints if item['id'] == device_id), device_id[:160])
            return True
        finally:
            _release(new_volume)
            _release(new_meter)
            _release(new_device)

    def peak(self) -> float:
        muted = ctypes.c_int()
        volume = ctypes.c_float()
        _check_hresult(_com_call(self.volume, 15, (ctypes.byref(muted),), (ctypes.POINTER(ctypes.c_int),)))
        _check_hresult(_com_call(self.volume, 9, (ctypes.byref(volume),), (ctypes.POINTER(ctypes.c_float),)))
        self.muted = bool(muted.value or volume.value <= 0)
        if self.muted:
            return 0.0
        peak = ctypes.c_float()
        _check_hresult(_com_call(self.meter, 3, (ctypes.byref(peak),), (ctypes.POINTER(ctypes.c_float),)))
        return min(1.0, max(0.0, peak.value)) if math.isfinite(peak.value) else 0.0

    def _close_device(self) -> None:
        _release(self.volume)
        _release(self.meter)
        _release(self.device)
        self.device_id = None

    def close(self) -> None:
        self._close_device()
        _release(self.enumerator)
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False



class _AudioSampler:
    """One-thread COM ownership with at most three attempts per configuration revision."""
    def __init__(self, meter_factory=_EndpointMeter):
        self.factory = meter_factory
        self.meter = None
        self.generation = -1
        self.next_refresh = self.next_retry = self.next_devices = 0.0
        self.attempts = 0
        self.endpoints, self.error = [], ''
        self.gate = AudioActivityGate()

    def close(self):
        if self.meter is not None:
            meter, self.meter = self.meter, None
            meter.close()

    def sample(self, config, now):
        if config['generation'] != self.generation:
            self.generation = config['generation']
            self.gate.reset()
            self.attempts, self.next_retry, self.error, self.next_devices = 0, 0.0, '', 0.0
            self.close()
        active, peak, state = False, 0.0, 'disabled'
        if config['enabled']:
            state = 'error' if self.attempts >= 3 else 'retrying'
            try:
                if self.meter is None and self.attempts < 3 and now >= self.next_retry:
                    self.meter = self.factory(config['endpoint'])
                    self.next_refresh = now + 2
                if self.meter is not None:
                    if now >= self.next_refresh:
                        if self.meter.refresh_default():
                            self.gate.reset()
                        self.next_refresh = now + 2
                    self.endpoints = self.meter.endpoints
                    level = self.meter.peak()
                    peak = max(0.0, min(1.0, level)) if math.isfinite(level) else 0.0
                    if self.meter.muted:
                        self.gate.reset()
                    else:
                        active = self.gate.sample(peak, now)
                    state = 'muted' if self.meter.muted else 'active' if active else 'ready'
                    self.error = ''
            except (OSError, ValueError, RuntimeError) as failure:
                self.attempts += 1
                self.next_retry = now + min(4, 2 ** (self.attempts - 1))
                self.endpoints = getattr(failure, 'endpoints', self.meter.endpoints if self.meter else self.endpoints)
                self.error = str(failure)[:240]
                self.gate.reset()
                self.close()
                active, peak = False, 0.0
                state = 'error' if self.attempts >= 3 else 'retrying'
        else:
            self.close()
            self.gate.reset()
        message = {'active': bool(active and config['enabled']), 'generation': self.generation,
            'initialized': self.meter is not None, 'peak': round(peak, 4), 'state': state, 'attempt': self.attempts,
            'endpointId': self.meter.device_id if self.meter else None,
            'endpointName': self.meter.device_name if self.meter else '',
            'muted': self.meter.muted if self.meter else False, 'error': self.error}
        if now >= self.next_devices:
            message['endpoints'] = self.endpoints
            self.next_devices = now + 2
        return message


def _write_status(message) -> None:
    # Pipe-connected Python inherits the Windows locale (often cp936), while
    # the parent protocol decodes UTF-8. ASCII JSON keeps device names lossless
    # through either encoding, including Unicode outside the local code page.
    sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def audio_child_main() -> int:
    # Only pipe-connected, ordinary-user instances may become sensor workers.
    if os.name != "nt" or sys.stdin is None or sys.stdout is None:
        return 2
    if ctypes.windll.shell32.IsUserAnAdmin():
        return 3
    stopping = threading.Event()
    control_lock = threading.Lock()
    control = {'enabled': False, 'generation': 0, 'endpoint': 'default'}
    def receive():
        try:
            while True:
                line = sys.stdin.readline(4097)
                if not line or len(line) > 4096:
                    break
                command = json.loads(line)
                if not isinstance(command, dict):
                    break
                if command.get("stop"):
                    break
                endpoint = command.get('endpoint', 'default')
                revision = command.get('generation', 0)
                if (not isinstance(endpoint, str) or not endpoint or len(endpoint) > 512 or '\0' in endpoint
                        or not isinstance(revision, int) or isinstance(revision, bool)):
                    break
                with control_lock:
                    control.update(enabled=command.get('enabled') is True, generation=revision, endpoint=endpoint)
        except (ValueError, OSError):
            pass
        finally:
            stopping.set()
    threading.Thread(target=receive, daemon=True, name="audio-control").start()
    sampler = _AudioSampler()
    try:
        while not stopping.is_set():
            now = time.monotonic()
            with control_lock:
                config = dict(control)
            # Scalar meter and device status only: no waveform, microphone or session names.
            message = sampler.sample(config, now)
            _write_status(message)
            stopping.wait(.05)
    except (OSError, ValueError, RuntimeError):
        return 1
    finally:
        sampler.close()
    return 0
