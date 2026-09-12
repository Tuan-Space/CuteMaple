"""Commit validated release metadata together, restoring originals on failure."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid
import warnings

ALLOWED = {"RELEASE-VERIFICATION.json", "SOURCE-MODEL-QA.json", "NATIVE-UV-QA.json",
           "NORMAL-DESKTOP-QA.json", "PACKAGED-RUNTIME-QA.json", "PACKAGED-RUNTIME.png",
           "READ-ME.txt", "BUILD-STATUS.json", "SHA256.txt", "DESKTOP-EVIDENCE", "CLEANUP-EVIDENCE", "CANDIDATE-ARCHIVE",
           "CANDIDATE-NOT-FINAL.txt"}


def commit_metadata(package: Path, files: dict[str, bytes], remove: set[str]) -> None:
    """Touch only explicit metadata; no executable/assets and no private temp ACL."""
    package = package.resolve(strict=True)
    if not package.is_dir() or set(files).intersection(remove):
        raise ValueError("Invalid metadata transaction")
    destinations = {}
    for name in set(files) | remove:
        relative = Path(name)
        destination = (package / relative).resolve()
        if (relative.is_absolute() or not relative.parts or relative.parts[0] not in ALLOWED
                or ".." in relative.parts or not destination.is_relative_to(package) or destination == package):
            raise ValueError("Metadata path escapes allowed release metadata")
        current = package
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Metadata transaction cannot follow symlinks")
        if destination.exists() and not destination.is_file():
            raise ValueError("Metadata target is not a file")
        if name in files and not isinstance(files[name], bytes):
            raise TypeError("Metadata payloads must be exact bytes")
        destinations[name] = destination
    staging = package.parent / (".finalize-" + uuid.uuid4().hex)
    staging.mkdir()  # Atomic new ownership; inherits ordinary parent ACL on Windows.
    backup, pending = staging / "backup", staging / "pending"
    backed_up, installed, created_parents = [], [], []
    preserve_backup = False
    committed = False
    try:
        for name, content in files.items():
            target = pending / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        try:
            for name in sorted(destinations):
                destination = destinations[name]
                if destination.exists():
                    original = backup / name
                    original.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, original)
                    backed_up.append(name)
            # Publish verification content before the status that points to it;
            # the complete checksum inventory is the final metadata write.
            order = lambda name: (0 if name == "RELEASE-VERIFICATION.json" else 2 if name == "BUILD-STATUS.json"
                                  else 3 if name == "SHA256.txt" else 1, name)
            for name in sorted(files, key=order):
                destination = destinations[name]
                missing = []
                current = destination.parent
                while current != package and not current.exists():
                    missing.append(current)
                    current = current.parent
                for directory in reversed(missing):
                    directory.mkdir()
                    created_parents.append(directory)
                os.replace(pending / name, destination)
                installed.append(name)
            committed = True
        except BaseException:
            try:
                for name in reversed(installed):
                    destinations[name].unlink()
                for name in reversed(backed_up):
                    os.replace(backup / name, destinations[name])
                for directory in reversed(created_parents):
                    directory.rmdir()  # Never recursively remove a package directory.
            except BaseException as rollback_error:
                preserve_backup = True
                raise RuntimeError(f"Metadata rollback could not finish; retained originals at {backup}") from rollback_error
            raise
    finally:
        if not preserve_backup:
            # The random directory was atomically created by this call and its
            # resolved absolute parent must still be the intended package parent.
            if staging.resolve().parent != package.parent:
                raise RuntimeError("Owned staging directory moved; refusing cleanup")
            try:
                shutil.rmtree(staging)
            except OSError:
                if not committed:
                    raise
                warnings.warn(f"Metadata committed; temporary original copies could not be removed: {staging}", RuntimeWarning)
