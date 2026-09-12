"""The optional v4 audit must reject regressions even when pixels are nonempty."""
from tools.verify_maple_model import (inspect_refinement, inspect_contacts, inspect_necks, native_uv_point,
                                     atlas_pixels_match, handoff_opacities, record_native_identity, ANIMATIONS, TRANSITIONS,
                                     V5_PARAMETERS, V5_DRAWABLES, V5_CLEAN_SEGMENTS, capture_binding_valid,
                                     mesh_scale_ratio, mesh_principal_scales, v5_probe_definitions, inspect_v5)
from PIL import Image
import pytest
import json
from pathlib import Path


@pytest.mark.parametrize('mutation', ['valid', 'parameter-count', 'nonfinite', 'duplicate-mesh', 'old-states', 'unfinished'])
def test_v5_identity_requires_exact_actual_parameter_mesh_motion_sets(mutation):
    core = {'ParamHeadTurn', 'ParamBodyTurn', 'ParamHandLShape', 'ParamHandRShape', 'ParamTransitionProgress',
            'ParamArmPoseMode', 'ParamPoseSwing', 'ParamSwing', 'ParamSwingVisible'} | V5_PARAMETERS
    parameters = {name: 0 for name in core}
    parameters.update({f'fixture_{i}': 0 for i in range(67-len(parameters))})
    names = list(V5_DRAWABLES)+[f'fixture_mesh_{i}' for i in range(141-len(V5_DRAWABLES))]
    states = [*ANIMATIONS, *TRANSITIONS, *V5_CLEAN_SEGMENTS]
    report = {'ready': True, 'actualNative': True, 'nativeStates': states,
              'finished': dict.fromkeys(states, True), 'captures': {'baseline': {
                  'parameters': parameters, 'drawables': [{'id': name} for name in names]}}}
    if mutation == 'parameter-count': parameters.pop('fixture_0')
    elif mutation == 'nonfinite': parameters['fixture_0'] = float('nan')
    elif mutation == 'duplicate-mesh': report['captures']['baseline']['drawables'][-1]['id'] = names[0]
    elif mutation == 'old-states': states.pop()
    elif mutation == 'unfinished': report['finished'][V5_CLEAN_SEGMENTS[0]] = False
    record_native_identity(report, {'version': 5, 'requiredParameters': list(core)})
    assert report['modelRevision'] == ('v5' if mutation == 'valid' else None)


def test_motion_snapshot_binding_rejects_stale_generation_token_name_or_unsampled_pose():
    sample = {'generation': 4, 'playback': {'token': 8, 'name': 'clean_top_enter', 'evaluated': True, 'nativeUpdateCount': 1}}
    assert capture_binding_valid(sample, 4, 8, 'clean_top_enter')
    assert not capture_binding_valid(sample, 3, 8, 'clean_top_enter')
    assert not capture_binding_valid(sample, 4, 7, 'clean_top_enter')
    assert not capture_binding_valid(sample, 4, 8, 'clean_top_exit')
    sample['playback']['evaluated'] = False
    assert not capture_binding_valid(sample, 4, 8, 'clean_top_enter')


def test_sleep_extent_measurement_ignores_translation_rotation_but_detects_stretched_face():
    before = {'positions': [0, 0, 2, 0, 0, 1, 2, 1]}
    rotated = {'positions': [10, 20, 10, 22, 9, 20, 9, 22]}
    assert mesh_scale_ratio(before, rotated) == pytest.approx(1)
    assert mesh_principal_scales(before, rotated) == pytest.approx([1, 1])
    scaled = {'positions': [value*1.2 for value in rotated['positions']]}
    assert mesh_scale_ratio(before, scaled) == pytest.approx(1.2)
    with pytest.raises(ValueError, match='Collapsed'):
        mesh_scale_ratio(before, {'positions': [0]*8})
    square = {'positions': [-1, -1, -1, 1, 1, -1, 1, 1]}
    # 1.4**2 + .2**2 == 2: width/height distortion preserves RMS extent.
    stretched = {'positions': [-1.4, -.2, -1.4, .2, 1.4, -.2, 1.4, .2]}
    assert mesh_scale_ratio(square, stretched) == pytest.approx(1)
    assert mesh_principal_scales(square, stretched) == pytest.approx([1.4, .2])


