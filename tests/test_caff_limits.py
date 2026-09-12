"""Editable models remain bounded even when their compressed file is tiny."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/authoring"))
import native_caff
from image2live2d.backends.live2d.cmo3.caff import CaffEntry, pack_caff, COMPRESS_FAST, COMPRESS_RAW


@pytest.mark.parametrize("compression", [COMPRESS_FAST, COMPRESS_RAW])
def test_per_entry_expansion_is_limited(tmp_path, monkeypatch, compression):
    path = tmp_path / "large.cmo3"
    path.write_bytes(pack_caff([CaffEntry("main.xml", b"x" * 1025, compress=compression)], key=42))
    monkeypatch.setattr(native_caff, "MAX_ENTRY_BYTES", 1024)
    with pytest.raises(ValueError, match="oversized|size limit"):
        native_caff.read_project(path)


def test_many_small_entries_cannot_exceed_project_expansion_limit(tmp_path, monkeypatch):
    path = tmp_path / "many.cmo3"
    entries = [CaffEntry(f"{i}.xml", b"x" * 600, compress=COMPRESS_FAST) for i in range(2)]
    path.write_bytes(pack_caff(entries, key=42))
    monkeypatch.setattr(native_caff, "MAX_PROJECT_BYTES", 1000)
    with pytest.raises(ValueError, match="oversized|size limit"):
        native_caff.read_project(path)


def test_entry_at_the_limit_roundtrips_with_crc_validation(tmp_path, monkeypatch):
    path = tmp_path / "exact.cmo3"
    path.write_bytes(pack_caff([CaffEntry("main.xml", b"x" * 1024, compress=COMPRESS_FAST)], key=42))
    monkeypatch.setattr(native_caff, "MAX_ENTRY_BYTES", 1024)
    assert native_caff.read_project(path)[0].content == b"x" * 1024
