"""Actual authored-IR fade/contact regressions; run in the authoring environment.

These require the documented authoring NumPy/OpenCV dependencies and real layer
meshes. They are numerical authoring evidence, not a native/visual acceptance.
"""
import json
import copy
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/authoring'))
import build_v5
from diagnose_rig import DiagnosticRenderer, motion_parameters
from free_arm_refinement import crouch_point
from maple_motions import build_motion, TRANSITIONS, V5_CLEAN_TRANSITIONS
from tools.authoring.animation_specs import ANIMATIONS

UV = {'l': ((.495, .518), (.274, .390)),
      'r': ((.526, .518), (.728, .390))}


def renderer(rig):
    value = DiagnosticRenderer.__new__(DiagnosticRenderer)
    value.rig = rig
    value.meshes = {mesh.part_id: mesh for mesh in rig.meshes}
    value.nodes = {node.id: node for node in rig.deformers}
    return value


@pytest.fixture(scope='module')
def setup(tmp_path_factory):
    folder = ROOT / 'assets/authoring/source'
    manifest = json.loads((folder / 'layers.json').read_text(encoding='utf-8'))
    rig = build_v5.V5Builder(copy.deepcopy(manifest), folder).build()
    result = renderer(rig)
    defaults = {p.id: p.default for p in rig.parameters}
    out = tmp_path_factory.mktemp('free-motion')

    def motion(name, phase):
        path = out / (name + '.motion3.json')
        if not path.exists():
            spec = {**ANIMATIONS, **TRANSITIONS, **V5_CLEAN_TRANSITIONS}[name]
            path.write_text(json.dumps(build_motion(name, spec, defaults)), encoding='utf-8')
        return motion_parameters(path, phase)

    cache = {}

    def point(pose, name, uv):
        key = name, uv
        if key not in cache:
            uvs = np.asarray(result.meshes[name].uvs)
            for ids in result.meshes[name].triangles:
                a, b, c = uvs[list(ids)]
                try:
                    w = np.linalg.solve(np.array([b - a, c - a]).T, np.asarray(uv) - a)
                except np.linalg.LinAlgError:
                    continue
                if min(w) >= -1e-8 and sum(w) <= 1 + 1e-8:
                    cache[key] = list(ids), np.array([1 - sum(w), *w])
                    break
            else:
                raise AssertionError(f'{name}: actual source contact outside mesh')
        ids, weights = cache[key]
        return (pose[name][0][ids] * weights[:, None]).sum(axis=0)

    return result, defaults, motion, point, folder, manifest


def blend(a, b, weight):
    assert a.keys() == b.keys()
    return {p: a[p] + (b[p] - a[p]) * weight for p in a}


def contact_errors(pose, point):
    for side, (hand_uv, cuff_uv) in UV.items():
        if pose[f'hand_{side}'][1] > .001:
            yield side, 'original', np.linalg.norm(point(pose, f'hand_{side}', hand_uv)
                - point(pose, f'arm_{side}', hand_uv))
        cuff = point(pose, f'sleeve_{side}_lower', cuff_uv)
        # Fingers are the complementary front patch of a grip, and contain no
        # wrist pixel. Its actual wrist belongs to the paired grip_palm mesh.
        for shape in ('relaxed', 'support', 'grip_palm'):
            name = f'hand_{side}_{shape}'
            if pose[name][1] > .001:
                yield side, shape, np.linalg.norm(point(pose, name, hand_uv) - cuff)


