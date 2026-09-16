"""Change only four parameter precision fields in an existing editable CMO3.

This does not create or patch MOC binaries. Export the result in Cubism Editor.
All other XML bytes and every embedded texture are preserved and verified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from native_caff import read_project
from image2live2d.backends.live2d.cmo3.caff import CaffEntry, pack_caff

PARAMETERS = {"ParamSwing", "ParamAngleZ", "ParamLegLA", "ParamLegRA"}
SOURCE = re.compile(rb"<CParameterSource\b[^>]*>.*?</CParameterSource>", re.S)
FIELDS = re.compile(rb'(<(?:i|f) xs\.n="(?:decimalPlaces|snapEpsilon)">)[^<]*(</(?:i|f)>)')


def patch_xml(data: bytes) -> tuple[bytes, list[str]]:
    found = []

    def replace(match):
        source = match[0]
        identifier = re.search(rb'<CParameterId\b[^>]*idstr="([^"]+)"', source)
        name = identifier[1].decode() if identifier else ""
        if name not in PARAMETERS:
            return source
        found.append(name)
        def precision(field):
            value = b"5" if b"decimalPlaces" in field[1] else b"0.00001"
            return field[1] + value + field[2]
        updated, count = FIELDS.subn(precision, source)
        if count != 2:
            raise ValueError(f"Expected two precision fields for {name}")
        return updated

    after = SOURCE.sub(replace, data)
    if len(found) != len(PARAMETERS) or set(found) != PARAMETERS:
        raise ValueError(f"Missing or duplicate parameters: {found}")
    # Replacing only those fields must leave the rest byte-for-byte identical.
    if FIELDS.sub(rb"\1\2", data) != FIELDS.sub(rb"\1\2", after):
        raise ValueError("Unexpected model data change")
    return after, sorted(found)


def refine(project: Path) -> dict:
    entries = read_project(project)
    main = next(e for e in entries if e.path == "main.xml")
    after, names = patch_xml(main.content)
    before_hash = hashlib.sha256(project.read_bytes()).hexdigest()
    content_hash = hashlib.sha256(FIELDS.sub(rb"\1\2", after)).hexdigest()
    if after != main.content:
        data = pack_caff([CaffEntry(e.path, after if e.path == "main.xml" else e.content,
                                   tag=e.tag, obfuscated=e.obfuscated, compress=e.compress)
                          for e in entries], key=42)
        temporary = project.with_suffix(".cmo3.tmp")
        temporary.write_bytes(data)
        verified = read_project(temporary)
        if [(e.path, e.content) for e in verified] != [
                (e.path, after if e.path == "main.xml" else e.content) for e in entries]:
            raise ValueError("CMO roundtrip changed embedded data")
        temporary.replace(project)
    return {"project": project.as_posix(), "parameters": names, "snapEpsilon": 0.00001,
            "beforeSha256": before_hash, "afterSha256": hashlib.sha256(project.read_bytes()).hexdigest(),
            "nonPrecisionXmlSha256": content_hash, "embeddedTexturesUnchanged": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps([refine(p) for p in args.projects], ensure_ascii=False, indent=2))
