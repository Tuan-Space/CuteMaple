"""Read-only evidence checks shared by native model QA and release validation."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path


def digest(path: Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atlas_coordinate_mapping_audit(actual, expected) -> dict:
    """Verify atlas coordinates while separately reporting low-alpha edge colors.

    Editor PNG export can round-trip 8-bit premultiplied colors and replace very
    faint fringe colors. Neither exception may move alpha or alter opaque art.
    This check certifies coordinate correspondence, never visual acceptance.
    """
    import numpy as np
    result = dict(actualSize=list(actual.size), sourceSize=list(expected.size),
                  sizesIdentical=actual.size == expected.size, rawPixelsIdentical=False,
                  alphaIdentical=False, opaqueRGBIdentical=False, premulAbove8Identical=False,
                  coordinateMappingVerified=False, lowAlphaThreshold=8,
                  premultiplication='(rgb * alpha + 127) // 255', counts={},
                  lowAlphaMaxContribution8Bit=None, lowAlphaMaxContributionNormalized=None,
                  visualReview='pending-human-review')
    if not result['sizesIdentical']:
        return result
    left, right = np.asarray(actual.convert('RGBA')), np.asarray(expected.convert('RGBA'))
    alpha = right[:, :, 3]
    same_alpha = left[:, :, 3] == alpha
    same_rgb = np.all(left[:, :, :3] == right[:, :, :3], axis=2)
    opaque, visible = alpha == 255, alpha > 0
    above, low = alpha > 8, (alpha > 0) & (alpha <= 8)
    # Integer arithmetic is deliberate: a general RGB epsilon would accept
    # changed colors in the body of the drawing rather than export rounding.
    premul_left = (left[:, :, :3].astype(np.uint16)*left[:, :, 3:4] + 127)//255
    premul_right = (right[:, :, :3].astype(np.uint16)*right[:, :, 3:4] + 127)//255
    difference = np.abs(premul_left.astype(np.int16)-premul_right.astype(np.int16))
    changed = np.any(difference != 0, axis=2)
    maximum = int(difference[low].max()) if np.any(low) else 0
    result.update(rawPixelsIdentical=bool(np.array_equal(left, right)),
                  alphaIdentical=bool(np.all(same_alpha)),
                  opaqueRGBIdentical=bool(np.all(same_rgb[opaque])),
                  premulAbove8Identical=bool(not np.any(changed[above])),
                  lowAlphaMaxContribution8Bit=maximum,
                  lowAlphaMaxContributionNormalized=maximum/255,
                  counts=dict(pixels=int(alpha.size), visiblePixels=int(visible.sum()),
                              opaquePixels=int(opaque.sum()), alphaChangedPixels=int((~same_alpha).sum()),
                              opaqueRGBChangedPixels=int((opaque & ~same_rgb).sum()),
                              premulAbove8ChangedPixels=int((above & changed).sum()),
                              premulAbove8ChangedChannels=int(np.count_nonzero(difference[above])),
                              lowAlphaPixels=int(low.sum()), lowAlphaRGBChangedPixels=int((low & ~same_rgb).sum()),
                              lowAlphaPremulChangedPixels=int((low & changed).sum()),
                              lowAlphaPremulChangedChannels=int(np.count_nonzero(difference[low]))))
    result['coordinateMappingVerified'] = bool(
        result['alphaIdentical'] and result['opaqueRGBIdentical'] and result['premulAbove8Identical']
        and result['counts']['opaquePixels'] > 0 and maximum <= 8)
    return result


def audit_atlas_files(actual_path: Path, source_path: Path, actual_sha256: str, source_sha256: str) -> dict:
    """Decode exactly the bytes whose hashes are recorded, avoiding a reopen race."""
    from PIL import Image
    actual_path, source_path = Path(actual_path).resolve(strict=True), Path(source_path).resolve(strict=True)
    actual_bytes, source_bytes = actual_path.read_bytes(), source_path.read_bytes()
    actual_hash, source_hash = [hashlib.sha256(data).hexdigest() for data in (actual_bytes, source_bytes)]
    if actual_hash != actual_sha256 or source_hash != source_sha256:
        raise ValueError('Atlas image SHA256 differs from its frozen coordinate/model reference')
    with Image.open(io.BytesIO(actual_bytes)) as actual, Image.open(io.BytesIO(source_bytes)) as expected:
        result = atlas_coordinate_mapping_audit(actual, expected)
    return dict(result, actualPath=str(actual_path), sourcePath=str(source_path),
                actualSha256=actual_hash, sourceSha256=source_hash)


def recheck_input_hashes(hashes: dict[str, str]) -> dict[str, str]:
    """Missing or altered evidence is a failure, including archived source files."""
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('Missing frozen evidence input hashes')
    actual = {name: digest(Path(name)) for name in hashes}
    if actual != hashes:
        raise ValueError('Frozen native UV evidence input SHA256 changed')
    return actual


def verify_native_uv_evidence(path: Path, moc_path: Path, atlas_path: Path, moc_sha256: str,
                              *, expected_mesh_count: int = 134,
                              expected_drawable_ids: set[str] | None = None) -> dict:
    """Require the entire revision's native mesh set and frozen source bytes."""
    if expected_mesh_count not in (134, 141) and not (
            143 <= expected_mesh_count <= 200 and expected_drawable_ids is not None):
        raise ValueError('Unsupported authored model mesh count')
    if expected_drawable_ids is not None and (len(expected_drawable_ids) != expected_mesh_count
            or any(not isinstance(name, str) or not name for name in expected_drawable_ids)):
        raise ValueError('Invalid authored drawable identity')
    path, moc_path, atlas_path = [Path(value).resolve(strict=True) for value in (path, moc_path, atlas_path)]
    data = path.read_bytes()
    report = json.loads(data)
    if not isinstance(report, dict):
        raise ValueError('Native UV audit must be an object')
    start, end = report.get('sourceHashesAtStart'), report.get('sourceHashesAtEnd')
    if (report.get('tool') != 'native-uv-pairing-audit' or report.get('schemaVersion') != 1
            or report.get('passed') is not True or report.get('errors') != []
            or report.get('sourcesUnchanged') is not True or not start or start != end):
        raise ValueError('Native UV audit is failed, missing, or not frozen')
    recheck_input_hashes(start)
    meshes = report.get('meshes', [])
    if not isinstance(meshes, list) or not all(isinstance(mesh, dict) for mesh in meshes):
        raise ValueError('Native UV audit mesh records are invalid')
    ids = [mesh.get('id') for mesh in meshes]
    tolerance = report.get('pixelTolerance')
    if (report.get('meshCount') != expected_mesh_count or report.get('matchedMeshCount') != expected_mesh_count or len(meshes) != expected_mesh_count
            or not all(isinstance(name, str) and name for name in ids) or len(set(ids)) != expected_mesh_count
            or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or not 0 < tolerance <= .001):
        raise ValueError(f'Native UV audit must verify exactly {expected_mesh_count} unique meshes at <= .001 atlas pixel tolerance')
    if expected_drawable_ids is not None and set(ids) != expected_drawable_ids:
        raise ValueError('Native UV drawable identity differs from the authored model')
    for mesh in meshes:
        error = mesh.get('maximumAtlasPixelError')
        if (mesh.get('passed') is not True or mesh.get('errors') != []
                or mesh.get('sameTriangleTopologyIgnoringOrder') is not True
                or not isinstance(error, (int, float)) or not math.isfinite(error) or not 0 <= error <= tolerance):
            raise ValueError(f'Unverified native UV mesh: {mesh.get("id")}')
    native_moc = Path(report['nativeMoc']).resolve(strict=True)
    if start.get(str(native_moc)) != moc_sha256 or digest(moc_path) != moc_sha256:
        raise ValueError('Native UV audit belongs to a different loaded MOC3')
    atlas_bytes = atlas_path.read_bytes()
    atlas_hash = hashlib.sha256(atlas_bytes).hexdigest()
    if start.get(str(atlas_path)) != atlas_hash:
        raise ValueError('Native UV audit does not bind this exact atlas placement audit')
    atlas = json.loads(atlas_bytes)
    required_hashes = [atlas['sourceSha256'], atlas['outputSha256'], *[page['sha256'] for page in atlas['atlases']]]
    if not all(value in start.values() for value in required_hashes):
        raise ValueError('Native UV audit omits an authored source, packed project, or atlas image hash')
    geometries = []
    for name in start:
        if Path(name).suffix.lower() == '.json':
            document = json.loads(Path(name).read_bytes())
            if document.get('tool') == 'official-cubism-native-geometry-audit':
                geometries.append(document)
    if len(geometries) != 1:
        raise ValueError('Native UV audit must bind one official Core geometry audit')
    geometry = geometries[0]
    if (geometry.get('passed') is not True or geometry.get('errors') != []
            or geometry.get('sourcesUnchanged') is not True or geometry.get('sourceSha256') != moc_sha256
            or not geometry.get('sourceHashesAtStart')
            or geometry['sourceHashesAtStart'] != geometry.get('sourceHashesAtEnd')
            or any(start.get(name) != value for name, value in geometry['sourceHashesAtStart'].items())
            or len(geometry.get('drawables', [])) != expected_mesh_count
            or {item.get('id') for item in geometry.get('drawables', [])} != set(ids)):
        raise ValueError('Native UV geometry source, mesh set, or official Core input is unverified')
    hashes = dict(start, **{str(path): hashlib.sha256(data).hexdigest()})
    recheck_input_hashes(hashes)
    return dict(verified=True, path=str(path), sha256=hashes[str(path)], tool=report['tool'],
                mocSha256=moc_sha256, atlasAuditPath=str(atlas_path), atlasAuditSha256=atlas_hash,
                meshCount=expected_mesh_count, matchedMeshCount=expected_mesh_count,
                pixelTolerance=tolerance, maximumAtlasPixelError=max(mesh['maximumAtlasPixelError'] for mesh in meshes),
                sourcesUnchanged=True, inputHashesAtStart=hashes, inputHashesAtEnd=dict(hashes))
