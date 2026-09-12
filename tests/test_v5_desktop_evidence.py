"""Synthetic regression fixtures only. No program, hardware action or release is executed."""
from copy import deepcopy
import json

import pytest

from test_desktop_combination import evidence, write, sha
from tools.combine_desktop_evidence import reconstruct, resolve_combination, Reader
from tools.validate_v5_desktop import PAUSED_CHECKS, paused_record


def drag(edge='left', corner=False, resume=False):
    binding = [1, 2, 'swing_idle' if edge == 'top' else 'climb_'+edge]
    def sample(at, paused=True, seconds=1., updates=10):
        return {'at': at, 'binding': binding, 'window': [0, 100, 200, 200], 'visiblePixels': 500,
                'nativeVertexSignature': {'floatCount': 240, 'hash32': 123},
                'playback': {'token': 2, 'name': binding[2], 'paused': paused, 'evaluated': True,
                             'motionTimeSeconds': seconds, 'nativeUpdateCount': updates, 'staticSampleCount': 1}}
    row = {'staticVerified': True, 'pausedSettingAtPress': True, 'status': 'static-verified',
           'moveEvents': 2, 'pointerTravel': 80, 'windowTravel': 80, 'pressedAt': 10., 'releasedAt': 10.2,
           'expectedSettle': edge, 'settlement': {'contact': edge}, 'generation': 1, 'settledBinding': binding,
           'cornerTop': corner, 'staticSamples': [sample(10.4), sample(11.)], 'resumeVerified': resume}
    if resume:
        row.update(status='passed', lastPausedAt=11., resumedAt=11.1,
                   resumeSamples=[sample(11.1, False, 1.1, 11), sample(11.4, False, 1.4, 12)])
    return row


@pytest.fixture
def v5_evidence(evidence, monkeypatch):
    evidence['complete_base']()
    root = evidence['base'].parent
    path = root/'soak/desktop-report.json'
    report = json.loads(path.read_text())
    first, second = '2026-09-09T00:12:00+00:00', '2026-09-09T00:13:00+00:00'
    providers = lambda endpoint: {'keyboard': {'state': 'ready', 'lowLevelHook': True, 'hookActivityCount': 2},
                                  'audio': {'state': 'ready', 'endpointId': endpoint, 'peak': .3}}
    report.update(pausedDrags=[drag('left', resume=True), drag('right'), drag('top'), drag('top', corner=True)],
                  pausedDragChecks={key: True for key in PAUSED_CHECKS}, simultaneousKeyboardAudioObserved=True,
                  providerStatusHistory=[{'at': first, 'status': providers('speaker-a')},
                                         {'at': second, 'status': providers('speaker-b')}],
                  reactionObservations=[{'at': second, 'binding': [1, 2, 'idle'], 'captureBinding': [1, 2, 'idle'],
                    'keyboardRecent': True, 'audioRecent': True, 'typing': 1., 'listening': 1., 'visiblePixels': 1000,
                    'providerStatus': providers('speaker-b'), 'actualVisibleEffects': {
                        'visiblePixels': 150, 'drawnCount': 2, 'kinds': ['leaf', 'note'],
                        'particles': [{'kind': kind, 'x': .5, 'y': .4, 'radius': .03} for kind in ('leaf', 'note')]}}])
    write(path, report)
    base = json.loads(evidence['base'].read_text())
    for key in ('pausedDrags', 'pausedDragChecks', 'simultaneousKeyboardAudioObserved',
                'providerStatusHistory', 'reactionObservations'):
        base[key] = report[key]
    package_hashes = {'CuteMaple-Live2D.exe': evidence['exe']['sha256'], 'cleaner/CuteMaple-Cleaner.exe': 'synthetic-helper'}
    base.update(packageUnchanged=True, packageSha256=package_hashes)
    write(evidence['base'], base)
    note = root/'synthetic-observation-note.txt'
    note.write_text('SYNTHETIC UNIT TEST OBSERVATION, NOT HARDWARE EVIDENCE')
    review_path = root/'synthetic-v5-review.json'
    item = {'report': {'path': str(path), 'sha256': sha(path)}, 'pid': report['pid'],
            'method': 'Synthetic unit test, never use as a review', 'observedAt': second,
            'evidence': [{'path': str(note), 'sha256': sha(note)}]}
    review = {'syntheticTestFixture': True, 'schemaVersion': 1, 'reportKind': 'cutemaple-v5-interaction-review',
              'status': 'accepted', 'reviewer': 'Synthetic test only', 'method': 'Synthetic unit test',
              'reviewedAt': '2026-09-09T02:00:00+00:00', 'packageSha256': package_hashes,
              'checks': {'toDeskKeyboard': dict(item, inputSource='ToDesk', reactionIndex=0),
                         'audioDeviceChange': dict(item, beforeHistoryIndex=0, afterHistoryIndex=1)}}
    write(review_path, review)
    original = Reader.json
    def read_test_fixture(reader, path):
        value = original(reader, path)
        value.pop('syntheticTestFixture', None)  # TEST ONLY; production has no bypass.
        return value
    monkeypatch.setattr(Reader, 'json', read_test_fixture)
    def refresh(change):
        report = json.loads(path.read_text()); change(report); write(path, report)
        base = json.loads(evidence['base'].read_text())
        for key in ('pausedDrags', 'pausedDragChecks', 'simultaneousKeyboardAudioObserved',
                    'providerStatusHistory', 'reactionObservations'):
            base[key] = report[key]
        write(evidence['base'], base)
        review = json.loads(review_path.read_text())
        for row in review['checks'].values(): row['report']['sha256'] = sha(path)
        write(review_path, review)
    make = lambda: reconstruct(evidence['base'], [], None, evidence['exe'], evidence['assets'],
                               model_version=5, interaction_review_path=review_path, package_hashes=package_hashes)
    return dict(evidence, make=make, refresh=refresh, review=review_path, package_hashes=package_hashes,
                original_reader=original)


