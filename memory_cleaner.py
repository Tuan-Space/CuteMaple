from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import psutil

from pet_core import APP_NAME, config_path, is_compiled
from resource_monitor import memory_snapshot


TASK_NAME = f"{APP_NAME}_内存清理"
TASK_SCHEMA_VERSION = 2
REQUEST_FILE = config_path().with_name("memory-clean-request.json")
RESULT_FILE = config_path().with_name("memory-clean-result.json")
INSTALL_FILE = config_path().with_name("memory-clean-install.json")


def current_executable_path() -> Path:
    """Return the user's onefile path, never Nuitka's temporary extraction path."""
    if is_compiled():
        return Path(sys.argv[0]).resolve()
    return Path(sys.executable).resolve()


def executable_command() -> tuple[str, str]:
    if is_compiled():
        return str(current_executable_path()), "--memory-clean-helper"
    return str(current_executable_path()), f'"{Path(__file__).with_name("main.py").resolve()}" --memory-clean-helper'


def _decode_output(value: bytes | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if value.startswith((b"\xff\xfe", b"\xfe\xff")) or value[:200].count(b"\0") > 10:
        try:
            return value.decode("utf-16")
        except UnicodeError:
            pass
    for encoding in ("utf-8", "gb18030"):
        try:
            return value.decode(encoding)
        except UnicodeError:
            pass
    return value.decode(errors="replace")


def task_details() -> dict:
    if os.name != "nt":
        return {"exists": False, "valid": False, "message": "仅 Windows 可用"}
    expected_exe, expected_args = executable_command()
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = _decode_output(result.stdout)
    if result.returncode != 0:
        return {"exists": False, "valid": False, "message": _decode_output(result.stderr).strip(),
                "expected_exe": expected_exe, "expected_args": expected_args}
    try:
        root = ElementTree.fromstring(output)
        command = root.findtext(".//{*}Actions/{*}Exec/{*}Command", "").strip().strip('"')
        arguments = root.findtext(".//{*}Actions/{*}Exec/{*}Arguments", "").strip()
    except ElementTree.ParseError as exc:
        return {"exists": True, "valid": False, "message": f"任务 XML 无法解析: {exc}",
                "expected_exe": expected_exe, "expected_args": expected_args}
    valid = (os.path.normcase(os.path.abspath(command)) ==
             os.path.normcase(os.path.abspath(expected_exe)) and arguments == expected_args)
    return {"exists": True, "valid": valid, "command": command, "arguments": arguments,
            "expected_exe": expected_exe, "expected_args": expected_args,
            "message": "" if valid else "计划任务路径或参数已失效"}


def task_is_valid() -> bool:
    return bool(task_details().get("valid"))


def is_process_elevated() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def request_elevated_install(operation_id: str) -> dict:
    if os.name != "nt" or os.environ.get("MEINIFENG_DISABLE_CLEAN_TASK") == "1":
        return {"ok": False, "cancelled": False, "operation_id": operation_id,
                "message": "当前环境已禁用授权安装"}
    executable = str(current_executable_path())
    if is_compiled():
        params = f"--install-clean-task --install-operation {operation_id}"
    else:
        params = (f'"{Path(__file__).with_name("main.py").resolve()}" --install-clean-task '
                  f"--install-operation {operation_id}")

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=0x00000040,
                             lpVerb="runas", lpFile=executable, lpParameters=params, nShow=0)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        error_code = ctypes.windll.kernel32.GetLastError()
        return {"ok": False, "cancelled": error_code == 1223, "error_code": error_code,
                "operation_id": operation_id,
                "message": "已取消管理员授权" if error_code == 1223 else f"无法启动授权程序（{error_code}）"}
    wait_code = ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 30000)
    exit_code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    if wait_code == 0x102:
        return {"ok": False, "cancelled": False, "operation_id": operation_id,
                "message": "授权安装等待超时"}
    try:
        result = json.loads(INSTALL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        result = {"ok": False, "message": f"未收到安装结果（退出码 {exit_code.value}）"}
    if result.get("operation_id") != operation_id:
        return {"ok": False, "cancelled": False, "operation_id": operation_id,
                "message": "授权结果已过期，请重试"}
    result["valid"] = task_is_valid()
    result["ok"] = bool(result.get("ok") and result["valid"])
    return result


def _current_user_sid() -> str:
    output = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    text = _decode_output(output.stdout)
    parts = [part.strip().strip('"') for part in text.strip().split(",")]
    if output.returncode != 0 or len(parts) < 2:
        raise RuntimeError("无法获取当前用户 SID")
    return parts[-1]


def _task_xml(executable: str, arguments: str, sid: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Author>{escape(sid)}</Author><Description>{APP_NAME}按需内存清理</Description></RegistrationInfo>
  <Principals><Principal id="Author"><UserId>{escape(sid)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>false</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun><ExecutionTimeLimit>PT1M</ExecutionTimeLimit><Priority>7</Priority></Settings>
  <Actions Context="Author"><Exec><Command>{escape(executable)}</Command><Arguments>{escape(arguments)}</Arguments></Exec></Actions>
</Task>'''


def install_task(operation_id: str = "") -> int:
    executable, arguments = executable_command()
    try:
        xml = _task_xml(executable, arguments, _current_user_sid())
        xml_path = Path(tempfile.gettempdir()) / f"meinifeng-clean-task-{os.getpid()}.xml"
        xml_path.write_text(xml, encoding="utf-16")
        try:
            result = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
                                    capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            try: xml_path.unlink()
            except OSError: pass
        message = (_decode_output(result.stdout) or _decode_output(result.stderr)).strip()
        code = result.returncode
    except Exception as exc:
        code, message = 1, str(exc)
    INSTALL_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(INSTALL_FILE, {"ok": code == 0, "code": code, "message": message,
                                "exe": executable, "arguments": arguments,
                                "operation_id": operation_id, "task_name": TASK_NAME})
    return code


def queue_cleanup() -> str | None:
    operation_id = uuid.uuid4().hex
    REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(REQUEST_FILE, {"operation_id": operation_id, "requested_at": time.time()})
    if is_process_elevated():
        executable, arguments = executable_command()
        command = [executable]
        if not is_compiled():
            command.append(str(Path(__file__).with_name("main.py").resolve()))
        command.append("--memory-clean-helper")
        try:
            subprocess.Popen(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return operation_id
        except OSError:
            return None
    if not task_is_valid():
        return None
    result = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return operation_id if result.returncode == 0 else None


def read_result(operation_id: str) -> dict | None:
    try:
        value = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        return value if value.get("operation_id") == operation_id else None
    except (OSError, ValueError, TypeError):
        return None


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _enable_privilege(name: str) -> bool:
    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD),
                    ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x20 | 0x8, ctypes.byref(token)):
        return False
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        privileges = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES(luid, 0x2),))
        ctypes.set_last_error(0)
        ok = advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(privileges), 0, None, None)
        return bool(ok and ctypes.get_last_error() != 1300)  # ERROR_NOT_ALL_ASSIGNED
    finally:
        kernel32.CloseHandle(token)


def _nt_set(info_class: int, payload) -> tuple[bool, str]:
    ntdll = ctypes.WinDLL("ntdll")
    size = ctypes.sizeof(payload) if payload is not None else 0
    status = ntdll.NtSetSystemInformation(info_class, ctypes.byref(payload) if payload is not None else None, size)
    return status >= 0, f"NTSTATUS 0x{status & 0xffffffff:08X}"


def _trim_process_working_sets() -> tuple[bool, str]:
    """Fallback when the system-wide working-set command is unavailable."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.EmptyWorkingSet.argtypes = (wintypes.HANDLE,)
    psapi.EmptyWorkingSet.restype = wintypes.BOOL

    attempted = succeeded = denied = 0
    current_pid = os.getpid()
    access = 0x0100 | 0x0400  # PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
    for pid in psutil.pids():
        if pid <= 4 or pid == current_pid:
            continue
        attempted += 1
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            denied += 1
            continue
        try:
            if psapi.EmptyWorkingSet(handle):
                succeeded += 1
        finally:
            kernel32.CloseHandle(handle)
    ok = succeeded > 0 or attempted == 0
    return ok, f"成功 {succeeded}/{attempted}，拒绝访问 {denied}"


def _shrink_system_file_cache() -> tuple[bool, str]:
    maximum = ctypes.c_size_t(-1).value
    ok = ctypes.windll.psapi.SetSystemFileCacheSize(maximum, maximum, 0)
    return bool(ok), "SetSystemFileCacheSize" if ok else f"WinError {ctypes.get_last_error()}"


def perform_cleanup() -> dict:
    before = memory_snapshot()
    steps: dict[str, dict] = {}

    privileges = ("SeProfileSingleProcessPrivilege", "SeIncreaseQuotaPrivilege", "SeDebugPrivilege")
    privilege_results = {name: _enable_privilege(name) for name in privileges}

    def run_step(name: str, operation) -> bool:
        try:
            ok, message = operation()
            steps[name] = {"ok": bool(ok), "message": str(message)}
        except Exception as exc:
            steps[name] = {"ok": False, "message": str(exc)}
        return bool(steps[name]["ok"])

    if not run_step("empty_working_sets", lambda: _nt_set(80, ctypes.c_int(2))):
        run_step("process_working_sets_fallback", _trim_process_working_sets)
    run_step("flush_modified_pages", lambda: _nt_set(80, ctypes.c_int(3)))
    run_step("purge_standby_list", lambda: _nt_set(80, ctypes.c_int(4)))

    run_step("system_file_cache", _shrink_system_file_cache)
    run_step("registry_cache", lambda: _nt_set(143, None))

    class MEMORY_COMBINE_INFORMATION_EX(ctypes.Structure):
        _fields_ = [("Handle", wintypes.HANDLE), ("PagesCombined", ctypes.c_size_t), ("Flags", wintypes.ULONG)]
    run_step("combine_memory", lambda: _nt_set(130, MEMORY_COMBINE_INFORMATION_EX(None, 0, 0)))
    time.sleep(1.0)
    after = memory_snapshot()
    return {
        "before_available": before.physical_available,
        "after_available": after.physical_available,
        "available_increase": after.physical_available - before.physical_available,
        "before_percent": before.load_percent, "after_percent": after.load_percent,
        "steps": steps, "privileges": privilege_results, "completed_at": time.time(),
    }


def helper_main() -> int:
    try:
        request = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
        operation_id = str(request["operation_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return 2
    result = perform_cleanup()
    result["operation_id"] = operation_id
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(RESULT_FILE, result)
    return 0
