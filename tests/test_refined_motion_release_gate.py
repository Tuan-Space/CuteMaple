"""Synthetic release-gate fixtures ONLY; never native evidence or an actual finalization.

The full-envelope tests stub native UV certification and numeric replay, whose
real algorithms have separate geometry tests. Files live only in pytest tmp.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from PIL import Image

from tools import refined_motion_evidence as gate
from tools import verify_refined_motion as verifier
from tools.native_model_contract import REFINED_MOTION_REVISION
from tools.qa_model_inputs import digest, audit_atlas_files
from tools.validate_release import validate_refined_release_evidence


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    (tmp_path/'SYNTHETIC-TEST-FIXTURE.txt').write_text('Mock unit-test geometry; not native acceptance evidence.')
    bundle = tmp_path/'package/CuteMaple-Live2D'
    folder = bundle/'assets/live2d/Maple'; folder.mkdir(parents=True)
    source = tmp_path/'source'; source.mkdir()
    output = tmp_path/'audit'; output.mkdir()
    params = {'ParamFreeArmActive', 'ParamFreeArmPose', 'ParamClimbRefine', 'ParamClimbHandoff'}
    params |= {f'ParamFixture{i}' for i in range(72-len(params))}
    ids = {'face_base', 'torso', 'leg_l_climb_handoff', 'leg_r_climb_handoff', *verifier.FEET,
           'skirt', 'skirt_sleep', 'hand_l', 'hand_r', 'arm_l', 'arm_r'}
    ids |= {f'fixture_mesh_{i}' for i in range(143-len(ids))}
    contacts = {s: {'hand': 'hand_'+s, 'cuff': 'arm_'+s, 'sourceUv': uv}
                for s, uv in [('l', [.495, .518]), ('r', [.526, .518])]}
    meta = {'refinement': {'version': 5, 'motionRevision': REFINED_MOTION_REVISION,
        'nativeParameterIds': sorted(params), 'nativeDrawableIds': sorted(ids), 'requiredParameters': sorted(params),
        'freeContacts': {'parameter': 'ParamFreeArmActive', 'poseParameter': 'ParamFreeArmPose', 'armPoseMode': 0,
                        'samePaintedSleevesThroughLanding': True, 'contacts': contacts}}}
    settings = {'Version': 3, 'CuteMaple': {'Metadata': 'Maple.pet.json'}, 'FileReferences': {
        'Moc': 'Maple.moc3', 'Textures': ['texture.png'], 'Motions': {'idle': [{'File': 'idle.motion3.json'}]}}}
    for parent in (folder, source):
        save(parent/'Maple.model3.json', settings); save(parent/'Maple.pet.json', meta)
        (parent/'Maple.moc3').write_bytes(b'MOC3 SYNTHETIC NOT A MODEL')
        save(parent/'idle.motion3.json', {'Meta': {'Duration': 1}, 'Curves': []})
        Image.new('RGBA', (2, 2), 'white').save(parent/'texture.png')
    core = source/'synthetic-core.js'; core.write_text('// never executed')
    monkeypatch.setattr(verifier, 'CORE', core)
    selected = sorted({*verifier.FEET, 'skirt', 'skirt_sleep', 'face_base', 'leg_l_climb_handoff',
                       'leg_r_climb_handoff', 'hand_l', 'arm_l'})
    frames = [{'id': f'fixture-{i}', 'group': 'free', 'geometryNames': selected,
               'parameters': dict.fromkeys(params, float(i))} for i in range(2)]
    monkeypatch.setattr(verifier, 'sampling_plan', lambda motions, metadata: deepcopy(frames))
    geometry = {'tool': 'official-cubism-native-geometry-audit', 'canvasInfo': {'width': 1, 'height': 1,
        'originX': .5, 'originY': .5, 'pixelsPerUnit': 1}, 'drawables': [
        {'id': name, 'textureIndex': 0, 'textureUvs': [0, 1, 1, 1, 0, 0], 'indices': [0, 1, 2]} for name in ids]}
    geometry_path = source/'native-geometry.json'; save(geometry_path, geometry)
    atlas = {'atlasSize': [2, 2], 'atlases': [{'path': str(source/'texture.png'), 'sha256': digest(source/'texture.png')}]}
    atlas_path = source/'atlas.json'; save(atlas_path, atlas)
    uv_path = source/'uv.json'; save(uv_path, {'syntheticTestFixture': True})
    manifest_path = source/'layers.json'; save(manifest_path, {'layers': []})
    inner = {str(p): digest(p) for p in (geometry_path, atlas_path, uv_path, source/'Maple.moc3', core, source/'texture.png')}
    uv = {'sha256': digest(uv_path), 'inputHashesAtStart': inner, 'verified': True}
    calls = []
    def fake_uv(path, moc, atlas_path, sha, **kwargs):
        calls.append(kwargs)
        assert kwargs == {'expected_mesh_count': 143, 'expected_drawable_ids': ids}
        assert sha == digest(folder/'Maple.moc3')
        return deepcopy(uv)
    monkeypatch.setattr(gate, 'verify_native_uv_evidence', fake_uv)
    samples = {'nativeCoreSamples': True, 'sourceHashesAtStart': {str(source/'Maple.moc3'): digest(source/'Maple.moc3'), str(core): digest(core)},
        'canvasInfo': geometry['canvasInfo'], 'topology': {name: {'textureIndex': 0, 'textureUvs': [0, 0, 1, 0, 0, 1], 'triangles': [0, 1, 2]} for name in selected},
        'frames': [{'id': f['id'], 'parameters': f['parameters'], 'drawables': {
            name: {'opacity': 1, 'positions': [0, 0, 1, 0, 0, 1]} for name in selected}} for f in frames]}
    samples['sourceHashesAtEnd'] = dict(samples['sourceHashesAtStart'])
    sample_path = output/'native-samples.json'; save(sample_path, samples)
    request = {'moc': str(source/'Maple.moc3'), 'core': str(core), 'parameterIds': sorted(params),
        'drawableIds': sorted(ids), 'selected': selected, 'frames': frames, 'output': str(sample_path)}
    request_path = output/'sampling-request.json'; save(request_path, request)
    def replay(raw, *args):
        gap = max(abs(f['drawables']['hand_l']['positions'][0]-f['drawables']['arm_l']['positions'][0]) for f in raw['frames'])
        result = {name: {'passed': True, 'errors': [], 'samples': ['SYNTHETIC NUMERIC REPLAY']} for name in gate.CHECKS}
        result['freeSleeveContacts'] = {'passed': gap <= .012, 'errors': [] if gap <= .012 else ['actual gap exceeds .012'],
                                       'limitCanvas': .012, 'maximumGap': gap}
        return result
    monkeypatch.setattr(gate, '_recompute_checks', replay)
    all_inputs = [*source.glob('*'), Path(verifier.__file__)]
    hashes = {str(p.resolve()): digest(p) for p in all_inputs if p.is_file()}
    report = {'schemaVersion': 1, 'tool': 'refined-native-motion-audit', 'passed': True, 'errors': [],
        'nativeCoreSamples': True, 'sourcesUnchanged': True, 'productionPlaybackVerified': False, 'visualAccepted': False,
        'sourceHashesAtStart': hashes, 'sourceHashesAtEnd': dict(hashes), 'nativeUvEvidence': uv,
        'inputReferences': {'model': str(source/'Maple.model3.json'), 'metadata': str(source/'Maple.pet.json'),
            'moc': str(source/'Maple.moc3'), 'sourceManifest': str(manifest_path), 'atlasAudit': str(atlas_path), 'nativeUvAudit': str(uv_path)},
        'atlasCoordinateMapping': [audit_atlas_files(source/'texture.png', source/'texture.png', digest(source/'texture.png'), digest(source/'texture.png'))],
        'nativeSamplePath': str(sample_path), 'nativeSampleSha256': digest(sample_path), 'samplingRequestSha256': digest(request_path),
        'sampleCount': len(frames), 'topologyCount': len(selected), 'checks': replay(samples)}
    report_path = output/'report.json'; save(report_path, report)
    return {'bundle': bundle, 'source': source, 'output': output, 'report': report, 'report_path': report_path,
            'sample_path': sample_path, 'request_path': request_path, 'metadata': meta, 'calls': calls, 'uv': uv}


def test_full_envelope_reopens_originals_and_compares_declared_exact_identity(synthetic):
    f = synthetic
    result = gate.verify_refined_motion_evidence(f['bundle'], f['report_path'], native_uv=f['uv'])
    assert result['verified'] and result['checksRecomputed'] == sorted(gate.CHECKS)
    assert result['visualAccepted'] is False and result['productionPlaybackVerified'] is False
    assert result['reportSha256'] == digest(f['report_path']) and f['calls']
    assert str(f['sample_path']) in result['inputSha256']


@pytest.mark.parametrize('mutation', ['missing-check', 'failed-check', 'integer-check', 'fake-measurement', 'synthetic', 'visual-claim',
                                     'missing-input', 'changed-end-hash', 'no-core', 'missing-reference'])
def test_report_flags_and_numeric_claims_are_not_sufficient(synthetic, mutation):
    f = synthetic; r = f['report']
    if mutation == 'missing-check': r['checks'].pop('sleepGround')
    elif mutation == 'failed-check': r['checks']['sleepGround']['passed'] = False
    elif mutation == 'integer-check': r['checks']['sleepGround']['passed'] = 1
    elif mutation == 'fake-measurement': r['checks']['freeSleeveContacts']['maximumGap'] = .011
    elif mutation == 'synthetic': r['syntheticTestFixture'] = True
    elif mutation == 'visual-claim': r['visualAccepted'] = True
    elif mutation == 'missing-input':
        r['sourceHashesAtStart'].pop(str(f['source']/'idle.motion3.json'))
        r['sourceHashesAtEnd'] = dict(r['sourceHashesAtStart'])
    elif mutation == 'changed-end-hash': r['sourceHashesAtEnd']['extra'] = '0'*64
    elif mutation == 'no-core': r['nativeCoreSamples'] = False
    elif mutation == 'missing-reference': r['inputReferences'].pop('sourceManifest')
    save(f['report_path'], r)
    with pytest.raises(ValueError): gate.verify_refined_motion_evidence(f['bundle'], f['report_path'])


@pytest.mark.parametrize('name', ['Maple.moc3', 'Maple.pet.json', 'idle.motion3.json', 'texture.png'])
def test_different_packaged_model_motion_metadata_or_texture_is_rejected(synthetic, name):
    f = synthetic; p = f['bundle']/'assets/live2d/Maple'/name
    if name == 'Maple.pet.json':
        data = json.loads(p.read_text()); data['changed'] = True; save(p, data)
    else: p.write_bytes(p.read_bytes()+b'changed')
    with pytest.raises(ValueError): gate.verify_refined_motion_evidence(f['bundle'], f['report_path'])


@pytest.mark.parametrize('mutation', ['gap', 'nonfinite', 'target', 'omit-frame', 'topology', 'native-input'])
def test_rehashed_bad_raw_samples_cannot_pass_using_four_true_booleans(synthetic, mutation):
    f = synthetic; raw = json.loads(f['sample_path'].read_text())
    if mutation == 'gap': raw['frames'][0]['drawables']['hand_l']['positions'][0] = .02
    elif mutation == 'nonfinite': raw['frames'][0]['drawables']['hand_l']['positions'][0] = float('nan')
    elif mutation == 'target': raw['frames'][0]['parameters']['ParamFreeArmActive'] = .5
    elif mutation == 'omit-frame': raw['frames'].pop()
    elif mutation == 'topology': raw['topology']['hand_l']['textureUvs'][0] = .01
    elif mutation == 'native-input': raw['sourceHashesAtStart'][next(iter(raw['sourceHashesAtStart']))] = '0'*64
    save(f['sample_path'], raw); f['report']['nativeSampleSha256'] = digest(f['sample_path']); save(f['report_path'], f['report'])
    with pytest.raises(ValueError): gate.verify_refined_motion_evidence(f['bundle'], f['report_path'])


def test_rehashed_reduced_sampling_request_is_rejected(synthetic):
    f = synthetic; request = json.loads(f['request_path'].read_text()); request['frames'].pop()
    save(f['request_path'], request); f['report']['samplingRequestSha256'] = digest(f['request_path']); save(f['report_path'], f['report'])
    with pytest.raises(ValueError, match='sampling request'): gate.verify_refined_motion_evidence(f['bundle'], f['report_path'])


def test_changed_authoring_input_is_not_ignored(synthetic):
    f = synthetic; (f['source']/'layers.json').write_text('{"layers":[],"changed":true}')
    with pytest.raises(ValueError, match='SHA256 changed'): gate.verify_refined_motion_evidence(f['bundle'], f['report_path'])


def test_refined_required_but_legacy_needs_no_report(synthetic):
    f = synthetic
    with pytest.raises(ValueError, match='requires --refined-motion-report'): gate.verify_refined_motion_evidence(f['bundle'], None)
    for version in (4, 5):
        save(f['bundle']/'assets/live2d/Maple/Maple.pet.json', {'refinement': {'version': version}})
        assert gate.verify_refined_motion_evidence(f['bundle'], None) is None


def test_finalized_package_reopens_report_and_requires_exact_packaged_copy(synthetic):
    f = synthetic; receipt = gate.verify_refined_motion_evidence(f['bundle'], f['report_path'])
    package = f['bundle'].parent
    save(package/'RELEASE-VERIFICATION.json', {'sourceModelQa': {'refinedMotionQa': receipt}})
    (package/'REFINED-MOTION-QA.json').write_bytes(f['report_path'].read_bytes())
    assert validate_refined_release_evidence(f['bundle'], f['metadata'], finalized=True) == []
    (package/'REFINED-MOTION-QA.json').write_bytes(b'changed')
    assert any('Packaged refined report' in e for e in validate_refined_release_evidence(f['bundle'], f['metadata'], finalized=True))
    assert validate_refined_release_evidence(f['bundle'], f['metadata']) == []  # pre-finalize build inventory


def test_finalized_refined_metadata_cannot_omit_receipt(synthetic):
    f = synthetic; save(f['bundle'].parent/'RELEASE-VERIFICATION.json', {'sourceModelQa': {}})
    assert any('no supplemental' in e for e in validate_refined_release_evidence(f['bundle'], f['metadata'], finalized=True))


def test_finalized_refined_receipt_cannot_be_bypassed_by_removing_metadata_revision(synthetic):
    f = synthetic; receipt = gate.verify_refined_motion_evidence(f['bundle'], f['report_path'])
    save(f['bundle'].parent/'RELEASE-VERIFICATION.json', {'sourceModelQa': {'refinedMotionQa': receipt}})
    legacy = {'refinement': {'version': 5}}
    save(f['bundle']/'assets/live2d/Maple/Maple.pet.json', legacy)
    assert any('different model revision' in e for e in validate_refined_release_evidence(f['bundle'], legacy, finalized=True))


@pytest.mark.parametrize('version', [4, 5])
def test_legacy_finalized_evidence_keeps_its_original_contract(tmp_path, version):
    bundle = tmp_path/'package/CuteMaple-Live2D'; bundle.mkdir(parents=True)
    save(bundle.parent/'RELEASE-VERIFICATION.json', {'sourceModelQa': {'modelVersion': f'v{version}'}})
    assert validate_refined_release_evidence(bundle, {'refinement': {'version': version}}, finalized=True) == []


def test_main_native_uv_evidence_must_match_supplement(synthetic):
    f = synthetic
    with pytest.raises(ValueError, match='different UV evidence'):
        gate.verify_refined_motion_evidence(f['bundle'], f['report_path'], native_uv={'different': True})
