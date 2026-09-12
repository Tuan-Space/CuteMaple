"""Explicit authorization boundary; these tests never launch a helper or UAC."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tools import install_cleanup_candidate as tool


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    executable = tmp_path / "CuteMaple-Cleaner.exe"
    executable.write_bytes(b"fixture-only-never-executed")
    profile, output = tmp_path / "isolated-profile", tmp_path / "evidence"
    monkeypatch.setenv("APPDATA", str(tmp_path / "user-data"))
    calls = []
    client = SimpleNamespace(TASK_NAME="美腻枫_内存清理", is_process_elevated=lambda: False,
        task_name=lambda: "美腻枫_内存清理_fixture", task_details=lambda: {"valid": True})
    client.executable_command = lambda: (client.helper_command()[0], "--memory-clean-helper --profile isolated")
    def install(operation):
        calls.append({"operation": operation, "command": client.helper_command("--install-clean-task")})
        return {"ok": True}
    client.request_elevated_install = install
    monkeypatch.setitem(sys.modules, "memory_cleaner", client)
    monkeypatch.setattr(tool, "_diagnose", lambda helper, p, o: {"passed": True})
    args = ["--profile", str(profile), "--helper", str(executable), "--expected-helper-sha256",
            hashlib.sha256(executable.read_bytes()).hexdigest(), "--output", str(output)]
    return args, calls, output, client


def test_default_validates_without_requesting_authorization(candidate):
    args, calls, output, _ = candidate
    assert tool.main(args) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["validationPassed"] and report["mode"] == "validate-only"
    assert not calls and not report["authorizationRequested"] and not report["cleanupScheduled"]
    assert not report["installationPassed"]


def test_removed_install_never_requests_uac(candidate):
    args, calls, output, _ = candidate
    with pytest.raises(ValueError, match="Persistent installation was removed"):
        tool.main([*args, "--install"])
    assert not calls and not output.exists()


def test_wrong_hash_does_not_even_diagnose(candidate, monkeypatch):
    args, calls, output, _ = candidate
    args[args.index("--expected-helper-sha256") + 1] = "0" * 64
    monkeypatch.setattr(tool, "_diagnose", lambda *_: pytest.fail("must not launch diagnostic"))
    with pytest.raises(ValueError, match="SHA256"):
        tool.main(args)
    assert not calls and not output.exists()


def test_personal_profile_is_rejected_before_diagnose(candidate, monkeypatch, tmp_path):
    args, calls, output, _ = candidate
    args[args.index("--profile") + 1] = str(tmp_path / "user-data" / "美腻枫")
    monkeypatch.setattr(tool, "_diagnose", lambda *_: pytest.fail("must not launch diagnostic"))
    with pytest.raises(ValueError, match="personal profile"):
        tool.main(args)
    assert not calls and not output.exists()


def test_failed_diagnostic_prevents_explicit_install(candidate, monkeypatch):
    args, calls, output, _ = candidate
    monkeypatch.setattr(tool, "_diagnose", lambda *_: {"passed": False})
    with pytest.raises(RuntimeError, match="diagnostic"):
        tool.main(args)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert not calls and not report["authorizationRequested"] and not report["validationPassed"]
