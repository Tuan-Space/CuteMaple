"""Real source-paint contact tests, independent of screen/anchor floor locking.

Only an in-memory authoring model is constructed. These tests neither export a
MOC3 nor claim native Cubism or visual acceptance.
"""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/authoring'))
import build_v5
from diagnose_rig import DiagnosticRenderer
from sleep_contact_refinement import (
    ContactEvaluator, FOOT_BRANCHES, apply_sleep_contact_refinement,
    painted_samples, sleep_ground_anchor,
)


def triangle_samples(builder, name):
    """Map original opaque pixel centres to real ArtMesh triangles, not a warp field."""
    samples = painted_samples(builder, name)
    mesh = builder.meshes[name]
    uvs = np.asarray(mesh.uvs)
    selected = np.full((len(samples), 3), -1, dtype=int)
    weights = np.zeros((len(samples), 3))
    for ids in mesh.triangles:
        a, b, c = uvs[list(ids)]
        matrix = np.array([b-a, c-a]).T
        if abs(np.linalg.det(matrix)) < 1e-15:
            continue
        w = (samples-a) @ np.linalg.inv(matrix).T
        valid = (selected[:, 0] < 0) & (w.min(axis=1) >= -1e-9) & (w.sum(axis=1) <= 1+1e-9)
        selected[valid] = ids
        weights[valid] = np.column_stack((1-w[valid].sum(axis=1), w[valid]))
    assert (selected >= 0).all(), (name, int((selected[:, 0] < 0).sum()))
    return selected, weights


@pytest.fixture(scope='module')
def setup():
    folder = ROOT / 'assets/authoring/source'
    manifest = json.loads((folder / 'layers.json').read_text(encoding='utf-8'))
    builder = build_v5.V5Builder(manifest, folder)
    # The owner integrates the public entry point into build_v5. Suppress that
    # call solely inside this fixture so a genuine before/after can be measured.
    with pytest.MonkeyPatch.context() as patch:
        if hasattr(build_v5, 'apply_sleep_contact_refinement'):
            patch.setattr(build_v5, 'apply_sleep_contact_refinement', lambda unused: None)
        builder._load_layers(*builder._hierarchy())
    before = ContactEvaluator(builder)
    protected = ('face_base', 'torso', 'skirt_sleep')
    protected_geometry = {
        (name, pose, breath): before.points(name, pose, breath=breath, points=builder.meshes[name].vertices)
        for name in protected for pose in (0, .4, .84, .9, 1) for breath in (0, .5)
    }
    neutral_feet = {name: before.points(name, 0, points=builder.meshes[name].vertices)
                    for name in FOOT_BRANCHES}
    mesh_bytes = {name: mesh.model_dump_json() for name, mesh in builder.meshes.items()}
    original_nodes = {name: node.model_dump_json() for name, node in builder.nodes.items()}
    parents = {name: builder.nodes[branch].parent for name, branch in FOOT_BRANCHES.items()}
    parameter_ids = tuple(builder.params)
    hashes = {name: hashlib.sha256((folder / builder.layer_by_id[name]['file']).read_bytes()).hexdigest()
              for name in [*FOOT_BRANCHES, 'skirt_sleep', 'face_base', 'torso']}
    audit = apply_sleep_contact_refinement(builder)
    after = ContactEvaluator(builder)
    bindings = {name: triangle_samples(builder, name) for name in [*FOOT_BRANCHES, 'skirt_sleep', 'skirt']}
    return dict(builder=builder, evaluator=after, audit=audit, bindings=bindings,
                protected=protected_geometry, neutral=neutral_feet, meshes=mesh_bytes,
                nodes=original_nodes, parents=parents, params=parameter_ids, hashes=hashes,
                folder=folder)


def actual_sole(setup, name, pose, breath):
    builder, evaluator = setup['builder'], setup['evaluator']
    vertices = evaluator.points(name, pose, breath=breath, points=builder.meshes[name].vertices)
    ids, weights = setup['bindings'][name]
    return float((vertices[ids] * weights[..., None]).sum(axis=1)[:, 1].max())


def plane(setup, pose):
    points = np.asarray(setup['audit']['groundAnchor']['points'])
    return float(np.interp(pose, points[:, 0], points[:, 2]))


def test_original_rest_pixels_uvs_and_upper_body_are_preserved(setup):
    b, ev = setup['builder'], setup['evaluator']
    assert tuple(b.params) == setup['params']
    assert {name: mesh.model_dump_json() for name, mesh in b.meshes.items()} == setup['meshes']
    for name, digest in setup['hashes'].items():
        assert hashlib.sha256((setup['folder'] / b.layer_by_id[name]['file']).read_bytes()).hexdigest() == digest
    for name, expected in setup['neutral'].items():
        np.testing.assert_allclose(ev.points(name, 0, points=b.meshes[name].vertices), expected, atol=1e-12)
    for (name, pose, breath), expected in setup['protected'].items():
        np.testing.assert_array_equal(ev.points(name, pose, breath=breath, points=b.meshes[name].vertices), expected)


