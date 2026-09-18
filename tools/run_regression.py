"""Run the complete regression suite in sequential, disposable pytest processes.

Use the project venv Python and a NEW --output-dir. Evidence is written before
launch and after every group; an interrupted or incomplete run never says pass.
Source hashes cover Git-listed Python, tests/fixtures and test/build config,
not the large authoring assets (individual asset tests validate those inputs).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


# Full rigs and native atlas images must be released by process exit.
ISOLATED_MODULES = (
    "tests/test_climb_refinement.py",
    "tests/test_free_arm_refinement.py",
    "tests/test_maple_v5_authoring.py",
    "tests/test_sleep_contact_refinement.py",
    "tests/test_native_atlas.py",
    "tests/test_transition_geometry.py",
)
CONFIG_INPUTS = ("scripts/build.ps1", "pytest.ini", "pyproject.toml", "setup.cfg",
                 "config/requirements.txt", "config/requirements-build.txt",
                 "tools/authoring/climb_contact_native08.json")
EXCLUDED_PARTS = {".build", "dist", ".venv", ".git", "__pycache__"}
COUNT_KEYS = ("tests", "passed", "failures", "errors", "skipped")
# Cleanup fixtures add up to ~175 characters below pytest's basetemp. Leave
# room below legacy Windows MAX_PATH without changing system policy.
MAX_WINDOWS_BASETEMP_LENGTH = 80


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data):
    """Replace atomically, keeping the preceding result if writing is interrupted."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def source_snapshot(root: Path):
    # Git lists names only: never walk or open withdrawn build/dist artifacts.
    if (root / '.git').exists():
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others",
             "--exclude-standard", "-z", "--", "*.py", "tests/fixtures", *CONFIG_INPUTS],
            capture_output=True, check=True,
        )
        names = sorted(set(os.fsdecode(result.stdout).split("\0")) - {""})
    else:
        manifest = json.loads((root / 'FILE-HASHES.json').read_text(encoding='utf-8'))
        names = sorted(n for n in manifest if n.endswith('.py') or n.startswith('tests/fixtures/') or n in CONFIG_INPUTS)
    hashes = {}
    for name in names:
        relative = Path(name)
        if any(part.casefold() in EXCLUDED_PARTS for part in relative.parts):
            continue
        path = root / relative
        # Missing tracked files are evidence too, rather than silently omitted.
        if not path.exists():
            hashes[relative.as_posix()] = None
            continue
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError(f"Source input escapes project: {relative}")
        if path.is_file():
            with path.open("rb") as stream:
                hashes[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    if not hashes:
        raise ValueError("No source inputs found; use the project Git checkout.")
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": hashes}


def junit_counts(path: Path):
    document = ET.parse(path).getroot()
    suites = list(document.iter("testsuite"))
    if not suites:
        raise ValueError("JUnit report contains no test suites")
    counts = {key: 0 for key in COUNT_KEYS}
    for suite in suites:
        # Pytest emits flat suites. Reject unexpected nested totals to avoid
        # double-counting a report from an incompatible plugin.
        if suite.find("testsuite") is not None:
            raise ValueError("Nested JUnit test suites are unsupported")
        values = {key: int(suite.attrib[key]) for key in
                  ("tests", "failures", "errors", "skipped")}
        values["passed"] = values["tests"] - sum(values[key] for key in
                                                ("failures", "errors", "skipped"))
        if min(values.values()) < 0:
            raise ValueError("Invalid JUnit counts")
        for key in COUNT_KEYS:
            counts[key] += values[key]
    return counts


def make_plan(root: Path, output: Path, pytest_command):
    groups = [(Path(module).stem.removeprefix("test_"), [module])
              for module in ISOLATED_MODULES]
    groups.append(("rest", ["tests", *(f"--ignore={module}" for module in ISOLATED_MODULES)]))
    plan = []
    for index, (name, selection) in enumerate(groups, 1):
        directory = output / f"{index:02d}-{name}"
        basetemp = output / f"t{index:02d}"
        if sys.platform == "win32" and len(str(basetemp)) > MAX_WINDOWS_BASETEMP_LENGTH:
            raise ValueError(
                f"Pytest basetemp is too long for Windows fixture paths ({len(str(basetemp))} "
                f"> {MAX_WINDOWS_BASETEMP_LENGTH}): {basetemp}. "
                "Choose a shorter --output-dir, for example artifacts/r-20260910-01.")
        junit = directory / "junit.xml"
        command = [*pytest_command, *selection, "-q", "--tb=short", "-p",
                   "no:cacheprovider", "-o", "addopts=", "--basetemp", str(basetemp),
                   "--junitxml", str(junit)]
        plan.append({"name": name, "status": "pending", "command": command,
                     "directory": str(directory), "basetemp": str(basetemp),
                     "junit": str(junit), "stdout": str(directory / "stdout.log"),
                     "stderr": str(directory / "stderr.log")})
    return plan


def run_regression(root: Path, output: Path, *, pytest_command=None):
    root = root.resolve(strict=True)
    output = output.resolve()
    if not (root / "tests").is_dir():
        raise ValueError(f"Missing tests directory: {root}")
    for module in ISOLATED_MODULES:
        if not (root / module).is_file():
            raise ValueError(f"Missing isolated module: {module}")
    # Never reuse a pytest basetemp: pytest recursively removes it on startup.
    pytest_command = list(pytest_command or [sys.executable, "-m", "pytest"])
    plan = make_plan(root, output, pytest_command)
    output.mkdir(parents=True, exist_ok=False)
    summary = {"status": "preparing", "complete": False, "startedAt": utc_now(),
               "projectRoot": str(root), "outputDirectory": str(output),
               "sourceScope": "Git-listed Python, tests/fixtures and test/build config; excludes .build, dist, .venv and model assets",
               "groups": plan, "totals": {key: 0 for key in COUNT_KEYS},
               "pytestAddoptsOverridden": True}
    summary_path = output / "summary.json"
    write_json(summary_path, summary)
    active = None
    try:
        before = source_snapshot(root)
        write_json(output / "source-inputs.json", before)
        summary["sourceSha256"] = before["sha256"]
        summary["status"] = "running"
        write_json(output / "plan.json", {"sourceSha256": before["sha256"], "groups": plan})
        write_json(summary_path, summary)
        environment = os.environ.copy()
        environment.pop("PYTEST_ADDOPTS", None)
        environment["PYTHONUNBUFFERED"] = "1"
        for group in plan:
            active = group
            directory = Path(group["directory"])
            directory.mkdir()
            basetemp = Path(group["basetemp"])
            if not basetemp.resolve().is_relative_to(output) or basetemp.exists():
                raise ValueError(f"Unsafe or reused pytest basetemp: {basetemp}")
            group.update(status="running", startedAt=utc_now(), sourceSha256=before["sha256"])
            write_json(directory / "result.json", group)
            write_json(summary_path, summary)
            print(f"Running {group['name']}; logs: {directory}", flush=True)
            started = time.monotonic()
            with Path(group["stdout"]).open("wb") as stdout, Path(group["stderr"]).open("wb") as stderr:
                # No shell, elevation, parallel workers or changes to user apps.
                result = subprocess.run(group["command"], cwd=root, env=environment,
                                        stdout=stdout, stderr=stderr)
            group.update(returncode=result.returncode, finishedAt=utc_now(),
                         elapsedSeconds=round(time.monotonic() - started, 3))
            try:
                group["counts"] = junit_counts(Path(group["junit"]))
            except (OSError, ET.ParseError, ValueError, KeyError) as error:
                group["reportError"] = str(error)
            counts = group.get("counts", {})
            group["status"] = "passed" if (result.returncode == 0 and counts
                and not counts["failures"] and not counts["errors"]) else "failed"
            for key in COUNT_KEYS:
                summary["totals"][key] += counts.get(key, 0)
            # Save the process outcome before doing further I/O or hashing.
            write_json(directory / "result.json", group)
            write_json(summary_path, summary)
            after = source_snapshot(root)
            group["sourceUnchanged"] = after == before
            if after != before:
                write_json(directory / "source-inputs-after.json", after)
                group["changedSourceInputs"] = sorted(name for name in
                    before["files"].keys() | after["files"].keys()
                    if before["files"].get(name) != after["files"].get(name))
                group["status"] = "failed"
            write_json(directory / "result.json", group)
            write_json(summary_path, summary)
            print(f"{group['name']}: {group['status']} {counts}", flush=True)
            if group["status"] != "passed":
                summary.update(status="failed", failedGroup=group["name"])
                break
        else:
            summary.update(status="passed", complete=True)
    except KeyboardInterrupt:
        summary.update(status="interrupted", error="Regression run interrupted")
        if active and active["status"] == "running":
            active.update(status="interrupted", finishedAt=utc_now())
            write_json(Path(active["directory"]) / "result.json", active)
    except Exception as error:
        summary.update(status="failed", error=f"{type(error).__name__}: {error}")
        if active:
            active.update(status="failed", infrastructureError=summary["error"])
            write_json(Path(active["directory"]) / "result.json", active)
    summary["finishedAt"] = utc_now()
    summary["completedGroups"] = sum(group["status"] == "passed" for group in plan)
    write_json(summary_path, summary)
    print(f"Regression {summary['status']}: {summary['totals']}; evidence: {summary_path}", flush=True)
    return 0 if summary["status"] == "passed" and summary["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New evidence directory; existing directories are rejected")
    args = parser.parse_args()
    try:
        return run_regression(args.project_root, args.output_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Regression setup failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