def test_v5_probes_use_actual_authored_support_phases_and_do_not_require_visual_hash_on_planted_hand():
    folder = Path(__file__).resolve().parents[1]/'assets/authoring/revisions/v5/runtime'
    metadata = json.loads((folder/'Maple.pet.json').read_text(encoding='utf-8-sig'))
    endpoints = {path.name.removesuffix('.motion3.json'): {'start': {curve['Id']: curve['Segments'][1]
                 for curve in json.loads(path.read_bytes())['Curves']}} for path in (folder/'motions').glob('*.motion3.json')}
    probes, support = v5_probe_definitions(metadata, endpoints)
    assert len(support) == 16 and len(probes) == 27
    for name, case in support.items():
        assert case['phases'] == [sample['ParamClimbPhase'] for sample in probes[name]]
        near = metadata['locomotion']['climb']['nearHand'][case['direction']]
        assert case['side'] == (near if case['role'] == 'near' else 'l' if near == 'r' else 'r')
    metadata['locomotion']['climb']['phaseTravel'][2][1] = -1
    with pytest.raises(ValueError, match='travel table'):
        v5_probe_definitions(metadata, endpoints)


def test_nonempty_v5_report_without_actual_native_contracts_cannot_pass_any_core_gate():
    report = {'captures': {'baseline': {'visiblePixels': 50000}}, 'probes': {}}
    assert inspect_v5(report)
    assert not any(report['v5Checks'][name] for name in ('nativeIdentity', 'motionBindings', 'cleanupSegments',
                    'restContacts', 'climbSupport', 'sleepProportions', 'ribbonWind', 'fanSweep'))


def _rest_and_active_contact_report():
    def snapshot(active=False):
        drawables = []
        for side in ('l', 'r'):
            for suffix, visible in (('', not active), ('_relaxed', active), ('_support', False), ('_grip_palm', False)):
                drawables.append({'id': 'hand_'+side+suffix, 'opacity': int(visible)})
            drawables.extend([{'id': 'arm_'+side, 'opacity': int(not active)},
                              {'id': 'sleeve_'+side+'_lower', 'opacity': int(active)}])
        for item in drawables:
            item.update({'textureIndex': 0, 'textureUvs': [0, 0, 1, 0, 0, 1, 1, 1],
                         'triangles': [0, 1, 2, 1, 3, 2], 'positions': [0, 0, 1, 0, 0, 1, 1, 1]})
        return {'parameters': {'ParamArmPoseMode': int(active), 'ParamHandLShape': 0, 'ParamHandRShape': 0},
                'canvasInfo': {'width': 1, 'height': 1, 'pixelsPerUnit': 1}, 'drawables': drawables}
    names = [item['id'] for item in snapshot()['drawables']]
    captures = {**{name: snapshot() for name in ('idle', 'happy', 'talk', 'petting', 'sleep_loop')},
                **{name: snapshot(True) for name in ('drag_left', 'drag_right', 'fall_float', 'land-start')}}
    bindings = {}
    for token, name in enumerate(('drag_left', 'drag_right', 'fall_float', 'land'), 1):
        bindings[name] = {'generation': 1, 'token': token, 'name': name}
        key = 'land-start' if name == 'land' else name
        captures[key].update({'generation': 1, 'playback': {'token': token, 'name': name,
            'evaluated': True, 'nativeUpdateCount': 1}})
    return {'captures': captures, 'motionBindings': bindings,
            'probes': {'v5_rest_'+side: {phase: snapshot() for phase in ('before', 'after')} for side in ('l', 'r')},
            'atlasCoordinateMappingVerified': True,
            'atlasAudit': {'atlasSize': [1, 1], 'regions': [{'name': name, 'page': 0,
                'sourceSize': [1, 1], 'translationToCanvas': [0, 0]} for name in names]},
            'v5Contract': {'restContacts': {side: {'hand': 'hand_'+side, 'cuff': 'arm_'+side, 'sourceUv': [.5, .5]}
                                           for side in ('l', 'r')}},
            'handContract': {side: {'rest': ['hand_'+side, 'hand_'+side+'_relaxed']} for side in ('l', 'r')},
            'contactContract': {side: {'cuff': 'sleeve_'+side+'_lower', 'sourceUv': [.5, .5],
                'hands': ['hand_'+side+suffix for suffix in ('_relaxed', '_support', '_grip_palm')]}
                for side in ('l', 'r')}}


@pytest.mark.parametrize('mutation', ['valid', 'hidden-rest-hand', 'hidden-rest-cuff', 'missing-rest-probe',
    'hidden-active-hand', 'hidden-active-cuff', 'active-gap', 'wrong-active-mode', 'wrong-rest-mode',
    'missing-landing-start', 'stale-landing-start', 'hidden-landing-cuff', 'nonfinite-active-opacity'])
