import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import pet_core
import memory_cleaner


def test_legacy_consent_migration_preserves_exact_backup_and_preferences(tmp_path):
    path = tmp_path / "settings.json"
    original = json.dumps({"autostart": True, "scale": 1.4, "roaming_enabled": True,
                           "keyboard_enabled": False, "auto_clean_interval_enabled": True,
                           "auto_clean_interval_minutes": 120, "clean_task_schema_version": 2}).encode()
    path.write_bytes(original)
    settings = pet_core.load_settings(path)
    backups = list(tmp_path.glob("settings.pre-v3.*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert settings.scale == 1.4 and settings.roaming_enabled
    assert not settings.keyboard_enabled
    assert not settings.autostart and not settings.auto_clean_interval_enabled
    assert settings.auto_clean_interval_minutes == 120
    assert settings.clean_task_schema_version == 0
    pet_core.load_settings(path)
    assert len(list(tmp_path.glob("settings.pre-v3.*.json"))) == 1


def test_failed_backup_never_overwrites_original_or_reactivates_consent(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    original = b'{"autostart":true,"scale":1.3}'
    path.write_bytes(original)
    original_open = Path.open

    def fail_backup(self, *args, **kwargs):
        if ".pre-v3." in self.name:
            raise PermissionError("read-only backup")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_backup)
    assert not pet_core.load_settings(path).autostart
    assert path.read_bytes() == original


def test_corrupt_legacy_profile_is_backed_up_before_repair(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b"invalid-json\xff")
    assert pet_core.load_settings(path) == pet_core.PetSettings()
    assert next(tmp_path.glob("settings.pre-v3.*.json")).read_bytes() == b"invalid-json\xff"


def test_compiled_startup_uses_installed_launcher_not_payload(tmp_path, monkeypatch):
    launcher = tmp_path / "安装目录" / "CuteMaple-Live2D.exe"
    payload = tmp_path / "onefile_100_200" / launcher.name
    monkeypatch.setattr(pet_core, "is_compiled", lambda: True)
    monkeypatch.setattr(pet_core, "__compiled__", SimpleNamespace(containing_dir=str(tmp_path)), raising=False)
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    monkeypatch.setattr(sys, "executable", str(payload))
    assert pet_core.stable_application_path() == launcher
    assert pet_core.startup_command() == f'"{launcher}"'
    monkeypatch.setattr(sys, "argv", [str(payload)])
    with pytest.raises(ValueError, match="temporary"):
        pet_core.startup_command()


def test_nuitka_containing_directory_resolves_relative_argv(tmp_path, monkeypatch):
    monkeypatch.setattr(pet_core, "is_compiled", lambda: True)
    monkeypatch.setattr(pet_core, "__compiled__", SimpleNamespace(containing_dir=str(tmp_path)), raising=False)
    monkeypatch.setattr(sys, "argv", ["CuteMaple-Live2D.exe"])
    assert pet_core.stable_application_path() == tmp_path / "CuteMaple-Live2D.exe"


def test_profile_override_does_not_use_real_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("MEINIFENG_PROFILE_DIRECTORY", str(tmp_path))
    assert pet_core.config_path() == tmp_path / "settings.json"


def test_cleanup_targets_separate_helper_and_missing_helper_fails(tmp_path, monkeypatch):
    launcher = tmp_path / "CuteMaple-Live2D.exe"
    monkeypatch.setattr(memory_cleaner, "is_compiled", lambda: True)
    monkeypatch.setattr(memory_cleaner, "stable_application_path", lambda: launcher)
    helper = tmp_path / "cleaner/CuteMaple-Cleaner.exe"
    assert memory_cleaner.helper_path() == helper
    with pytest.raises(FileNotFoundError):
        memory_cleaner.helper_command("--memory-clean-helper")
    helper.parent.mkdir()
    helper.write_bytes(b"test-only path fixture")
    assert memory_cleaner.executable_command() == (str(helper), subprocess.list2cmdline(
        ["--memory-clean-helper", "--profile", str(memory_cleaner.profile_path())]))


def test_cleanup_never_replays_legacy_global_request(tmp_path, monkeypatch):
    import cleanup_helper
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"operation_id": "a" * 32, "requested_at": 1}))
    monkeypatch.setattr(cleanup_helper, "profile_path", lambda: tmp_path)
    monkeypatch.setattr(cleanup_helper, "supervise", lambda *_: pytest.fail("must not run cleanup"))
    assert cleanup_helper.helper_main() == 2


def test_helper_diagnostic_imports_no_qt_and_performs_no_privileged_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("MEINIFENG_PROFILE_DIRECTORY", str(tmp_path))
    script = Path(__file__).resolve().parents[1] / "cleanup_helper.py"
    report_path = tmp_path / "helper-report.json"
    result = subprocess.run([sys.executable, str(script), "--diagnose", "--report", str(report_path)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"component": "cleaner", "qtImported": False,
                                       "privilegedOperationPerformed": False}
    assert json.loads(report_path.read_text()) == json.loads(result.stdout)
