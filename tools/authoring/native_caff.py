"""Read native Editor CAFF, including ZIP streams without a central directory.

Reads project/texture data only. No executable model compiler is involved.
"""
from __future__ import annotations

from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
from image2live2d.backends.live2d.cmo3.caff import CaffEntry, _Reader

# The final v5.1 editable rig has 322 MiB of XML. Retain finite per-entry and
# project-wide expansion limits, including the existing CRC/length checks.
MAX_ENTRY_BYTES = 384 * 1024 * 1024
MAX_PROJECT_BYTES = 512 * 1024 * 1024


def read_project(path: Path) -> list[CaffEntry]:
    data = Path(path).read_bytes()
    if data[:4] != b"CAFF" or data[-2:] != b"bc":
        raise ValueError("Invalid CAFF signature or guard")
    reader = _Reader(data)
    reader.pos = 14
    key = reader.int32()
    key = key if key < 2**31 else key - 2**32
    reader.pos = 54
    heads = []
    count = reader.int32(key)
    if not 1 <= count <= 10000:
        raise ValueError("Unreasonable CAFF entry count")
    for _ in range(count):
        name, tag = reader.string(key), reader.string(key)
        start, size = reader.int64(key), reader.int32(key)
        obfuscated, compression = bool(reader.byte(key)), reader.byte(key)
        reader.skip(8)
        if start + size > len(data) - 2:
            raise ValueError("CAFF entry exceeds container bounds")
        heads.append((name, tag, start, size, obfuscated, compression))
    entries = []
    expanded_bytes = 0
    for name, tag, start, size, obfuscated, compression in heads:
        limit = min(MAX_ENTRY_BYTES, MAX_PROJECT_BYTES - expanded_bytes)
        if limit <= 0:
            raise ValueError("CAFF project exceeds expanded size limit")
        reader.pos = start
        stored = reader.raw(size, key if obfuscated else 0)
        content = stored
        if compression != 16:
            if len(stored) < 30:
                raise ValueError("Truncated ZIP local header")
            signature, version, flags, method, _, _, crc, compressed, length, namesize, extrasize = struct.unpack("<IHHHHHIIIHH", stored[:30])
            if signature != 0x04034B50 or method != 8 or flags & 1:
                raise ValueError("Unsupported compressed CAFF entry")
            offset = 30 + namesize + extrasize
            inflater = zlib.decompressobj(-15)
            content = inflater.decompress(stored[offset:], limit + 1)
            if len(content) > limit or not inflater.eof:
                raise ValueError("Truncated/oversized compressed CAFF entry")
            if flags & 8:
                tail = inflater.unused_data
                if tail[:4] != b"PK\x07\x08" or len(tail) < 16:
                    raise ValueError("Missing ZIP data descriptor")
                _, crc, compressed, length = struct.unpack("<IIII", tail[:16])
            if crc != zlib.crc32(content) or length != len(content) or compressed != len(stored[offset:]) - len(inflater.unused_data):
                raise ValueError("ZIP CRC/length mismatch")
        if len(content) > limit:
            raise ValueError("CAFF entry exceeds expanded size limit")
        expanded_bytes += len(content)
        entries.append(CaffEntry(name, content, tag=tag, obfuscated=obfuscated, compress=compression))
    return entries
