"""Windows process ownership for the cleanup supervisor; no cleanup operations."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess

DWORD = wintypes.DWORD
HANDLE = wintypes.HANDLE
SIZE_T = ctypes.c_size_t
WAIT_TIMEOUT = 258
QUERY = 0x1000 | 0x100000  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE


class FILETIME(ctypes.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", DWORD), ("reserved", wintypes.LPWSTR), ("desktop", wintypes.LPWSTR),
                ("title", wintypes.LPWSTR), ("x", DWORD), ("y", DWORD), ("xsize", DWORD),
                ("ysize", DWORD), ("xchars", DWORD), ("ychars", DWORD), ("fill", DWORD),
                ("flags", DWORD), ("show", wintypes.WORD), ("reserved2size", wintypes.WORD),
                ("reserved2", ctypes.c_void_p), ("stdin", HANDLE), ("stdout", HANDLE), ("stderr", HANDLE)]


class PROCESSINFO(ctypes.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", DWORD), ("tid", DWORD)]


class BASICLIMIT(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", DWORD),
                ("min_ws", SIZE_T), ("max_ws", SIZE_T), ("active_processes", DWORD),
                ("affinity", SIZE_T), ("priority", DWORD), ("scheduling", DWORD)]


class IOCOUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]


class EXTENDEDLIMIT(ctypes.Structure):
    _fields_ = [("basic", BASICLIMIT), ("io", IOCOUNTERS), ("process_memory", SIZE_T),
                ("job_memory", SIZE_T), ("peak_process", SIZE_T), ("peak_job", SIZE_T)]


def kernel():
    if os.name != "nt":
        raise OSError("Cleanup process ownership requires Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    definitions = {
        "CloseHandle": ([HANDLE], wintypes.BOOL),
        "OpenProcess": ([DWORD, wintypes.BOOL, DWORD], HANDLE),
        "GetProcessTimes": ([HANDLE] + [ctypes.POINTER(FILETIME)] * 4, wintypes.BOOL),
        "WaitForSingleObject": ([HANDLE, DWORD], DWORD),
        "GetExitCodeProcess": ([HANDLE, ctypes.POINTER(DWORD)], wintypes.BOOL),
        "TerminateProcess": ([HANDLE, wintypes.UINT], wintypes.BOOL),
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], HANDLE),
        "SetInformationJobObject": ([HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD], wintypes.BOOL),
        "AssignProcessToJobObject": ([HANDLE, HANDLE], wintypes.BOOL),
        "ResumeThread": ([HANDLE], DWORD),
        "CreateProcessW": ([wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.BOOL, DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                            ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESSINFO)], wintypes.BOOL),
        "CreateMutexW": ([ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], HANDLE),
        "ReleaseMutex": ([HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], HANDLE),
        "LocalFree": ([ctypes.c_void_p], ctypes.c_void_p),
    }
    for name, (args, result) in definitions.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def creation_time(api, handle) -> int:
    values = [FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (int(values[0].high) << 32) | int(values[0].low)


class ObservedProcess:
    def __init__(self, identity: dict):
        if not valid_identity(identity):
            raise ValueError("Invalid process identity")
        self.api = kernel()
        self.identity = identity
        self.handle = self.api.OpenProcess(QUERY, False, int(identity["pid"]))
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if creation_time(self.api, self.handle) != identity["creationFiletime"]:
                raise ProcessLookupError("Process id has been reused")
        except BaseException:
            self.close()
            raise

    def poll(self) -> int | None:
        code = self.api.WaitForSingleObject(self.handle, 0)
        if code == WAIT_TIMEOUT:
            return None
        if code != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        result = DWORD()
        if not self.api.GetExitCodeProcess(self.handle, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return result.value

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def current_identity() -> dict:
    api = kernel()
    return {"pid": os.getpid(), "creationFiletime": creation_time(api, api.GetCurrentProcess())}


def valid_identity(identity) -> bool:
    return (isinstance(identity, dict) and isinstance(identity.get("pid"), int) and identity["pid"] > 0
            and isinstance(identity.get("creationFiletime"), int) and identity["creationFiletime"] > 0)


def identity_alive(identity: dict | None) -> bool:
    if not identity:
        return False
    try:
        process = ObservedProcess(identity)
        try:
            return process.poll() is None
        finally:
            process.close()
    except ProcessLookupError:
        return False
    except OSError as exc:
        # Access denied is unknown, never proof of exit.
        if getattr(exc, "winerror", None) in (87, 1168):
            return False
        raise


def user_sid() -> str:
    api = kernel()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [HANDLE, DWORD, ctypes.POINTER(HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    token, size, text = HANDLE(), DWORD(), ctypes.c_void_p()
    if not advapi.OpenProcessToken(api.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        data = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p)).contents
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.wstring_at(text)
    finally:
        if text.value:
            api.LocalFree(text)
        api.CloseHandle(token)


class ExecutionMutex:
    """One elevated cleanup supervisor per current user across all profiles."""
    def __init__(self, scope="supervisor"):
        if scope not in {"supervisor", "step"}:
            raise ValueError("Unknown cleanup mutex scope")
        self.api = kernel()
        self.handle = self.api.CreateMutexW(None, False, "Local\\CuteMapleCleanup-" + scope + "-" + hashlib.sha256(user_sid().encode()).hexdigest()[:24])
        self.acquired = False
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def acquire(self) -> bool:
        result = self.api.WaitForSingleObject(self.handle, 0)
        self.acquired = result in (0, 0x80)
        if not self.acquired and result != WAIT_TIMEOUT:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.acquired

    def close(self):
        if self.handle:
            if self.acquired:
                self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle)
            self.handle = None


class WorkerJob:
    def __init__(self):
        self.api = kernel()
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = EXTENDEDLIMIT()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE, no unrelated process assignment.
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            code = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(code)

    def spawn(self, command: list[str], cwd: Path, before_resume=None):
        info, startup = PROCESSINFO(), STARTUPINFO()
        startup.cb, startup.flags, startup.show = ctypes.sizeof(startup), 1, 0
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        if not self.api.CreateProcessW(command[0], line, None, None, False,
                                      0x08000000 | 0x4, None, str(cwd), ctypes.byref(startup), ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        process = None
        try:
            process = OwnedWorker(self.api, info.process, info.pid)
            if not self.api.AssignProcessToJobObject(self.handle, info.process):
                raise ctypes.WinError(ctypes.get_last_error())
            if before_resume is not None:
                before_resume(process.identity)
            if self.api.ResumeThread(info.thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            return process
        except BaseException:
            # Even a failure reading creation time owns a suspended process.
            # It must never leave that child or either native handle behind.
            if process is None:
                self.api.TerminateProcess(info.process, 71)
                self.api.WaitForSingleObject(info.process, 2000)
                self.api.CloseHandle(info.process)
            else:
                try:
                    process.terminate(71)
                    process.wait(2)
                finally:
                    process.close()
            raise
        finally:
            self.api.CloseHandle(info.thread)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class OwnedWorker:
    def __init__(self, api, handle, pid):
        self.api, self.handle = api, handle
        self.identity = {"pid": int(pid), "creationFiletime": creation_time(api, handle)}

    poll = ObservedProcess.poll
    close = ObservedProcess.close

    def terminate(self, code=72):
        if self.poll() is None and not self.api.TerminateProcess(self.handle, code):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait(self, timeout: float) -> int | None:
        result = self.api.WaitForSingleObject(self.handle, max(0, int(timeout * 1000)))
        if result == WAIT_TIMEOUT:
            return None
        return self.poll()