@pytest.mark.parametrize('state', ['drag_left', 'drag_right', 'fall_float'])
def test_ground_pickup_fade_keeps_visible_cuff_and_exchange_at_rest(setup, state):
    r, defaults, motion, point, *_ = setup
    start, end = motion('idle', 0), motion(state, 0)
    assert start['ParamArmSupportBlend'] == end['ParamArmSupportBlend'] == 0
    assert end['ParamArmPoseMode'] == pytest.approx(0, abs=1e-12)
    for weight in [0, .01, .025, .04, .05, .075, .1, .2, .35, .5, .75, 1]:
        pose = r.evaluate(blend(start, end, weight))
        for side, shape, error in contact_errors(pose, point):
            assert error < .001, (state, weight, side, shape, error)
        # One continuous original sleeve, not two different paintings whose
        # wrists happen to be near each other during a material crossfade.
        for side, (hand_uv, _) in UV.items():
            assert pose[f'hand_{side}'][1] > .999
            assert pose[f'arm_{side}'][1] > .999
            for name in (f'hand_{side}_relaxed', f'sleeve_{side}_upper', f'sleeve_{side}_lower'):
                assert pose[name][1] < 1e-10, (state, weight, name)


@pytest.mark.parametrize('state,phase,effects', [
    ('talk', .3, {}), ('happy', .4, {}), ('sleep_loop', .3, {}),
    ('idle', .4, {'ParamTyping': 1, 'ParamTypingPulse': 1, 'ParamListening': 1}),
])
def test_pickup_can_interrupt_real_ground_poses_without_leaving_old_hands(setup, state, phase, effects):
    r, _, motion, point, *_ = setup
    a, b = {**motion(state, phase), **effects}, motion('drag_left', 0)
    for weight in [0, .01, .025, .04, .05, .1, .35, .5, 1]:
        pose = r.evaluate(blend(a, b, weight))
        for side, shape, error in contact_errors(pose, point):
            assert error < .001, (state, weight, side, shape, error)
        for side, (uv, _) in UV.items():
            assert pose[f'hand_{side}'][1] > .999
            assert pose[f'hand_{side}_relaxed'][1] < 1e-10


@pytest.mark.parametrize('support,phase', [('climb_left', .37), ('climb_right', .71),
    ('swing_idle', .35), ('clean_climb_left', .6), ('clean_ground', .4), ('clean_top', .25),
    ('climb_to_top_left', .18), ('climb_to_top_left', .47),
    ('climb_to_top_right', .18), ('climb_to_top_right', .47)])
def test_support_free_fades_keep_every_visible_hand_at_its_cuff(setup, support, phase):
    r, _, motion, point, *_ = setup
    a, b = motion(support, phase), motion('drag_right', .3)
    # Ground brushing now uses the free arm chain; wall/rope states use support.
    assert a['ParamArmSupportBlend'] == pytest.approx(0 if support=='clean_ground' else 1, abs=1e-12)
    assert b['ParamArmSupportBlend'] == pytest.approx(0, abs=1e-12)
    # Reverse traversal covers free -> support with the same current curves.
    for weight in np.linspace(0, 1, 21):
        pose = r.evaluate(blend(a, b, float(weight)))
        for side, shape, error in contact_errors(pose, point):
            assert error < .012, (support, weight, side, shape, error)


def test_land_folds_before_artwork_exchange_and_does_not_reopen_on_idle_fade(setup):
    r, _, motion, point, *_ = setup
    # v5.1 releases the elbow brace before returning the hanging hands. Keep
    # checking the actual cuffs during that return, not only after it settles.
    previous_pose = 1.
    for phase in [.5, .67, .74, .77, .78, .785, .79, .795, .8, .84, .85, .9, .94, 1]:
        params = motion('land', phase)
        assert params['ParamArmPoseMode'] == pytest.approx(0, abs=1e-12)
        assert 0 <= params['ParamFreeArmPose'] <= previous_pose + 1e-12
        previous_pose = params['ParamFreeArmPose']
        if 'ParamFreeArmBrace' in params:
            assert params['ParamFreeArmBrace'] == pytest.approx(0, abs=1e-12)
        pose = r.evaluate(params)
        for side, shape, error in contact_errors(pose, point):
            assert error < .001, (phase, side, shape, error)
        for side in UV:
            assert pose[f'hand_{side}'][1] > .999
            assert pose[f'hand_{side}_relaxed'][1] < 1e-10
        settled_phase = .85 if 'ParamFreeArmBrace' in params else .78
        if phase >= settled_phase:
            assert params['ParamFreeArmPose'] == pytest.approx(0, abs=1e-12)
    for weight in np.linspace(0, 1, 11):
        pose = r.evaluate(blend(motion('land', 1), motion('idle', 0), float(weight)))
        assert pose['hand_l_relaxed'][1] == pose['hand_r_relaxed'][1] == 0


