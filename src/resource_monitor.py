from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field
from ctypes import wintypes

import psutil


def format_bytes(value: float, per_second: bool = False) -> str:
    value = max(0.0, float(value))
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    digits = 0 if index == 0 else (1 if value < 100 else 0)
    return f"{value:.{digits}f} {units[index]}{'/s' if per_second else ''}"


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t), ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD), ("ThreadCount", wintypes.DWORD),
    ]


@dataclass
class MemorySnapshot:
    load_percent: float = 0.0
    physical_total: int = 0
    physical_available: int = 0
    pagefile_total: int = 0
    pagefile_available: int = 0
    virtual_total: int = 0
    virtual_available: int = 0
    commit_total: int = 0
    commit_limit: int = 0
    commit_peak: int = 0
    system_cache: int = 0
    system_cache_peak: int = 0
    kernel_paged: int = 0
    kernel_nonpaged: int = 0

    @property
    def physical_used(self) -> int:
        return max(0, self.physical_total - self.physical_available)


def memory_snapshot() -> MemorySnapshot:
    if hasattr(ctypes, "windll"):
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        perf = PERFORMANCE_INFORMATION()
        perf.cb = ctypes.sizeof(perf)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            result = MemorySnapshot(
                float(status.dwMemoryLoad), int(status.ullTotalPhys), int(status.ullAvailPhys),
                int(status.ullTotalPageFile), int(status.ullAvailPageFile),
                int(status.ullTotalVirtual), int(status.ullAvailVirtual),
            )
            if ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(perf), perf.cb):
                page = int(perf.PageSize)
                result.commit_total = int(perf.CommitTotal) * page
                result.commit_limit = int(perf.CommitLimit) * page
                result.commit_peak = int(perf.CommitPeak) * page
                result.system_cache = int(perf.SystemCache) * page
                result.kernel_paged = int(perf.KernelPaged) * page
                result.kernel_nonpaged = int(perf.KernelNonpaged) * page
            class SYSTEM_FILECACHE_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("CurrentSize", ctypes.c_size_t), ("PeakSize", ctypes.c_size_t),
                    ("PageFaultCount", wintypes.ULONG), ("MinimumWorkingSet", ctypes.c_size_t),
                    ("MaximumWorkingSet", ctypes.c_size_t),
                    ("CurrentSizeIncludingTransitionInPages", ctypes.c_size_t),
                    ("PeakSizeIncludingTransitionInPages", ctypes.c_size_t),
                    ("TransitionRePurposeCount", wintypes.ULONG), ("Flags", wintypes.ULONG),
                ]
            cache = SYSTEM_FILECACHE_INFORMATION()
            ntdll = ctypes.WinDLL("ntdll"); ntdll.NtQuerySystemInformation.restype = wintypes.LONG
            status_code = ntdll.NtQuerySystemInformation(21, ctypes.byref(cache), ctypes.sizeof(cache), None)
            if status_code >= 0:
                result.system_cache = int(cache.CurrentSize); result.system_cache_peak = int(cache.PeakSize)
            return result
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return MemorySnapshot(vm.percent, vm.total, vm.available, swap.total, swap.free)


@dataclass
class NetworkSnapshot:
    download_bps: float = 0.0
    upload_bps: float = 0.0
    session_download: int = 0
    session_upload: int = 0
    adapters: dict[str, tuple[float, float]] = field(default_factory=dict)


class NetworkSampler:
    def __init__(self, smoothing: float = 0.42) -> None:
        self.smoothing = smoothing
        self._last_time = time.monotonic()
        self._last = psutil.net_io_counters(pernic=True, nowrap=True)
        self._start_recv = sum(item.bytes_recv for item in self._last.values())
        self._start_sent = sum(item.bytes_sent for item in self._last.values())
        self._down = self._up = 0.0

    def sample(self) -> NetworkSnapshot:
        now = time.monotonic()
        elapsed = max(0.001, now - self._last_time)
        current = psutil.net_io_counters(pernic=True, nowrap=True)
        stats = psutil.net_if_stats()
        adapters: dict[str, tuple[float, float]] = {}
        raw_down = raw_up = 0.0
        for name, item in current.items():
            previous = self._last.get(name)
            if previous is None or not stats.get(name) or not stats[name].isup:
                continue
            recv = max(0, item.bytes_recv - previous.bytes_recv) / elapsed
            sent = max(0, item.bytes_sent - previous.bytes_sent) / elapsed
            if recv or sent:
                adapters[name] = (recv, sent)
            raw_down += recv
            raw_up += sent
        self._down += self.smoothing * (raw_down - self._down)
        self._up += self.smoothing * (raw_up - self._up)
        total_recv = sum(item.bytes_recv for item in current.values())
        total_sent = sum(item.bytes_sent for item in current.values())
        self._last, self._last_time = current, now
        return NetworkSnapshot(
            self._down, self._up,
            max(0, total_recv - self._start_recv), max(0, total_sent - self._start_sent), adapters,
        )