def test_rest_contacts_require_rest_poses_and_independent_active_drag_hands(mutation):
    report = _rest_and_active_contact_report()
    if mutation == 'nonfinite-active-opacity':
        next(item for item in report['captures']['land-start']['drawables'] if item['id'] == 'sleeve_r_lower')['opacity'] = float('nan')
    elif mutation == 'hidden-landing-cuff':
        next(item for item in report['captures']['land-start']['drawables'] if item['id'] == 'sleeve_r_lower')['opacity'] = .94
    elif mutation.startswith('hidden-'):
        _, mode, part = mutation.split('-')
        sample = report['captures']['drag_left' if mode == 'active' else 'happy']
        name = ('hand_l_relaxed' if mode == 'active' else 'hand_l') if part == 'hand' else (
            'sleeve_l_lower' if mode == 'active' else 'arm_l')
        next(item for item in sample['drawables'] if item['id'] == name)['opacity'] = .94
    elif mutation == 'missing-rest-probe':
        del report['probes']['v5_rest_l']['after']
    elif mutation == 'active-gap':
        hand = next(item for item in report['captures']['fall_float']['drawables'] if item['id'] == 'hand_r_relaxed')
        hand['positions'] = [value+.02 if i % 2 == 0 else value for i, value in enumerate(hand['positions'])]
    elif mutation == 'wrong-active-mode':
        report['captures']['drag_right']['parameters']['ParamArmPoseMode'] = 0
    elif mutation == 'wrong-rest-mode':
        report['captures']['talk']['parameters']['ParamArmPoseMode'] = 1
    elif mutation == 'missing-landing-start':
        del report['captures']['land-start']
    elif mutation == 'stale-landing-start':
        report['captures']['land-start']['playback']['token'] = 999
    errors = inspect_v5(report)
    assert report['v5Checks']['restContacts'] is (mutation == 'valid')
    if mutation == 'valid':
        assert set(report['v5Measurements']['restContacts']) == {'idle', 'happy', 'talk', 'petting', 'sleep_loop',
            'v5_rest_l-before', 'v5_rest_l-after', 'v5_rest_r-before', 'v5_rest_r-after'}
        assert set(report['v5Measurements']['activeMotionContacts']) == {'drag_left', 'drag_right', 'fall_float', 'land'}
        assert all(set(row['contacts']) == {'hand_l_relaxed', 'hand_r_relaxed'}
                   for row in report['v5Measurements']['activeMotionContacts'].values())
        assert report['v5Measurements']['activeMotionContacts']['land']['captureKey'] == 'land-start'
    elif mutation == 'active-gap':
        assert any('fall_float/hand_r_relaxed' in error and 'separates' in error for error in errors)


def test_climb_world_support_combines_real_uv_points_with_host_travel_and_detects_slip():
    names = ['hand_l_support', 'hand_r_support', 'cuff_l', 'cuff_r']
    def snapshot(phase, slip=0):
        return {'canvasInfo': {'width': 1, 'height': 1, 'pixelsPerUnit': 1}, 'drawables': [
            {'id': name, 'opacity': 1, 'textureIndex': 0, 'textureUvs': [0, 0, 1, 0, 0, 1, 1, 1],
             'triangles': [0, 1, 2, 1, 3, 2],
             'positions': [0, -.16*phase+slip, 1, -.16*phase+slip, 0, 1-.16*phase+slip, 1, 1-.16*phase+slip]}
            for name in names]}
    report = {'captures': {}, 'v5Contract': {}, 'atlasCoordinateMappingVerified': True,
              'atlasAudit': {'atlasSize': [1, 1], 'regions': [{'name': name, 'page': 0,
                  'sourceSize': [1, 1], 'translationToCanvas': [0, 0]} for name in names]},
              'contactContract': {side: {'cuff': 'cuff_'+side, 'hands': ['hand_'+side+'_support'],
                                       'sourceUv': [.5, .5]} for side in ('l', 'r')},
              'locomotion': {'climb': {'phaseTravel': [[0, 0], [1, 1]], 'risePerCycle': .16}},
              'climbProbeContract': {f'support_{i}': {'side': 'l' if i % 2 else 'r', 'phases': [0, .5]} for i in range(16)},
              'probes': {f'support_{i}': {'before': snapshot(0), 'after': snapshot(.5)} for i in range(16)}}
    inspect_v5(report)
    assert report['v5Checks']['climbSupport'] is True
    assert report['v5Measurements']['climbSupport']['support_0']['maximumAxisSlip'] < 1e-12
    report['probes']['support_0']['after'] = snapshot(.5, .02)
    errors = inspect_v5(report)
    assert report['v5Checks']['climbSupport'] is False
    assert any('slides in world space' in error for error in errors)


