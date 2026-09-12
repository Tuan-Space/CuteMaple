"""Numerical guard tests; synthetic fixtures are not native acceptance evidence."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image
import pytest

from tools.verify_refined_motion import (ROOT, CORE, NODE_PROGRAM, SOLE_LIMIT, FEET,
    bind_samples, canvas_positions, curve_value, ground_point, inspect_sleep,
    inspect_handoff, inspect_free, motion_pose, painted_points, resource, sampling_plan, climb_leg_drawables)


def cubic(start=0, end=1):
    return {'Target': 'Parameter', 'Id': 'ParamPoseSleep',
            'Segments': [0, start, 1, 1/3, start, 2/3, end, 1, end]}


def test_restricted_cubic_and_duplicate_parameter_guard():
    c = cubic()
    assert curve_value(c, .5) == .5
    assert curve_value(c, 0) == 0 and curve_value(c, 1) == 1
    c['Segments'][3] = .2
    with pytest.raises(ValueError, match='Unrestricted'):
        curve_value(c, .5)
    with pytest.raises(ValueError, match='Duplicate'):
        motion_pose({'Meta': {'Duration': 1, 'AreBeziersRestricted': True}, 'Curves': [cubic(), cubic()]}, .5)


def test_resource_rejects_escape_and_missing_inputs(tmp_path):
    root = tmp_path / 'model'; root.mkdir()
    (tmp_path / 'outside.json').write_text('{}')
    with pytest.raises(ValueError, match='escapes'):
        resource(root, '../outside.json')
    with pytest.raises(FileNotFoundError):
        resource(root, 'missing.json')


def test_ground_curve_rejects_disappearing_drawable_and_duplicate_knots():
    p = {'ParamPoseSleep': .85}
    anchor = {'parameter': 'ParamPoseSleep', 'point': [.5, .93], 'points': [[0, .5, .93], [1, .5, .95]]}
    np.testing.assert_allclose(ground_point(anchor, p), [.5, .947])
    with pytest.raises(ValueError):
        ground_point({**anchor, 'drawable': 'skirt_sleep'}, p)
    with pytest.raises(ValueError):
        ground_point({**anchor, 'points': [[0, .5, .93], [0, .5, .94], [1, .5, .95]]}, p)


def test_native_uv_interpolation_and_coordinate_orientation():
    topology = {'textureUvs': [0, 0, 1, 0, 0, 1], 'triangles': [0, 1, 2]}
    indices, weights = bind_samples(topology, np.array([[.2, .3]]))
    np.testing.assert_array_equal(indices, [[0, 1, 2]])
    np.testing.assert_allclose(weights, [[.5, .2, .3]])
    with pytest.raises(ValueError, match='outside native'):
        bind_samples(topology, np.array([[.9, .9]]))
    p = canvas_positions([-.5, .5, 0, 0, .5, -.5],
        {'originX': 512, 'originY': 512, 'width': 1024, 'height': 1024, 'pixelsPerUnit': 1024})
    np.testing.assert_allclose(p, [[0, 0], [.5, .5], [1, 1]])


def test_paint_contours_keep_white_pixels_instead_of_color_keying(tmp_path):
    path = tmp_path / 'paint.png'
    rgba = np.zeros((12, 12, 4), dtype=np.uint8)
    rgba[3:9, 3:9] = [255, 255, 255, 255]
    Image.fromarray(rgba).save(path)
    region = {'sourceSize': [12, 12], 'translationToCanvas': [0, 0]}
    points = painted_points(path, region, [12, 12])
    assert len(points) == 20
    np.testing.assert_allclose(points.max(axis=0), [8.5/12, 8.5/12])


def sleep_fixture():
    item = {'positions': [-.1, -.35, .1, -.35, 0, -.4], 'opacity': 1}
    frame = {'id': 'sleep', 'parameters': {'ParamPoseSleep': .85, 'ParamBreath': 1},
             'drawables': {name: deepcopy(item) for name in (*FEET, 'skirt', 'skirt_sleep')}}
    info = {'originX': .5, 'originY': .5, 'width': 1, 'height': 1, 'pixelsPerUnit': 1}
    anchor = {'parameter': 'ParamPoseSleep', 'point': [.5, .9], 'points': [[0, .5, .9], [1, .5, .9]]}
    metadata = {'states': {state: {'anchors': {'ground': deepcopy(anchor)}}
                          for state in ('idle', 'sleep_enter', 'sleep_loop', 'sleep_exit')}}
    bindings = {name: (np.array([[0, 1, 2]]), np.array([[0, 0, 1]]))
                for name in frame['drawables']}
    return {'frames': [frame], 'canvasInfo': info}, {'sleep': {'group': 'sleep'}}, metadata, bindings


def test_sleep_rejects_visible_penetration_and_wrong_idle_completion_anchor():
    samples, targets, metadata, bindings = sleep_fixture()
    assert inspect_sleep(samples, targets, metadata, bindings)['passed']
    samples['frames'][0]['drawables']['leg_r']['positions'][-1] -= SOLE_LIMIT+.0001
    result = inspect_sleep(samples, targets, metadata, bindings)
    assert not result['passed'] and any('crosses ground' in x for x in result['errors'])
    metadata['states']['idle']['anchors']['ground']['points'][0][2] += .01
    with pytest.raises(ValueError, match='different ground anchors'):
        inspect_sleep(samples, targets, metadata, bindings)


def handoff_fixture(refined):
    """Synthetic legacy/refined leg families; no native acceptance evidence."""
    info = {'originX': .5, 'originY': .5, 'width': 1, 'height': 1, 'pixelsPerUnit': 1}
    names = [f'leg_{side}_{kind}' for side in ('l', 'r') for kind in ('climb_handoff', 'climb', 'climb_refined', 'swing')]
    base = {name: {'positions': [0, 0, .1, 0, 0, .1], 'opacity': float(name.endswith('handoff'))} for name in names}
    targets = {}; frames = []
    for side in ('left', 'right'):
        for suffix in ('start', 'handoff-000', 'handoff-060'):
            name = side+'-'+suffix
            targets[name] = {'group': 'handoff' if suffix.startswith('handoff') else 'reference'}
            drawables = deepcopy(base)
            if suffix == 'start':
                for mesh, item in drawables.items():
                    item['opacity'] = float(mesh.endswith('climb_refined' if refined else '_climb'))
            frames.append({'id': name, 'drawables': drawables})
    swing = deepcopy(base)
    for mesh, item in swing.items():
        item['opacity'] = float(mesh.endswith('_swing'))
    frames.append({'id': 'swing-start', 'drawables': swing}); targets['swing-start'] = {'group': 'reference'}
    pair = {f'leg_{s}_climb_handoff|leg_{s}_{kind}': [0, 1, 2] for s in ('l', 'r') for kind in ('climb', 'climb_refined', 'swing')}
    samples = {'frames': frames, 'canvasInfo': info, 'sourceVertexPairing': pair}
    metadata = {'locomotion': {'climb': {'handoffParameter': 'ParamClimbHandoff',
        'handoffDrawables': ['leg_l_climb_handoff', 'leg_r_climb_handoff']}}}
    if refined:
        metadata['locomotion']['climb'].update(refinedLegDrawables=['leg_l_climb_refined', 'leg_r_climb_refined'],
            refinedLegRoles={'leg_l_climb_refined': 'near', 'leg_r_climb_refined': 'far'})
    return samples, targets, metadata


@pytest.mark.parametrize('refined', (False, True))
def test_handoff_rejects_double_leg_and_endpoint_movement(refined):
    samples, targets, metadata = handoff_fixture(refined)
    frames = samples['frames']
    assert inspect_handoff(samples, targets, metadata)['passed']
    frames[1]['drawables']['leg_l_climb']['opacity'] = .5
    assert not inspect_handoff(samples, targets, metadata)['passed']
    frames[1]['drawables']['leg_l_climb']['opacity'] = 0
    frames[2]['drawables']['leg_r_climb_handoff']['positions'][0] += .003
    result = inspect_handoff(samples, targets, metadata)
    assert not result['passed'] and any('endpoint differs' in x for x in result['errors'])


@pytest.mark.parametrize('refined', (False, True))
@pytest.mark.parametrize('side,phase', [('left', .35), ('left', .7), ('right', .35), ('right', .7)])
def test_handoff_rejects_midcycle_third_leg_in_existing_pickup_sample(refined, side, phase):
    samples, targets, metadata = handoff_fixture(refined)
    reference = next(frame for frame in samples['frames'] if frame['id'] == side+'-start')
    frame = {'id': 'midcycle-pickup', 'drawables': deepcopy(reference['drawables'])}
    # Real pickup frames carry only leg opacity, not extra full leg geometry.
    frame['drawables'] = {name: {'opacity': item['opacity']} for name, item in frame['drawables'].items()}
    samples['frames'].append(frame)
    targets[frame['id']] = {'group': 'free', 'from': ['climb_'+side, phase],
                            'to': ['drag_'+side, 0], 'fraction': 0}
    result = inspect_handoff(samples, targets, metadata)
    assert result['passed']
    assert any(row['id'] == frame['id'] for row in result['samples'])
    extra = 'leg_l_climb' if refined else 'leg_l_climb_refined'
    frame['drawables'][extra]['opacity'] = 1
    result = inspect_handoff(samples, targets, metadata)
    assert not result['passed']
    assert result['errors'] == ['Native climbing cycle does not show exactly one opaque leg pair: '+frame['id']]
    # A real nonzero pickup blend is not a pure climbing-pair reference.
    targets[frame['id']]['fraction'] = .05
    assert inspect_handoff(samples, targets, metadata)['passed']


def test_declared_native_refined_legs_cannot_fall_back_to_hidden_legacy_artwork():
    metadata = {'refinement': {'nativeDrawableIds': ['leg_l_climb_refined', 'leg_r_climb_refined']}}
    with pytest.raises(ValueError, match='missing their motion contract'):
        climb_leg_drawables(metadata)
    metadata['locomotion'] = {'climb': {'refinedLegDrawables': ['leg_l_climb_refined', 'leg_r_climb_refined'],
        'refinedLegRoles': {'leg_l_climb_refined': 'far', 'leg_r_climb_refined': 'near'}}}
    with pytest.raises(ValueError, match='Invalid refined'):
        climb_leg_drawables(metadata)


def test_node_sampler_rejects_fake_moc_without_generating_native_evidence(tmp_path):
    moc = tmp_path / 'fake.moc3'; moc.write_bytes(b'not a moc file')
    request = tmp_path / 'request.json'
    output = tmp_path / 'native.json'
    request.write_text(json.dumps({'moc': str(moc), 'core': str(CORE), 'output': str(output)}))
    process = subprocess.run(['node', '-e', NODE_PROGRAM, str(request)], cwd=ROOT,
                             capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert process.returncode != 0
    assert 'Official Core rejected MOC3' in process.stderr
    assert not output.exists()


def free_contract():
    contacts = {s: {'hand': 'hand_'+s, 'cuff': 'arm_'+s, 'sourceUv': [.25, .25]} for s in ('l', 'r')}
    return {'refinement': {'freeContacts': {'parameter': 'ParamFreeArmActive', 'poseParameter': 'ParamFreeArmPose',
        'armPoseMode': 0, 'samePaintedSleevesThroughLanding': True, 'contacts': contacts},
        'contacts': {s: {'cuff': 'sleeve_'+s, 'hands': ['hand_'+s+'_support'], 'sourceUv': [.25, .25]} for s in ('l', 'r')}}}


def free_fixture():
    names = [prefix+s+suffix for s in ('l', 'r') for prefix, suffix in
             [('hand_', ''), ('arm_', ''), ('sleeve_', ''), ('hand_', '_support')]]
    topology = {'textureUvs': [0, 0, 1, 0, 0, 1], 'triangles': [0, 1, 2], 'textureIndex': 0}
    frame = {'id': 'fade', 'parameters': {'ParamArmPoseMode': 0},
             'drawables': {name: {'positions': [0, 0, 1, 0, 0, 1], 'opacity': 0 if name.endswith('support') or name.startswith('sleeve_') else 1}
                           for name in names}}
    samples = {'canvasInfo': {'originX': 0, 'originY': 0, 'pixelsPerUnit': 1, 'width': 1, 'height': 1},
               'topology': {name: topology for name in names}, 'frames': [frame]}
    targets = {'fade': {'group': 'free', 'freeEndpoint': True}}
    regions = {name: {'page': 0, 'sourceSize': [1, 1], 'translationToCanvas': [0, 0]} for name in names}
    return samples, targets, regions


def test_free_contact_uses_original_sleeve_and_rejects_actual_geometric_gap():
    samples, targets, regions = free_fixture()
    frame = samples['frames'][0]
    assert inspect_free(samples, targets, free_contract(), regions, np.array([1, 1]))['passed']
    frame['drawables']['hand_r']['positions'] = [.013, 0, 1.013, 0, .013, 1]
    result = inspect_free(samples, targets, free_contract(), regions, np.array([1, 1]))
    assert not result['passed'] and any('hand separates' in x for x in result['errors'])


@pytest.mark.parametrize('endpoint', (False, True))
def test_visible_free_hand_cannot_pass_against_an_invisible_cuff(endpoint):
    samples, targets, regions = free_fixture()
    targets['fade']['freeEndpoint'] = endpoint
    samples['frames'][0]['drawables']['arm_r']['opacity'] = 0
    result = inspect_free(samples, targets, free_contract(), regions, np.array([1, 1]))
    assert not result['passed'] and any('no visible cuff' in x for x in result['errors'])
    contact = next(row for row in result['samples'][0]['contacts'] if row['hand'] == 'hand_r')
    assert contact['handOpacity'] == 1 and contact['cuffOpacity'] == 0


@pytest.mark.parametrize('name,opacity,message', (
    ('hand_l', 0, 'Missing visible'), ('arm_l', .3, 'hides original'),
    ('hand_l_support', .2, 'shows donor'), ('sleeve_r', .2, 'shows donor'),
))
def test_free_endpoint_requires_both_original_contacts_and_hidden_donors(name, opacity, message):
    samples, targets, regions = free_fixture()
    samples['frames'][0]['drawables'][name]['opacity'] = opacity
    result = inspect_free(samples, targets, free_contract(), regions, np.array([1, 1]))
    assert not result['passed'] and any(message in x for x in result['errors'])


def test_sampling_plan_keeps_geometry_bounded_and_labels_fractional_stress():
    motion = {'Meta': {'Duration': 1, 'AreBeziersRestricted': True}, 'Curves': [cubic()]}
    names = ['idle', 'sleep_enter', 'sleep_exit', 'sleep_loop', 'climb_left', 'climb_right',
             'climb_to_top_left', 'climb_to_top_right', 'swing_cycle', 'happy',
             'drag_left', 'drag_right', 'fall_float', 'land', 'talk']
    contract = free_contract()
    for side, item in contract['refinement']['contacts'].items():
        item['hands'] = [f'hand_{side}_{shape}' for shape in ('relaxed', 'support', 'grip_palm')]
    frames = sampling_plan({name: motion for name in names}, contract)
    assert len({f['id'] for f in frames}) == len(frames)
    assert len(frames) < 650
    assert max(len(f['geometryNames']) for f in frames) <= 12
    assert {f['fraction'] for f in frames if f['id'].startswith('free-fade-0-')} == {0, .025, .05, .15, .4, .7, 1}
