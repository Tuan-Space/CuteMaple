"""Native-to-packed UV checks reject mismatches independently of vertex order."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from tools.authoring import verify_native_uv as audit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = np.array([[.2, .3], [.4, .3], [.2, .6], [.4, .6]])
TRIANGLES = np.array([[0, 1, 2], [1, 3, 2]])


def native_quad():
    permutation = np.array([3, 1, 0, 2])
    inverse = np.argsort(permutation)
    uv = SOURCE[permutation].copy()
    uv[:, 1] = 1-uv[:, 1]
    return {'id': 'mesh', 'vertexCount': 4, 'positions': [0., 0., 1., 0., 0., 1., 1., 1.],
            'textureUvs': uv.flatten().tolist(), 'indices': inverse[TRIANGLES].flatten().tolist(),
            'textureIndex': 0, 'finiteVertices': True, 'drawOrder': 80, 'renderOrder': 3}


def compare(mesh, size=(2048, 4096)):
    return audit.compare_mesh_uvs(mesh, SOURCE, TRIANGLES, {'page': 0}, size)


def test_vertex_reordering_and_native_v_inversion_preserve_topology():
    result = compare(native_quad())
    assert result['passed']
    assert result['vertexMapping'] == [3, 1, 0, 2]
    assert result['sameTriangleTopologyIgnoringOrder']
    assert result['maximumAtlasPixelError'] < 1e-9


def test_actual_non_square_atlas_dimensions_control_pixel_tolerance():
    mesh = native_quad()
    mesh['textureUvs'][0] += .0008/2048
    assert compare(mesh, (2048, 4096))['passed']
    assert not compare(mesh, (4096, 2048))['passed']


@pytest.mark.parametrize('damage,match', [
    (lambda m: m['textureUvs'].pop(), 'count mismatch'),
    (lambda m: m['positions'].__setitem__(0, float('nan')), 'nonfinite'),
    (lambda m: m['textureUvs'].__setitem__(0, float('nan')), 'Nonfinite'),
    (lambda m: m['textureUvs'].__setitem__(0, 1.1), 'outside'),
    (lambda m: m.update(textureIndex=1), 'page'),
    (lambda m: m['indices'].__setitem__(0, 99), 'triangle indices'),
    (lambda m: m['textureUvs'].__setitem__(slice(0, 2), m['textureUvs'][2:4]), 'Duplicate'),
    (lambda m: m['textureUvs'].__setitem__(0, .45), 'differs'),
    (lambda m: m.update(indices=[0, 1, 2, 0, 1, 3]), 'topology'),
])
def test_malformed_or_mispaired_native_data_fails_instead_of_throwing(damage, match):
    mesh = native_quad()
    damage(mesh)
    result = compare(mesh)
    assert not result['passed']
    assert any(match.lower() in error.lower() for error in result['errors'])


def _files(tmp_path, monkeypatch):
    moc, packed, source, atlas = [tmp_path/name for name in ('model.moc3', 'packed.cmo3', 'source.cmo3', 'atlas.png')]
    moc.write_bytes(b'unit fixture, not an official MOC')
    packed.write_bytes(b'unit packed fixture')
    source.write_bytes(b'unit source fixture')
    Image.new('RGBA', (16, 32), (100, 100, 100, 255)).save(atlas)
    native = {'tool': 'official-cubism-native-geometry-audit', 'schemaVersion': 1, 'passed': True,
              'sourcesUnchanged': True, 'errors': [], 'source': str(moc), 'sourceSha256': audit.digest(moc),
              'sourceHashesAtStart': {str(moc): audit.digest(moc)}, 'sourceHashesAtEnd': {str(moc): audit.digest(moc)},
              'drawables': [native_quad()]}
    packed_audit = {'source': str(source), 'sourceSha256': audit.digest(source), 'output': str(packed),
                    'outputSha256': audit.digest(packed), 'atlasSize': [16, 32], 'atlasCount': 1,
                    'atlases': [{'path': str(atlas), 'sha256': audit.digest(atlas)}],
                    'regions': [{'drawables': ['mesh'], 'page': 0}]}
    native_path, atlas_path = tmp_path/'native.json', tmp_path/'atlas.json'
    native_path.write_text(json.dumps(native))
    atlas_path.write_text(json.dumps(packed_audit))
    monkeypatch.setattr(audit, 'read_packed_meshes', lambda _: {'mesh': (SOURCE, TRIANGLES)})
    return native_path, atlas_path, native, atlas


def test_complete_pair_binds_native_moc_project_and_atlas_inputs(tmp_path, monkeypatch):
    native_path, atlas_path, _, _ = _files(tmp_path, monkeypatch)
    report = audit.verify(native_path, atlas_path)
    assert report['passed'] and report['matchedMeshCount'] == 1
    assert len(report['sourceHashesAtStart']) == 6
    assert report['sourceHashesAtStart'] == report['sourceHashesAtEnd']


def test_archived_source_can_be_relocated_but_must_match_original_hash(tmp_path, monkeypatch):
    native_path, atlas_path, _, _ = _files(tmp_path, monkeypatch)
    archived = tmp_path/'frozen-source.cmo3'
    (tmp_path/'source.cmo3').rename(archived)
    report = audit.verify(native_path, atlas_path, source_cmo3=archived)
    assert report['passed']
    assert str(archived) in report['sourceHashesAtStart']
    archived.write_bytes(b'wrong source revision')
    report = audit.verify(native_path, atlas_path, source_cmo3=archived)
    assert not report['passed']
    assert any('SHA256 differs' in error for error in report['errors'])


@pytest.mark.parametrize('damage,match', [
    (lambda n: n['drawables'].clear(), 'Missing or duplicate native'),
    (lambda n: n['drawables'].append(deepcopy(n['drawables'][0])), 'duplicate native'),
    (lambda n: n['drawables'][0].update(id='unknown'), 'mesh IDs differ'),
    (lambda n: n.update(passed=False), 'failed'),
])
def test_missing_duplicate_or_unverified_meshes_fail_explicitly(tmp_path, monkeypatch, damage, match):
    native_path, atlas_path, native, _ = _files(tmp_path, monkeypatch)
    damage(native)
    native_path.write_text(json.dumps(native))
    report = audit.verify(native_path, atlas_path)
    assert not report['passed']
    assert any(match in error for error in report['errors'])


def test_changed_png_and_during_run_replacement_invalidate_evidence(tmp_path, monkeypatch):
    native_path, atlas_path, _, png = _files(tmp_path, monkeypatch)
    def mutate(_):
        png.write_bytes(png.read_bytes()+b'changed after it was checked')
        return {'mesh': (SOURCE, TRIANGLES)}
    monkeypatch.setattr(audit, 'read_packed_meshes', mutate)
    report = audit.verify(native_path, atlas_path)
    assert not report['passed'] and not report['sourcesUnchanged']
    assert any('changed during' in error for error in report['errors'])
    report = audit.verify(native_path, atlas_path)
    assert not report['passed']
    assert any('SHA256 differs' in error for error in report['errors'])


def test_bad_cli_input_writes_failed_report_and_does_not_overwrite_it(tmp_path):
    source, output = tmp_path/'malformed.json', tmp_path/'report.json'
    source.write_text('{}')
    command = [sys.executable, str(ROOT/'tools/authoring/verify_native_uv.py'), '--native-audit', str(source),
               '--atlas-audit', str(source), '--output', str(output)]
    first = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert first.returncode != 0
    assert json.loads(output.read_text())['passed'] is False
    saved = output.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert second.returncode != 0 and output.read_bytes() == saved


def test_official_geometry_cli_rejects_fake_moc_and_preserves_report(tmp_path):
    node = shutil.which('node')
    assert node, 'Node.js is required for the actual official Core audit; do not treat this check as a pass without it'
    moc, output = tmp_path/'fake.moc3', tmp_path/'geometry.json'
    moc.write_bytes(b'MOC3'+bytes(124))
    command = [node, str(ROOT/'tools/authoring/audit_native_geometry.mjs'), str(moc), '--output', str(output)]
    first = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert first.returncode != 0
    report = json.loads(output.read_text())
    assert report['passed'] is False and report['errors']
    saved = hashlib.sha256(output.read_bytes()).hexdigest()
    second = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert second.returncode != 0 and hashlib.sha256(output.read_bytes()).hexdigest() == saved
