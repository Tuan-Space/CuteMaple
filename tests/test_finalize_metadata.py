"""Metadata transaction tests use tiny synthetic files, never an application."""
from pathlib import Path

import pytest

from tools.finalize_metadata import commit_metadata
import tools.finalize_metadata as metadata


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def package(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "BUILD-STATUS.json").write_bytes(b"original unverified")
    (package / "CANDIDATE-NOT-FINAL.txt").write_bytes(b"original candidate")
    runtime = package / "CuteMaple-Live2D"
    runtime.mkdir()
    (runtime / "untouched.txt").write_bytes(b"runtime bytes")
    return package


def test_success_preserves_archived_candidate_and_updates_only_metadata(package):
    commit_metadata(package, {"BUILD-STATUS.json": b"verified", "READ-ME.txt": b"instructions",
                             "CANDIDATE-ARCHIVE/BUILD-STATUS.json": b"original unverified"},
                    {"CANDIDATE-NOT-FINAL.txt"})
    assert (package / "BUILD-STATUS.json").read_bytes() == b"verified"
    assert (package / "CANDIDATE-ARCHIVE/BUILD-STATUS.json").read_bytes() == b"original unverified"
    assert not (package / "CANDIDATE-NOT-FINAL.txt").exists()
    assert (package / "CuteMaple-Live2D/untouched.txt").read_bytes() == b"runtime bytes"
    assert not list(package.parent.glob(".finalize-*"))


@pytest.mark.parametrize("failed_call", [1, 2, 3, 4, 5])
def test_any_backup_or_install_failure_restores_exact_package(package, monkeypatch, failed_call):
    before = snapshot(package)
    original = metadata.os.replace
    calls = 0
    def failing(source, target):
        nonlocal calls
        calls += 1
        if calls == failed_call:
            raise OSError("synthetic replace failure")
        return original(source, target)
    monkeypatch.setattr(metadata.os, "replace", failing)
    with pytest.raises(OSError, match="synthetic"):
        commit_metadata(package, {"BUILD-STATUS.json": b"verified", "READ-ME.txt": b"instructions",
                                 "CANDIDATE-ARCHIVE/BUILD-STATUS.json": b"original unverified"},
                        {"CANDIDATE-NOT-FINAL.txt"})
    assert snapshot(package) == before
    assert not (package / "CANDIDATE-ARCHIVE").exists()
    assert not list(package.parent.glob(".finalize-*"))


def test_staging_write_failure_does_not_touch_package(package, monkeypatch):
    before = snapshot(package)
    def fail(*args, **kwargs):
        raise OSError("staging unavailable")
    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError, match="staging"):
        commit_metadata(package, {"BUILD-STATUS.json": b"verified"}, set())
    assert snapshot(package) == before


@pytest.mark.parametrize("name", ["../outside.json", "CuteMaple-Live2D/untouched.txt", "CANDIDATE-ARCHIVE/../../outside.json"])
def test_escape_or_runtime_mutation_rejected_before_writes(package, name):
    before = snapshot(package)
    with pytest.raises(ValueError, match="Metadata path"):
        commit_metadata(package, {name: b"bad"}, set())
    assert snapshot(package) == before