def test_complete_v5_original_sessions_reconstruct_and_reopen(v5_evidence):
    e = v5_evidence
    value = e['make']()
    assert value['derivedReport']['durationSeconds'] == 3600 and value['supplements'] == []
    assert all(value['derivedReport']['v5InteractionEvidence']['pausedDragChecks'].values())
    assert resolve_combination(value, e['exe']['sha256'], e['assets'], model_version=5,
                               package_hashes=e['package_hashes']) == value['derivedReport']


def test_synthetic_marker_is_rejected_by_production_v5_gate(v5_evidence, monkeypatch):
    monkeypatch.setattr(Reader, 'json', v5_evidence['original_reader'])
    with pytest.raises(ValueError, match='Invalid or differently packaged'):
        v5_evidence['make']()


@pytest.mark.parametrize('mutation,expected', [
    (lambda r: r['reactionObservations'][0]['actualVisibleEffects'].update(visiblePixels=0), 'simultaneous'),
    (lambda r: r['reactionObservations'][0].update(audioRecent=False), 'simultaneous'),
    (lambda r: r['reactionObservations'][0].update(captureBinding=[1, 99, 'idle']), 'simultaneous'),
    (lambda r: r['reactionObservations'][0]['actualVisibleEffects']['particles'][1].update(x=1.5), 'simultaneous'),
    (lambda r: r['pausedDrags'][0]['staticSamples'][1]['playback'].update(motionTimeSeconds=2), 'clock moved'),
    (lambda r: r['pausedDrags'][0]['resumeSamples'][1]['playback'].update(motionTimeSeconds=8), 'consumed paused'),
    (lambda r: r['pausedDrags'].pop(), 'cornerTop'),
    (lambda r: r['providerStatusHistory'][1]['status']['audio'].update(endpointId='speaker-a'), 'distinct endpoints'),
    (lambda r: r['providerStatusHistory'][1]['status']['audio'].update(peak=0), 'measure output'),
    (lambda r: r['reactionObservations'][0]['providerStatus']['keyboard'].update(hookActivityCount=0), 'compatibility-hook'),
])
def test_v5_missing_actual_observations_cannot_be_repaired_by_true_flags(v5_evidence, mutation, expected):
    v5_evidence['refresh'](mutation)
    with pytest.raises(ValueError, match=expected): v5_evidence['make']()


def test_reopened_v5_review_and_full_bundle_are_not_cached_booleans(v5_evidence):
    e = v5_evidence
    value = e['make']()
    with pytest.raises(ValueError, match='complete bundle-bound'):
        resolve_combination(value, e['exe']['sha256'], e['assets'], model_version=5,
                            package_hashes={**e['package_hashes'], 'cleaner/CuteMaple-Cleaner.exe': 'changed'})
    review = json.loads(e['review'].read_text()); review['checks']['toDeskKeyboard']['pid'] += 1
    write(e['review'], review)
    with pytest.raises(ValueError, match='changed after review'):
        resolve_combination(value, e['exe']['sha256'], e['assets'], model_version=5, package_hashes=e['package_hashes'])


def test_v5_supplement_must_match_whole_bundle(v5_evidence):
    e = v5_evidence
    e['capture']()  # New raw supplement binding after the synthetic base update.
    with pytest.raises(ValueError, match='exact full base bundle'):
        reconstruct(e['base'], [e['supplement']], None, e['exe'], e['assets'], model_version=5,
                    interaction_review_path=e['review'], package_hashes=e['package_hashes'])


def test_paused_geometry_fractional_motion_drift_rejected():
    row = drag(resume=True)
    assert paused_record(row) == ('left', False, True)
    row['staticSamples'][1]['nativeVertexSignature']['hash32'] += 1
    with pytest.raises(ValueError, match='clock moved'): paused_record(row)