@pytest.mark.parametrize('missing', [None, 'ready', 'parameter', 'state', 'finished', 'metadata'])
def test_native_revision_identity_requires_actual_loaded_contract(missing):
    # Synthetic identity evidence only; this is never a model/artwork acceptance record.
    names = ['ParamHeadTurn', 'ParamBodyTurn', 'ParamHandLShape', 'ParamHandRShape',
             'ParamTransitionProgress', 'ParamArmPoseMode', 'ParamPoseSwing', 'ParamSwing', 'ParamSwingVisible']
    report = {'ready': True, 'actualNative': True, 'nativeStates': [*ANIMATIONS, *TRANSITIONS],
              'finished': {state: True for state in (*ANIMATIONS, *TRANSITIONS)},
              'captures': {'baseline': {'parameters': {name: 0 for name in names}}}}
    metadata = {'version': 4, 'requiredParameters': names}
    if missing == 'ready':
        report['ready'] = False
    elif missing == 'parameter':
        del report['captures']['baseline']['parameters'][names[0]]
    elif missing == 'state':
        report['nativeStates'].remove(TRANSITIONS[0])
    elif missing == 'finished':
        report['finished'][TRANSITIONS[0]] = False
    elif missing == 'metadata':
        metadata['version'] = 3
    record_native_identity(report, metadata)
    assert report['modelRevision'] == ('v4' if missing is None else None)


def _neck_report():
    views = {'front': 'neck', 'left': 'profile_l_neck', 'right': 'profile_r_neck',
             'leftMid': 'mid_l_neck', 'rightMid': 'mid_r_neck'}
    def snapshot(shown):
        return {'drawables': [{'id': name, 'opacity': int(view == shown), 'finiteVertices': True,
                                'vertexCount': 4, 'bounds': [-.1, .1, .1, .05]}
                               for view, name in views.items()]}
    return {'neckContract': {'materialParameter': 'ParamBodyTurn',
                             'geometryParameters': ['ParamHeadTurn', 'ParamBodyTurn'], 'views': views},
            'captures': {'baseline': snapshot('front')},
            'probes': {'neck_head_only': {'before': snapshot('front'), 'after': snapshot('front')},
                       'neck_body_only': {'before': snapshot('leftMid'), 'after': snapshot('rightMid')},
                       'neck_mixed_turn': {'before': snapshot('leftMid'), 'after': snapshot('rightMid')}}}


def test_neck_material_tracks_body_even_when_head_has_crossed_the_view_boundary():
    report = _neck_report()
    assert inspect_necks(report) == []
    # The former bug followed the frontal head instead of the middle-view body.
    items = report['probes']['neck_mixed_turn']['before']['drawables']
    items[0]['opacity'], items[3]['opacity'] = 1, 0
    assert any('does not follow body material' in error for error in inspect_necks(report))


def test_neck_contract_and_actual_exported_mesh_are_required():
    assert inspect_necks({}) == ['Missing or invalid native neck registration contract']
    report = _neck_report()
    report['captures']['baseline']['drawables'].pop(0)
    assert 'Missing native neck drawable: neck' in inspect_necks(report)
    report = _neck_report()
    report['probes']['neck_body_only']['after']['drawables'][4]['finiteVertices'] = False
    assert 'Invalid native visible neck geometry: neck_body_only/after' in inspect_necks(report)


