from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
import threading
from ctypes import wintypes
from pathlib import Path
from pet_core import APP_NAME, config_path, is_compiled, stable_application_path
from cleanup_protocol import (SCHEMA, STEPS, TERMINAL, EXIT_CODES, START_TIMEOUT, UI_TIMEOUT,
                              active_operation, create_operation, operation_path, root_path,
                              safe_profile, transaction_lock, read_json, write_json, release_lease, digest)
from cleanup_process import ObservedProcess, ExecutionMutex, current_identity, identity_alive, valid_identity
from concurrent.futures import Future


TASK_NAME = f"{APP_NAME}_内存清理"
TASK_SCHEMA_VERSION = SCHEMA
REQUEST_FILE = config_path().with_name("memory-clean-request.json")
RESULT_FILE = config_path().with_name("memory-clean-result.json")
INSTALL_FILE = config_path().with_name("memory-clean-install.json")
_observers: dict[str, ObservedProcess] = {}
_observer_lock = threading.RLock()
_recoveries: dict[str, tuple[Future, float]] = {}


def profile_path() -> Path:
    return safe_profile(config_path().parent)


def task_name() -> str:
    profile = profile_path()
    ordinary = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP_NAME
    if os.path.normcase(str(profile)) == os.path.normcase(str(ordinary.resolve())):
        return TASK_NAME
    import hashlib
    return TASK_NAME + "_" + hashlib.sha256(os.path.normcase(str(profile)).encode()).hexdigest()[:12]


def current_executable_path() -> Path:
    """Return the user's onefile path, never Nuitka's temporary extraction path."""
    if is_compiled():
        return stable_application_path()
    return Path(sys.executable).resolve()


def helper_path() -> Path:
    if is_compiled():
        application = stable_application_path()
        if application.name.lower() == "cutemaple-cleaner.exe":
            return application
        return application.parent / "cleaner" / "CuteMaple-Cleaner.exe"
    return Path(__file__).parent / "artifacts" / "native-cleaner" / "CuteMaple-Cleaner.exe"


def helper_command(*arguments: str) -> list[str]:
    helper = helper_path()
    if not helper.is_file():
        raise FileNotFoundError(f"独立清理程序缺失：{helper}")
    return [str(helper)] + list(arguments)


def executable_command() -> tuple[str, str]:
    command = helper_command("--memory-clean-helper", "--profile", str(profile_path()))
    return command[0], subprocess.list2cmdline(command[1:])


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


def session_is_valid() -> bool:
    from cleanup_session import session_valid
    return session_valid()


def task_is_valid() -> bool:
    # Compatibility for archived diagnostic clients; never queries a task.
    return session_is_valid()

def is_process_elevated() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def request_session_authorization(operation_id: str = "") -> dict:
    if os.name != "nt" or os.environ.get("MEINIFENG_DISABLE_CLEAN_TASK") == "1":
        return {"ok": False, "status": "failed", "message": "当前环境已禁用清理授权"}
    try:
        helper_command()  # Missing/quarantined binaries are not an authorization failure.
        from cleanup_session import authorize
        return authorize(helper_command, profile_path())
    except (OSError, ValueError) as exc:
        return cleanup_start_error(exc)


def request_elevated_install(operation_id: str = "") -> dict:
    return request_session_authorization(operation_id)


def close_cleanup_session() -> None:
    from cleanup_session import close_session
    close_session()


def cleanup_start_error(exc) -> dict:
    code = getattr(exc, "winerror", None)
    if isinstance(exc, FileNotFoundError):
        status, message = "helper_missing", "清理助手缺失，可能已被系统防护移除；请查看 Windows 安全中心保护历史。"
    elif code in (225, 226):
        status, message = "security_blocked", f"Windows 防护阻止了清理助手（{code}）；本次清理未完成。"
    else:
        status, message = "failed", str(exc)
    return {"ok": False, "status": status, "error_code": code, "message": message}

def _failure_before_start(profile: Path, operation_id: str, message: str, error_code=None) -> dict:
    result = {"schema": SCHEMA, "operation_id": operation_id, "status": "failed", "ok": False,
              "terminal": True, "processExitVerified": True, "exitCodeVerified": False, "helperStarted": False,
              "message": message, "error_code": error_code, "steps": {}, "completed_at": time.time(), "available_increase": None}
    write_json(operation_path(profile, operation_id) / "launch-failure.json", result, immutable=True)
    release_lease(profile, operation_id)
    return result


