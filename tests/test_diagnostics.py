import json
from pathlib import Path
import subprocess
import sys


def test_qt_import_failure_is_recorded_before_ui_import(tmp_path, monkeypatch):
    monkeypatch.setenv("MEINIFENG_PROFILE_DIRECTORY", str(tmp_path))
    code = '''
import sys
import main
class RejectUi:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pet_app":
            raise ImportError("simulated early Qt import failure")
sys.meta_path.insert(0, RejectUi())
main.main()
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=15)
    assert result.returncode != 0
    records = [json.loads(line) for path in (tmp_path / "logs").glob("*.jsonl")
               for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["event"] == "entry"
    assert any(row["event"] == "startup_or_runtime_failure" and
               "simulated early Qt import failure" in row["message"] for row in records)
    assert any(row["event"] == "unhandled_python_exception" for row in records)


def test_thread_failure_is_logged_without_capturing_stdout(tmp_path, monkeypatch):
    monkeypatch.setenv("MEINIFENG_PROFILE_DIRECTORY", str(tmp_path))
    code = '''
import threading
from diagnostics import initialize
initialize("probe")
def fail():
    raise RuntimeError("thread diagnostic fixture")
worker=threading.Thread(target=fail)
worker.start()
worker.join()
print("protocol-ok")
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=15)
    assert result.returncode == 0 and result.stdout.strip() == "protocol-ok"
    records = [json.loads(line) for path in (tmp_path / "logs").glob("*.jsonl")
               for line in path.read_text(encoding="utf-8").splitlines()]
    assert any(row["event"] == "unhandled_thread_exception" for row in records)


def test_old_privileged_entry_is_rejected_before_importing_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("MEINIFENG_PROFILE_DIRECTORY", str(tmp_path))
    code = '''
import sys
import main
sys.argv = ["main.py", "--memory-clean-helper"]
assert main.main() == 2
assert "pet_app" not in sys.modules
assert "cleanup_helper" not in sys.modules
assert not any(name.startswith("PySide6") for name in sys.modules)
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=15)
    assert result.returncode == 0, result.stderr


def test_desktop_profile_is_applied_before_first_diagnostic(tmp_path, monkeypatch):
    monkeypatch.delenv("MEINIFENG_PROFILE_DIRECTORY", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "real-profile-must-stay-empty"))
    profile = tmp_path / "qa-profile"
    code = '''
import sys
from types import SimpleNamespace
import main
sys.modules["desktop_check"] = SimpleNamespace(run=lambda args: 0)
sys.argv = ["main.py", "--verify-desktop", "--profile", sys.argv[1]]
assert main.main() == 0
'''
    result = subprocess.run([sys.executable, "-c", code, str(profile)], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=15)
    assert result.returncode == 0, result.stderr
    assert list((profile / "logs").glob("app-*.jsonl"))
    assert not (tmp_path / "real-profile-must-stay-empty").exists()
