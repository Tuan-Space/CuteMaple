"""Compare an official Core audit with a packed CMO3 and its exact atlas bytes.

This reads files and writes a new audit only. It neither installs a model nor
changes MOC3/CMO3/atlas data. Native V is inverted to the Editor's PNG convention.
Every native vertex must match one source UV, independent of vertex ordering;
triangle connectivity and texture pages must match as well.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

try:
    from .native_caff import read_project
except ImportError:
    from native_caff import read_project


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _triangles(values, count, label):
    array = np.asarray(values)
    if array.size == 0 or array.size % 3 or not np.issubdtype(array.dtype, np.integer):
        raise ValueError(f'Invalid {label} triangle indices')
    array = array.reshape(-1, 3)
    if np.any(array < 0) or np.any(array >= count):
        raise ValueError(f'Out-of-range {label} triangle indices')
    return array


def _triangle_multiset(indices):
    triangles = np.sort(indices, axis=1)
    return sorted(map(tuple, triangles.tolist()))


def compare_mesh_uvs(native_mesh, source_uvs, source_indices, region, atlas_size, *, pixel_tolerance=.001):
    """Return explicit failures for malformed geometry as well as mismatches."""
    result = {'id': native_mesh.get('id') if isinstance(native_mesh, dict) else None,
              'passed': False, 'errors': [], 'maximumAtlasPixelError': None, 'vertexMapping': None}
    try:
        size = np.asarray(atlas_size, dtype=float)
        if size.shape != (2,) or not np.isfinite(size).all() or np.any(size <= 0):
            raise ValueError('Invalid atlas dimensions')
        if not math.isfinite(pixel_tolerance) or pixel_tolerance <= 0:
            raise ValueError('Invalid pixel tolerance')
        source = np.asarray(source_uvs, dtype=float)
        actual = np.asarray(native_mesh['textureUvs'], dtype=float)
        positions = np.asarray(native_mesh['positions'], dtype=float)
        if source.ndim != 2 or source.shape[1] != 2 or len(source) < 3:
            raise ValueError('Invalid packed mesh UV dimensions')
        if actual.ndim != 1 or len(actual) % 2 or len(actual) != source.size:
            raise ValueError('Native/packed vertex count mismatch')
        if positions.shape != actual.shape or not np.isfinite(positions).all() or native_mesh.get('finiteVertices') is not True:
            raise ValueError('Invalid/nonfinite native vertex geometry')
        actual = actual.reshape(-1, 2).copy()
        if native_mesh.get('vertexCount') != len(actual):
            raise ValueError('Native vertex count does not match its arrays')
        if not np.isfinite(source).all() or not np.isfinite(actual).all():
            raise ValueError('Nonfinite mesh UVs')
        if np.any(source < -1e-6) or np.any(source > 1+1e-6) or np.any(actual < -1e-6) or np.any(actual > 1+1e-6):
            raise ValueError('Mesh UVs outside 0..1')
        if (not isinstance(native_mesh.get('textureIndex'), int) or isinstance(native_mesh['textureIndex'], bool)
                or native_mesh['textureIndex'] != region['page']):
            raise ValueError('Native texture page differs from packed atlas page')
        if len(np.unique(source, axis=0)) != len(source) or len(np.unique(actual, axis=0)) != len(actual):
            raise ValueError('Duplicate mesh UV vertices make matching ambiguous')
        actual[:, 1] = 1-actual[:, 1]
        source_triangles = _triangles(source_indices, len(source), 'packed')
        native_triangles = _triangles(native_mesh['indices'], len(actual), 'native')
        # Bounded blocks avoid an NxN allocation on a densely triangulated export.
        mapping, distances = [], []
        for first in range(0, len(actual), 256):
            delta = (actual[first:first+256, None, :]-source[None, :, :])*size
            squared = np.einsum('ijk,ijk->ij', delta, delta)
            indices = squared.argmin(axis=1)
            mapping.extend(indices.tolist())
            distances.extend(np.sqrt(squared[np.arange(len(indices)), indices]).tolist())
        result.update(nativeVertexCount=len(actual), sourceVertexCount=len(source), vertexMapping=mapping,
                      maximumAtlasPixelError=max(distances), nativeTextureIndex=native_mesh['textureIndex'],
                      expectedPage=region['page'])
        if len(set(mapping)) != len(source):
            result['errors'].append('Native UVs do not map bijectively to the packed mesh')
        if max(distances) > pixel_tolerance:
            result['errors'].append(f'Native UV differs from packed atlas by {max(distances):.9g} pixels')
        same_topology = _triangle_multiset(np.asarray(mapping)[native_triangles]) == _triangle_multiset(source_triangles)
        result['sameTriangleTopologyIgnoringOrder'] = same_topology
        if not same_topology:
            result['errors'].append('Native/packed triangle topology differs')
        result['passed'] = not result['errors']
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as error:
        result['errors'].append(str(error))
    return result


def read_packed_meshes(path):
    entries = read_project(path)
    xml = [entry.content for entry in entries if entry.path == 'main.xml']
    if len(xml) != 1:
        raise ValueError('Packed CMO3 must contain exactly one main.xml')
    meshes = {}
    for _, node in ET.iterparse(io.BytesIO(xml[0]), events=['end']):
        if node.tag != 'CArtMeshSource' or not node.get('xs.id'):
            continue
        names = [child.text for child in node.iter() if child.get('xs.n') == 'localName']
        if len(names) != 1 or not names[0] or names[0] in meshes:
            raise ValueError('Missing or duplicate packed drawable ID')
        fields = {child.get('xs.n'): child for child in node}
        uv = np.fromstring(fields['uvs'].text or '', sep=' ')
        indices = np.fromstring(fields['indices'].text or '', sep=' ', dtype=int)
        if len(uv) != int(fields['uvs'].get('count', '-1')) or len(uv) % 2:
            raise ValueError(f'Invalid packed UV count: {names[0]}')
        if len(indices) != int(fields['indices'].get('count', '-1')) or len(indices) % 3:
            raise ValueError(f'Invalid packed index count: {names[0]}')
        meshes[names[0]] = (uv.reshape(-1, 2), indices.reshape(-1, 3))
        node.clear()
    if not meshes:
        raise ValueError('Packed CMO3 contains no meshes')
    return meshes


def verify(native_audit, atlas_audit, *, packed_cmo3=None, source_cmo3=None):
    report = {'schemaVersion': 1, 'tool': 'native-uv-pairing-audit', 'passed': False, 'errors': [],
              'sourceHashesAtStart': {}, 'sourceHashesAtEnd': {}, 'sourcesUnchanged': False, 'meshes': [],
              'pixelTolerance': .001,
              'validationScope': 'Native UV/topology/page pairing with exact packed PNG bytes; animation and visual quality are not certified.'}
    def track(path, expected=None):
        path = Path(path).resolve(strict=True)
        actual = digest(path)
        previous = report['sourceHashesAtStart'].get(str(path))
        if previous is not None and previous != actual:
            raise ValueError(f'Input changed while reading: {path}')
        report['sourceHashesAtStart'][str(path)] = actual
        if expected is not None and actual != expected:
            raise ValueError(f'Input SHA256 differs from its bound audit: {path}')
        return path
    def referenced(value, parent):
        path = Path(value)
        return path if path.is_absolute() else parent/path
    try:
        native_path, atlas_path = track(native_audit), track(atlas_audit)
        native = json.loads(native_path.read_text(encoding='utf-8-sig'))
        audit = json.loads(atlas_path.read_text(encoding='utf-8-sig'))
        if (native.get('tool') != 'official-cubism-native-geometry-audit' or native.get('schemaVersion') != 1
                or native.get('passed') is not True or native.get('sourcesUnchanged') is not True
                or native.get('errors') or not native.get('sourceHashesAtStart')
                or native['sourceHashesAtStart'] != native.get('sourceHashesAtEnd')):
            raise ValueError('Native geometry audit is missing, failed, or not frozen')
        for name, expected in native['sourceHashesAtStart'].items():
            track(referenced(name, native_path.parent), expected)
        moc_path = track(referenced(native['source'], native_path.parent), native['sourceSha256'])
        report['nativeMoc'] = str(moc_path)
        packed = track(packed_cmo3 or referenced(audit['output'], atlas_path.parent), audit['outputSha256'])
        track(source_cmo3 or referenced(audit['source'], atlas_path.parent), audit['sourceSha256'])
        size = audit['atlasSize']
        if (not isinstance(size, list) or len(size) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size)):
            raise ValueError('Invalid packed atlas dimensions')
        report['atlasSize'] = size
        atlases = audit['atlases']
        if not isinstance(atlases, list) or not atlases or audit.get('atlasCount') != len(atlases):
            raise ValueError('Missing or inconsistent packed atlas pages')
        for page in atlases:
            png = track(referenced(page['path'], atlas_path.parent), page['sha256'])
            with Image.open(png) as image:
                if list(image.size) != size:
                    raise ValueError(f'Actual atlas dimensions differ from its audit: {png}')
        regions = {}
        for region in audit['regions']:
            if (not isinstance(region['page'], int) or isinstance(region['page'], bool)
                    or not 0 <= region['page'] < len(atlases)):
                raise ValueError('Invalid packed atlas region page')
            for name in region['drawables']:
                if not isinstance(name, str) or not name or name in regions:
                    raise ValueError('Missing or duplicate atlas region drawable ID')
                regions[name] = region
        meshes = read_packed_meshes(packed)
        drawables = native['drawables']
        names = [item['id'] for item in drawables]
        if not names or not all(isinstance(n, str) and n for n in names) or len(names) != len(set(names)):
            raise ValueError('Missing or duplicate native drawable ID')
        for label, ids in [('native', set(names)), ('atlas region', set(regions))]:
            missing, extra = set(meshes)-ids, ids-set(meshes)
            if missing or extra:
                report['errors'].append(f'{label} mesh IDs differ: missing={sorted(missing)}, extra={sorted(extra)}')
        for item in drawables:
            name = item['id']
            if name not in meshes or name not in regions:
                report['meshes'].append({'id': name, 'passed': False, 'errors': ['Missing paired packed mesh or atlas region']})
                continue
            result = compare_mesh_uvs(item, *meshes[name], regions[name], size)
            report['meshes'].append(result)
            if not result['passed']:
                report['errors'].append(f'Native mesh pairing failed: {name}: {"; ".join(result["errors"])}')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, ET.ParseError) as error:
        report['errors'].append(str(error))
    finally:
        for name, expected in report['sourceHashesAtStart'].items():
            try:
                actual = digest(name)
                report['sourceHashesAtEnd'][name] = actual
                if actual != expected:
                    report['errors'].append(f'Input changed during UV audit: {name}')
            except OSError as error:
                report['errors'].append(f'Cannot recheck audit input {name}: {error}')
        report['sourcesUnchanged'] = bool(report['sourceHashesAtStart']) and report['sourceHashesAtStart'] == report['sourceHashesAtEnd']
    report['meshCount'] = len(report['meshes'])
    report['matchedMeshCount'] = sum(item['passed'] for item in report['meshes'])
    report['maximumAtlasPixelError'] = max((item.get('maximumAtlasPixelError') or 0 for item in report['meshes']), default=None)
    report['passed'] = report['sourcesUnchanged'] and bool(report['meshes']) and not report['errors']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-audit', required=True, type=Path)
    parser.add_argument('--atlas-audit', required=True, type=Path)
    parser.add_argument('--packed-cmo3', type=Path, help='Relocated packed CMO3, still required to match the atlas audit SHA256')
    parser.add_argument('--source-cmo3', type=Path, help='Archived original CMO3, still required to match the atlas audit source SHA256')
    parser.add_argument('--output', required=True, type=Path)
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new output path to preserve earlier audit evidence')
    report = verify(options.native_audit, options.atlas_audit, packed_cmo3=options.packed_cmo3, source_cmo3=options.source_cmo3)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with options.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({key: report[key] for key in ('passed', 'meshCount', 'matchedMeshCount', 'maximumAtlasPixelError', 'errors')}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