def _read_cleanup(profile: Path, operation_id: str, *, release: bool) -> dict | None:
    folder = operation_path(profile, operation_id)
    request = read_json(folder / "request.json")
    if not request or request.get("operation_id") != operation_id:
        return None
    failed = read_json(folder / "launch-failure.json")
    if failed:
        return failed
    state = read_json(folder / "result.json") or read_json(folder / "status.json") or {}
    if state.get("operation_id") != operation_id:
        return None
    value = {**state, "terminal": False, "processExitVerified": False,
             "requested_at": request["requested_at"], "elapsedSeconds": max(0, time.time() - request["requested_at"])}
    started = read_json(folder / "started.json")
    observed = read_json(folder / "exit-observed.json")
    observer_error = None
    if started and started.get("operation_id") == operation_id:
        identity = started.get("supervisor")
        try:
            with _observer_lock:
                observer = _observers.get(operation_id)
                if observer is None and observed is None:
                    observer = ObservedProcess(identity)
                    _observers[operation_id] = observer
                if observer is not None:
                    code = observer.poll()
                    if code is None:
                        acknowledged = folder / "client-observed.json"
                        if not acknowledged.exists():
                            write_json(acknowledged, {"operation_id": operation_id, "supervisor": identity,
                                                      "client": request["client"], "observed_at": time.time()}, immutable=True)
                    else:
                        observed = {"operation_id": operation_id, "supervisor": identity,
                                    "exit_code": code, "observed_at": time.time(), "method": "process-handle"}
                        write_json(folder / "exit-observed.json", observed)
                        observer.close()
                        _observers.pop(operation_id, None)
        except (OSError, TypeError, KeyError, ValueError) as exc:
            observer_error = str(exc)
    if observed and started and observed.get("supervisor") == started.get("supervisor"):
        # The helper may write result.json between the first file read and the
        # process-handle poll. Refresh after exit rather than rejecting a stale
        # running snapshot or overlooking its last owned worker.
        final = read_json(folder / "result.json")
        state = final or read_json(folder / "status.json") or state
        if state.get("operation_id") != operation_id:
            raise ValueError("清理终态不属于当前操作")
        value.update(state)
        # An exit code is meaningful only with a matching result and every owned
        # worker confirmed gone. Never promote a final-looking file by itself.
        workers = state.get("workers", [])
        if not isinstance(workers, list) or any(not valid_identity(row.get("worker")) for row in workers):
            raise ValueError("无法核验步骤进程身份")
        live_worker = any(identity_alive(row["worker"]) for row in workers)
        if not live_worker:
            value.update(terminal=True, processExitVerified=True, exitCodeVerified=True, supervisorExitCode=observed["exit_code"])
            wanted = EXIT_CODES.get(state.get("status"))
            complete = (final is not None and final.get("schema") == SCHEMA
                        and final.get("request_sha256") == digest(folder / "request.json")
                        and final.get("supervisor") == started["supervisor"]
                        and set(final.get("steps", {})) == set(STEPS)
                        and final.get("expected_exit_code") == observed["exit_code"])
            if state.get("status") == "succeeded":
                complete = complete and len(workers) == len(STEPS) and all(
                    isinstance(final["steps"].get(step), dict)
                    and final["steps"][step].get("ok") is True
                    and final["steps"][step].get("exit_verified") is True
                    and final["steps"][step].get("exit_code") == 0 for step in STEPS)
            if not complete or state.get("status") not in TERMINAL or wanted != observed["exit_code"]:
                value.update(status="failed", ok=False, message="清理助手退出，但未返回与退出码一致的完整结果")
            else:
                value["ok"] = state["status"] == "succeeded"
            if release:
                release_lease(profile, operation_id)
            return value
    if (started and valid_identity(started.get("supervisor")) and not identity_alive(started["supervisor"])
            and all(valid_identity(row.get("worker")) for row in state.get("workers", []))
            and not any(identity_alive(row["worker"]) for row in state.get("workers", []))):
        # A previous UI may have exited, losing its process handle. Absence of
        # the exact PID/creation identity proves death, never historical success.
        value.update(status="failed", ok=False, terminal=True, processExitVerified=True,
                     exitCodeVerified=False, completed_at=time.time(), available_increase=None,
                     message="清理助手及其步骤进程已退出，但缺少可核验的历史退出码；本次不能认定成功")
        if release:
            release_lease(profile, operation_id)
        return value
    if not started and value["elapsedSeconds"] >= START_TIMEOUT:
        # No helper may begin an old request. Retain its lease until the outer
        # deadline, preventing an uncertain schtasks timeout from spawning a retry.
        value.update(status="starting", message="清理助手尚未确认接单，正在核验会话状态")
    if value["elapsedSeconds"] >= UI_TIMEOUT:
        value.update(status="unresponsive", recoveryRequired=True,
                     message="清理助手未按期完成退出核验；保留操作锁，避免重复执行")
        if observer_error:
            value["diagnostic"] = observer_error
        if not started and release:
            _schedule_recovery(profile, operation_id)
    return value


def _query_task_state() -> int:
    # Legacy recovery-state values retained locally; no scheduled task exists.
    if not session_is_valid():
        return 3
    from cleanup_session import session_request
    value = session_request("status")
    if not value.get("ok"):
        raise OSError(value.get("message", "无法核验会话状态"))
    return 4 if value.get("operation") else 3

