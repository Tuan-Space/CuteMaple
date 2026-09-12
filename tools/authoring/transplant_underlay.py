"""Update the repaired underlay inside a separate native Editor project copy.

Preserve every native object identity and all other atlas placements. This is
editable project/PNG serialization, never runtime MOC3 generation. An Editor
open/export verification remains required after the structural checks.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from native_caff import read_project
from image2live2d.backends.live2d.cmo3 import pack_caff

ROOT = Path(__file__).resolve().parents[2]
UNDERLAY = "costume_underlay"


def field(node, name):
    return next(n for n in node.iter() if n.get("xs.n") == name)


def parse(entries):
    files = {e.path: e.content for e in entries}
    root = ET.fromstring(files["main.xml"])
    ids = {n.get("xs.id"): n for n in root.iter() if n.get("xs.id")}
    return files, root, ids


def image(data):
    return Image.open(io.BytesIO(data)).convert("RGBA")


def png(pixels):
    buf = io.BytesIO()
    pixels.save(buf, format="PNG")
    return buf.getvalue()


def layers(root, ids):
    result = {}
    for node in root.iter("CLayer"):
        if node.get("xs.id"):
            resource = ids[field(node, "imageResource").get("xs.ref")]
            result[field(node, "name").text] = (resource, field(resource, "imageFileBuf").get("path"))
    return result


def meshes(root):
    return {field(n, "localName").text: n for n in root.iter("CArtMeshSource") if n.get("xs.id")}


def numbers(node):
    return np.fromstring(node.text, sep=" ")


def mesh_positions(mesh):
    return numbers(next(n for n in mesh if n.get("xs.n") == "positions")).reshape(-1, 2)


def affine_values(node):
    return np.array([[float(node.get("m00")), float(node.get("m01")), float(node.get("m02"))],
                     [float(node.get("m10")), float(node.get("m11")), float(node.get("m12"))]])


def inverse_points(points, affine):
    return (points - affine[:, 2]) @ np.linalg.inv(affine[:, :2]).T


def set_affine(node, tx, ty):
    node.attrib.update(m00="1.0", m01="0.0", m02=str(float(tx)),
                       m10="0.0", m11="1.0", m12=str(float(ty)))


def rect(points, padding=0):
    return [int(round(points[:, 0].min()))-padding, int(round(points[:, 1].min()))-padding,
            int(round(points[:, 0].max()))+padding, int(round(points[:, 1].max()))+padding]


def intersects(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def run(source, repaired, output):
    source, repaired, output = source.resolve(), repaired.resolve(), output.resolve()
    if output in (source, repaired) or output.exists():
        raise ValueError("Output must be a new, separate project path")
    original_entries, repaired_entries = read_project(source), read_project(repaired)
    files, root, ids = parse(original_entries)
    repaired_files, repaired_root, repaired_ids = parse(repaired_entries)
    old_layers, new_layers = layers(root, ids), layers(repaired_root, repaired_ids)
    native_meshes, repaired_meshes = meshes(root), meshes(repaired_root)
    if old_layers.keys() != new_layers.keys() or len(old_layers) != 29:
        raise ValueError("Layer inventory mismatch")
    for name in old_layers:
        if name != UNDERLAY and not np.array_equal(np.asarray(image(files[old_layers[name][1]])), np.asarray(image(repaired_files[new_layers[name][1]]))):
            raise ValueError(f"Unexpected changed layer: {name}")
    unchanged_meshes = {name: ET.tostring(mesh) for name, mesh in native_meshes.items() if name != UNDERLAY}
    immutable_objects = {n.get("xs.id"): ET.tostring(n) for n in root.iter()
                         if n.get("xs.id") and n.tag in {"CParameterGuid", "CParameter", "CWarpDeformerSource"}}
    atlas = next(n for n in root.iter("CTextureAtlas") if n.get("xs.id"))
    width, height = int(field(atlas, "width").text), int(field(atlas, "height").text)
    if (width, height) != (2048, 2048):
        raise ValueError("Expected the saved 2048 atlas")
    atlas_resource = ids[field(atlas, "cachedAtlasImage").get("xs.ref")]
    atlas_path = field(atlas_resource, "imageFileBuf").get("path")
    old_atlas = image(files[atlas_path])
    original_atlas = np.asarray(old_atlas).copy()
    model_images = {field(n, "guid").get("xs.ref"): n for n in root.iter("CModelImage")}
    image_entries = {field(model_images[field(n, "modelImageGuid").get("xs.ref")], "name").text: n
                     for n in atlas.iter("ModelImageEntry")}
    if image_entries.keys() != native_meshes.keys():
        raise ValueError("Not all meshes are assigned to the saved atlas")
    old_underlay, new_underlay = native_meshes[UNDERLAY], repaired_meshes[UNDERLAY]
    old_geometry = mesh_positions(old_underlay)
    new_geometry = mesh_positions(new_underlay)
    old_affine = affine_values(field(image_entries[UNDERLAY], "atlasLocalToCanvasTransform"))
    old_rectangle = rect(inverse_points(old_geometry, old_affine), padding=16)
    canvas_rectangle = rect(new_geometry)
    target_x, target_y = 264, 1320
    tx, ty = canvas_rectangle[0] - target_x, canvas_rectangle[1] - target_y
    new_affine = np.array([[1., 0., tx], [0., 1., ty]])
    new_rectangle = rect(inverse_points(new_geometry, new_affine), padding=16)
    for bounds in (old_rectangle, new_rectangle):
        if min(bounds[:2]) < 0 or bounds[2] > width or bounds[3] > height:
            raise ValueError("Atlas region is out of bounds")
    occupied = {}
    for name, mesh in native_meshes.items():
        affine = affine_values(field(image_entries[name], "atlasLocalToCanvasTransform"))
        expected = inverse_points(mesh_positions(mesh), affine) / [width, height]
        native_uv = numbers(field(mesh, "uvs")).reshape(-1, 2)
        if np.max(np.abs(native_uv - expected)) > 1e-6:
            raise ValueError(f"Native UV/atlas transform mismatch: {name}")
        if name != UNDERLAY:
            occupied[name] = rect(inverse_points(mesh_positions(mesh), affine), padding=16)
            if intersects(occupied[name], old_rectangle) or intersects(occupied[name], new_rectangle):
                raise ValueError(f"Replacement rectangle overlaps another mesh: {name}")
    if any(c.get("count") != "1" for mesh in (old_underlay, new_underlay) for c in mesh if c.get("xs.n") == "keyforms"):
        raise ValueError("Expected exactly one rest keyform for the underlay")
    for name in ("indices", "edge", "pointUid"):
        if not np.array_equal(numbers(field(old_underlay, name)), numbers(field(new_underlay, name))):
            raise ValueError(f"Underlay topology changed: {name}")
    for name in ("point", "positions"):
        old_arrays = [n for n in old_underlay.iter("float-array") if n.get("xs.n") == name]
        new_arrays = [n for n in new_underlay.iter("float-array") if n.get("xs.n") == name]
        if len(old_arrays) != len(new_arrays):
            raise ValueError("Underlay geometry layout mismatch")
        for old, new in zip(old_arrays, new_arrays):
            old.text, old.attrib["count"] = new.text, new.attrib["count"]
    new_uv = inverse_points(new_geometry, new_affine) / [width, height]
    field(old_underlay, "uvs").text = " ".join(f"{v:.10f}" for v in new_uv.flat)
    set_affine(field(image_entries[UNDERLAY], "atlasLocalToCanvasTransform"), tx, ty)
    material_transform = field(image_entries[UNDERLAY], "materialLocalToAtlasTransform")
    position = field(material_transform, "position")
    field(position, "x").text, field(position, "y").text = str(float(-tx)), str(float(-ty))
    field(material_transform, "eulerAngle").text = "0.0"
    extension = ids[next(n for n in old_underlay.iter("CTextureInputExtension") if n.get("xs.ref")).get("xs.ref")]
    region = ids[field(extension, "currentTextureInputData").get("xs.ref")]
    if region.tag != "CTextureInput_TextureAtlasRegion":
        raise ValueError("Underlay is not using the native atlas input")
    set_affine(field(region, "inputImageLocalToCanvasTransform"), tx, ty)
    resource, source_path = old_layers[UNDERLAY]
    replacement_bytes = repaired_files[new_layers[UNDERLAY][1]]
    repaired_image = image(replacement_bytes)
    new_atlas = old_atlas.copy()
    new_atlas.paste((0, 0, 0, 0), old_rectangle)
    new_atlas.paste((0, 0, 0, 0), new_rectangle)
    patch = repaired_image.crop(canvas_rectangle)
    new_atlas.paste(patch, (target_x, target_y))
    atlas_bytes = png(new_atlas)
    changes = {source_path: replacement_bytes, atlas_path: atlas_bytes}
    resource.set("imageFileBuf_size", str(len(replacement_bytes)))
    atlas_resource.set("imageFileBuf_size", str(len(atlas_bytes)))
    # The model-image resource and its cached image share the source resource;
    # refresh only its 16px editor thumbnail, retaining every existing reference.
    model_image = model_images[field(image_entries[UNDERLAY], "modelImageGuid").get("xs.ref")]
    icon = field(model_image, "icon16")
    icon_file = next(iter(icon.iter("file")), None)
    if icon_file is not None:
        changes[icon_file.get("path")] = png(repaired_image.resize((16, 16), Image.Resampling.LANCZOS))
    current_atlas = np.asarray(new_atlas)
    changed_mask = np.zeros((height, width), dtype=bool)
    for left, top, right, bottom in (old_rectangle, new_rectangle):
        changed_mask[top:bottom, left:right] = True
    if not np.array_equal(original_atlas[~changed_mask], current_atlas[~changed_mask]):
        raise ValueError("Unrelated atlas pixels changed")
    for name, bounds in occupied.items():
        left, top, right, bottom = bounds
        if not np.array_equal(original_atlas[top:bottom, left:right], current_atlas[top:bottom, left:right]):
            raise ValueError(f"Original atlas region changed: {name}")
        if ET.tostring(native_meshes[name]) != unchanged_meshes[name]:
            raise ValueError(f"Original mesh changed: {name}")
    for object_id, original in immutable_objects.items():
        if ET.tostring(ids[object_id]) != original:
            raise ValueError("Original parameter/deformer changed")
    if not np.array_equal(np.asarray(patch), current_atlas[target_y:target_y+patch.height, target_x:target_x+patch.width]):
        raise ValueError("New source pixels do not match the atlas")
    if not np.all((new_uv >= 0) & (new_uv <= 1)):
        raise ValueError("Replacement mesh UV is out of atlas bounds")
    # Preserve all version declarations from the real native saved project.
    prefix = files["main.xml"].split(b"<root", 1)[0]
    changes["main.xml"] = prefix + ET.tostring(root, encoding="utf-8")
    entries = [replace(entry, content=changes.get(entry.path, entry.content)) for entry in original_entries]
    result = pack_caff(entries, key=42)
    audit = {"status": "editable-project-updated-requires-native-editor-open-export-and-visual-QA",
             "source": str(source), "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
             "repaired": str(repaired), "repairedSha256": hashlib.sha256(repaired.read_bytes()).hexdigest(),
             "output": str(output), "outputSha256": hashlib.sha256(result).hexdigest(),
             "atlasSize": [width, height], "layers": 29, "preservedOtherMeshes": 28,
             "preservedParameterGuids": sum(n.tag == "CParameterGuid" for n in ids.values()),
             "preservedWarpDeformers": sum(n.tag == "CWarpDeformerSource" for n in ids.values()),
             "underlayCanvasBounds": canvas_rectangle, "oldAtlasRegionWithPadding": old_rectangle,
             "newAtlasRegionWithPadding": new_rectangle, "newAtlasToCanvasAffine": new_affine.tolist(),
             "newUvBounds": [new_uv.min(axis=0).tolist(), new_uv.max(axis=0).tolist()],
             "unchangedAtlasPixelsOutsideReplacementRegions": True, "otherAtlasRegionsPixelIdentical": True,
             "nativeMeshUvsMatchSavedAtlasTransforms": True, "replacementAtlasPixelsMatchV2Source": True,
             "changedArchiveEntries": sorted(changes), "noMoc3Generated": True}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(result)
    if {e.path: e.content for e in read_project(output)} != {e.path: e.content for e in entries}:
        raise ValueError("Saved project roundtrip mismatch")
    output.with_suffix(".atlas-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT/"assets/authoring/Maple.cmo3")
    parser.add_argument("--repaired", type=Path, default=ROOT/"assets/authoring/revisions/v2/Maple.cmo3")
    parser.add_argument("--output", type=Path, default=ROOT/"assets/authoring/revisions/v3/Maple.cmo3")
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.repaired, args.output), ensure_ascii=False, indent=2))
