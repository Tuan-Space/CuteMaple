"""Record isolated cleanup acceptance; scheduling requires explicit --begin.

This is a normal-user observer/client, never an elevation or native-cleanup
entry point. The helper must already have an explicitly authorized profile task.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--profile", type=Path, required=True)
    result.add_argument("--helper", type=Path, required=True)
    result.add_argument("--expected-helper-sha256", required=True)
    result.add_argument("--output", type=Path, required=True,
                        help="New directory; existing evidence is never replaced")
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--begin", action="store_true", help="Explicitly schedule actual cleanup using the existing authorized task")
    mode.add_argument("--operation", help="Observe this existing operation; never schedule another")
    result.add_argument("--runs", type=int, choices=(1, 2, 3), default=1)
    result.add_argument("--timeout", type=float, default=80,
                        help="Observation limit per operation; does not change helper execution deadlines")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.begin and args.runs != 1:
        raise ValueError("--runs only applies to explicit --begin")
    if not 65 <= args.timeout <= 120:
        raise ValueError("Observation timeout must be 65–120 seconds")
    sys.path.insert(0, str(PROJECT))
    from cleanup_protocol import safe_profile, digest, operation_path, write_json
    profile = safe_profile(args.profile)
    helper = args.helper.resolve(strict=True)
    if helper.name.lower() != "cutemaple-cleaner.exe":
        raise ValueError("Only an explicitly selected compiled CuteMaple-Cleaner.exe is supported")
    expected = args.expected_helper_sha256.lower()
    if len(expected) != 64 or digest(helper) != expected:
        raise ValueError("Selected helper does not match the frozen expected SHA256")
    os.environ["MEINIFENG_PROFILE_DIRECTORY"] = str(profile)
    import memory_cleaner as client
    from cleanup_process import current_identity, identity_alive
    if client.is_process_elevated():
        raise PermissionError("Run this observer as the ordinary user; it never requests elevation")
    client.helper_path = lambda: helper
    client.helper_command = lambda *arguments: [str(helper), *arguments]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_hashes = {name: digest(PROJECT / name) for name in (
        "cleanup_helper.py", "cleanup_protocol.py", "cleanup_process.py", "memory_cleaner.py",
        "pet_core.py", "resource_monitor.py", "diagnostics.py")}
    report = {"schema": 1, "reportKind": "cleanup-operation-observation", "profile": str(profile),
              "evidenceVersion": 1, "executionMode": "native-compiled-helper",
              "observerElevated": False,
              "helper": str(helper), "helperSha256": expected, "observer": current_identity(),
              "sourceClientHashes": source_hashes, "startedAtUnix": time.time(),
              "explicitSchedulingRequested": args.begin, "requestedRuns": args.runs,
              "operations": [], "passed": False, "privilegedCallsInObserver": False}
    report_path = output / "report.json"
    observations = output / "observations.jsonl"
    write_json(report_path, report)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cleanup-observer-schedule") as executor:
            for index in range(args.runs):
                entry = {"index": index + 1, "observedStartedAtUnix": time.time()}
                report["operations"].append(entry)
                if args.begin:
                    scheduled = executor.submit(client.begin_cleanup).result(timeout=20)
                    entry["submission"] = scheduled
                    if not scheduled.get("ok"):
                        entry["error"] = "Scheduling was not accepted; no automatic authorization or retry"
                        break
                    operation = scheduled["operation_id"]
                else:
                    operation = args.operation
                entry["operation_id"] = operation
                started = time.monotonic()
                last = None
                while time.monotonic() - started < args.timeout:
                    current = client.read_cleanup(operation)
                    if current != last:
                        record = {"observedAtUnix": time.time(), "operation_id": operation, "value": current}
                        with observations.open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                        last = current
                    if current and current.get("terminal") and current.get("processExitVerified"):
                        entry["result"] = current
                        break
                    time.sleep(.2)
                else:
                    entry["result"] = last
                    entry["observationEndedBeforeCompletion"] = True
                entry["observedFinishedAtUnix"] = time.time()
                folder = operation_path(profile, operation)
                entry["operationFiles"] = {
                    str(path): digest(path) for path in sorted(folder.glob("*.json")) if path.is_file()}
                value = entry.get("result") or {}
                identities = [row["worker"] for row in value.get("workers", []) if row.get("worker")]
                if value.get("supervisor"):
                    identities.append(value["supervisor"])
                entry["ownedProcessesStillAlive"] = [identity for identity in identities if identity_alive(identity)]
                entry["exitChecks"] = [{"identity": identity, "alive": identity_alive(identity),
                                         "observedAtUnix": time.time()} for identity in identities]
                entry["observedFinishedAtUnix"] = time.time()
                entry["passed"] = bool(value.get("status") == "succeeded" and value.get("ok")
                                       and value.get("terminal") and value.get("processExitVerified")
                                       and value.get("exitCodeVerified") and not entry["ownedProcessesStillAlive"]
                                       and not entry.get("observationEndedBeforeCompletion"))
                write_json(report_path, report)
                if not entry["passed"]:
                    break  # A failed run is evidence, never an invitation to retry automatically.
        report["helperSha256After"] = digest(helper)
        report["sourceClientHashesAfter"] = {name: digest(PROJECT / name) for name in source_hashes}
        report["observerScript"] = {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__).resolve())}
        report["passed"] = (len(report["operations"]) == args.runs
                            and all(row.get("passed") for row in report["operations"])
                            and report["helperSha256After"] == expected
                            and report["sourceClientHashesAfter"] == source_hashes)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finishedAtUnix"] = time.time()
        if observations.exists():
            report["observationsSha256"] = digest(observations)
        write_json(report_path, report)
        write_json(output / "OBSERVATION-MANIFEST.json", {
            "schema": 1, "reportKind": "cleanup-observation-manifest", "createdAtUnix": time.time(),
            "report": {"path": str(report_path), "sha256": digest(report_path)},
            "helperSha256": expected,
            "operationFiles": {path: checksum for row in report["operations"]
                               for path, checksum in row.get("operationFiles", {}).items()},
        }, immutable=True)
    print(json.dumps({"report": str(report_path), "passed": report["passed"]}))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