@pytest.fixture(scope='module')
def before_free_refinement(setup):
    _, _, _, _, folder, manifest = setup
    # Brushing depends on the active cuff nodes introduced by free refinement.
    # Omit both for the historical baseline, keeping production hooks intact.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(build_v5, 'apply_free_arm_refinement',
                      lambda b: b.deform('ParamCrouch', 'Body_Weight', crouch_point))
        def omit_brush(builder):
            for name in ('ParamCleanBrushL','ParamCleanBrushR','ParamCleanContext',
                         'ParamCleanPoseL','ParamCleanPoseR','ParamCleanStroke'):
                builder.params.pop(name, None)
        patch.setattr(build_v5.brush_refinement, 'apply_brush_refinement', omit_brush)
        return renderer(build_v5.V5Builder(copy.deepcopy(manifest), folder).build())


def test_original_ground_and_source_material_vertices_remain_unchanged(setup, before_free_refinement):
    r, _, _, _, folder, manifest = setup
    # Rebuild without the new active-chain registration. The archived native05
    # is the rollback; this candidate intentionally repairs the active chain.
    # Preserve ground artwork and every original source material vertex.
    original = before_free_refinement
    fixture = json.loads((ROOT / 'tests/fixtures/v5-native-contact-04.json').read_text(encoding='utf-8'))
    for name, mesh in original.meshes.items():
        actual = r.meshes[name]
        assert actual.uvs[:len(mesh.uvs)] == mesh.uvs
        assert actual.vertices[:len(mesh.vertices)] == mesh.vertices
    compared = 0
    for label, sample in fixture['samples'].items():
        params = sample['parameters']
        if params.get('ParamArmPoseMode', 0) != 1:
            continue
        before, after = original.evaluate(params), r.evaluate(params)
        for name in ('arm_l', 'arm_r', 'hand_l', 'hand_r'):
            # Brushing appends a wrist anchor vertex; compare every original
            # vertex by its preserved index, not the changed mesh length.
            np.testing.assert_allclose(after[name][0][:len(before[name][0])], before[name][0], atol=1e-10, rtol=0,
                                       err_msg=f'{label}: {name}')
            assert after[name][1] == pytest.approx(before[name][1], abs=1e-12)
        compared += 1
    assert compared >= 50


def test_each_native_deformer_stays_within_three_parameter_axes(setup):
    r, *_ = setup
    for node in r.rig.deformers:
        drivers = [p.id for p in r.rig.parameters if any(
            node.id in k.deformer_offsets or node.id in k.deformer_offset_weights
            or node.id in k.deformer_opacity_overrides for k in p.keyforms)]
        assert len(drivers) <= 3, (node.id, drivers)


