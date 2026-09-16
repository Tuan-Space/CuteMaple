"""Separate privileged helper. This executable never imports or creates Qt."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from ctypes import wintypes
import subprocess
import uuid
if __name__ == "__main__":
    # Task Scheduler does not inherit the requesting GUI's environment. Select
    # the explicitly authorized profile before diagnostics or request paths.
    if "--profile" in sys.argv:
        from cleanup_protocol import safe_profile
        try:
            os.environ["MEINIFENG_PROFILE_DIRECTORY"] = str(safe_profile(Path(sys.argv[sys.argv.index("--profile") + 1])))
        except (ValueError, IndexError, OSError):
            raise SystemExit(2)
    from diagnostics import initialize
    initialize("cleaner")
import psutil
from pet_core import APP_NAME
from resource_monitor import memory_snapshot
from memory_cleaner import (profile_path, helper_path, helper_command, is_process_elevated)

from cleanup_protocol import (SCHEMA, STEPS, STEP_TIMEOUT, WORK_TIMEOUT, SUPERVISOR_TIMEOUT,
                              START_TIMEOUT, HANDSHAKE_TIMEOUT, EXIT_CODES, load_request, active_operation,
                              operation_path, read_json, write_json, digest, transaction_lock)
from cleanup_process import WorkerJob, ExecutionMutex, current_identity, identity_alive

SYSTEM_MEMORY_LIST_INFORMATION = 80
SYSTEM_COMBINE_PHYSICAL_MEMORY_INFORMATION = 130
SYSTEM_REGISTRY_RECONCILIATION_INFORMATION = 155
_native_trace: list[dict] | None = None



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
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)]
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi32.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
                                             wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
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
    ntdll.NtSetSystemInformation.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtSetSystemInformation.restype = wintypes.LONG
    size = ctypes.sizeof(payload) if payload is not None else 0
    status = ntdll.NtSetSystemInformation(info_class, ctypes.byref(payload) if payload is not None else None, size)
    if _native_trace is not None:
        _native_trace.append({"api": "NtSetSystemInformation", "dll": "ntdll",
                              "informationClass": info_class, "inputSize": size,
                              "inputInteger": payload.value if isinstance(payload, ctypes.c_int) else None,
                              "ntstatus": int(status) & 0xffffffff, "ntstatusHex": f"0x{status & 0xffffffff:08X}",
                              "succeeded": status >= 0})
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
    returns = []
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
            returned = psapi.EmptyWorkingSet(handle)
            returns.append({"returnValue": int(returned), "lastError": 0 if returned else ctypes.get_last_error()})
            if returned:
                succeeded += 1
        finally:
            kernel32.CloseHandle(handle)
    ok = succeeded > 0 or attempted == 0
    if _native_trace is not None:
        _native_trace.append({"api": "EmptyWorkingSet", "dll": "psapi", "attempted": attempted,
                              "succeededCount": succeeded, "accessDeniedCount": denied,
                              "returns": returns, "succeeded": ok})
    return ok, f"成功 {succeeded}/{attempted}，拒绝访问 {denied}"

def _shrink_system_file_cache() -> tuple[bool, str]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetSystemFileCacheSize.argtypes = [ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    kernel32.SetSystemFileCacheSize.restype = wintypes.BOOL
    maximum = ctypes.c_size_t(-1).value
    ok = kernel32.SetSystemFileCacheSize(maximum, maximum, 0)
    error = 0 if ok else ctypes.get_last_error()
    if _native_trace is not None:
        _native_trace.append({"api": "SetSystemFileCacheSize", "dll": "kernel32", "returnValue": int(ok),
                              "lastError": error, "succeeded": bool(ok)})
    return bool(ok), "SetSystemFileCacheSize" if ok else f"WinError {error}"

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
    run_step("registry_cache", lambda: _nt_set(SYSTEM_REGISTRY_RECONCILIATION_INFORMATION, None))

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

def perform_step(name: str) -> tuple[bool, str]:
    """Fixed operations only. This is called exclusively inside a step worker."""
    if name == "empty_working_sets":
        ok, message = _nt_set(SYSTEM_MEMORY_LIST_INFORMATION, ctypes.c_int(2))
        return (ok, message) if ok else _trim_process_working_sets()
    if name == "flush_modified_pages":
        return _nt_set(SYSTEM_MEMORY_LIST_INFORMATION, ctypes.c_int(3))
    if name == "purge_standby_list":
        return _nt_set(SYSTEM_MEMORY_LIST_INFORMATION, ctypes.c_int(4))
    if name == "system_file_cache":
        return _shrink_system_file_cache()
    if name == "registry_cache":
        if tuple(sys.getwindowsversion()[:2]) < (6, 3):
            return False, "SystemRegistryReconciliationInformation requires Windows 8.1 or later"
        return _nt_set(SYSTEM_REGISTRY_RECONCILIATION_INFORMATION, None)
    if name == "combine_memory":
        if sys.getwindowsversion().major < 10:
            return False, "SystemCombinePhysicalMemoryInformation requires Windows 10 or later"
        class MEMORY_COMBINE_INFORMATION_EX(ctypes.Structure):
            _fields_ = [("Handle", wintypes.HANDLE), ("PagesCombined", ctypes.c_size_t), ("Flags", wintypes.ULONG)]
        return _nt_set(SYSTEM_COMBINE_PHYSICAL_MEMORY_INFORMATION, MEMORY_COMBINE_INFORMATION_EX(None, 0, 0))
    raise ValueError("清理步骤不在固定允许列表中")


def _step_file(folder: Path, step: str, suffix: str) -> Path:
    return folder / f"step-{STEPS.index(step):02d}-{step}-{suffix}.json"


def step_worker_main(step: str, operation_id: str, token: str) -> int:
    global _native_trace
    from diagnostics import event, exception
    if step not in STEPS:
        return 2
    profile = profile_path()
    request = load_request(profile, operation_id, helper=helper_path())
    folder = operation_path(profile, operation_id)
    launch = read_json(_step_file(folder, step, "launch"))
    identity = current_identity()
    if (not launch or launch.get("token") != token or launch.get("operation_id") != operation_id
            or launch.get("worker") != identity or launch.get("step") != step
            or launch.get("request_sha256") != digest(folder / "request.json")
            or not identity_alive(launch.get("supervisor"))
            or os.getppid() != launch["supervisor"]["pid"]):
        event("cleanup_worker_rejected", operation_id=operation_id, step=step)
        return 2
    result = {"schema": SCHEMA, "operation_id": operation_id, "step": step, "token": token,
              "evidenceKind": "windows-native-cleanup-step", "evidenceVersion": 1,
              "processElevated": is_process_elevated(), "supervisor": launch["supervisor"],
              "worker": identity, "started_at": time.time(), "ok": False,
              "status": "failed", "privileges": {}, "before_available": None,
              "after_available": None, "before_percent": None, "after_percent": None}
    _native_trace = result["nativeCalls"] = []
    worker_started = time.monotonic()
    event("cleanup_step_started", operation_id=operation_id, step=step)
    write_json(_step_file(folder, step, "started"), {**result, "status": "running"}, immutable=True)
    execution_mutex = None
    try:
        # This survives a supervisor dying while a kernel operation is still
        # completing cancellation. Another profile cannot overlap native steps.
        execution_mutex = ExecutionMutex("step")
        if not execution_mutex.acquire():
            raise RuntimeError("另一清理步骤仍在退出中；未调用系统清理接口")
        before = memory_snapshot()
        result.update(before_available=before.physical_available, before_percent=before.load_percent)
        names = (("SeIncreaseQuotaPrivilege",) if step == "system_file_cache" else
                 ("SeProfileSingleProcessPrivilege",) if step != "registry_cache" else ())
        result["privileges"] = {name: _enable_privilege(name) for name in names}
        if not all(result["privileges"].values()):
            result["message"] = "此步骤所需的管理员令牌权限未能启用"
        else:
            result["ok"], result["message"] = perform_step(step)
        result["status"] = "succeeded" if result["ok"] else "failed"
        after = memory_snapshot()
        result.update(after_available=after.physical_available, after_percent=after.load_percent)
    except Exception as exc:
        result.update(ok=False, status="failed", message=f"{type(exc).__name__}: {exc}")
        exception("cleanup_step_failed", exc)
    finally:
        if execution_mutex is not None:
            execution_mutex.close()
    result["completed_at"] = time.time()
    result["duration_seconds"] = time.monotonic() - worker_started
    result["exit_code"] = 0 if result["ok"] else 20
    write_json(_step_file(folder, step, "result"), result, immutable=True)
    event("cleanup_step_finished", operation_id=operation_id, step=step,
          status=result["status"], exit_code=result["exit_code"], duration_seconds=result["duration_seconds"])
    return result["exit_code"]


def _worker_command(step: str, operation_id: str, token: str, profile: Path) -> list[str]:
    return helper_command("--clean-step", step, "--operation", operation_id, "--token", token,
                          "--profile", str(profile))


def supervise(profile: Path, operation_id: str, *, job_factory=WorkerJob, mutex_factory=ExecutionMutex,
              identity_factory=current_identity, alive=identity_alive, clock=time.monotonic,
              sleep=time.sleep, command_factory=_worker_command) -> int:
    """Own fixed step processes; never invoke a cleanup API in this process."""
    from diagnostics import event, exception
    folder = operation_path(profile, operation_id)
    identity = identity_factory()
    with transaction_lock(profile):
        request = load_request(profile, operation_id, helper=helper_path())
        if (active_operation(profile) != operation_id or time.time() - request["requested_at"] > START_TIMEOUT
                or (folder / "launch-failure.json").exists()):
            return 2
        state = {"schema": SCHEMA, "operation_id": operation_id, "status": "starting",
                 "evidenceKind": "windows-cleanup-supervisor", "evidenceVersion": 1,
                 "message": "清理助手已接单，等待客户端建立退出观察", "supervisor": identity,
                 "steps": {}, "workers": [], "started_at": time.time(),
                 "request_sha256": digest(folder / "request.json"), "client_observed": False,
                 "before_available": None, "after_available": None, "available_increase": None,
                 "guardTriggered": False, "guards": [], "work_duration_seconds": None,
                 "limits": {"stepSeconds": STEP_TIMEOUT, "workSeconds": WORK_TIMEOUT,
                            "supervisorSeconds": SUPERVISOR_TIMEOUT, "handshakeSeconds": HANDSHAKE_TIMEOUT}}
        write_json(folder / "started.json", state, immutable=True)
        write_json(folder / "status.json", state)
    event("cleanup_supervisor_started", operation_id=operation_id, supervisor=identity)
    started = clock()
    job = mutex = worker = None
    work_started = work_finished = None
    terminal, message = "failed", "清理助手没有完成"

    def publish():
        state["heartbeat_at"] = time.time()
        write_json(folder / "status.json", state)

    def cancelled():
        cancel = read_json(folder / "cancel.json")
        return bool(cancel and cancel.get("operation_id") == operation_id)

    try:
        # Prevent a fast child from exiting before the ordinary UI can hold a
        # query/synchronize handle. No privileged operation precedes this ack.
        # /Run itself can take five seconds before the UI receives the operation
        # id and begins polling. Allow that delay plus UI delivery; this waiting
        # phase invokes no native cleanup and remains inside the same 60s clock.
        while clock() - started < min(HANDSHAKE_TIMEOUT, SUPERVISOR_TIMEOUT - 3):
            ack = read_json(folder / "client-observed.json")
            if ack and ack.get("supervisor") == identity and ack.get("client") == request["client"]:
                state["client_observed"] = True
                break
            if not alive(request["client"]):
                break
            sleep(.05)
        state["handshake_duration_seconds"] = clock() - started
        if not state["client_observed"]:
            message = "客户端未建立退出观察，未执行任何清理步骤"
        else:
            mutex = mutex_factory()
            if not mutex.acquire():
                message = "该用户的另一个配置正在执行清理，本次未运行步骤"
            else:
                job = job_factory()
                work_started = clock()
                terminal, message = "succeeded", "所有清理步骤已完成"
                for step in STEPS:
                    if cancelled() or not alive(request["client"]):
                        terminal, message = "cancelled", "已停止本次清理；此前完成的步骤不会回滚"
                        break
                    if clock() - work_started >= WORK_TIMEOUT or clock() - started >= SUPERVISOR_TIMEOUT - 3:
                        state["guardTriggered"] = True
                        state["guards"].append({"scope": "work-or-supervisor", "atSeconds": clock()-started})
                        terminal, message = "timed_out", "达到清理总执行期限，未启动后续步骤"
                        break
                    token = uuid.uuid4().hex
                    launch = {"schema": SCHEMA, "operation_id": operation_id, "step": step, "token": token,
                              "supervisor": identity, "request_sha256": state["request_sha256"]}
                    state.update(status="running", step=step, message=f"正在清理：{step}")

                    def before_resume(worker_identity):
                        launch["worker"] = worker_identity
                        state["workers"].append({"step": step, "worker": worker_identity})
                        write_json(_step_file(folder, step, "launch"), launch, immutable=True)
                        publish()

                    worker = job.spawn(command_factory(step, operation_id, token, profile), helper_path().parent,
                                       before_resume=before_resume)
                    step_started, last_publish = clock(), clock()
                    interruption = None
                    while worker.poll() is None:
                        if cancelled() or not alive(request["client"]):
                            interruption = "cancelled"
                            break
                        if (clock() - step_started >= STEP_TIMEOUT or clock() - work_started >= WORK_TIMEOUT
                                or clock() - started >= SUPERVISOR_TIMEOUT - 3):
                            interruption = "timed_out"
                            break
                        if clock() - last_publish >= .5:
                            publish()
                            last_publish = clock()
                        sleep(.05)
                    code = worker.poll()
                    if interruption:
                        if interruption == "timed_out":
                            state["guardTriggered"] = True
                            state["guards"].append({"scope": "step-or-work-or-supervisor", "step": step,
                                                    "stepSeconds": clock()-step_started, "atSeconds": clock()-started})
                        state.update(status="cancelling", message="正在终止本次清理步骤并核验退出")
                        publish()
                        worker.terminate(72 if interruption == "timed_out" else 73)
                        code = worker.wait(2)
                        terminal = interruption
                        message = "清理步骤达到期限" if interruption == "timed_out" else "已取消本次清理"
                        row = {"ok": False, "status": interruption, "message": message,
                               "exit_code": code, "exit_verified": code is not None, "worker": worker.identity}
                    else:
                        row = read_json(_step_file(folder, step, "result"))
                        if (not row or row.get("operation_id") != operation_id or row.get("step") != step
                                or row.get("worker") != worker.identity or row.get("token") != token
                                or row.get("exit_code") != code or code not in (0, 20)
                                or row.get("ok") is not (code == 0)):
                            row = {"ok": False, "status": "failed", "message": "步骤退出码与独立结果不一致",
                                   "exit_code": code, "worker": worker.identity}
                        row["exit_verified"] = code is not None
                    row["duration_seconds"] = max(0, clock() - step_started)
                    state["steps"][step] = row
                    state["workers"][-1].update(exit_code=code, exit_verified=code is not None)
                    if state["before_available"] is None and isinstance(row.get("before_available"), int):
                        state["before_available"] = row["before_available"]
                        state["before_percent"] = row.get("before_percent")
                    if isinstance(row.get("after_available"), int):
                        state["after_available"] = row["after_available"]
                        state["after_percent"] = row.get("after_percent")
                    publish()
                    worker.close()
                    worker = None
                    if interruption:
                        break
                work_finished = clock()
                if terminal == "succeeded" and any(not row.get("ok") for row in state["steps"].values()):
                    terminal = "partial" if any(row.get("ok") for row in state["steps"].values()) else "failed"
                    message = "清理已结束，部分步骤失败" if terminal == "partial" else "清理步骤未成功"
    except Exception as exc:
        exception("cleanup_supervisor_failed", exc)
        state["error_code"] = getattr(exc, "winerror", None)
        terminal, message = "failed", f"清理执行失败：{exc}"
    finally:
        if work_started is not None:
            state["work_duration_seconds"] = (work_finished if work_finished is not None else clock()) - work_started
        if worker is not None:
            try:
                worker.terminate(74)
                worker.wait(2)
            except OSError as exc:
                state["worker_shutdown_error"] = str(exc)
            worker.close()
        if job is not None:
            job.close()  # OS kills only workers assigned by this supervisor.
        if mutex is not None:
            mutex.close()
    for step in STEPS:
        if step not in state["steps"]:
            state["steps"][step] = {"ok": False, "status": "not_run", "message": "本次未执行"}
    if state["before_available"] is not None and state["after_available"] is not None:
        state["available_increase"] = state["after_available"] - state["before_available"]
    state.update(status=terminal, message=message, completed_at=time.time(),
                 duration_seconds=clock() - started, expected_exit_code=EXIT_CODES[terminal])
    write_json(folder / "result.json", state, immutable=True)
    write_json(folder / "status.json", state)
    event("cleanup_supervisor_finished", operation_id=operation_id, status=terminal,
          expected_exit_code=EXIT_CODES[terminal], duration_seconds=state["duration_seconds"])
    return EXIT_CODES[terminal]


def helper_main(operation_id: str | None = None) -> int:
    try:
        profile = profile_path()
        if not operation_id:
            return 2  # Never replay the legacy global request.json.
        return supervise(profile, operation_id)
    except (OSError, ValueError, KeyError, TypeError):
        return 2

def main() -> int:
    from diagnostics import initialize, event, exception
    initialize("cleaner")
    try:
        import argparse
        parser = argparse.ArgumentParser()
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--diagnose", action="store_true")
        mode.add_argument("--clean-session", action="store_true")
        mode.add_argument("--memory-clean-helper", action="store_true")
        mode.add_argument("--clean-step", choices=STEPS)
        parser.add_argument("--profile")
        parser.add_argument("--report", type=Path)
        parser.add_argument("--session")
        parser.add_argument("--owner-pid", type=int)
        parser.add_argument("--owner-created", type=int)
        parser.add_argument("--operation")
        parser.add_argument("--token")
        args = parser.parse_args()
        if args.diagnose:
            report = {"component": "cleaner", "qtImported": any(name.startswith("PySide6") for name in sys.modules),
                      "privilegedOperationPerformed": False}
            if args.report:
                args.report.write_text(json.dumps(report), encoding="utf-8")
            if sys.stdout is not None:
                print(json.dumps(report))
            return 0
        if not is_process_elevated():
            event("privileged_operation_rejected", reason="administrator permission required")
            return 5
        if args.clean_session and args.session and args.owner_pid and args.owner_created:
            from cleanup_session import serve
            return serve(profile_path(), {"pid": args.owner_pid, "creationFiletime": args.owner_created},
                         args.session, helper_command, helper_path())
        if args.memory_clean_helper and args.operation:
            return helper_main(args.operation)
        if args.clean_step and args.operation and args.token:
            return step_worker_main(args.clean_step, args.operation, args.token)
        return 2
    except Exception as exc:
        exception("cleanup_helper_failed", exc)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