@pytest.mark.parametrize('breath', [0, .5, .7, 1])
def test_real_painted_triangle_soles_share_exchange_plane_including_breath(setup, breath):
    # Native export transforms mesh vertices first, then samples their UV
    # triangles. This intentionally differs from the builder's contour field.
    for pose in sorted(set(np.linspace(0, 1, 31).tolist() + [.82, .8201, .83, .84, .87, .9199, .92])):
        names = ('leg_l', 'leg_r') if pose < .82 else (
            tuple(FOOT_BRANCHES) if pose < .92 else ('leg_l_sleep', 'leg_r_sleep'))
        soles = {name: actual_sole(setup, name, pose, breath) for name in names}
        ground = plane(setup, pose)
        assert max(abs(value-ground) for value in soles.values()) <= .0015, (pose, breath, soles, ground)
        if .82 <= pose <= .92:
            assert max(soles.values())-min(soles.values()) < .0002, (pose, breath, soles)
        if pose >= .82:
            assert actual_sole(setup, 'skirt_sleep', pose, breath) <= ground + .001
        if pose <= .92:
            assert actual_sole(setup, 'skirt', pose, breath) <= ground + .001


def test_only_four_private_uniform_standard_warps_are_added(setup):
    b = setup['builder']
    assert set(b.nodes)-set(setup['nodes']) == {'SleepContact_'+name for name in FOOT_BRANCHES}
    for name, original in setup['nodes'].items():
        if name not in FOOT_BRANCHES.values():
            assert b.nodes[name].model_dump_json() == original
    for name, branch in FOOT_BRANCHES.items():
        new = 'SleepContact_'+name
        assert b.nodes[branch].parent == new
        assert b.nodes[new].parent == setup['parents'][name]
        assert (b.nodes[new].grid_rows, b.nodes[new].grid_cols) == (5, 5)
        for form in b.params['ParamPoseSleep'].keyforms:
            offsets = np.asarray(form.deformer_offsets[new])
            np.testing.assert_allclose(offsets, np.broadcast_to(offsets[0], offsets.shape), atol=1e-15)
            np.testing.assert_allclose(offsets[:, 0], 0, atol=1e-15)
            if form.value == 0:
                np.testing.assert_array_equal(offsets, np.zeros_like(offsets))


def test_standing_shoes_keep_original_painted_proportions_during_crouch(setup):
    b, ev = setup['builder'], setup['evaluator']
    for name in ('leg_l', 'leg_r'):
        neutral = setup['neutral'][name]
        for pose in (0, .2, .4, .7, .82, .87, .9199):
            vertices = ev.points(name, pose, points=b.meshes[name].vertices)
            np.testing.assert_allclose(vertices-vertices.mean(axis=0),
                                       neutral-neutral.mean(axis=0), atol=1e-12)


def test_planted_shoes_remain_joined_to_painted_lower_clothing(setup):
    b = setup['builder']
    selected = {'leg_l', 'leg_r', 'skirt', 'leg_l_sleep', 'leg_r_sleep', 'skirt_sleep'}
    renderer = DiagnosticRenderer.__new__(DiagnosticRenderer)
    renderer.rig = SimpleNamespace(parts=b.parts, parameters=list(b.params.values()))
    renderer.res, renderer.meshes, renderer.nodes = 512, b.meshes, b.nodes
    renderer.textures = {}
    texture_ids = {part.texture_id for part in b.parts if part.id in selected}
    for texture in b.textures:
        if texture.id in texture_ids:
            with Image.open(setup['folder'] / texture.path) as image:
                pixels = np.asarray(image.convert('RGBA'), dtype=np.float32) / 255
            pixels[:, :, :3] *= pixels[:, :, 3:4]
            renderer.textures[texture.id] = pixels
    for value in (.4, .7, .82):
        image, _ = renderer.render({'ParamPoseSleep': value, 'ParamBreath': .5,
                                    '__hiddenLayers': [p.id for p in b.parts if p.id not in selected]})
        _, _, stats, _ = cv2.connectedComponentsWithStats((np.asarray(image)[:, :, 3] > 8).astype('uint8'))
        # The rejected uniform-translation-only candidate produced a separate
        # 1,000–1,500px shoe island, separated from the skirt by 1–5 screen px.
        assert len(stats[1:][stats[1:, 4] > 100]) == 1, (value, stats.tolist())


def test_anchor_uses_calibrated_ground_not_hidden_mesh_and_has_no_switch_jump(setup):
    anchor = sleep_ground_anchor(setup['builder'])
    assert anchor['parameter'] == 'ParamPoseSleep' and 'drawable' not in anchor
    assert anchor['points'][0][0] == 0 and anchor['points'][-1][0] == 1
    assert all(a[0] < b[0] for a, b in zip(anchor['points'], anchor['points'][1:]))
    assert abs(plane(setup, 0)-actual_sole(setup, 'leg_r', 0, 0)) < 1e-12
    assert abs(plane(setup, 1)-actual_sole(setup, 'skirt_sleep', 1, 0)) < 1e-12
    for value in (.82, .92):
        assert abs(plane(setup, value+1e-6)-plane(setup, value-1e-6)) < 1e-6
    assert plane(setup, 1)-plane(setup, 0) < .025
    # Defensive copies/idempotence keep metadata consumers from changing rig audit.
    anchor['points'][0][2] = -1
    assert sleep_ground_anchor(setup['builder'])['points'][0][2] > .9
    assert apply_sleep_contact_refinement(setup['builder']) is setup['audit']