def test_painted_free_sleeve_triangles_do_not_reverse_or_collapse(setup):
    import cv2
    from PIL import Image

    r, defaults, _, _, folder, manifest = setup
    names = ['arm_l', 'arm_r', 'hand_l', 'hand_r']
    reference = r.evaluate({**defaults, 'ParamArmPoseMode': 0, 'ParamArmSupportBlend': 0})

    def edges(vertices, mesh):
        tri = vertices[np.asarray(mesh.triangles)]
        a, b = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
        return np.stack((a, b), axis=-1)

    initial = {name: edges(reference[name][0], r.meshes[name]) for name in names}
    source_files = {layer['id']: layer['file'] for layer in manifest['layers']}
    coverage = {}
    for name in names:
        with Image.open(folder / source_files[name]) as image:
            alpha = np.asarray(image.getchannel('A'))
        mesh = r.meshes[name]
        uv = np.asarray(mesh.uvs) * alpha.shape[::-1]
        covered, weights = [], []
        for points in uv[np.asarray(mesh.triangles)]:
            # A triangle's centroid can be transparent while its edge contains
            # painted cloth. Inspect every source pixel and include a one-pixel
            # conservative texture-filter footprint; never raise alpha > 0.
            low = np.maximum(np.floor(points.min(0)-2).astype(int), 0)
            high = np.minimum(np.ceil(points.max(0)+3).astype(int), alpha.shape[::-1])
            if np.any(high <= low):
                covered.append(False); weights.append(0.)
                continue
            mask = np.zeros(tuple((high-low)[::-1]), dtype=np.uint8)
            cv2.fillConvexPoly(mask, np.round((points-low-.5)*256).astype(np.int32), 1, shift=8)
            cut = alpha[low[1]:high[1], low[0]:high[0]]
            weights.append(float(cut[mask > 0].sum()) / 255)
            footprint = cv2.dilate(mask, np.ones((3, 3), np.uint8))
            covered.append(bool(np.any(cut[footprint > 0] > 0)))
        assert any(covered) and sum(weights) > 0, name
        coverage[name] = np.asarray(covered), np.asarray(weights)

    for phase in np.linspace(0, 1, 31):
        pose = r.evaluate({**defaults, 'ParamArmPoseMode': 0,
            'ParamFreeArmActive': 1, 'ParamFreeArmPose': float(phase),
            'ParamArmSupportBlend': 0})
        for name in names:
            assert pose[name][1] > .999
            actual = edges(pose[name][0], r.meshes[name])
            baseline = np.linalg.det(initial[name])
            valid = abs(baseline) > 1e-10
            ratios = np.linalg.det(actual[valid]) / baseline[valid]
            # Preserve direction and finite geometry for the entire IR mesh,
            # including its transparent controls. Painted-material bounds below
            # additionally reject narrow spikes with positive but tiny area.
            assert np.all(np.isfinite(ratios))
            assert np.all(ratios > 0), (name, phase, float(ratios.min()))
            painted, weight = coverage[name]
            visible = painted[valid]
            singular = np.linalg.svd(actual[valid] @ np.linalg.inv(initial[name][valid]), compute_uv=False)
            assert np.all(np.isfinite(singular))
            # The new gravity frame intentionally gathers the hem horizontally
            # to 60% while preserving its vertical length. Near-rigid 95% area
            # is no longer the intended cloth shape. Bound local compression
            # to at most 4x for cloth / 2.5x for hands and preserve 60% / 80% of
            # total painted area, so a positive needle cannot satisfy the test.
            hand = name.startswith('hand_')
            local_limit, total_limit = (.4, .8) if hand else (.25, .6)
            assert ratios[visible].min() > local_limit, (name, phase, 'paint area', float(ratios[visible].min()))
            assert singular[visible].min() > local_limit, (name, phase, 'paint width', float(singular[visible].min()))
            total_ratio = float(np.sum(ratios * weight[valid]) / weight[valid].sum())
            assert total_ratio >= total_limit, (name, phase, 'total painted area', total_ratio)


def test_closed_free_pose_is_the_original_mesh_not_a_second_folded_painting(setup, before_free_refinement):
    r, _, motion, _, folder, manifest = setup
    original = before_free_refinement
    for state in ('idle', 'talk', 'sleep_loop', 'happy', 'petting'):
        params = motion(state, .4)
        assert params['ParamFreeArmPose'] == pytest.approx(0, abs=1e-12)
        before, after = original.evaluate(params), r.evaluate(params)
        for name in ('arm_l', 'arm_r', 'hand_l', 'hand_r'):
            np.testing.assert_allclose(after[name][0][:len(before[name][0])], before[name][0], atol=1e-10, rtol=0)
            assert after[name][1] == pytest.approx(before[name][1], abs=1e-12)


