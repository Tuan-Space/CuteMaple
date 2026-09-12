"""Bind model QA evidence to the bytes actually supplied to its renderer."""
from __future__ import annotations

import hashlib
from pathlib import Path


def resource_snapshot(model_directory: Path, frontend_directory: Path) -> dict[str, str]:
    result = {}
    for label, directory in (("assets/live2d/Maple", model_directory), ("web/dist", frontend_directory)):
        directory = directory.resolve(strict=True)
        files = sorted(path for path in directory.rglob("*") if path.is_file())
        if not files:
            raise ValueError(f"Empty renderer resource directory: {directory}")
        for path in files:
            if not path.resolve(strict=True).is_relative_to(directory):
                raise ValueError(f"Renderer resource escapes its directory: {path}")
            relative = label + "/" + path.relative_to(directory).as_posix()
            with path.open("rb") as stream:
                result[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def changed_resources(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
