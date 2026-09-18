"""Synthetic model/release fixtures only; no native application or cleanup is run."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_release_validation import synthetic_release, synthetic_desktop_evidence
from tools.validate_release import (validate_model_evidence, validate, V5_PARAMETERS, V5_DRAWABLES,
                                    V5_CLEAN_SEGMENTS, V4_TRANSITIONS, ANIMATIONS, V5_NATIVE_CHECKS,
                                    V5_REST_CONTACT_POSES, V5_REST_CONTACT_PROBES, V5_ACTIVE_CONTACT_CAPTURES)
from tools.authoring.animation_specs import ANIMATIONS
from pet_core import ANIMATIONS as RUNTIME_ANIMATIONS
from tools.finalize_release import read, digest
from tools.qa_resource_snapshot import resource_snapshot
from tools.qa_model_inputs import verify_native_uv_evidence


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf8')


@pytest.fixture
def v5_release(synthetic_release):
    args = synthetic_release
    folder = args['bundle']/'assets/live2d/Maple'
    model = read(folder/'Maple.model3.json')
    native = read(args['source_runtime_path'])
    native.pop('syntheticTestFixture')  # Only this explicitly synthetic test exercises deeper release gates.
    parameters = set(native['modelRevisionEvidence']['requiredParameters']) | V5_PARAMETERS
    actual = sorted(parameters) + [f'ParamSynthetic{n}' for n in range(67-len(parameters))]
    states = list((*ANIMATIONS, *V4_TRANSITIONS, *V5_CLEAN_SEGMENTS))
    contact_ids = [prefix+side+suffix for side in ('l', 'r') for prefix, suffix in
                   (('hand_', ''), ('arm_', ''), ('hand_', '_relaxed'), ('sleeve_', '_lower'))]
    ids = sorted(V5_DRAWABLES) + contact_ids + [f'synthetic-mesh-{n}' for n in range(134-len(contact_ids))]
    metadata = {'refinement': {'version': 5, 'requiredParameters': sorted(parameters),
        'restContacts': {s: {'hand': 'hand_'+s, 'cuff': 'arm_'+s, 'sourceUv': [.5, .5]} for s in ('l', 'r')},
        'contacts': {s: {'hands': ['hand_'+s+'_relaxed'], 'cuff': 'sleeve_'+s+'_lower', 'sourceUv': [.5, .5]}
                     for s in ('l', 'r')},
        'hands': {s: {'rest': ['hand_'+s, 'hand_'+s+'_relaxed']} for s in ('l', 'r')}}, 'locomotion': {}}
    write(folder/'Maple.pet.json', metadata)
    endpoints = {}
    for name in states:
        relative = f'motions/{name}.motion3.json'
        write(folder/relative, {'Meta': {'Duration': 1}, 'Curves': [{'Target': 'Parameter', 'Id': 'ParamSynthetic0', 'Segments': [0, 0, 0, 1, 0]}]})
        model['FileReferences']['Motions'][name] = [{'File': relative}]
        native['finished'][name] = True
        native['captures'][name] = {}
        endpoints[name] = {'start': {'ParamSynthetic0': 0}, 'end': {'ParamSynthetic0': 0}, 'sha256': digest(folder/relative)}
    model['FileReferences']['Motions'] = {k:v for k,v in model['FileReferences']['Motions'].items() if k in RUNTIME_ANIMATIONS or k in V4_TRANSITIONS}
    write(folder/'Maple.model3.json', model)
    uv_path = Path(native['nativeUvAudit']['path'])
    uv = read(uv_path)
    geometry_path = uv_path.parent/'geometry.json'
    geometry = read(geometry_path)
    geometry['drawables'] = [{'id': name} for name in ids]
    write(geometry_path, geometry)
    uv.update(meshCount=141, matchedMeshCount=141, meshes=[{'id': name, 'passed': True, 'errors': [],
        'maximumAtlasPixelError': 0, 'sameTriangleTopologyIgnoringOrder': True} for name in ids])
    for field in ('sourceHashesAtStart', 'sourceHashesAtEnd'):
        uv[field][str(geometry_path)] = digest(geometry_path)
    write(uv_path, uv)
    native['nativeUvAudit'] = verify_native_uv_evidence(uv_path, folder/'Maple.moc3',
        Path(native['nativeUvAudit']['atlasAuditPath']), digest(folder/'Maple.moc3'), expected_mesh_count=141)
    native.update(modelRevision='v5', modelRevisionEvidence={'metadataVersion': 5,
        'requiredParameters': sorted(parameters), 'nativeParameters': actual, 'nativeStates': states})
    native['captures']['baseline'] = {'parameters': {p: 0. for p in actual}, 'drawables': [{'id': name} for name in ids]}
    native.update(v5Checks={key: True for key in V5_NATIVE_CHECKS}, v5Contract=metadata['refinement'],
                  contactContract=deepcopy(metadata['refinement']['contacts']), handContract=deepcopy(metadata['refinement']['hands']),
                  locomotion=metadata['locomotion'], motionEndpoints=endpoints, motionBindings={}, finishedEvents={}, markers={})
    measurements = native['v5Measurements'] = {'nativeIdentity': deepcopy(native['modelRevisionEvidence']),
        'motionBindings': {}, 'cleanupSegments': {}, 'restContacts': {}, 'activeMotionContacts': {}, 'climbSupport': {},
        'sleepProportions': {'scaleRatios': {'face_base': 1, 'torso': 1}, 'principalScales': {'face_base': [1, 1], 'torso': [1, 1]},
                            'tolerance': .03, 'measurement': 'SYNTHETIC TEST ONLY'},
        'ribbonWind': {'v5_wind_'+s+a: {'rootShift': 0, 'tailShift': .05, 'oppositeRibbonShift': 0}
                       for s in ('l', 'r') for a in ('x', 'y')},
        'fanSweep': {s: {'nativeFanCenterDistance': .02, 'supportHandMovement': 0} for s in ANIMATIONS if s.startswith('clean_')},
        'probeTargets': {'definitions': 27, 'capturedPairs': 27}}
    for index, name in enumerate(states):
        binding = {'generation': 1, 'token': index+1, 'name': name}
        event = dict(binding, type='finished', cycle=1)
        snapshot = {'generation': 1, 'playback': {**binding, 'evaluated': True, 'nativeUpdateCount': 10},
                    'parameters': {'ParamArmPoseMode': 0, 'ParamHandLShape': 0, 'ParamHandRShape': 0},
                    'canvasInfo': {'pixelsPerUnit': 1000, 'width': 1000, 'height': 1000},
                    'drawables': [{'id': name, 'finiteVertices': True, 'opacity': 1} for name in ids]}
        native['captures'][name] = snapshot
        native['motionBindings'][name] = binding
        native['finishedEvents'][name] = event
        measurements['motionBindings'][name] = {'binding': binding, 'finishedEvent': event, 'captureNativeUpdateCount': 10}
        if name in V5_CLEAN_SEGMENTS:
            measurements['cleanupSegments'][name] = {}
            for phase in ('start', 'end'):
                native['captures'][name+'-'+phase] = deepcopy(snapshot)
                measurements['cleanupSegments'][name][phase] = {'maximumParameterError': 0, 'parametersCompared': 1, 'binding': binding}
        elif name.startswith('clean_'):
            native['markers'][name] = ['clean_sweep']
    for name in (*V5_REST_CONTACT_POSES, *V5_REST_CONTACT_PROBES):
        measurements['restContacts'][name] = {hand: {'distance': 0, 'cuff': [0, 0], 'wrist': [0, 0]} for hand in ('hand_l', 'hand_r')}
    native['captures']['land-start'] = deepcopy(native['captures']['land'])
    native['captures']['land-start']['playback'].update(paused=True, staticSampleCount=1)
    for name, key in V5_ACTIVE_CONTACT_CAPTURES.items():
        native['captures'][key]['parameters']['ParamArmPoseMode'] = 1
        measurements['activeMotionContacts'][name] = {'captureKey': key, 'binding': native['motionBindings'][name],
            'contacts': {hand: {'distance': 0, 'cuff': [0, 0], 'wrist': [0, 0]}
                         for hand in ('hand_l_relaxed', 'hand_r_relaxed')}}
    for n in range(16):
        measurements['climbSupport'][f'synthetic-{n}'] = {'worldPoints': [[0, 0], [0, .001]], 'maximumAxisSlip': .001, 'limit': .008}
    native['v5ProbeDefinitions'] = {name: [{'ParamSynthetic0': 0}, {'ParamSynthetic0': 1}]
        for name in ('v5_rest_l', 'v5_rest_r', *(f'synthetic-v5-probe-{n}' for n in range(25)))}
    native['probes'].update({name: {phase: {'visiblePixels': 200, 'glError': 0, 'parameters': target,
        'canvasInfo': {'pixelsPerUnit': 1000, 'width': 1000, 'height': 1000},
        'drawables': [{'id': id_, 'finiteVertices': True, 'opacity': 1} for id_ in ids]}
        for phase, target in zip(('before', 'after'), targets)} for name, targets in native['v5ProbeDefinitions'].items()})
    for side in ('l', 'r'):
        for sample in native['probes']['v5_rest_'+side].values():
            sample['parameters']['ParamArmPoseMode'] = 0
    assets = resource_snapshot(folder, args['bundle']/'web/dist')
    native['modelSha256'] = assets['assets/live2d/Maple/Maple.model3.json']
    for field in ('resourceHashesAtStart', 'resourceHashesLoaded', 'resourceHashesAtEnd'):
        native[field] = assets.copy()
    write(args['source_runtime_path'], native)
    source = read(args['source_qa_path'])
    source.pop('syntheticTestFixture')  # No fixture is distributed or treated as actual acceptance.
    source.update(modelVersion='v5', files={name: {'sha256': value} for name, value in assets.items()},
                  fullReportSha256=digest(args['source_runtime_path']))
    source['visualReview']['fullReportSha256'] = source['fullReportSha256']
    write(args['source_qa_path'], source)
    runtime = read(args['smoke']/'packaged-runtime.json')
    runtime['modelSha256'] = native['modelSha256']; write(args['smoke']/'packaged-runtime.json', runtime)
    return dict(args=args, assets=assets, native=native, source=source)


def test_actual_v5_identity_counts_uv_and_full_states_are_required(v5_release):
    e = v5_release
    assert validate(e['args']['bundle'], packaged=True) == []
    assert validate_model_evidence(e['source'], e['native'], e['assets'], e['source']['fullReportSha256'], 5) == []
    assert len(e['native']['modelRevisionEvidence']['nativeParameters']) == 67
    assert len(e['native']['modelRevisionEvidence']['nativeStates']) == 31


def contact_drawable(native, capture, name):
    return next(row for row in native['captures'][capture]['drawables'] if row['id'] == name)


@pytest.mark.parametrize('change,expected', [
    (lambda n: n['v5Measurements'].pop('activeMotionContacts'), 'four actual active'),
    (lambda n: n['v5Measurements']['activeMotionContacts'].pop('fall_float'), 'four actual active'),
    (lambda n: n['v5Measurements']['activeMotionContacts'].pop('land'), 'four actual active'),
    (lambda n: n['captures'].pop('land-start'), 'stale or unbound: land'),
    (lambda n: n['v5Measurements']['activeMotionContacts']['land'].update(captureKey='land'), 'stale or unbound: land'),
    (lambda n: n['captures']['land-start']['playback'].update(token=999), 'stale or unbound: land'),
    (lambda n: n['captures']['land-start'].update(generation=99), 'stale or unbound: land'),
    (lambda n: n['captures']['land-start']['playback'].update(evaluated=False), 'stale or unbound: land'),
    (lambda n: n['captures']['drag_left']['parameters'].update(ParamArmPoseMode=0), 'actual relaxed active hands'),
    (lambda n: n['captures']['fall_float']['parameters'].update(ParamHandRShape=1), 'actual relaxed active hands'),
    (lambda n: n['captures']['land-start']['parameters'].update(ParamArmPoseMode=float('nan')), 'actual relaxed active hands'),
    (lambda n: contact_drawable(n, 'drag_left', 'hand_l_relaxed').update(opacity=0), 'pixels are hidden'),
    (lambda n: contact_drawable(n, 'drag_right', 'sleeve_r_lower').update(opacity=.94), 'pixels are hidden'),
    (lambda n: contact_drawable(n, 'land-start', 'hand_r_relaxed').update(opacity=float('nan')), 'pixels are hidden'),
    (lambda n: n['v5Measurements']['activeMotionContacts']['drag_left']['contacts'].pop('hand_l_relaxed'), 'gap exceeds .012'),
    (lambda n: n['v5Measurements']['activeMotionContacts']['fall_float']['contacts']['hand_r_relaxed'].update(
        distance=.0121, wrist=[.0121, 0]), 'gap exceeds .012'),
    (lambda n: n['v5Measurements']['activeMotionContacts']['land']['contacts']['hand_l_relaxed'].update(
        distance=0, wrist=[.02, 0]), 'measured coordinates differ'),
    (lambda n: n['v5Measurements']['restContacts'].pop('sleep_loop'), 'Incomplete measured rest'),
    (lambda n: n['v5Measurements']['restContacts'].pop('v5_rest_l-after'), 'Incomplete measured rest'),
    (lambda n: n['captures']['idle']['parameters'].update(ParamArmPoseMode=1), 'actual resting pose'),
    (lambda n: contact_drawable(n, 'sleep_loop', 'hand_r').update(opacity=.5), 'Rest hand/cuff pixels are hidden'),
    (lambda n: n['v5Measurements']['restContacts']['petting']['hand_l'].update(
        distance=.013, wrist=[.013, 0]), 'Native resting hands separate'),
    (lambda n: n['handContract']['l'].update(rest=['hand_l_support']), 'metadata contract'),
])
def test_active_contact_classification_cannot_hide_missing_or_separated_hands(v5_release, change, expected):
    """Passing summary flags cannot replace the actual visible pose and native gap."""
    e = v5_release
    change(e['native'])
    errors = validate_model_evidence(e['source'], e['native'], e['assets'], e['source']['fullReportSha256'], 5)
    assert any(expected in error for error in errors), errors


def test_land_uses_bound_native_active_start_while_preserving_rest_sleep_and_original_gap_limit(v5_release):
    e = v5_release
    native = e['native']
    assert native['captures']['land']['parameters']['ParamArmPoseMode'] == 0
    assert native['captures']['land-start']['parameters']['ParamArmPoseMode'] == 1
    assert set(native['v5Measurements']['activeMotionContacts']) == {'drag_left', 'drag_right', 'fall_float', 'land'}
    assert set(native['v5Measurements']['restContacts']) == {'idle', 'happy', 'talk', 'petting', 'sleep_loop',
        'v5_rest_l-before', 'v5_rest_l-after', 'v5_rest_r-before', 'v5_rest_r-after'}
    for row in native['v5Measurements']['activeMotionContacts'].values():
        for contact in row['contacts'].values():
            contact.update(distance=.012, wrist=[.012, 0])
    for row in native['v5Measurements']['restContacts'].values():
        for contact in row.values():
            contact.update(distance=.012, wrist=[.012, 0])
    assert validate_model_evidence(e['source'], native, e['assets'], e['source']['fullReportSha256'], 5) == []


@pytest.mark.parametrize('change,expected', [
    (lambda n: n['nativeUvAudit'].update(meshCount=134, matchedMeshCount=134), '141-mesh'),
    (lambda n: n['nativeUvAudit'].update(maximumAtlasPixelError=.002), '.001'),
    (lambda n: n['modelRevisionEvidence'].update(metadataVersion=4), 'actual metadata'),
    (lambda n: n['captures']['baseline']['parameters'].pop('ParamCleanGround'), '67 finite'),
    (lambda n: n['captures']['baseline']['parameters'].update(ParamCleanGround=float('nan')), '67 finite'),
    (lambda n: n['captures']['baseline']['drawables'].pop(), '141 drawables'),
    (lambda n: n['captures']['baseline']['drawables'].__setitem__(0, n['captures']['baseline']['drawables'][1]), '141 drawables'),
    (lambda n: n['finished'].update(clean_top_enter=False), 'clean_top_enter'),
    (lambda n: n['modelRevisionEvidence']['nativeStates'].remove('clean_ground_exit'), '31 states'),
    (lambda n: n['v5Checks'].update(climbSupport=False), 'climbSupport'),
    (lambda n: n['v5Measurements'].update(restContacts={}), 'restContacts'),
    (lambda n: n['captures']['idle']['playback'].update(token=999), 'stale or unbound'),
    (lambda n: n['v5Measurements']['climbSupport']['synthetic-0'].update(maximumAxisSlip=.009), 'world-position slip'),
    (lambda n: n['v5Measurements']['sleepProportions']['principalScales'].update(face_base=[1.08, 1]), 'preserve native scale'),
    (lambda n: n['v5Measurements']['ribbonWind']['v5_wind_lx'].update(rootShift=.1), 'pinned roots'),
    (lambda n: n['v5Measurements']['fanSweep']['clean_top'].update(nativeFanCenterDistance=0), 'actual fan sweep'),
    (lambda n: n['markers'].update(clean_top_enter=['clean_sweep']), 'endpoints/markers'),
])
def test_v5_legacy_or_incomplete_native_data_is_rejected(v5_release, change, expected):
    e = v5_release; change(e['native'])
    errors = validate_model_evidence(e['source'], e['native'], e['assets'], e['source']['fullReportSha256'], 5)
    assert any(expected in error for error in errors), errors


def test_version_must_come_from_actual_packaged_metadata(v5_release):
    e = v5_release
    errors = validate_model_evidence(e['source'], e['native'], e['assets'], e['source']['fullReportSha256'], 4)
    assert any('actual native v4' in error for error in errors)


def test_explicit_synthetic_native_model_evidence_is_rejected_for_v5(v5_release):
    e = v5_release; e['native']['syntheticTestFixture'] = True
    assert any('Synthetic or mock' in error for error in
        validate_model_evidence(e['source'], e['native'], e['assets'], e['source']['fullReportSha256'], 5))


def test_shipping_inventory_rejects_missing_transition_motion(v5_release):
    folder = v5_release['args']['bundle']/'assets/live2d/Maple'
    model = read(folder/'Maple.model3.json'); del model['FileReferences']['Motions']['climb_to_top_left']
    write(folder/'Maple.model3.json', model)
    assert any('climb_to_top_left' in error for error in validate(v5_release['args']['bundle']))


def test_v5_finalizer_rejects_bare_passed_desktop_even_with_complete_model(v5_release):
    from tools.finalize_release import finalize
    e = v5_release
    with pytest.raises(ValueError, match='re-opened original'):
        finalize(**e['args'])


@pytest.mark.parametrize('cleanup', [None, 'failed'])
def test_v5_finalizer_requires_actual_cleanup_validator_and_preserves_candidate(v5_release, monkeypatch, cleanup):
    import tools.finalize_release as f
    e = v5_release; args = e['args']
    write(args['desktop_report'], {'reportKind': 'cutemaple-combined-desktop-evidence'})
    # Test this integration boundary independently: real original-session
    # reconstruction and all native-cleanup return validation have own tests.
    desktop = synthetic_desktop_evidence(digest(args['bundle']/'CuteMaple-Live2D.exe'), e['assets'])
    def resolve(manifest, sha, assets, *, model_version, package_hashes):
        assert model_version == 5 and 'cleaner/CuteMaple-Cleaner.exe' in package_hashes
        return desktop
    monkeypatch.setattr(f, 'resolve_combination', resolve)
    called = []
    def cleanup_result(bundle, path):
        called.append((bundle, path))
        return {'passed': False, 'errors': ['Actual cleanup receipt is missing']}
    monkeypatch.setattr(f, 'validate_cleanup_evidence', cleanup_result)
    before = {p.relative_to(args['package']): p.read_bytes() for p in args['package'].rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='three actual|cleanup evidence incomplete'):
        f.finalize(**args, cleanup_evidence_path=args['package']/'missing-real-cleanup' if cleanup else None)
    assert bool(called) is bool(cleanup)
    assert {p.relative_to(args['package']): p.read_bytes() for p in args['package'].rglob('*') if p.is_file()} == before


def test_v5_final_metadata_attaches_validated_cleanup_originals(v5_release, monkeypatch):
    """Final metadata boundary only; validator real-data guards have separate fixtures."""
    import tools.finalize_release as f
    e = v5_release; args = e['args']
    write(args['desktop_report'], {'reportKind': 'cutemaple-combined-desktop-evidence', 'inputSha256': {}})
    desktop = synthetic_desktop_evidence(digest(args['bundle']/'CuteMaple-Live2D.exe'), e['assets'])
    desktop['evidenceComposition'] = {'syntheticTestFixture': True}
    monkeypatch.setattr(f, 'resolve_combination', lambda *a, **k: desktop)
    original = args['package'].parent/'synthetic-cleanup-original.json'
    write(original, {'synthetic': True, 'message': 'Unit-test transaction boundary, NOT actual cleanup evidence'})
    helper = args['bundle']/'cleaner/CuteMaple-Cleaner.exe'
    checked = {'reportKind': 'cleanup-evidence-validation', 'passed': True, 'errors': [],
               'helperSha256': digest(helper), 'operations': [{'operation_id': f'synthetic-{n}'} for n in range(3)],
               'inputSha256': {str(original): digest(original), str(helper): digest(helper)}}
    # Production validator explicitly refuses this marked synthetic input.
    monkeypatch.setattr(f, 'validate_cleanup_evidence', lambda *a: checked)
    result = f.finalize(**args, cleanup_evidence_path=original)
    assert result['cleanupHelperQa']['administratorActionsExercised'] is True
    assert result['cleanupHelperQa']['actualCleanupEvidence'] == checked
    original_ref = next(r for r in result['cleanupEvidenceOriginals'] if r['originalPath'] == str(original))
    assert digest(args['package']/original_ref['packagedPath']) == digest(original)
    assert 'native v5' in read(args['package']/'BUILD-STATUS.json')['modelValidation']