def test_declared_local_material_blend_requires_opaque_lower_surface():
    contract = {'materialBlend': 'opaque-lower-surface-normal-over', 'materialBlendTurnWidth': .1}
    before = handoff_opacities('right_front_join', 'before', contract)
    after = handoff_opacities('right_front_join', 'after', contract)
    assert before[''] == after[''] == 1
    assert .48 < before['mid_r_'] < .5 < after['mid_r_'] < .52
    assert before['profile_r_'] == after['profile_r_'] == 0
    profile = handoff_opacities('right_profile_join', 'after', contract)
    assert profile['profile_r_'] == 1 and .48 < profile['mid_r_'] < .5
    report = {'captures': {}, 'probes': {}, 'turnContract': contract}
    for phase, weights in [('before', before), ('after', after)]:
        report['probes'].setdefault('right_front_join', {})[phase] = {'drawables': [
            {'id': prefix+family, 'opacity': opacity, 'drawOrder': 80,
             'renderOrder': 100 if prefix == 'mid_r_' else 10}
            for prefix, opacity in weights.items() for family in ('face_base', 'torso')]}
    errors = inspect_refinement(report)
    assert not any('right_front_join' in error and ('handoff visibility' in error or 'surface order' in error) for error in errors)
    # Equal authored orders are legal, but native tie resolution must put the
    # fading middle surface above the opaque front. Editor reverses ties in
    # some exports, so authored order or list insertion cannot prove this.
    front = report['probes']['right_front_join']['before']['drawables'][0]
    front['renderOrder'] = 101
    errors = inspect_refinement(report)
    assert 'Native view handoff surface order wrong: right_front_join/before/face_base' in errors
    front.pop('renderOrder')
    assert 'Native view handoff surface order wrong: right_front_join/before/face_base' in inspect_refinement(report)
    front['renderOrder'] = 10
    report['probes']['right_front_join']['before']['drawables'][0]['opacity'] = .5
    errors = inspect_refinement(report)
    assert any('Native view handoff visibility wrong: right_front_join/before/face_base' in error for error in errors)
    with pytest.raises(ValueError, match='Unsupported'):
        handoff_opacities('right_front_join', 'before', {**contract, 'materialBlendTurnWidth': float('nan')})


def test_middle_view_cannot_pass_with_a_profile_or_only_one_eye():
    phases = {}
    for phase, side in [('before', 'l'), ('after', 'r')]:
        items = [{'id': prefix+'face_base', 'opacity': int(prefix == f'mid_{side}_')}
                 for prefix in ('', 'mid_l_', 'mid_r_', 'profile_l_', 'profile_r_')]
        items += [{'id': f'mid_{side}_{feature}{eye}', 'opacity': 1}
                  for feature in ('eye_', 'pupil_') for eye in ('l', 'r')]
        phases[phase] = {'drawables': items}
    report = {'captures': {}, 'probes': {'mid_head_turn': phases}}
    errors = inspect_refinement(report)
    assert not any('mid_head_turn' in error and ('visibility wrong' in error or 'lost a visible eye' in error) for error in errors)
    phases['after']['drawables'][-1]['opacity'] = 0
    errors = inspect_refinement(report)
    assert any('Middle view lost a visible eye: mid_head_turn/after/mid_r_pupil_r' in error for error in errors)
    phases['before']['drawables'][1]['opacity'] = 0
    phases['before']['drawables'][3]['opacity'] = 1
    errors = inspect_refinement(report)
    assert any('Native middle view visibility wrong: mid_head_turn/before' in error for error in errors)


def test_missing_native_v4_contract_cannot_pass_on_nonempty_image_alone():
    report = {'captures': {'baseline': {'visiblePixels': 5000, 'parameters': {}, 'drawables': []}},
              'probes': {}, 'finished': {}, 'markers': {}}
    errors = inspect_refinement(report)
    assert any('native v4 parameter' in error for error in errors)
    assert any('transition did not finish' in error for error in errors)
    assert any('transition markers' in error for error in errors)
    assert any('clipping' in error for error in errors)
    assert any('draw order' in error for error in errors)
    assert any('hand drawable' in error for error in errors)
    assert any('seated artwork' in error for error in errors)


def test_reordered_markers_are_rejected_even_after_native_finished():
    report = {'captures': {}, 'probes': {}, 'finished': {'climb_to_top_left': True},
              'markers': {'climb_to_top_left': ['wall_release', 'top_grab', 'settled']}}
    errors = inspect_refinement(report)
    assert 'Missing, repeated or out-of-order native transition markers: climb_to_top_left' in errors


def test_three_hand_forms_at_once_are_detected_without_counting_split_grip_as_two():
    def capture(rest, support, palm, fingers):
        return {'drawables': [{'id': name, 'opacity': value, 'vertexCount': 4, 'bounds': [0, 0, 1, 1]}
                for name, value in zip(['hand_l', 'hand_l_support', 'hand_l_grip_palm', 'hand_l_grip_fingers'],
                                       [rest, support, palm, fingers])]}
    report = {'captures': {'baseline': capture(0, 0, 1, 1)}, 'probes': {}}
    assert not any('Multiple hand forms' in error for error in inspect_refinement(report))
    report['captures']['baseline'] = capture(.5, .5, 1, 1)
    assert any('Multiple hand forms' in error for error in inspect_refinement(report))