@pytest.mark.parametrize('state', ['climb_left', 'climb_right'])
def test_refined_visible_palms_follow_their_cuffs_without_triangle_folds(setup, state):
    r, _, motion, point, *_ = setup
    # The unshipped Refine=1 palm is allowed to rotate/shear with its forearm.
    # Check its actual painted mesh, not equality to the former translation.
    for phase in [0, .1, .18, .25, .35, .4, .5, .55, .6, .7, .78, .85, .9, 1]:
        params = motion(state, phase)
        assert params['ParamClimbRefine'] == pytest.approx(1)
        pose = r.evaluate(params)
        old = r.evaluate({**params, 'ParamClimbRefine': 0})
        for side in UV:
            for shape in ('relaxed', 'support', 'grip_palm'):
                name = f'hand_{side}_{shape}'
                if pose[name][1] <= .001:
                    continue
                tri = np.asarray(r.meshes[name].triangles)
                def area(vertices):
                    p = vertices[tri]
                    a, b = p[:, 1]-p[:, 0], p[:, 2]-p[:, 0]
                    return a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0]
                before, after = area(old[name][0]), area(pose[name][0])
                valid = abs(before) > 1e-10
                assert np.all(after[valid]*before[valid] > 0), (state, phase, name)
                assert np.min(abs(after[valid]/before[valid])) > .1, (state, phase, name)
        for side, shape, error in contact_errors(pose, point):
            assert error < .012, (state, phase, side, shape, error)


def test_real_export06_failure_parameters_now_use_identical_explicit_material_contacts(setup):
    r, _, _, point, *_ = setup
    fixture = json.loads((ROOT/'tests/fixtures/v5-native-contact-06-free.json').read_text(encoding='utf-8'))
    assert len(fixture['samples']) == 18
    for frame in fixture['samples']:
        assert any(c['gapCanvas'] > .012 for c in frame['oldNativeContacts'])
        pose = r.evaluate(frame['parameters'])
        for side, shape, error in contact_errors(pose, point):
            assert error < 1e-9, (frame['id'], side, shape, error)


def test_active_contacts_are_explicit_vertices_under_one_native_parent(setup):
    r, *_ = setup
    parts = {p.id: p for p in r.rig.parts}
    for side, (huv, cuv) in UV.items():
        cuff = 'sleeve_'+side+'_lower'
        assert any(np.linalg.norm(np.asarray(uv)-cuv) < 1e-12 for uv in r.meshes[cuff].uvs)
        for suffix in ('relaxed', 'support', 'grip_palm'):
            name = f'hand_{side}_{suffix}'
            assert any(np.linalg.norm(np.asarray(uv)-huv) < 1e-12 for uv in r.meshes[name].uvs)
            registration = r.nodes[parts[name].parent_deformer]
            assert registration.parent == parts[cuff].parent_deformer
        fan = 'clean_fan_'+side
        assert any(np.linalg.norm(np.asarray(uv)-huv) < 1e-12 for uv in r.meshes[fan].uvs)
        fan_registration = r.nodes[parts[fan].parent_deformer]
        hand_registration = r.nodes[parts[f'hand_{side}_grip_palm'].parent_deformer]
        assert fan_registration.type == 'rotation'
        assert fan_registration.parent == hand_registration.id
        np.testing.assert_allclose(fan_registration.pivot, (.5, .5), atol=1e-12)


