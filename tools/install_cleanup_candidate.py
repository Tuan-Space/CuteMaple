"""Validate a compiled cleanup candidate without elevation or cleanup.

Only an isolated profile is supported. This tool never schedules cleanup,
enables startup, writes personal settings, or changes Windows protection.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--profile", type=Path, required=True)
    result.add_argument("--helper", type=Path, required=True)
    result.add_argument("--expected-helper-sha256", required=True)
    result.add_argument("--output", type=Path, required=True, help="A new evidence directory")
    result.add_argument("--install", action="store_true",
                        help="Removed: authorize the session from the running desktop pet instead")
    return result


def _diagnose(helper: Path, profile: Path, output: Path) -> dict:
    report = output / "helper-diagnose.json"
    command = [str(helper), "--diagnose", "--profile", str(profile), "--report", str(report)]
    started = time.time()
    completed = subprocess.run(command, cwd=helper.parent, capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (output / "diagnose-stdout.txt").write_bytes(completed.stdout)
    (output / "diagnose-stderr.txt").write_bytes(completed.stderr)
    value = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    passed = (completed.returncode == 0 and isinstance(value, dict)
              and value.get("component") == "cleaner" and value.get("qtImported") is False
              and value.get("privilegedOperationPerformed") is False)
    return {"command": command, "startedAtUnix": started, "finishedAtUnix": time.time(),
            "exitCode": completed.returncode, "passed": passed, "report": value}


def main(argv=None):
    args = parser().parse_args(argv)
    if args.install:
        raise ValueError("Persistent installation was removed; authorize this session from the running desktop pet.")
    sys.path.insert(0, str(PROJECT))
    from cleanup_protocol import safe_profile, digest, write_json
    if not args.helper.is_absolute() or not args.output.is_absolute():
        raise ValueError("Helper and output must be explicit absolute paths")
    profile = safe_profile(args.profile)
    # Check the lexical parent before resolving away any links.
    safe_profile(args.helper.parent)
    if args.helper.is_symlink():
        raise ValueError("Helper must not be a symbolic link")
    helper = args.helper.resolve(strict=True)
    if helper.name.lower() != "cutemaple-cleaner.exe":
        raise ValueError("Expected the explicitly selected compiled CuteMaple-Cleaner.exe")
    expected = args.expected_helper_sha256.lower()
    if (len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)
            or digest(helper) != expected):
        raise ValueError("Helper does not match the frozen SHA256; nothing was launched")
    ordinary = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "美腻枫"
    if os.path.normcase(str(profile)) == os.path.normcase(str(ordinary.resolve())):
        raise ValueError("This candidate tool refuses the personal profile; use an isolated profile")
    os.environ["MEINIFENG_PROFILE_DIRECTORY"] = str(profile)
    import memory_cleaner as client
    if client.is_process_elevated():
        raise PermissionError("Launch diagnostics as the ordinary user")
    client.helper_path = lambda: helper
    client.helper_command = lambda *arguments: [str(helper), *arguments]
    output = safe_profile(args.output)
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "report.json"
    report = {"schema": 1, "reportKind": "isolated-cleanup-authorization",
              "startedAtUnix": time.time(), "profile": str(profile), "helper": str(helper),
              "helperSha256": expected, "mode": "validate-only",
              "authorizationRequested": False, "cleanupScheduled": False,
              "validationPassed": False, "installationPassed": False}
    write_json(report_path, report)
    code = 2
    try:
        report["diagnostic"] = _diagnose(helper, profile, output)
        report["helperSha256AfterDiagnostic"] = digest(helper)
        if not report["diagnostic"]["passed"] or report["helperSha256AfterDiagnostic"] != expected:
            raise RuntimeError("Candidate diagnostic or hash verification failed; no authorization requested")
        report["validationPassed"] = True
        code = 0
        report["helperSha256After"] = digest(helper)
        if report["helperSha256After"] != expected:
            report["validationPassed"] = report["installationPassed"] = False
            code = 2
        report["evidenceFiles"] = {path.name: digest(path) for path in sorted(output.iterdir())
                                   if path.is_file() and path != report_path}
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finishedAtUnix"] = time.time()
        write_json(report_path, report)
    print(json.dumps({"report": str(report_path), "mode": report["mode"],
                      "validationPassed": report["validationPassed"],
                      "installationPassed": report["installationPassed"]}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
