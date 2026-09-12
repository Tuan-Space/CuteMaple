"""Verify authoring dependencies and vendored source without rewriting artwork."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor" / "image2live2d"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))


def verify() -> None:
    manifest = json.loads((VENDOR / "UPSTREAM.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = (VENDOR / entry["path"]).resolve()
        if not path.is_relative_to(VENDOR.resolve()) or not path.is_file():
            raise ValueError(f"Missing/unsafe vendored source: {entry['path']}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["vendored_sha256"]:
            raise ValueError(f"Vendored source hash mismatch: {entry['path']}")
    for module in ("cv2", "numpy", "PIL", "psd_tools", "pydantic", "prepare_layers", "build_rig", "diagnose_rig", "native_caff", "transplant_underlay"):
        importlib.import_module(module)
    from image2live2d.backends.live2d.cmo3 import CaffEntry, pack_caff, unpack_caff
    from image2live2d.irr.schema import Rig
    assert Path(sys.modules[Rig.__module__].__file__).resolve().is_relative_to(VENDOR.resolve())
    # Round-trip a harmless tiny container in memory, exercising actual serialization.
    payload = b"authoring setup check"
    packed = pack_caff([CaffEntry("check.txt", payload)])
    entries = unpack_caff(packed)
    assert entries[0].content == payload
    assert not any("moc3_emit" in name for name in sys.modules)
    print(f"Verified {len(manifest['files'])} vendored files and authoring imports; no model files changed.")


if __name__ == "__main__":
    verify()