def test_active_wrist_is_the_registration_lattice_control_point_at_every_key(setup):
    r, *_ = setup
    parts = {p.id: p for p in r.rig.parts}
    parameters = {p.id: p for p in r.rig.parameters}
    for side, (huv, cuv) in UV.items():
        registration = r.nodes[parts[f'hand_{side}_support'].parent_deformer]
        assert (registration.grid_rows, registration.grid_cols) == (3, 3)
        np.testing.assert_allclose(registration.grid_vertices[4], cuv, atol=1e-12, rtol=0)
        for climb in parameters['ParamPoseClimb'].keyforms:
            np.testing.assert_allclose(
                np.asarray(registration.grid_vertices[4]) + climb.deformer_offsets[registration.id][4],
                cuv, atol=1e-12, rtol=0)
            for name in [f'hand_{side}_{shape}' for shape in ('relaxed', 'support', 'grip_palm')] + ['clean_fan_'+side]:
                mesh = r.meshes[name]
                index = next(i for i, uv in enumerate(mesh.uvs) if math.dist(uv, huv) < 1e-12)
                for fan in parameters['ParamCleanFan'+side.upper()].keyforms:
                    for mode in parameters['ParamArmPoseMode'].keyforms:
                        local = np.asarray(mesh.vertices[index]) + sum(
                            np.asarray(key.mesh_offsets.get(name, [(0., 0.)]*len(mesh.vertices))[index])
                            for key in (climb, fan, mode))
                        np.testing.assert_allclose(local, (.5, .5), atol=1e-12, rtol=0,
                            err_msg=f'{name}: climb={climb.value}, fan={fan.value}, mode={mode.value}')