def test_native_uv_contact_survives_export_vertex_reordering_and_rejects_missing_geometry():
    # Positions are a rotated, translated UV quad, and vertices are reordered.
    mesh = {'textureUvs': [1, 1, 0, 0, 0, 1, 1, 0],
            'positions': [1, 4, 2, 3, 1, 3, 2, 4], 'triangles': [1, 3, 2, 3, 0, 2]}
    assert native_uv_point(mesh, [.25, .75]) == pytest.approx((1.25, 3.25))
    with pytest.raises(ValueError, match='outside'):
        native_uv_point(mesh, [1.5, .5])
    with pytest.raises(ValueError, match='topology'):
        native_uv_point({}, [.5, .5])


def test_native_contact_guard_detects_hand_separation_even_with_overlapping_bounds():
    def drawable(name, shift=0):
        return {'id': name, 'opacity': 1, 'textureIndex': 0, 'bounds': [0, 0, 2, 2],
                'textureUvs': [0, 0, 1, 0, 0, 1, 1, 1], 'triangles': [0, 1, 2, 1, 3, 2],
                'positions': [shift, 0, 1+shift, 0, shift, 1, 1+shift, 1]}
    report = {'contactContract': {side: {'cuff': 'cuff_'+side, 'hands': ['hand_'+side], 'sourceUv': [.5, .5]} for side in ('l', 'r')},
              'atlasCoordinateMappingVerified': True, 'atlasPixelsMatch': False, 'atlasAudit': {'atlasSize': [1, 1], 'regions': [
                  {'drawables': [name], 'sourceSize': [1, 1], 'translationToCanvas': [0, 0], 'page': 0}
                  for name in ('cuff_l', 'cuff_r', 'hand_l', 'hand_r')]}}
    snapshot = {'drawables': [drawable(name) for name in ('cuff_l', 'cuff_r', 'hand_l', 'hand_r')],
                'canvasInfo': {'width': 1024, 'height': 1024, 'pixelsPerUnit': 1024}}
    assert inspect_contacts(report, {'transition-wall_release': snapshot}) == []
    snapshot['drawables'][-1] = drawable('hand_r', .04)
    assert any('hand/cuff contact separates' in error for error in inspect_contacts(report, {'transition-wall_release': snapshot}))
    report['atlasCoordinateMappingVerified'] = False
    report['atlasPixelsMatch'] = True
    assert inspect_contacts(report, {'transition-wall_release': snapshot}) == ['Missing verified atlas placement and native contact contract']


def test_active_arm_mode_cannot_fall_back_to_rest_sleeves_at_transition_marker():
    name = 'climb_to_top_right-wall_release'
    report = {'captures': {name: {'parameters': {'ParamArmPoseMode': 0}, 'drawables': [
        {'id': 'arm_r', 'opacity': 1}, {'id': 'sleeve_r_lower', 'opacity': 0}]}}}
    errors = inspect_refinement(report)
    assert f'Attached pose lost active sleeves: {name}' in errors
    assert f'Native rest/active sleeves are not exclusive: {name}/r' in errors


def test_export_atlas_comparison_ignores_only_fully_transparent_rgb():
    first = Image.new('RGBA', (2, 1))
    first.putdata([(10, 20, 30, 255), (255, 0, 255, 0)])
    second = Image.new('RGBA', (2, 1))
    second.putdata([(10, 20, 30, 255), (0, 0, 0, 0)])
    assert atlas_pixels_match(first, second)
    second.putpixel((0, 0), (11, 20, 30, 255))
    assert not atlas_pixels_match(first, second)
    assert not atlas_pixels_match(first, second.resize((4, 2)))


def test_old_and_active_relaxed_hands_are_counted_from_metadata():
    report = {'handContract': {'l': {'rest': ['hand_l', 'hand_l_relaxed'], 'support': ['hand_l_support'],
                                   'grip': ['hand_l_grip_palm', 'hand_l_grip_fingers']}},
              'captures': {'baseline': {'drawables': [{'id': 'hand_l', 'opacity': 1}, {'id': 'hand_l_relaxed', 'opacity': 1}]}}}
    assert any('Multiple hand forms' in error for error in inspect_refinement(report))
    report['captures']['baseline']['drawables'][0]['opacity'] = 0
    assert not any('Multiple hand forms' in error for error in inspect_refinement(report))