def _recover_queued(profile: Path, operation_id: str) -> bool:
    folder = operation_path(profile, operation_id)
    with transaction_lock(profile):
        request = read_json(folder / "request.json")
        if (not request or time.time() - request.get("requested_at", 0) < UI_TIMEOUT
                or (folder / "started.json").exists() or active_operation(profile) != operation_id):
            return False
        task_state = _query_task_state()
        if task_state not in (1, 3):  # Disabled or Ready, never Queued/Running/Unknown.
            return False
        mutex = ExecutionMutex()
        try:
            if not mutex.acquire():
                return False
            # Supervisor claims under this same transaction lock and refuses a
            # request older than START_TIMEOUT. It cannot start after recovery.
            result = {"schema": SCHEMA, "operation_id": operation_id, "status": "failed", "ok": False,
                      "terminal": True, "processExitVerified": True, "exitCodeVerified": False,
                      "helperStarted": False, "message": "过期请求未被助手接单，已核验会话空闲并解除占用",
                      "completed_at": time.time(), "steps": {}, "available_increase": None,
                      "recovery": {"taskState": task_state, "executionMutexIdle": True,
                                   "expiredRequestUnclaimable": True}}
            write_json(folder / "launch-failure.json", result, immutable=True)
            (root_path(profile) / "active.json").unlink(missing_ok=True)
            return True
        finally:
            mutex.close()


def _schedule_recovery(profile: Path, operation_id: str) -> None:
    with _observer_lock:
        previous = _recoveries.get(operation_id)
        if previous and (not previous[0].done() or time.monotonic() - previous[1] < 5):
            return
        future = Future()
        _recoveries[operation_id] = (future, time.monotonic())
        def recover():
            try:
                future.set_result(_recover_queued(profile, operation_id))
            except Exception as exc:
                future.set_result({"error": str(exc)})
        threading.Thread(target=recover, name="cleanup-recovery", daemon=True).start()


def read_cleanup(operation_id: str) -> dict | None:
    """Nonblocking GUI poll, including the started/observed handshake and real exit."""
    try:
        return _read_cleanup(profile_path(), operation_id, release=True)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"operation_id": operation_id, "status": "unresponsive", "terminal": False,
                "processExitVerified": False, "recoveryRequired": True, "message": str(exc)}


def begin_cleanup() -> dict:
    """Background-only request; authorization remains an explicit UI action."""
    if os.name != "nt" or os.environ.get("MEINIFENG_DISABLE_CLEAN_TASK") == "1":
        return {"ok": False, "status": "failed", "message": "当前环境禁止系统清理"}
    try:
        helper_command()
        if not session_is_valid():
            return {"ok": False, "status": "authorization_required", "message": "本次运行尚未授权"}
        profile = profile_path()
        with transaction_lock(profile):
            old = active_operation(profile)
            if old:
                state = _read_cleanup(profile, old, release=False)
                if not state or not (state.get("terminal") and state.get("processExitVerified")):
                    return {"ok": False, "status": "busy", "operation_id": old,
                            "message": "已有清理操作尚未完成退出核验", "existing": state}
            request = create_operation(profile, helper_path(), current_identity())
        from cleanup_session import session_request
        operation_id = request["operation_id"]
        try:
            result = session_request("start", operation_id)
        except (OSError, ValueError) as exc:
            # The start may have reached the broker. Retain its lease until
            # observation/recovery proves that no supervisor can still start.
            return {"ok": True, "status": "starting", "operation_id": operation_id,
                    "message": "会话响应异常，正在核验本次助手状态", "diagnostic": str(exc)}
        if not result.get("ok"):
            return _failure_before_start(profile, operation_id, result.get("message", "会话拒绝清理请求"), result.get("error_code"))
        return {"ok": True, "status": "queued", "operation_id": operation_id,
                "message": "清理请求已提交，等待监督进程确认"}
    except BlockingIOError:
        # The supervisor can be publishing its acknowledgement while a second
        # click arrives. Lock contention is an existing operation, not failure.
        try:
            operation_id = active_operation(profile_path())
        except (OSError, ValueError):
            operation_id = None
        return {"ok": False, "status": "busy", "operation_id": operation_id,
                "message": "正在更新当前清理进度，请稍候"}
    except (OSError, ValueError) as exc:
        return cleanup_start_error(exc)

def cancel_cleanup(operation_id: str) -> dict:
    try:
        profile = profile_path()
        folder = operation_path(profile, operation_id)
        if not (folder / "request.json").is_file():
            return {"ok": False, "message": "清理操作不存在"}
        path = folder / "cancel.json"
        if not path.exists():
            write_json(path, {"operation_id": operation_id, "requested_at": time.time()}, immutable=True)
        return {"ok": True, "operation_id": operation_id, "status": "cancelling",
                "message": "已请求取消，等待当前步骤退出确认"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "operation_id": operation_id, "message": str(exc)}


def queue_cleanup() -> str | None:
    result = begin_cleanup()
    return result.get("operation_id") if result.get("ok") else None


def read_result(operation_id: str) -> dict | None:
    value = read_cleanup(operation_id)
    return value if value and value.get("terminal") and value.get("processExitVerified") else None


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
