"""Local cleanup transactions. No Windows cleanup calls and no Qt imports."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = 4
STEP_TIMEOUT = 15.0
WORK_TIMEOUT = 45.0
SUPERVISOR_TIMEOUT = 60.0
UI_TIMEOUT = 65.0
START_TIMEOUT = 15.0
HANDSHAKE_TIMEOUT = 10.0
STEPS = ("empty_working_sets", "flush_modified_pages", "purge_standby_list",
         "system_file_cache", "registry_cache", "combine_memory")
TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "timed_out"})
EXIT_CODES = {"succeeded": 0, "partial": 10, "failed": 11, "cancelled": 12, "timed_out": 13}
_MAX_JSON_BYTES = 1_000_000
_REPLACE_RETRY_SECONDS = 0.2
_IO_LOCK_SECONDS = 0.2


def _open_json_read(path: Path):
    """Read an immutable file snapshot without blocking a Windows rename.

    Python's normal Windows open omits FILE_SHARE_DELETE. A concurrent status
    poll would then make os.replace fail while its read handle remained open.
    Delete sharing permits rename; it grants no filesystem access rights.
    """
    return _open_regular_file(path)


def _open_regular_file(path: Path, *, lock_file: bool = False):
    if os.name != "nt":
        flags = (os.O_RDWR | os.O_CREAT) if lock_file else os.O_RDONLY
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    else:
        import ctypes
        from ctypes import wintypes
        import msvcrt

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        create = api.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = api.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        info = api.GetFileInformationByHandleEx
        info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        info.restype = wintypes.BOOL
        # A persistent sidecar must not be renamed while a lock handle exists.
        access, sharing, creation = (0xC0000000, 0x3, 4) if lock_file else (0x80000000, 0x7, 3)
        handle = create(str(path), access, sharing, None, creation, 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            attributes = (wintypes.DWORD * 2)()  # FILE_ATTRIBUTE_TAG_INFO
            if not info(handle, 9, attributes, ctypes.sizeof(attributes)):
                raise ctypes.WinError(ctypes.get_last_error())
            # Check the actual opened object, including a link swapped in after
            # the caller's path check. Never read through a final reparse point.
            if attributes[0] & (0x400 | 0x10):
                raise ValueError("清理状态文件不能是链接或目录")
            fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if lock_file else os.O_RDONLY) | os.O_BINARY)
            handle = None  # Ownership transferred to the CRT descriptor.
        finally:
            if handle is not None:
                close(handle)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("清理状态必须是普通文件")
        return os.fdopen(fd, "r+b" if lock_file else "rb")
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _json_io_lock(path: Path, *, exclusive: bool = False):
    """Windows readers close before publication; no lock covers JSON parsing.

    Delete sharing alone does not make MoveFileEx replace an open destination.
    A stable per-file sidecar coordinates this product's processes, without
    changing any ACL or waiting for external readers beyond the existing bound.
    POSIX rename already supplies this snapshot property without a sidecar.
    """
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class Overlapped(ctypes.Structure):
        _fields_ = [("internal", ctypes.c_size_t), ("internal_high", ctypes.c_size_t),
                    ("offset", wintypes.DWORD), ("offset_high", wintypes.DWORD),
                    ("event", wintypes.HANDLE)]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    acquire, release = api.LockFileEx, api.UnlockFileEx
    acquire.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                        wintypes.DWORD, ctypes.POINTER(Overlapped)]
    release.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                        wintypes.DWORD, ctypes.POINTER(Overlapped)]
    acquire.restype = release.restype = wintypes.BOOL
    sidecar = path.with_name(f".{path.name}.io.lock")
    with _open_regular_file(sidecar, lock_file=True) as stream:
        handle = msvcrt.get_osfhandle(stream.fileno())
        offset = Overlapped()
        deadline = time.monotonic() + _IO_LOCK_SECONDS
        while not acquire(handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(offset)):
            error = ctypes.get_last_error()
            remaining = deadline - time.monotonic()
            if error != 33 or remaining <= 0:
                raise ctypes.WinError(error)
            time.sleep(min(0.005, remaining))
        try:
            yield
        finally:
            if not release(handle, 0, 1, 0, ctypes.byref(offset)):
                raise ctypes.WinError(ctypes.get_last_error())


def _replace_json(temporary: Path, path: Path) -> None:
    # Legacy/external readers may still omit delete sharing. Keep this bound
    # short; permanent ACL/read-only failures remain failures, with no fallback
    # to truncation, changed permissions, or increased cleanup time budgets.
    deadline = time.monotonic() + _REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(temporary, path)
            return
        except OSError as exc:
            remaining = deadline - time.monotonic()
            if os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33) or remaining <= 0:
                raise
            time.sleep(min(0.01, remaining))


def valid_operation(value: object) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_profile(profile: Path) -> Path:
    profile = Path(profile)
    if not profile.is_absolute():
        raise ValueError("清理配置目录必须是绝对路径")
    # Elevated writes must not follow a user-replaced junction/symlink.
    for item in (profile, *profile.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("清理目录不能经过符号链接或目录联接")
    return profile.resolve()


def root_path(profile: Path) -> Path:
    root = safe_profile(profile) / "cleanup"
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ValueError("清理目录不能是链接")
    return root


def operation_path(profile: Path, operation_id: str) -> Path:
    if not valid_operation(operation_id):
        raise ValueError("无效的清理操作标识")
    root = root_path(profile)
    target = root / "operations" / operation_id
    for item in (target.parent, target):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("清理操作目录不能是链接")
    return target


def read_json(path: Path) -> dict | None:
    try:
        if path.is_symlink() or path.is_dir():
            return None
        with _json_io_lock(path):
            with _open_json_read(path) as stream:
                if os.fstat(stream.fileno()).st_size > _MAX_JSON_BYTES:
                    return None
                data = stream.read(_MAX_JSON_BYTES + 1)
        if len(data) > _MAX_JSON_BYTES:
            return None
        value = json.loads(data.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path: Path, value: dict, *, immutable: bool = False) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if immutable:
        with _json_io_lock(path, exclusive=True):
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        with _json_io_lock(path, exclusive=True):
            _replace_json(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def transaction_lock(profile: Path):
    """Short cross-process lock around the active lease, never around OS work."""
    root = root_path(profile)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "transaction.lock"
    if lock.is_symlink():
        raise ValueError("清理锁不能是链接")
    with lock.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError("另一个进程正在更新清理状态") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def load_request(profile: Path, operation_id: str, *, helper: Path | None = None,
                 now: float | None = None) -> dict:
    folder = operation_path(profile, operation_id)
    request = read_json(folder / "request.json")
    if (not request or request.get("schema") != SCHEMA or request.get("operation_id") != operation_id
            or request.get("profile") != str(safe_profile(profile)) or request.get("steps") != list(STEPS)):
        raise ValueError("清理请求不完整或不属于当前配置目录")
    age = (time.time() if now is None else now) - request.get("requested_at", 0)
    if not 0 <= age <= SUPERVISOR_TIMEOUT:
        raise ValueError("清理请求已过期")
    if helper is not None and (request.get("helper") != str(helper.resolve())
                               or request.get("helper_sha256") != digest(helper)):
        raise ValueError("清理程序已变化，请从新版本重新发起")
    return request


def active_operation(profile: Path) -> str | None:
    lease = read_json(root_path(profile) / "active.json")
    value = lease.get("operation_id") if lease else None
    return value if valid_operation(value) else None


def release_lease(profile: Path, operation_id: str) -> None:
    with transaction_lock(profile):
        if active_operation(profile) == operation_id:
            (root_path(profile) / "active.json").unlink(missing_ok=True)


def create_operation(profile: Path, helper: Path, client: dict, *, now: float | None = None) -> dict:
    """Caller holds transaction_lock and has already checked the old lease."""
    operation_id = uuid.uuid4().hex
    folder = operation_path(profile, operation_id)
    folder.parent.mkdir(exist_ok=True)
    folder.mkdir()  # Parent ACL inheritance; runtime resources are not secret.
    request = {"schema": SCHEMA, "operation_id": operation_id,
               "profile": str(safe_profile(profile)), "helper": str(helper.resolve()),
               "helper_sha256": digest(helper), "client": client,
               "requested_at": time.time() if now is None else now, "steps": list(STEPS)}
    write_json(folder / "request.json", request, immutable=True)
    write_json(root_path(profile) / "active.json", {"schema": SCHEMA, "operation_id": operation_id,
                                                   "request_sha256": digest(folder / "request.json")})
    write_json(folder / "status.json", {"schema": SCHEMA, "operation_id": operation_id,
                                       "status": "queued", "steps": {}, "message": "等待清理助手接单"})
    return request
