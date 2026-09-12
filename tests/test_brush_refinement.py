"""Cleaning geometry contracts using small real authoring lattices and paint.

These do not build the character or start Cubism/Core/Qt. Native and visual
acceptance of the subsequent full export remains a separate requirement.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace

import numpy as np
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/authoring'))
from build_rig import MapleBuilder, grid_mesh, Part, SemanticRole
from build_v4 import palm_on_rope
from climb_refinement import refined_arm_joints
from brush_refinement import (CONTEXTS, POSE_KEYS, STROKE_KEYS, add_parameters, add_metadata,
                              apply_brush_refinement, clean_joints, material_map,
                              seam_axis, _posed_sampler, _selection, working_side,
                              ground_painted_joints, ground_material_point)
from v51_pose import full_sleeve_point
from image2live2d.backends.live2d.cmo3.model_xml import _Shared, _build_deformers


def reference(side, context):
    if context in (-1, 1):
        return refined_arm_joints(side, context, 0)
    return ((.434, .39), (.409, .485), palm_on_rope('l', 0)) if side == 'l' else (
        (.568, .39), (.601, .485), palm_on_rope('r', 0))


@pytest.mark.parametrize('context', CONTEXTS)
def test_cleaning_bends_two_bones_below_chest_with_curved_wrist_travel(context):
    side = working_side(context)
    ref = reference(side, context)
    lengths = [math.dist(a, b) for a, b in zip(ref, ref[1:])]
    for pose in np.linspace(0, 1, 61):
        for stroke in (-1, 0, 1):
            joints = clean_joints(ref, side, context, pose, stroke)
            assert joints[0] == ref[0]
            assert [math.dist(a, b) for a, b in zip(joints, joints[1:])] == pytest.approx(lengths, abs=1e-12)
    ready = clean_joints(ref, side, context)
    assert ready[2][1] > ready[0][1]+.07
    wrists = np.asarray([clean_joints(ref, side, context, 1, s)[2] for s in STROKE_KEYS])
    chord = wrists[-1]-wrists[0]
    offsets = wrists-wrists[0]
    distances = np.abs(chord[0]*offsets[:, 1]-chord[1]*offsets[:, 0])/np.linalg.norm(chord)
    assert distances.max() > .0006, 'A straight push/pull path is not a brush stroke'
    assert np.ptp(wrists[:, 0]) < .09
    assert np.ptp(wrists[:, 1]) < .09


def test_ground_idle_hand_does_not_sweep_and_wall_anatomical_support_is_preserved():
    ref = reference('l', 0)
    assert clean_joints(ref, 'l', 0, 1, -1) == clean_joints(ref, 'l', 0, 1, 1)
    for context in (-1, 1):
        work = working_side(context)
        assert (work == 'l') != (context > 0)  # Working hand is anatomical far side.
        support = 'r' if work == 'l' else 'l'
        old = reference(support, context)
        for stroke in STROKE_KEYS:
            assert clean_joints(old, support, context, 0, stroke) == old


@pytest.mark.parametrize('context,side', [(-1, 'l'), (1, 'r')])
def test_wall_pickup_routes_under_shoulder_without_folding_sleeve_into_a_needle(context, side):
    # Actual registered native reference joints from frozen build03. These
    # differ materially from the nominal skeleton used by earlier tests.
    ref = (((.49325145, .49431988), (.42334066, .52402316), (.32542878, .43410827))
           if side == 'l' else
           ((.49547854, .49985047), (.56642019, .52863033), (.67824680, .43371719)))
    original = np.asarray([np.subtract(p, ref[0]) for p in ref[1:]]).T
    for progress in np.linspace(0, 1, 101):
        target = clean_joints(ref, side, context, progress)
        matrix = np.asarray([np.subtract(p, target[0]) for p in target[1:]]).T @ np.linalg.inv(original)
        assert np.linalg.det(matrix) > .5
        assert np.linalg.svd(matrix)[1].min() > .45, 'Real sleeve width must not collapse at mid-pickup'
    middle = clean_joints(ref, side, context, .5)
    assert middle[2][1] > ref[0][1]+.12, 'The released wrist must pass below the shoulder'


def test_ground_resting_arm_points_down_and_distant_cloth_is_gathered_monotonically():
    ref = reference('l', 0)
    target = clean_joints(ref, 'l', 0)
    for index, angle in enumerate((86., 110.)):
        a, b = target[index:index+2]
        assert math.degrees(math.atan2(b[1]-a[1], b[0]-a[0])) == pytest.approx(angle)
        assert b[1] > a[1]
        assert math.dist(a, b) == pytest.approx(math.dist(*ref[index:index+2]))
    axis = (-.3, .7)
    target_axis = seam_axis(ref, target, axis)
    samples = []
    for across in np.linspace(-.3, .3, 61):
        point = tuple(ref[1][i]+across*axis[i] for i in (0, 1))
        upper = material_map(point, ref, target, axis, target_axis, 'upper', 1.)
        lower = material_map(point, ref, target, axis, target_axis, 'lower', 1.)
        assert upper == pytest.approx(lower, abs=1e-12)
        samples.append(upper)
    displacement = np.asarray(samples)-target[1]
    projection = displacement @ np.asarray(target_axis)
    assert np.all(np.diff(projection) > 0), 'Folding must not reverse the material'
    assert np.linalg.norm(displacement[-1]) < .060, 'The old wide transverse hem must not bridge the waist'


@pytest.mark.parametrize('context', CONTEXTS)
def test_entire_elbow_seam_stays_common_not_only_joint_center(context):
    side = working_side(context)
    ref = reference(side, context)
    original_axis = (-.3, .7) if side == 'l' else (.3, .7)
    for pose in POSE_KEYS:
        target = clean_joints(ref, side, context, pose)
        axis = seam_axis(ref, target, original_axis)
        for cross in np.linspace(-.06, .18, 17):
            point = tuple(ref[1][i]+cross*original_axis[i] for i in (0, 1))
            upper = material_map(point, ref, target, original_axis, axis, 'upper')
            lower = material_map(point, ref, target, original_axis, axis, 'lower')
            assert upper == pytest.approx(lower, abs=1e-12)
        for segment in ('upper', 'lower'):
            points = [(0., 0.), (1., 0.), (0., 1.)]
            mapped = np.asarray([material_map(p, ref, target, original_axis, axis, segment) for p in points])
            assert np.linalg.det((mapped[1:]-mapped[0]).T) > 0, 'Sleeve cannot flip while taking out the brush'


def small_builder(curved=False):
    """Two independently triangulated sleeves with a shared registered cuff."""
    b = SimpleNamespace(params={}, nodes={}, parts=[], meshes={}, scene='Scene',
                        manifest={'brushRefinementVersion': 1}, layer_by_id={})
    for name in ('parameter', 'warp', 'deform', 'opacity'):
        setattr(b, name, MethodType(getattr(MapleBuilder, name), b))
    b._source_joints = lambda side: (((.434, .39), (.354, .39), (.274, .39)) if side == 'l'
                                    else ((.568, .39), (.648, .39), (.728, .39)))
    b._joints = lambda side: ((.434, .39), (.439, .465), (.495, .518)) if side == 'l' else (
        (.568, .39), (.572, .463), (.526, .518))
    b.parameter('ParamPoseClimb', keys=[-1, 0, 1])
    b.parameter('ParamArmPoseMode', 0, 1, keys=[0, .025, .05, 1])
    b.parameter('ParamFreeArmActive', 0, 1, keys=[0, .25, .5, .75, 1])
    add_parameters(b)
    b.warp(b.scene, None, 2)
    for side in ('l', 'r'):
        src = b._source_joints(side)
        common = b.scene
        if curved:
            common = b.warp('CurvedSupport_'+side, b.scene, 13)
            b.deform('ParamPoseClimb', common, lambda p, v:
                (p[0]+.8*(p[1]-.4)**2, p[1]+.04*math.sin(7*p[0])))
        for segment, index in (('upper', 0), ('lower', 1)):
            name = f'sleeve_{side}_{segment}'
            node = b.warp('Original_'+name, common, 17)
            # The two native parent grids use the same whole-arm field (V4's
            # actual contract). Distinct affine extensions of a common cut
            # split after nonlinear parent-grid resampling, even if their
            # analytic cut agrees. The painted mesh triangulations differ.
            for key in b.params['ParamPoseClimb'].keyforms:
                target = reference(side, int(key.value))
                def map_point(p):
                    return full_sleeve_point(p, src, target)
                key.deformer_offsets[node] = [tuple(q-p for p, q in zip(point, map_point(point)))
                    for point in b.nodes[node].grid_vertices]
            b.parts.append(Part(id=name, semantic_role=SemanticRole('accessory'), texture_id=name,
                                parent_deformer=node, draw_order=158))
            b.meshes[name] = grid_mesh(name, (0, 0, 1, 1), 4 if index else 3, 3)
        cuff = b.warp('ActiveCuffMaterial_'+side, 'Original_sleeve_'+side+'_lower', 3)
        b.nodes[cuff].grid_vertices = [(src[2][0]+p[0]-.5, src[2][1]+p[1]-.5)
                                       for p in b.nodes[cuff].grid_vertices]
        wrist = b._joints(side)[2]
        grip = (wrist[0], wrist[1]-.015)
        for name in [f'clean_brush_{side}', f'clean_hand_{side}_palm', f'clean_hand_{side}_fingers']:
            b.parts.append(Part(id=name, semantic_role=SemanticRole('accessory'), texture_id=name,
                                parent_deformer=b.scene, draw_order=165))
            b.meshes[name] = grid_mesh(name, (.2, .2, .8, .8), 3, 3)
            b.layer_by_id[name] = {'gripSourceUv': grip, 'tipSourceUv': (grip[0], grip[1]-.05)}
        for suffix in ('relaxed', 'support', 'grip_palm', 'grip_fingers'):
            b.parts.append(Part(id=f'hand_{side}_{suffix}', semantic_role=SemanticRole('accessory'),
                                texture_id='unused', parent_deformer=cuff, draw_order=164))
    return b


def original_painted_builder():
    """Small real-PNG ground/free component; no complete character/IR load."""
    from free_arm_refinement import original_sleeve_point
    b = small_builder()
    b.parameter('ParamFreeArmPose', 0, 1, keys=[0, .25, .5, .75, 1])
    for side in ('L', 'R'):
        b.parameter('ParamHand'+side+'Shape', -1, 1)
    folder = ROOT/'assets/authoring/revisions/v5-1-motion-polish-20260911'
    manifest = json.loads((folder/'layers.json').read_text(encoding='utf-8'))
    b.layer_by_id.update({p['id']: p for p in manifest['layers']})
    for side in ('l', 'r'):
        for name in ('arm_'+side, 'hand_'+side):
            parent = b.warp('OriginalGround_'+name, b.scene, 17)
            b.deform('ParamFreeArmPose', parent,
                     lambda point, value, side=side: original_sleeve_point(b, side, point, value))
            with Image.open(folder/b.layer_by_id[name]['file']) as painting:
                box = painting.getchannel('A').getbbox()
                box = tuple(value/painting.width for value in box)
            b.parts.append(Part(id=name, semantic_role=SemanticRole('accessory'),
                                texture_id=name, parent_deformer=parent, draw_order=162))
            b.meshes[name] = grid_mesh(name, box, 19, 19)
            b.opacity('ParamArmPoseMode', name, lambda value: max(0, 1-value/.05))
            if name.startswith('hand_'):
                b.opacity('ParamHand'+side.upper()+'Shape', name, lambda value: 1-abs(value))
    with Image.open(folder/b.layer_by_id['hand_r_relaxed']['file']) as painting:
        box = tuple(value/painting.width for value in painting.getchannel('A').getbbox())
    b.meshes['hand_r_relaxed'] = grid_mesh('hand_r_relaxed', box, 7, 7)
    return b


def test_inserted_fields_leave_unselected_support_chain_exact_and_preserve_material_wrist():
    b = small_builder()
    source = copy.deepcopy(b.nodes)
    original_uvs = {name: list(mesh.uvs) for name, mesh in b.meshes.items()}
    apply_brush_refinement(b)
    parts = {p.id: p for p in b.parts}
    for context in CONTEXTS:
        settings = {'ParamPoseClimb': context if context in (-1, 1) else 0,
                    'ParamCleanContext': context, 'ParamCleanStroke': 1}
        before = copy.copy(b); before.nodes = source
        before_sample = _posed_sampler(before, settings, b.scene)
        after_sample = _posed_sampler(b, settings, b.scene)
        for side in ('l', 'r'):
            for segment in ('upper', 'lower'):
                node = parts[f'sleeve_{side}_{segment}'].parent_deformer
                probes = [(.3, .4), (.7, .6), (.51, .49)]
                assert after_sample(node, probes) == pytest.approx(before_sample(node, probes), abs=1e-12)
        for side in ('l', 'r'):
            assert b.nodes['BrushGripFrame_'+side].parent == 'BrushWrist_'+side
            ancestor = b.nodes['BrushWrist_'+side].parent
            while ancestor.startswith('BrushWristTurn_'):
                ancestor = b.nodes[ancestor].parent
            assert ancestor == 'ActiveCuffMaterial_'+side
            names = [f'clean_brush_{side}', f'clean_hand_{side}_palm', f'clean_hand_{side}_fingers']
            assert len({parts[name].parent_deformer for name in names}) == 1
    for name, uvs in original_uvs.items():
        assert b.meshes[name].uvs[:len(uvs)] == uvs
    for node in b.nodes:
        axes = {p.id for p in b.params.values() if any(node in k.deformer_offsets or
                node in k.deformer_offset_weights for k in p.keyforms)}
        assert len(axes) <= 3


@pytest.mark.parametrize('curved', [False, True])
def test_native_composed_cleaning_fields_reach_actual_shoulder_elbow_and_wrist(curved):
    b = small_builder(curved=curved)
    apply_brush_refinement(b)
    parts = {p.id: p for p in b.parts}
    for context, side in [(c, s) for c in CONTEXTS for s in (('l', 'r') if c == 0 else (working_side(c),))]:
        ref = b.brush_references[side, context][0]
        for progress in (0., .15, .45, .75, 1.):
            settings = {'ParamPoseClimb': context if context in (-1, 1) else 0,
                        'ParamCleanContext': context, 'ParamCleanPose'+side.upper(): progress,
                        'ParamCleanStroke': 0}
            sample = _posed_sampler(b, settings, b.scene)
            actual = [sample(parts[f'sleeve_{side}_{"upper" if i < 2 else "lower"}'].parent_deformer,
                             [point])[0] for i, point in enumerate(b._source_joints(side))]
            assert actual == pytest.approx(np.asarray(clean_joints(ref, side, context, progress)), abs=2e-12)


def test_ground_whole_sleeve_keeps_painted_area_width_and_joint_order_through_entire_pickup():
    # Independent finite-difference material Jacobians: no PNG reshaping or
    # fabricated geometry expectation. A horizontal painted section keeps
    # >=75% width; vertical material cannot reverse at an elbow/cuff band.
    b = original_painted_builder()
    folder = ROOT/'assets/authoring/revisions/v5-1-motion-polish-20260911'
    for side in ('l', 'r'):
        ref = b._joints(side)
        with Image.open(folder/b.layer_by_id['arm_'+side]['file']) as painting:
            alpha = np.asarray(painting.getchannel('A'))
        rows, columns = np.where(alpha[::24, ::24] > 32)
        points = np.column_stack((columns*24+.5, rows*24+.5))/alpha.shape[0]
        for progress in np.linspace(0, 1, 41):
            target = ground_painted_joints(ref, side, progress)
            assert target[0][1] < target[1][1] < target[2][1]
            epsilon = 1e-5
            for point in points:
                origin = np.asarray(ground_material_point(point, ref, target, progress))
                horizontal = (np.asarray(ground_material_point(point+(epsilon, 0), ref, target, progress))-origin)/epsilon
                vertical = (np.asarray(ground_material_point(point+(0, epsilon), ref, target, progress))-origin)/epsilon
                assert horizontal[0] >= .74999
                assert np.linalg.det(np.column_stack((horizontal, vertical))) > .4


def test_ground_tool_stays_on_real_original_cuff_during_cleanup_to_drag_blends():
    b = original_painted_builder()
    original_nodes = copy.deepcopy(b.nodes)
    original_parents = {p.id: p.parent_deformer for p in b.parts}
    apply_brush_refinement(b)
    parts = {p.id: p for p in b.parts}
    for pose in (0., .25, .55, 1.):
        for free in (0., .2, .5, .8, 1.):
            for stroke in (-1., 0., 1.):
                settings = {'ParamCleanContext': 0, 'ParamCleanPoseL': pose,
                            'ParamCleanPoseR': pose, 'ParamCleanStroke': stroke,
                            'ParamFreeArmActive': free, 'ParamFreeArmPose': free,
                            'ParamArmPoseMode': 0}
                sample = _posed_sampler(b, settings, b.scene)
                wrist = b._joints('r')[2]
                arm = sample(parts['arm_r'].parent_deformer, [wrist])[0]
                hand = sample(parts['hand_r'].parent_deformer, [wrist])[0]
                tool = sample('GroundBrushCuff_r', [(.5, .5)])[0]
                assert arm == pytest.approx(hand, abs=2e-12)
                assert arm == pytest.approx(tool, abs=2e-12)
                if free == 1:
                    before = copy.copy(b); before.nodes = original_nodes
                    expected = _posed_sampler(before, settings, b.scene)
                    probes = [(.3, .44), wrist, (.35, .68), (.7, .7)]
                    for name in ('arm_l', 'hand_l', 'arm_r', 'hand_r'):
                        assert sample(parts[name].parent_deformer, probes) == pytest.approx(
                            expected(original_parents[name], probes), abs=2e-12)
    for node in b.nodes:
        axes = {p.id for p in b.params.values() if any(node in k.deformer_offsets or
                node in k.deformer_offset_weights for k in p.keyforms)}
        assert len(axes) <= 3


def test_right_tool_families_follow_visible_sleeve_mode_across_supported_to_drag_interrupts():
    b = original_painted_builder()
    apply_brush_refinement(b)
    # Use the actual generated drawable opacity keyforms. Context may change
    # ahead of arm mode during an interruption; it cannot hide both tools.
    def opacity(name, values):
        value = next(p.opacity for p in b.parts if p.id == name)
        for parameter in b.params.values():
            selection = _selection(parameter, values.get(parameter.id, parameter.default))
            if any(name in key.opacity_overrides for key, weight in selection):
                value *= sum(weight*key.opacity_overrides.get(name, 1) for key, weight in selection)
        return value
    for context in (0., .25, .75, 1., 1.5, 2.):
        for mode in (0., .0125, .025, .0375, .05, .3, 1.):
            for brush in (0., .2, .5, .8, 1.):
                values = {'ParamCleanContext': context, 'ParamArmPoseMode': mode, 'ParamCleanBrushR': brush}
                active = opacity('clean_hand_r_fingers', values)
                ground = opacity('clean_ground_hand_r_fingers', values)
                assert active+ground == pytest.approx(brush, abs=1e-12)
                for shape in (-1., -.5, 0., .5, 1.):
                    shaped = dict(values, ParamHandRShape=shape)
                    hands = opacity('hand_r', shaped)+opacity('clean_ground_hand_r_relaxed', shaped)+ground
                    assert hands == pytest.approx(opacity('arm_r', values), abs=1e-12)
                assert ground == pytest.approx(opacity('clean_ground_brush_r', values), abs=1e-12)


@pytest.mark.parametrize('curved', [False, True])
def test_actual_inserted_lattices_keep_the_full_elbow_seam_during_take_and_strokes(curved):
    b = small_builder(curved=curved)
    # First establish a valid, coincident source seam independently of the
    # cleaning map, including a genuinely curved support-material cut.
    parts = {p.id: p for p in b.parts}
    for context, side in [(c, s) for c in CONTEXTS for s in (('l', 'r') if c == 0 else (working_side(c),))]:
        source = b._source_joints(side)
        sample = _posed_sampler(b, {'ParamPoseClimb': context if context in (-1, 1) else 0}, b.scene)
        seam = [(source[1][0], source[1][1]+v) for v in (-.02, 0, .04, .12)]
        before = sample(parts[f'sleeve_{side}_upper'].parent_deformer, seam)
        assert before == pytest.approx(sample(parts[f'sleeve_{side}_lower'].parent_deformer, seam), abs=2e-12)
        if curved:
            chord = before[-1]-before[0]
            mid = before[2]-before[0]
            assert abs(chord[0]*mid[1]-chord[1]*mid[0]) > .00005
    apply_brush_refinement(b)
    for context, side in [(c, s) for c in CONTEXTS for s in (('l', 'r') if c == 0 else (working_side(c),))]:
        source = b._source_joints(side)
        upper = parts[f'sleeve_{side}_upper'].parent_deformer
        lower = parts[f'sleeve_{side}_lower'].parent_deformer
        for pose in (0, .15, .45, .7, 1):
            for stroke in (-1, 0, 1):
                settings = {'ParamPoseClimb': context if context in (-1, 1) else 0,
                    'ParamCleanContext': context, 'ParamCleanPose'+side.upper(): pose,
                    'ParamCleanStroke': stroke}
                sample = _posed_sampler(b, settings, b.scene)
                seam = [(source[1][0], source[1][1]+v) for v in (-.02, 0, .04, .12)]
                assert sample(upper, seam) == pytest.approx(sample(lower, seam), abs=2e-12)
                cuff = sample('ActiveCuffMaterial_'+side, [(.5, .5)])[0]
                wrist = sample(lower, [source[2]])[0]
                assert cuff == pytest.approx(wrist, abs=2e-12)


def test_wall_wrist_rotation_follows_the_mirrored_forearm_sweep():
    b = small_builder()
    apply_brush_refinement(b)
    key = next(k for k in b.params['ParamCleanStroke'].keyforms if k.value == 1)
    for context, label, sign in ((-1, 'left', 1), (1, 'right', -1)):
        side = working_side(context)
        node = b.nodes[f'BrushWristTurn_{label}_{side}']
        grid = np.asarray(node.grid_vertices)+key.deformer_offsets[node.id]
        tangent = grid[1]-grid[4]
        angle = math.degrees(math.atan2(tangent[0], -tangent[1]))
        assert angle == pytest.approx(sign*16, abs=1e-12)


def test_registered_tool_material_grip_stays_in_fist_and_bristles_point_into_open_space():
    b = small_builder()
    apply_brush_refinement(b)
    for context in CONTEXTS:
        side = working_side(context)
        names = ['clean_brush_'+side, f'clean_hand_{side}_fingers']
        settings = {'ParamPoseClimb': context if context in (-1, 1) else 0,
                    'ParamCleanContext': context, 'ParamCleanPose'+side.upper(): 1,
                    'ParamCleanBrush'+side.upper(): 1}
        def point(name, uv):
            mesh = b.meshes[name]
            index = next(i for i, p in enumerate(mesh.uvs) if math.dist(p, uv) < 1e-12)
            value = np.asarray(mesh.vertices[index], dtype=float).copy()
            for parameter in b.params.values():
                for key, weight in _selection(parameter, settings.get(parameter.id, parameter.default)):
                    if name in key.mesh_offsets:
                        value += np.asarray(key.mesh_offsets[name][index])*weight
            return value
        layer = b.layer_by_id[names[0]]
        grip = point(names[0], layer['gripSourceUv'])
        assert grip == pytest.approx(point(names[1], layer['gripSourceUv']), abs=1e-12)
        origin, up = _posed_sampler(b, settings, None)('BrushWrist_'+side,
                                                       [(.5, .5), (.5, .5-1e-5)])
        sine, negative_cosine = (up-origin)/np.linalg.norm(up-origin)
        matrix = np.asarray([[-negative_cosine, -sine], [sine, -negative_cosine]])
        direction = matrix @ (point(names[0], layer['tipSourceUv'])-grip)
        outward = -1 if context == 1 else 1
        assert outward*direction[0] > .9*np.linalg.norm(direction)


def test_native_rigid_grip_sources_have_one_common_unscaled_origin():
    b = small_builder()
    apply_brush_refinement(b)
    names = ['BrushGripFrame_l', 'BrushGripFrame_r']
    shared = _Shared()
    guids = {name: '#guid_'+name for name in b.nodes}
    emitted = _build_deformers(shared, SimpleNamespace(deformers=[b.nodes[n] for n in names]),
        1024, 1024, '#coord', {}, {}, '#part', guids, '#root')
    for side, (tag, ref) in zip(('l', 'r'), emitted):
        assert tag == 'CRotationDeformerSource'
        source = next(obj for obj in shared.objects if obj.get('xs.id') == ref)
        form = source.find('.//CRotationDeformerForm')
        assert [float(form.get(k)) for k in ('originX', 'originY', 'angle', 'scale')] == [512, 512, 0, 1]
        parent = source.find(".//CDeformerGuid[@xs.n='targetDeformerGuid']")
        assert parent.get('xs.ref') == guids['BrushWrist_'+side]
        assert not source.findall('.//KeyformBindingSource')


def test_visible_working_forearm_is_same_material_and_parent_with_complementary_visibility():
    b = small_builder()
    apply_brush_refinement(b)
    parts = {p.id:p for p in b.parts}
    for side in ('l', 'r'):
        original, visible = f'sleeve_{side}_lower', f'clean_sleeve_{side}_lower'
        assert parts[visible].parent_deformer == parts[original].parent_deformer
        assert parts[visible].texture_id == parts[original].texture_id
        assert parts[visible].draw_order == 158
        assert b.meshes[visible].uvs == b.meshes[original].uvs
        assert b.meshes[visible].triangles == b.meshes[original].triangles
        for key in b.params['ParamCleanBrush'+side.upper()].keyforms:
            assert key.opacity_overrides[visible]+key.opacity_overrides[original] == pytest.approx(1.)
            assert key.opacity_overrides[visible] == key.value
        for parameter in b.params.values():
            for key in parameter.keyforms:
                assert visible not in key.draw_order_overrides
                if original in key.mesh_offsets:
                    assert key.mesh_offsets[visible] == key.mesh_offsets[original]


def test_new_brush_grip_points_are_inside_original_finger_paint_and_assets_remain_exact():
    folder = ROOT/'assets/authoring/revisions/v5-1-motion-polish-20260911'
    manifest = json.loads((folder/'layers.json').read_text(encoding='utf8'))
    by_id = {layer['id']: layer for layer in manifest['layers']}
    for side in ('l', 'r'):
        brush = by_id['clean_brush_'+side]
        finger = by_id[f'clean_hand_{side}_fingers']
        x, y = [round(v*1024) for v in brush['gripSourceUv']]
        with Image.open(folder/finger['file']) as im:
            assert im.getpixel((x, y))[3] > 240
        with Image.open(folder/brush['file']) as im:
            assert im.getpixel((x, y))[3] > 240
            tip = tuple(round(v*1024) for v in brush['tipSourceUv'])
            assert im.getpixel(tip)[3] > 200
            # The bristle silhouette has enough transverse width to remain
            # legible near the pet's 384-pixel display scale.
            scan_y = round(brush['gripSourceUv'][1]*1024)-37
            opaque = [ix for ix in range(im.width) if im.getpixel((ix, scan_y))[3] > 200]
            assert max(opaque)-min(opaque) >= 30
        assert [by_id[f'clean_hand_{side}_palm']['draw_order'], brush['draw_order'], finger['draw_order']] == [164, 165, 166]
    for proof in manifest['brushProvenance']['hands'].values():
        assert hashlib.sha256((folder/proof['sourceFile']).read_bytes()).hexdigest() == proof['sha256']


def test_cleaning_metadata_exposes_visible_forearm_through_take_sweep_and_return():
    manifest = json.loads((ROOT/'assets/authoring/revisions/v5-1-motion-polish-20260911/layers.json').read_text(encoding='utf8'))
    states = ('clean_ground', 'clean_top', 'clean_climb_left', 'clean_climb_right')
    metadata = {'refinement': {'requiredParameters': []},
                'states': {state+suffix: {'anchors': {}} for state in states for suffix in ('', '_enter', '_exit')}}
    builder = SimpleNamespace(manifest=manifest, layer_by_id={layer['id']: layer for layer in manifest['layers']})
    add_metadata(metadata, builder)
    for state in states:
        public = metadata['states'][state]
        side = public['cleaningTool']['workSide']
        assert public['cleaningTool']['forearmDrawable'] == f'clean_sleeve_{side}_lower'
        for suffix in ('enter', 'exit'):
            internal = metadata['states'][state+'_'+suffix]
            assert internal['cleaningTool'] == public['cleaningTool']
            assert internal['cleaningTool'] is not public['cleaningTool']
            assert internal['anchors'] == public['anchors']


def test_ground_metadata_names_the_visible_original_sleeve_and_its_material_cuff():
    builder = original_painted_builder()
    apply_brush_refinement(builder)
    states = ('clean_ground', 'clean_top', 'clean_climb_left', 'clean_climb_right')
    metadata = {'refinement': {'requiredParameters': []},
                'states': {state+suffix: {'anchors': {}} for state in states for suffix in ('', '_enter', '_exit')}}
    add_metadata(metadata, builder)
    for suffix in ('', '_enter', '_exit'):
        state = metadata['states']['clean_ground'+suffix]
        assert state['cleaningTool']['forearmDrawable'] == 'arm_r'
        assert state['cleaningTool']['forearmKind'] == 'whole'
        assert state['cleaningTool']['armPoseMode'] == 0
        assert state['cleaningTool']['relaxedHandDrawable'] == 'clean_ground_hand_r_relaxed'
        assert state['cleaningTool']['cuffSourceUv'] == [.526, .518]
        assert state['anchors']['brushTip']['drawable'] == 'clean_ground_brush_r'
        assert state['anchors']['brushGrip']['drawable'] == 'clean_ground_brush_r'
        assert state['anchors']['freeHand']['drawable'] == 'clean_ground_hand_r_fingers'
    for source in ('clean_brush_r', 'clean_hand_r_palm', 'clean_hand_r_fingers'):
        alias = source.replace('clean_', 'clean_ground_', 1)
        assert builder.layer_by_id[alias]['file'] == builder.layer_by_id[source]['file']
        assert builder.meshes[alias].uvs == builder.meshes[source].uvs