def test_cuff_control_point_survives_parent_lattice_resampling_and_fan_opening(monkeypatch):
    """A small nonlinear parent catches the native07 mechanism without a build.

    Interpolating a parent-deformed child lattice differs from applying its
    affine registration to each point first. Preserve the shared control
    point under both evaluations, including the fan/pose cross terms.
    """
    import free_arm_refinement as refinement
    from diagnose_rig import interpolate_keys, warp_points

    def key(value):
        return SimpleNamespace(value=value, mesh_offsets={}, deformer_offsets={},
                               deformer_offset_weights={}, deformer_opacity_overrides={})

    builder = SimpleNamespace(nodes={}, meshes={}, parts=[], params={})
    for name, values in [('ParamPoseClimb', [-1, 0, 1]), ('ParamFreeArmActive', [0, 1]),
                         ('ParamClimbRefine', [0, 1]), ('ParamCleanFanL', [0, .5, 1]),
                         ('ParamCleanFanR', [0, .5, 1]), ('ParamArmPoseMode', [0, .025, .05, 1])]:
        builder.params[name] = SimpleNamespace(id=name, default=0, keyforms=[key(v) for v in values])

    def warp(name, parent, size):
        points = [(x/(size-1), y/(size-1)) for y in range(size) for x in range(size)]
        builder.nodes[name] = SimpleNamespace(id=name, parent=parent, grid_cols=size,
                                              grid_rows=size, grid_vertices=points)
        return name

    builder.warp = warp
    builder._source_joints = lambda side: ((0., 0.), (0., 0.), UV[side][1])
    builder._joints = lambda side: ((0., 0.), (0., 0.), UV[side][0])
    bases = {pose: ((1+.13*pose, .17), (-.11, .8+.09*pose)) for pose in (-1, 0, 1)}
    monkeypatch.setattr(refinement, '_cuff_registration_bases', lambda *args: bases)
    originals = {}
    for side, (huv, cuv) in UV.items():
        parent = warp('cuff_parent_'+side, None, 5)
        builder.nodes[parent].grid_vertices = [(x+.16*y*y, y+.12*x*x)
                                               for x, y in builder.nodes[parent].grid_vertices]
        names = ['sleeve_'+side+'_lower', 'clean_fan_'+side]
        names += [f'hand_{side}_{shape}' for shape in ('relaxed', 'support', 'grip_palm', 'grip_fingers')]
        for name in names:
            uv = cuv if name.startswith('sleeve_') else huv
            points = [(uv[0]+x, uv[1]+y) for x, y in [(-.08, -.06), (.09, -.06), (.09, .1), (-.08, .1)]]
            builder.meshes[name] = SimpleNamespace(vertices=points.copy(), uvs=points.copy(),
                                                   triangles=[(0, 1, 2), (0, 2, 3)])
            builder.parts.append(SimpleNamespace(id=name, parent_deformer=parent))
            originals[name] = points.copy()
        for fan in builder.params['ParamCleanFan'+side.upper()].keyforms:
            fan.mesh_offsets['clean_fan_'+side] = [((x-huv[0])*(fan.value-1)*.8,
                                                   (y-huv[1])*(fan.value-1)*.6)
                                                  for x, y in originals['clean_fan_'+side]]
    refinement.finish_free_arm_refinement(builder)
    for side, (huv, cuv) in UV.items():
        registration = builder.nodes['ActiveCuffMaterial_'+side]
        parent = builder.nodes[registration.parent]
        expected_contact = warp_points([cuv], parent.grid_vertices, 5, 5)[0]
        for pose in (-1, -.73, -.1, 0, .31, 1):
            selected = interpolate_keys(builder.params['ParamPoseClimb'], pose)
            grid = np.asarray(registration.grid_vertices) + sum(
                weight*np.asarray(k.deformer_offsets[registration.id]) for k, weight in selected)
            parent_deformed_grid = warp_points(grid, parent.grid_vertices, 5, 5)
            inverse = sum(weight*np.asarray(bases[k.value]) for k, weight in selected)
            for amount in (0, .2, .5, .85, 1):
                fan_keys = interpolate_keys(builder.params['ParamCleanFan'+side.upper()], amount)
                for name in [f'hand_{side}_{shape}' for shape in ('relaxed', 'support', 'grip_palm')] + ['clean_fan_'+side]:
                    mesh = builder.meshes[name]
                    assert mesh.vertices[:4] == mesh.uvs[:4] == originals[name]
                    index = next(i for i, uv in enumerate(mesh.uvs) if math.dist(uv, huv) < 1e-12)
                    zero = [(0., 0.)]*len(mesh.vertices)
                    mode = builder.params['ParamArmPoseMode'].keyforms[0]
                    local = np.asarray(mesh.vertices) + np.asarray(mode.mesh_offsets.get(name, zero)) + sum(
                        weight*np.asarray(k.mesh_offsets.get(name, zero)) for k, weight in fan_keys)
                    actual_registration = builder.nodes[next(p.parent_deformer for p in builder.parts if p.id == name)]
                    actual_grid = np.asarray(actual_registration.grid_vertices) + sum(
                        weight*np.asarray(k.deformer_offsets[actual_registration.id]) for k, weight in selected)
                    actual_parent_grid = warp_points(actual_grid, parent.grid_vertices, 5, 5)
                    size = actual_registration.grid_rows
                    native_contact = warp_points([local[index]], actual_parent_grid, size, size)[0]
                    np.testing.assert_allclose(native_contact, expected_contact, atol=1e-12, rtol=0)
                    before_parent = warp_points(local, actual_grid, size, size)
                    opening = (np.asarray(mesh.vertices)-huv) * ((amount-1)*np.array([.8, .6])) if name.startswith('clean_fan_') else 0
                    expected = np.asarray(cuv) + (np.asarray(mesh.vertices)+opening-huv) @ inverse.T
                    np.testing.assert_allclose(before_parent, expected, atol=1e-12, rtol=0)


def test_all_31_actual_motion_poses_keep_active_wrists_and_fan_shafts_closed(setup):
    r, _, motion, point, *_ = setup
    names = {**ANIMATIONS, **TRANSITIONS, **V5_CLEAN_TRANSITIONS}
    assert len(names) == 31
    for state in names:
        for phase in (0, .25, .46, .75, 1):
            pose = r.evaluate(motion(state, phase))
            for side, shape, error in contact_errors(pose, point):
                assert error < .001, (state, phase, side, shape, error)
            for side, (uv, _) in UV.items():
                fan, palm = 'clean_fan_'+side, f'hand_{side}_grip_palm'
                if pose[fan][1] > .001:
                    # Taking/putting away the fan crosses relaxed/support/grip
                    # artwork; bind its shaft to whichever real hand is visible.
                    hands = [f'hand_{side}_{shape}' for shape in ('relaxed','support','grip_palm')
                             if pose[f'hand_{side}_{shape}'][1] > .001]
                    assert hands, (state, phase, side)
                    for hand in hands:
                        assert np.linalg.norm(point(pose, fan, uv)-point(pose, hand, uv)) < 1e-9
