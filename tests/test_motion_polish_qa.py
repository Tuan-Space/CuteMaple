"""Small native-topology fixtures test new material gates without building a rig or launching Qt."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.native_model_contract import (BRUSH_PARAMETERS, POLISH_PARAMETERS, BRUSH_DRAWABLES,
    CLEAN_CONTEXTS, REFINED_MOTION_REVISION, brush_contract, declared_native_parameters,
    declared_native_drawables, motion_polish_enabled, physics_output_parameters, physics_resource_contract, climb_drape_exchange)
from tools.native_material_geometry import (measure_brush_sweep, measure_free_brace,
    measure_climb_materials, material_point, continuous_swing_targets, cleanup_endpoint_parameters, brush_forearm, measure_brush_transitions)
from tools.verify_maple_model import v5_probe_definitions, inspect_refinement
from tools.authoring.install_editor_export import resolve_material_anchors
from tools.validate_release import validate_v5_measurements


def metadata():
    refinement = {'version': 5, 'motionRevision': REFINED_MOTION_REVISION, 'motionPolishVersion': 1,
        'requiredParameters': sorted(POLISH_PARAMETERS), 'cleaningTool': {'type': 'brush',
        'parameters': sorted(BRUSH_PARAMETERS), 'contexts': {'-1': 'left', '0': 'ground', '1': 'right', '2': 'top'},
        'rigidGrip': True, 'legacyFanUsed': False, 'forearms': {side: {
            'drawable': f'clean_sleeve_{side}_lower', 'replaces': f'sleeve_{side}_lower',
            'visibilityParameter': 'ParamCleanBrush'+side.upper(), 'drawOrder': 158} for side in ('l', 'r')}},
        'climbMaterials': {direction: {'drawable': f'profile_{side}_skirt_climb_drape',
            'replaces': f'profile_{side}_skirt', 'underlay': f'profile_{side}_costume_underlay_skirt'}
            for direction, side in [('left', 'l'), ('right', 'r')]}}
    refinement['climbDrapeExchange'] = {'parameter': 'ParamClimbDrapeBlend', 'default': 0,
        'unfoldEndSeconds': .4, 'exchangeEndSeconds': .52, 'blend': 'opaque-receiving-surface-normal-over',
        'receivingOpacityGuard': .9999}
    parameters = POLISH_PARAMETERS | {'ParamFreeArmActive', 'ParamFreeArmPose', 'ParamClimbRefine'}
    refinement['freeContacts'] = {'parameter': 'ParamFreeArmActive', 'poseParameter': 'ParamFreeArmPose',
        'armPoseMode': 0, 'samePaintedSleevesThroughLanding': True, 'contacts': {
            side: {'cuff': 'arm_'+side, 'hand': 'hand_'+side, 'sourceUv': uv}
            for side, uv in [('l', [.495, .518]), ('r', [.526, .518])]}}
    refinement['nativeParameterIds'] = sorted(parameters | {f'ParamFixture{i}' for i in range(80-len(parameters))})
    names = BRUSH_DRAWABLES | {'face_base', 'torso', 'leg_l_climb_handoff', 'leg_r_climb_handoff'}
    names |= {name for group in refinement['climbMaterials'].values() for name in group.values()}
    refinement['nativeDrawableIds'] = sorted(names | {f'fixture{i}' for i in range(159-len(names))})
    states = {}
    for state, context in CLEAN_CONTEXTS.items():
        side = 'l' if context == -1 else 'r'
        prefix = 'clean_ground_' if context == 0 else 'clean_'
        anchors = {'brushTip': {'drawable': prefix+'brush_'+side, 'sourceUv': [.5, .8]},
            'brushGrip': {'drawable': prefix+'brush_'+side, 'sourceUv': [.5, .5]},
            'freeHand': {'drawable': prefix+'hand_'+side+'_fingers', 'sourceUv': [.5, .5]}}
        states[state] = {'anchors': anchors, 'cleaningTool': {'workSide': side, 'context': context,
            'supportSide': None if context == 0 else ('r' if side == 'l' else 'l'),
            'forearmDrawable': f'clean_sleeve_{side}_lower'}}
        if context == 0:
            states[state]['cleaningTool'].update(forearmDrawable='arm_r', forearmKind='whole',
                cuffSourceUv=[.526, .518], armPoseMode=0, relaxedHandDrawable='clean_ground_hand_r_relaxed')
        for suffix in ('enter', 'exit'):
            states[state+'_'+suffix] = deepcopy(states[state])
    return {'refinement': refinement, 'states': states}


def mesh(name, shift=0, opacity=1, order=0):
    return {'id': name, 'textureIndex': 0, 'opacity': opacity, 'drawOrder': order,
            'positions': [shift, 0, 1+shift, 0, shift, 1, 1+shift, 1],
            'textureUvs': [0, 0, 1, 0, 0, 1, 1, 1], 'triangles': [0, 1, 2, 1, 3, 2]}


def report_fixture():
    data = metadata()
    report = {'v5Contract': data['refinement'], 'stateContracts': data['states'], 'probes': {},
              'atlasCoordinateMappingVerified': True,
              'contactContract': {side: {'cuff': f'sleeve_{side}_lower', 'hands': ['hand_'+side], 'sourceUv': [.5, .5]}
                                  for side in ('l', 'r')}}
    names = set()
    for state, context in CLEAN_CONTEXTS.items():
        side = 'l' if context == -1 else 'r'
        other = 'r' if side == 'l' else 'l'
        for index, pair in enumerate(((-1, 0), (0, 1))):
            probe = {}
            for phase, stroke in zip(('before', 'after'), pair):
                shift = stroke*.02
                drawables = [mesh('clean_brush_'+side, shift, order=165),
                    mesh('clean_hand_'+side+'_palm', shift, order=164),
                    mesh('clean_hand_'+side+'_fingers', shift, order=166),
                    mesh(f'sleeve_{side}_lower', shift, opacity=0), mesh('hand_'+side, opacity=0),
                    mesh(f'clean_sleeve_{side}_lower', shift, order=158),
                    mesh(f'clean_sleeve_{other}_lower', opacity=0, order=158),
                    mesh(f'sleeve_{other}_lower'), mesh('hand_'+other), mesh('clean_brush_'+other, opacity=0),
                    mesh('ribbon_l', order=110), mesh('ribbon_r', order=114),
                    mesh('clean_fan_l', opacity=0), mesh('clean_fan_r', opacity=0)]
                drawables += [mesh(f'clean_hand_{other}_{part}', opacity=0, order=164+i*2)
                              for i, part in enumerate(('palm', 'fingers'))]
                drawables += [mesh(f'sleeve_{s}_upper', opacity=int(context != 0)) for s in ('l', 'r')]
                drawables += [mesh('arm_'+s, shift if s == side else 0, opacity=int(context == 0), order=75)
                              for s in ('l', 'r')]
                drawables += [mesh(name, shift, opacity=int(context == 0), order=order) for name, order in
                              [('clean_ground_hand_r_palm', 164), ('clean_ground_brush_r', 165),
                               ('clean_ground_hand_r_fingers', 166)]]
                drawables.append(mesh('clean_ground_hand_r_relaxed', shift, opacity=0, order=164))
                if context == 0:
                    for item in drawables:
                        if item['id'].startswith(('sleeve_', 'clean_sleeve_', 'clean_hand_', 'clean_brush_')):
                            item['opacity'] = 0
                names.update(item['id'] for item in drawables)
                probe[phase] = {'drawables': drawables, 'canvasInfo': {'pixelsPerUnit': 1000, 'width': 1000, 'height': 1000},
                    'parameters': {'ParamCleanBrush'+side.upper(): 1, 'ParamCleanBrush'+other.upper(): 0,
                                   'ParamArmPoseMode': int(context != 0),
                                   'ParamHandRShape': int(context == 0), 'ParamHandLShape': 0}}
            report['probes'][f'v51_brush_{state}_{index}'] = probe
    report['atlasAudit'] = {'atlasSize': [1, 1], 'regions': [
        {'name': name, 'page': 0, 'sourceSize': [1, 1], 'translationToCanvas': [0, 0], 'crop': [0, 0, 1, 1]}
        for name in names]}
    return report


def test_version_branch_and_exact_new_inventory():
    value = metadata()['refinement']
    assert len(declared_native_parameters(value)) == 80
    assert len(declared_native_drawables(value)) == 159
    for key, missing in [('nativeParameterIds', 'ParamFreeArmBrace'), ('nativeParameterIds', 'ParamClimbDrapeBlend'),
                         ('nativeDrawableIds', 'clean_brush_r'),
                         ('nativeDrawableIds', 'profile_l_skirt_climb_drape'), ('nativeDrawableIds', 'clean_sleeve_r_lower'),
                         ('nativeDrawableIds', 'clean_ground_brush_r'), ('nativeDrawableIds', 'clean_ground_hand_r_relaxed')]:
        bad = deepcopy(value); bad[key].remove(missing)
        with pytest.raises(ValueError):
            (declared_native_parameters if key == 'nativeParameterIds' else declared_native_drawables)(bad)
    for marker in (True, '1', 2):
        with pytest.raises(ValueError): motion_polish_enabled({'motionPolishVersion': marker})
    assert not motion_polish_enabled({})


@pytest.mark.parametrize('mutation', ['tip', 'registration', 'transition', 'transition-work', 'fan', 'support'])
def test_brush_contract_rejects_ambiguous_or_stale_material_definitions(mutation):
    data = metadata(); state = data['states']['clean_ground']
    if mutation == 'tip': state['anchors']['brushTip'] = deepcopy(state['anchors']['brushGrip'])
    elif mutation == 'registration': state['anchors']['freeHand']['sourceUv'][0] = .6
    elif mutation == 'transition': data['states']['clean_ground_exit']['anchors'].pop('brushTip')
    elif mutation == 'transition-work': data['states']['clean_ground_exit'].pop('cleaningTool')
    elif mutation == 'fan': state['anchors']['fanTip'] = {'drawable': 'clean_fan_r'}
    else: state['cleaningTool']['supportSide'] = 'l'
    with pytest.raises(ValueError): brush_contract(data)


def test_installer_resolves_both_brush_and_fingers_without_losing_material_registration():
    data, report = metadata(), report_fixture()
    names = sorted({region['name'] for region in report['atlasAudit']['regions']})
    installed, receipt = resolve_material_anchors(data, report['atlasAudit'],
        {'drawables': names, 'textureIndices': [0]*len(names)}, {'verified': True})
    assert len(receipt) == 36 and brush_contract(installed)
    report['stateContracts'] = installed['states']
    assert measure_brush_sweep(report)['clean_ground']['nativeBristleDistance'] == pytest.approx(.04)
    assert data['states']['clean_ground']['anchors']['brushTip']['sourceUv'] == [.5, .8]


@pytest.mark.parametrize('mutation', ['none', 'gap', 'support', 'no-sweep', 'hidden', 'fan', 'order', 'page', 'atlas', 'cuff'])
def test_brush_measures_actual_topology_and_preserves_existing_contact_limits(mutation):
    report = report_fixture()
    snapshot = report['probes']['v51_brush_clean_ground_1']['after']
    items = {item['id']: item for item in snapshot['drawables']}
    if mutation in ('gap', 'support', 'cuff'):
        key = {'gap': 'clean_ground_hand_r_fingers', 'support': 'hand_l', 'cuff': 'arm_r'}[mutation]
        items[key]['positions'][::2] = [v+.02 for v in items[key]['positions'][::2]]
        if mutation == 'support': items['arm_l']['positions'][::2] = items[key]['positions'][::2]
    elif mutation == 'no-sweep':
        for name, probe in report['probes'].items():
            for phase, sample in probe.items():
                for item in sample['drawables']:
                    item['positions'] = [0, 0, 1, 0, 0, 1, 1, 1]
    elif mutation == 'hidden': items['clean_ground_brush_r']['opacity'] = 0
    elif mutation == 'fan': items['clean_fan_r']['opacity'] = 1
    elif mutation == 'order': items['clean_ground_brush_r']['drawOrder'] = 170
    elif mutation == 'page': items['clean_ground_brush_r']['textureIndex'] = 1
    elif mutation == 'atlas': report['atlasCoordinateMappingVerified'] = False
    if mutation == 'none':
        measured = measure_brush_sweep(report)
        assert set(measured) == set(CLEAN_CONTEXTS)
        assert measured['clean_ground']['supportSide'] is None
        assert measured['clean_ground']['nonWorkingHandMovement'] == 0
        assert all(row['gripGap'] == 0 for row in measured['clean_ground']['samples'])
    else:
        with pytest.raises(ValueError): measure_brush_sweep(report)


def test_climb_cycle_probes_branch_to_one_second_and_add_real_brush_and_recovery_samples():
    folder = Path(__file__).resolve().parents[1]/'assets/authoring/revisions/v5/runtime'
    data = json.loads((folder/'Maple.pet.json').read_bytes())
    endpoints = {path.name.removesuffix('.motion3.json'): {'start': {curve['Id']: curve['Segments'][1]
        for curve in json.loads(path.read_bytes())['Curves']}} for path in (folder/'motions').glob('*.motion3.json')}
    assert len(v5_probe_definitions(data, endpoints)[0]) == 27
    modern = metadata(); data['refinement'] = modern['refinement']; data['states'] = modern['states']
    data['locomotion']['climb'].update(refinementParameter='ParamClimbRefine', cycleDuration=1., risePerCycle=.12)
    for side in ('left', 'right'):
        endpoints['climb_'+side]['start'].update(ParamClimbRefine=1, ParamClimbDrapeBlend=1)
    for state, context in CLEAN_CONTEXTS.items():
        side = 'L' if context == -1 else 'R'; other = 'R' if side == 'L' else 'L'
        endpoints[state]['start'].update({'ParamCleanContext': context, 'ParamCleanPose'+side: 1,
            'ParamCleanBrush'+side: 1, 'ParamCleanBrush'+other: 0, 'ParamArmPoseMode': int(context != 0),
            'ParamClimbDrapeBlend': int(context in (-1, 1))})
    endpoints['fall_float']['start'].update(dict.fromkeys(('ParamFreeArmActive', 'ParamFreeArmPose', 'ParamFreeArmBrace'), 1))
    probes, support = v5_probe_definitions(data, endpoints)
    assert len(probes) == 35 and len(support) == 16
    assert not any(name.startswith('v5_fan') for name in probes)
    endpoints['climb_left']['start']['ParamClimbDrapeBlend'] = 0
    with pytest.raises(ValueError, match='drape'): v5_probe_definitions(data, endpoints)
    endpoints['climb_left']['start']['ParamClimbDrapeBlend'] = 1
    endpoints['clean_climb_left']['start']['ParamClimbDrapeBlend'] = 0
    with pytest.raises(ValueError, match='working pose'): v5_probe_definitions(data, endpoints)
    endpoints['clean_climb_left']['start']['ParamClimbDrapeBlend'] = 1
    data['locomotion']['climb']['cycleDuration'] = 1.6
    with pytest.raises(ValueError, match='climbing'): v5_probe_definitions(data, endpoints)


def test_continuous_swing_uses_same_capture_phase_instead_of_authored_static_zero():
    snapshot = {'playback': {'swingPhase': 1.5707963267948966, 'swingAmplitude': .2,
        'swingActive': 1, 'swingPettingWeight': 0}}
    assert continuous_swing_targets(snapshot) == pytest.approx({'ParamSwing': .2, 'ParamAngleZ': -.6, 'ParamLegLA': .8, 'ParamLegRA': .8})
    snapshot['playback']['swingPhase'] = float('nan')
    with pytest.raises(ValueError): continuous_swing_targets(snapshot)


PHYSICS_OUTPUTS = sorted(['ParamHairFront', 'ParamHairSide', 'ParamHairBack', 'ParamSkirt', 'ParamSleeveLHang', 'ParamSleeveRHang'])


def physics_endpoint_fixture():
    names = [*PHYSICS_OUTPUTS, 'ParamRibbonLX', 'ParamCleanPoseR', 'ParamClimbRefine', 'ParamFreeArmBrace']
    targets = dict.fromkeys(names, 0.)
    parameters = {**targets, **dict.fromkeys(PHYSICS_OUTPUTS, -.0434080623)}
    sample = {'parameters': parameters, 'motionPhysicsValues': dict.fromkeys(PHYSICS_OUTPUTS, 0)}
    key, digest = 'assets/live2d/Maple/Maple.physics3.json', 'a'*64
    report = {'v5Contract': {'motionPolishVersion': 1},
        'physicsOutputs': {'resource': key, 'sha256': digest, 'parameters': list(PHYSICS_OUTPUTS)},
        'captures': {'clean_ground_enter-end': sample},
        'motionEndpoints': {'clean_ground_enter': {'end': targets}}}
    for field in ('resourceHashesAtStart', 'resourceHashesLoaded', 'resourceHashesAtEnd'):
        report[field] = {key: digest}
    return report, sample


@pytest.mark.parametrize('parameter', PHYSICS_OUTPUTS)
@pytest.mark.parametrize('wrong_motion', [False, True])
def test_all_declared_physics_outputs_keep_strict_authored_endpoint_while_actual_physics_moves(parameter, wrong_motion):
    report, sample = physics_endpoint_fixture()
    if wrong_motion:
        sample['motionPhysicsValues'][parameter] = .031
        sample['parameters'][parameter] = 0 # A visually zero value cannot hide the wrong authored endpoint.
        with pytest.raises(ValueError, match='Authored physics endpoint'):
            cleanup_endpoint_parameters(report, 'clean_ground_enter', 'end')
    else:
        result = cleanup_endpoint_parameters(report, 'clean_ground_enter', 'end')
        assert set(result['physicsOutputs']) == set(PHYSICS_OUTPUTS)
        assert result['physicsOutputs'][parameter]['physicsValue'] == -.0434080623
        assert result['maximumParameterError'] == 0 and result['parametersCompared'] == 10


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'nonfinite', 'hash', 'ribbon', 'support', 'invented-support-output'])
def test_physics_endpoint_cannot_exempt_undeclared_or_support_parameters(mutation):
    report, sample = physics_endpoint_fixture()
    if mutation == 'missing': sample['motionPhysicsValues'].pop('ParamSleeveLHang')
    elif mutation == 'extra': sample['motionPhysicsValues']['ParamRibbonLX'] = 0
    elif mutation == 'nonfinite': sample['parameters']['ParamSkirt'] = float('nan')
    elif mutation == 'hash': report['resourceHashesAtEnd']['assets/live2d/Maple/Maple.physics3.json'] = 'b'*64
    elif mutation == 'ribbon': sample['parameters']['ParamRibbonLX'] = .031
    elif mutation == 'support': sample['parameters']['ParamCleanPoseR'] = .031
    else:
        report['physicsOutputs']['parameters'] = sorted([*PHYSICS_OUTPUTS, 'ParamCleanPoseR'])
        sample['motionPhysicsValues']['ParamCleanPoseR'] = 0
    with pytest.raises(ValueError): cleanup_endpoint_parameters(report, 'clean_ground_enter', 'end')


def test_physics_declaration_is_reopened_from_actual_model_resource(tmp_path):
    definition = {'Version': 3, 'PhysicsSettings': [
        {'Output': [{'Destination': {'Target': 'Parameter', 'Id': name}}]} for name in PHYSICS_OUTPUTS]}
    assert physics_output_parameters(definition, PHYSICS_OUTPUTS) == PHYSICS_OUTPUTS
    path = tmp_path/'physics.json'; path.write_text(json.dumps(definition), encoding='utf-8')
    settings = {'FileReferences': {'Physics': 'physics.json'}}
    before = physics_resource_contract(tmp_path, settings, PHYSICS_OUTPUTS)
    assert before['parameters'] == PHYSICS_OUTPUTS
    definition['PhysicsSettings'].pop()
    path.write_text(json.dumps(definition), encoding='utf-8')
    after = physics_resource_contract(tmp_path, settings, PHYSICS_OUTPUTS)
    assert after['sha256'] != before['sha256'] and after['parameters'] != before['parameters']


@pytest.mark.parametrize('mutation', ['none', 'old-still-visible', 'new-hidden', 'support-duplicated', 'far-order', 'wrong-parameter', 'missing-contract'])
def test_brush_forearm_uses_visible_replacement_and_preserves_support_depth(mutation):
    report = report_fixture(); sample = report['probes']['v51_brush_clean_top_0']['before']
    parts = {item['id']: item for item in sample['drawables']}
    if mutation == 'old-still-visible': parts['sleeve_r_lower']['opacity'] = 1
    elif mutation == 'new-hidden': parts['clean_sleeve_r_lower']['opacity'] = 0
    elif mutation == 'support-duplicated': parts['clean_sleeve_l_lower']['opacity'] = 1
    elif mutation == 'far-order': parts['clean_sleeve_r_lower']['drawOrder'] = 75
    elif mutation == 'wrong-parameter': sample['parameters']['ParamCleanBrushR'] = 0
    elif mutation == 'missing-contract': report['v5Contract']['cleaningTool'].pop('forearms')
    if mutation == 'none':
        row = measure_brush_sweep(report)['clean_top']['samples'][0]
        assert row['workingForearm']['drawable'] == 'clean_sleeve_r_lower'
        assert row['workingForearm']['originalOpacity'] == 0
        assert row['nonWorkingForearm']['drawable'] == 'sleeve_l_lower'
        assert row['workingCuff']['distance'] == 0
        # Moving only hidden old geometry must not substitute for measuring
        # the actual foreground sleeve touching the brush hand.
        parts['sleeve_r_lower']['positions'] = [v+.2 for v in parts['sleeve_r_lower']['positions']]
        assert measure_brush_sweep(report)['clean_top']['samples'][0]['workingCuff']['distance'] == 0
    else:
        with pytest.raises(ValueError): measure_brush_sweep(report)


@pytest.mark.parametrize('mutation', ['none', 'active-mode', 'hidden-ground-arm', 'active-sleeve',
    'wrong-ground-cuff', 'extra-active-tool', 'extra-ground-tool-on-top', 'extra-relaxed-hand'])
def test_ground_brush_uses_original_painted_cuff_and_context_excludes_other_tools(mutation):
    report = report_fixture()
    sample = report['probes']['v51_brush_clean_ground_0']['before']
    parts = {item['id']: item for item in sample['drawables']}
    if mutation == 'active-mode': sample['parameters']['ParamArmPoseMode'] = 1
    elif mutation == 'hidden-ground-arm': parts['arm_r']['opacity'] = 0
    elif mutation == 'active-sleeve': parts['sleeve_r_upper']['opacity'] = 1
    elif mutation == 'wrong-ground-cuff':
        parts['arm_r']['positions'][::2] = [v+.02 for v in parts['arm_r']['positions'][::2]]
    elif mutation == 'extra-active-tool': parts['clean_hand_r_palm']['opacity'] = 1
    elif mutation == 'extra-relaxed-hand': parts['clean_ground_hand_r_relaxed']['opacity'] = .2
    elif mutation == 'extra-ground-tool-on-top':
        top = {item['id']: item for item in report['probes']['v51_brush_clean_top_0']['before']['drawables']}
        top['clean_ground_brush_r']['opacity'] = 1
    if mutation == 'none':
        # A hidden active donor can move without invalidating the genuine
        # original cuff. Its coordinates must never stand in for arm_r.
        for name in ('sleeve_r_lower', 'clean_sleeve_r_lower'):
            parts[name]['positions'] = [v+100 for v in parts[name]['positions']]
        row = measure_brush_sweep(report)['clean_ground']['samples'][0]
        assert row['workingForearm']['drawable'] == 'arm_r'
        assert row['nonWorkingForearm']['drawable'] == 'arm_l'
        assert row['nonWorkingHand'] == 'hand_l'
        assert row['workingCuff']['distance'] == 0
    else:
        with pytest.raises(ValueError): measure_brush_sweep(report)


@pytest.mark.parametrize('mutation', ['old-cuff', 'wrong-uv', 'active-mode', 'old-brush', 'old-relaxed-hand', 'missing-original-contact'])
def test_ground_metadata_cannot_claim_the_hidden_active_sleeve_or_an_unbound_material_point(mutation):
    data = metadata()
    ground = data['states']['clean_ground']
    if mutation == 'old-cuff': ground['cleaningTool']['forearmDrawable'] = 'clean_sleeve_r_lower'
    elif mutation == 'wrong-uv': ground['cleaningTool']['cuffSourceUv'] = [.728, .39]
    elif mutation == 'active-mode': ground['cleaningTool']['armPoseMode'] = 1
    elif mutation == 'old-brush': ground['anchors']['brushGrip']['drawable'] = 'clean_brush_r'
    elif mutation == 'old-relaxed-hand': ground['cleaningTool']['relaxedHandDrawable'] = 'hand_r_relaxed'
    else: data['refinement']['freeContacts']['contacts']['l']['cuff'] = 'sleeve_l_lower'
    with pytest.raises(ValueError): brush_contract(data)


def test_attached_cleaning_visibility_guard_accepts_only_the_declared_foreground_lower():
    report = report_fixture(); sample = deepcopy(report['probes']['v51_brush_clean_top_0']['before'])
    sample['parameters']['ParamArmPoseMode'] = 1
    sample['drawables'] += [mesh('arm_'+side, opacity=0) for side in ('l', 'r')]
    sample['drawables'] += [mesh(f'sleeve_{side}_upper') for side in ('l', 'r')]
    report['captures'] = {'clean_top': sample}
    # This intentionally incomplete report still fails unrelated full QA;
    # only the precise old visibility assumption is under test here.
    errors = inspect_refinement(report)
    assert not any('sleeves are not exclusive: clean_top' in value or 'Invalid visible cleaning forearm' in value for value in errors)
    report['v5Contract'].pop('motionPolishVersion')
    assert any('sleeves are not exclusive: clean_top/r' in value for value in inspect_refinement(report))


@pytest.mark.parametrize('extra_hand', ['hand_r', 'clean_ground_hand_r_relaxed'])
def test_duplicate_hand_guard_counts_the_ground_brush_hand_once_but_alongside_the_original(extra_hand):
    report = report_fixture()
    sample = deepcopy(report['probes']['v51_brush_clean_ground_0']['before'])
    sample['screenshot'] = 'ground-overlap-fixture'
    report['captures'] = {'clean_ground': sample}
    assert not any('Multiple hand forms' in error for error in inspect_refinement(report))
    next(item for item in sample['drawables'] if item['id'] == extra_hand)['opacity'] = .2
    assert any('Multiple hand forms remain visible simultaneously: r/ground-overlap-fixture' in error
               for error in inspect_refinement(report))


def transition_report_fixture():
    report = report_fixture()
    report.update(captures={}, motionBindings={})
    for index, (public, context) in enumerate(CLEAN_CONTEXTS.items()):
        work = 'l' if context == -1 else 'r'
        for suffix in ('enter', 'exit'):
            name = public+'_'+suffix
            binding = {'name': name, 'token': index+1, 'generation': 2}
            report['motionBindings'][name] = binding
            for phase, progress in [('start', 0), ('interior', .5), ('end', 1)]:
                value = progress if suffix == 'enter' else 1-progress
                sample = deepcopy(report['probes']['v51_brush_'+public+'_0']['before'])
                sample.update(generation=2, playback={**binding, 'evaluated': True, 'nativeUpdateCount': 5})
                # Ground preserves the actual original sleeve throughout; only
                # its crossed hand, complete relaxed hand and brush hand exchange.
                active = int(context != 0)
                sample['parameters']['ParamArmPoseMode'] = active
                items = {part['id']: part for part in sample['drawables']}
                for side in ('l', 'r'):
                    brush = value if side == work else 0
                    sample['parameters']['ParamCleanBrush'+side.upper()] = brush
                    if context == 0:
                        shape = (.5 if phase == 'interior' else value) if side == work else 0
                        sample['parameters']['ParamHand'+side.upper()+'Shape'] = shape
                        items['hand_'+side]['opacity'] = (1-shape)*(1-brush)
                        items['hand_'+side]['positions'] = list(items['arm_'+side]['positions'])
                        if side == work:
                            items['clean_ground_hand_r_relaxed']['opacity'] = shape*(1-brush)
                            for part in ('clean_ground_brush_r', 'clean_ground_hand_r_palm',
                                         'clean_ground_hand_r_fingers'):
                                items[part]['opacity'] = brush
                        continue
                    items[f'sleeve_{side}_upper']['opacity'] = active
                    items[f'sleeve_{side}_lower']['opacity'] = active*(1-brush)
                    items[f'clean_sleeve_{side}_lower']['opacity'] = active*brush
                    items['hand_'+side]['opacity'] = active*(1-brush)
                    items['hand_'+side]['positions'] = list(items[f'sleeve_{side}_lower']['positions'])
                    if side == work:
                        items[f'clean_hand_{side}_palm']['opacity'] = active*brush
                    else:
                        items[f'clean_hand_{side}_palm']['opacity'] = 0
                key = name if phase == 'interior' else name+'-'+phase
                report['captures'][key] = sample
    return report


@pytest.mark.parametrize('mutation', ['none', 'missing-frame', 'stale-token', 'old-opaque', 'new-hidden',
    'support-duplicate', 'hidden-upper', 'foreground-gap', 'original-gap', 'wrong-order', 'hand-without-sleeve'])
def test_brush_transition_checks_all_visible_material_pairs_and_complementary_opacity(mutation):
    report = transition_report_fixture()
    name = 'clean_top_enter'
    sample = report['captures'][name]
    items = {part['id']: part for part in sample['drawables']}
    if mutation == 'missing-frame': report['captures'].pop('clean_climb_left_exit-end')
    elif mutation == 'stale-token': sample['playback']['token'] += 1
    elif mutation == 'old-opaque': items['sleeve_r_lower']['opacity'] = 1
    elif mutation == 'new-hidden': items['clean_sleeve_r_lower']['opacity'] = 0
    elif mutation == 'support-duplicate': items['clean_sleeve_l_lower']['opacity'] = 1
    elif mutation == 'hidden-upper': items['sleeve_r_upper']['opacity'] = 0
    elif mutation in ('foreground-gap', 'original-gap'):
        part = items['clean_sleeve_r_lower' if mutation == 'foreground-gap' else 'sleeve_r_lower']
        part['positions'][::2] = [v+.02 for v in part['positions'][::2]]
    elif mutation == 'wrong-order': items['clean_sleeve_r_lower']['drawOrder'] = 75
    elif mutation == 'hand-without-sleeve':
        end = {part['id']: part for part in report['captures']['clean_ground_exit-end']['drawables']}
        end['clean_hand_r_palm']['opacity'] = .2
    if mutation == 'none':
        result = measure_brush_transitions(report)
        assert len(result) == 8 and all(set(row) == {'start', 'interior', 'end'} for row in result.values())
        partial = result['clean_ground_enter']['interior']['sides']['r']
        assert partial['opacities']['arm_r'] == 1
        assert partial['opacities']['clean_ground_hand_r_palm'] == .5
        assert set(partial['contacts']) == {'hand_r', 'clean_ground_hand_r_relaxed', 'clean_ground_hand_r_palm'}
        assert set(result['clean_ground_exit']['end']['sides']['r']['contacts']) == {'hand_r'}
    else:
        with pytest.raises((ValueError, KeyError)): measure_brush_transitions(report)


@pytest.mark.parametrize('mutation', ['hidden-cloth', 'opaque-old-hand', 'hidden-new-hand',
    'new-hand-gap', 'old-hand-gap', 'wrong-layer-order', 'old-active-visible',
    'hidden-relaxed-hand', 'relaxed-hand-gap', 'relaxed-hand-order', 'wrong-shape'])
def test_ground_take_stow_checks_each_visible_hand_on_the_same_continuous_sleeve(mutation):
    report = transition_report_fixture()
    sample = report['captures']['clean_ground_enter']
    parts = {part['id']: part for part in sample['drawables']}
    if mutation == 'hidden-cloth': parts['arm_r']['opacity'] = 0
    elif mutation == 'opaque-old-hand': parts['hand_r']['opacity'] = 1
    elif mutation == 'hidden-new-hand': parts['clean_ground_hand_r_palm']['opacity'] = 0
    elif mutation == 'hidden-relaxed-hand': parts['clean_ground_hand_r_relaxed']['opacity'] = 0
    elif mutation == 'wrong-shape': sample['parameters']['ParamHandRShape'] = 0
    elif mutation == 'relaxed-hand-order': parts['clean_ground_hand_r_relaxed']['drawOrder'] = 70
    elif mutation == 'relaxed-hand-gap':
        parts['clean_ground_hand_r_relaxed']['positions'][::2] = [v+.02 for v in parts['clean_ground_hand_r_relaxed']['positions'][::2]]
    elif mutation in ('new-hand-gap', 'old-hand-gap'):
        name = 'clean_ground_hand_r_palm' if mutation == 'new-hand-gap' else 'hand_r'
        parts[name]['positions'][::2] = [v+.02 for v in parts[name]['positions'][::2]]
    elif mutation == 'wrong-layer-order': parts['clean_ground_hand_r_palm']['drawOrder'] = 70
    else: parts['clean_hand_r_palm']['opacity'] = .5
    with pytest.raises(ValueError): measure_brush_transitions(report)


def test_release_does_not_accept_legacy_fan_booleans_for_new_brush_model():
    errors = validate_v5_measurements({'v5Contract': {'motionPolishVersion': 1},
        'v5Checks': {'fanSweep': True}, 'v5Measurements': {'fanSweep': {'old': 'evidence'}}})
    assert any('brushSweep' in error for error in errors)
    assert any('brushTransitions' in error for error in errors)
    assert any('freeBrace' in error for error in errors)
    assert any('climbMaterials' in error for error in errors)
    assert not any('fanSweep' in error for error in errors)


@pytest.mark.parametrize('mutation', ['none', 'gap', 'hidden', 'no-response', 'land-not-restored',
    'land-active-zero', 'missing-land-end', 'stale-land-end', 'hidden-land-cuff', 'land-cuff-gap', 'wrong-authored-end'])
def test_brace_recovers_original_painted_cuffs_and_checks_native_motion(mutation):
    report = report_fixture()
    contacts = {side: {'hand': 'hand_'+side, 'cuff': 'arm_'+side, 'sourceUv': uv}
                for side, uv in [('l', [.495, .518]), ('r', [.526, .518])]}
    report['v5Contract']['freeContacts'] = {'parameter': 'ParamFreeArmActive', 'poseParameter': 'ParamFreeArmPose',
        'armPoseMode': 0, 'samePaintedSleevesThroughLanding': True, 'contacts': contacts}
    for side in ('l', 'r'):
        report['atlasAudit']['regions'].append({'name': 'arm_'+side, 'page': 0,
            'sourceSize': [1, 1], 'translationToCanvas': [0, 0]})
    for index in range(4):
        report['probes'][f'v51_free_recovery_{index}'] = {}
        for phase, step in [('before', index), ('after', index+1)]:
            report['probes'][f'v51_free_recovery_{index}'][phase] = {'canvasInfo': {'pixelsPerUnit': 1, 'width': 1, 'height': 1},
                'drawables': [mesh(kind+'_'+side, 0 if mutation == 'no-response' else step*.01)
                              for side in ('l', 'r') for kind in ('hand', 'arm')]}
    report['captures'] = {state: {'parameters': dict.fromkeys(('ParamFreeArmActive', 'ParamFreeArmPose', 'ParamFreeArmBrace'), 1)}
                          for state in ('fall_float', 'land-start', 'land')}
    targets = {'ParamFreeArmActive': 1, 'ParamFreeArmPose': 0, 'ParamFreeArmBrace': 0, 'ParamArmPoseMode': 0}
    binding = {'name': 'land', 'token': 4, 'generation': 2}
    end = deepcopy(report['probes']['v51_free_recovery_3']['after'])
    end.update(parameters=deepcopy(targets), generation=2,
               playback={'name': 'land', 'token': 4, 'evaluated': True, 'nativeUpdateCount': 200})
    report['captures']['land-end'] = end
    report['motionEndpoints'] = {'land': {'end': deepcopy(targets)}}
    report['motionBindings'] = {'land': binding}
    report['finishedEvents'] = {'land': {**binding, 'type': 'finished', 'cycle': 1}}
    item = report['probes']['v51_free_recovery_1']['after']['drawables'][0]
    if mutation == 'gap': item['positions'][::2] = [v+.02 for v in item['positions'][::2]]
    elif mutation == 'hidden': item['opacity'] = 0
    elif mutation == 'land-not-restored': end['parameters']['ParamFreeArmBrace'] = .2
    elif mutation == 'land-active-zero': end['parameters']['ParamFreeArmActive'] = 0
    elif mutation == 'missing-land-end': report['captures'].pop('land-end')
    elif mutation == 'stale-land-end': end['playback']['token'] = 3
    elif mutation == 'hidden-land-cuff': end['drawables'][1]['opacity'] = 0
    elif mutation == 'land-cuff-gap': end['drawables'][1]['positions'][::2] = [v+.02 for v in end['drawables'][1]['positions'][::2]]
    elif mutation == 'wrong-authored-end': report['motionEndpoints']['land']['end']['ParamFreeArmActive'] = 0
    if mutation == 'none':
        assert measure_free_brace(report)['braceWristTravel'] == pytest.approx({'l': .02, 'r': .02})
    else:
        with pytest.raises((ValueError, KeyError)): measure_free_brace(report)


@pytest.mark.parametrize('mutation', ['none', 'hidden-new', 'old-skirt', 'old-underlay', 'missing-phase',
                                     'wrong-drape-gate', 'missing-drape-contract'])
def test_climb_material_gate_uses_every_actual_phase_and_checks_retired_underlay(mutation):
    report = report_fixture(); report['climbProbeContract'] = {}
    for index in range(16):
        name = 'climb'+str(index); direction = 'left' if index < 8 else 'right'
        material = report['v5Contract']['climbMaterials'][direction]
        report['climbProbeContract'][name] = {'direction': direction}
        report['probes'][name] = {phase: {'parameters': {'ParamClimbDrapeBlend': 1}, 'drawables': [mesh(material['drawable']),
            mesh(material['replaces'], opacity=0), mesh(material['underlay'], opacity=0)]} for phase in ('before', 'after')}
    actual = report['probes']['climb7']['after']['drawables']
    if mutation == 'hidden-new': actual[0]['opacity'] = .1
    elif mutation == 'old-skirt': actual[1]['opacity'] = 1
    elif mutation == 'old-underlay': actual[2]['opacity'] = 1
    elif mutation == 'missing-phase': report['climbProbeContract'].pop('climb3')
    elif mutation == 'wrong-drape-gate': report['probes']['climb7']['after']['parameters']['ParamClimbDrapeBlend'] = 0
    elif mutation == 'missing-drape-contract': report['v5Contract'].pop('climbDrapeExchange')
    if mutation == 'none': assert len(measure_climb_materials(report)) == 16
    else:
        with pytest.raises(ValueError): measure_climb_materials(report)


@pytest.mark.parametrize('field,value', [('parameter', 'ParamClimbRefine'), ('default', 1),
    ('unfoldEndSeconds', .52), ('exchangeEndSeconds', .4), ('blend', 'complementary'),
    ('receivingOpacityGuard', .5)])
def test_drape_exchange_contract_cannot_substitute_the_body_refinement_axis_or_change_material_timing(field, value):
    refinement = metadata()['refinement']
    refinement['climbDrapeExchange'][field] = value
    with pytest.raises(ValueError): climb_drape_exchange(refinement)
