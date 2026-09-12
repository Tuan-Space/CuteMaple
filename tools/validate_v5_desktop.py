"""Recheck v5 observations from original ordinary PetWindow sessions, without input injection."""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path

PAUSED_CHECKS = ('left', 'right', 'top', 'cornerTop', 'pausedStatePreserved',
                 'staticGeometry', 'noMotionWhilePaused', 'resumeWithoutCatchUp')


def require(value, message):
    if not value:
        raise ValueError(message)


def finite(value, minimum=-float('inf')):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'v5 observation timestamps must be zoned')
    return result


def reaction(row, simultaneous=False):
    binding = row.get('binding', [])
    require(len(binding) == 3 and row.get('captureBinding') == binding and finite(row.get('visiblePixels'), 50),
            'v5 reaction needs actual current native capture binding and visible model pixels')
    if simultaneous:
        require(row.get('keyboardRecent') is True and row.get('audioRecent') is True and
                finite(row.get('typing'), .20000001) and finite(row.get('listening'), .20000001),
                'Both reactions must occur in the same actual capture')
        effects = row.get('actualVisibleEffects', {})
        require(finite(effects.get('visiblePixels'), 1) and finite(effects.get('drawnCount'), 2)
                and {'leaf', 'note'}.issubset(effects.get('kinds', [])),
                'Simultaneous response needs actual visible leaf/note canvas readback')
        for kind in ('leaf', 'note'):
            require(any(p.get('kind') == kind and finite(p.get('x')) and finite(p.get('y'))
                        and finite(p.get('radius'), .000001) and 0 < p['x']-p['radius'] < p['x']+p['radius'] < 1
                        and 0 < p['y']-p['radius'] < p['y']+p['radius'] < 1 for p in effects.get('particles', [])),
                    f'Actual {kind} particle must be inside the drawn canvas, not only queued offscreen')


def paused_record(row):
    """Recompute static/resume invariants instead of trusting the eight check flags."""
    require(row.get('staticVerified') is True and row.get('pausedSettingAtPress') is True
            and row.get('status') in ('static-verified', 'holding', 'passed') and row.get('moveEvents', 0) > 0
            and finite(row.get('pointerTravel'), 6) and finite(row.get('windowTravel'), 6)
            and finite(row.get('pressedAt'), 0) and finite(row.get('releasedAt'), row['pressedAt'])
            and row['releasedAt'] > row['pressedAt'], 'Paused drag lacks an actual completed physical gesture')
    edge, binding = row.get('expectedSettle'), row.get('settledBinding', [])
    require(edge in ('left', 'right', 'top') and row.get('settlement', {}).get('contact') == edge
            and len(binding) == 3 and binding[0] == row.get('generation'), 'Paused drag settlement/binding mismatch')
    require(not row.get('cornerTop') or edge == 'top', 'Paused corner must attach to the top')
    samples = row.get('staticSamples', [])
    require(len(samples) >= 2 and samples[-1]['at']-samples[0]['at'] >= .5,
            'Paused geometry must be observed unchanged for at least half a second')
    first = samples[0]
    signature, clock = first.get('nativeVertexSignature', {}), first.get('playback', {})
    require(isinstance(signature.get('floatCount'), int) and signature['floatCount'] > 0
            and isinstance(signature.get('hash32'), int) and 0 <= signature['hash32'] <= 0xffffffff
            and finite(clock.get('motionTimeSeconds'), 0) and isinstance(clock.get('nativeUpdateCount'), int),
            'Missing actual native vertex signature/clock')
    previous = row['releasedAt']
    for sample in samples:
        play = sample.get('playback', {})
        require(finite(sample.get('at'), previous) and sample.get('binding') == binding
                and [binding[0], play.get('token'), play.get('name')] == binding
                and play.get('paused') is True and play.get('evaluated') is True and play.get('staticSampleCount', 0) >= 1
                and finite(sample.get('visiblePixels'), 50) and sample.get('nativeVertexSignature') == signature
                and finite(play.get('motionTimeSeconds'), 0) and abs(play['motionTimeSeconds']-clock['motionTimeSeconds']) <= 1e-6
                and play.get('nativeUpdateCount') == clock['nativeUpdateCount']
                and len(sample.get('window', [])) == 4 and len(first.get('window', [])) == 4
                and max(abs(a-b) for a, b in zip(sample['window'], first['window'])) <= 1,
                'Paused native geometry, window, binding or clock moved')
        previous = sample['at']
    resumed = row.get('resumeVerified') is True
    if resumed:
        after = row.get('resumeSamples', [])
        require(row.get('status') == 'passed' and len(after) >= 2 and finite(row.get('resumedAt'), previous)
                and finite(row.get('lastPausedAt'), previous) and after[-1]['at']-row['resumedAt'] >= .2,
                'Resume needs two actual samples after the verified pause')
        baseline = samples[-1]['playback']
        for sample in after:
            play = sample.get('playback', {})
            delta = play.get('motionTimeSeconds', float('nan'))-baseline['motionTimeSeconds']
            require(finite(sample.get('at'), previous) and sample.get('binding') == binding
                    and play.get('paused') is False and play.get('evaluated') is True
                    and play.get('token') == binding[1] and play.get('name') == binding[2]
                    and finite(delta, -1e-6) and delta <= sample['at']-row['lastPausedAt']+.1,
                    'Resumed motion consumed paused time or changed native binding')
            previous = sample['at']
        require(after[-1]['playback']['motionTimeSeconds'] > baseline['motionTimeSeconds']
                and after[-1]['playback']['nativeUpdateCount'] > baseline['nativeUpdateCount'],
                'Actual native activity did not resume')
    return edge, bool(row.get('cornerTop')), resumed


def validate_sessions(reader, runs, review_path, package_hashes):
    """Runs were already reopened and lifecycle/security-checked by the combiner."""
    require(review_path is not None, 'v5 needs an independent ToDesk and audio-device observation review')
    review = reader.json(review_path)
    require(review.get('schemaVersion') == 1 and review.get('reportKind') == 'cutemaple-v5-interaction-review'
            and review.get('status') == 'accepted' and review.get('reviewer') and review.get('method')
            and not any(review.get(k) for k in ('synthetic', 'syntheticTestFixture', 'diagnosticOnly', 'mock'))
            and review.get('packageSha256') == package_hashes, 'Invalid or differently packaged v5 interaction review')
    reviewed = stamp(review.get('reviewedAt'))
    eligible, checks, selected = {}, set(), {}
    for run in runs:
        report = run['report']
        require(not any(report.get(k) for k in ('synthetic', 'syntheticTestFixture', 'diagnosticOnly', 'mock')),
                'Synthetic/provider-only evidence cannot establish v5 desktop interactions')
        require(run['end'] <= reviewed, 'Review predates the observed desktop session')
        eligible[str(run['path'])] = run
        for index, row in enumerate(report.get('reactionObservations', [])):
            if not run['start'] <= stamp(row['at']) <= run['end']:
                continue
            try:
                reaction(row, simultaneous=True)
            except (ValueError, TypeError, KeyError):
                continue
            if report.get('simultaneousKeyboardAudioObserved') is True:
                selected['simultaneousVisibleReactions'] = {'report': reader.reference(run['path']), 'pid': report['pid'], 'reactionIndex': index}
        for index, row in enumerate(report.get('pausedDrags', [])):
            if row.get('staticVerified') is not True:
                continue
            edge, corner, resumed = paused_record(row)
            fields = {edge, 'pausedStatePreserved', 'staticGeometry', 'noMotionWhilePaused'}
            if corner: fields.add('cornerTop')
            if resumed: fields.add('resumeWithoutCatchUp')
            require(all(report.get('pausedDragChecks', {}).get(k) is True for k in fields),
                    'Paused drag raw record and observer flags disagree')
            checks |= fields
            selected[f'pausedDrag:{edge}:{index}:{report["pid"]}'] = {'report': reader.reference(run['path']), 'pid': report['pid'], 'row': index}
    require(set(PAUSED_CHECKS).issubset(checks), 'v5 paused drag checks pending: '+', '.join(sorted(set(PAUSED_CHECKS)-checks)))
    require('simultaneousVisibleReactions' in selected, 'v5 simultaneous actual keyboard/audio visible effects remain pending')

    def reviewed_run(name):
        item = review.get('checks', {}).get(name, {})
        path = str(Path(item.get('report', {}).get('path', '')).resolve())
        require(path in eligible, f'{name}: review must reference a verified base or supplement original report')
        run = eligible[path]
        require(item.get('report') == reader.reference(run['path']) and item.get('pid') == run['report']['pid']
                and item.get('method') and run['start'] <= stamp(item['observedAt']) <= run['end'],
                f'{name}: exact report SHA, PID, observation time and actual method are required')
        evidence = item.get('evidence', [])
        require(evidence, f'{name}: the independent observation record is missing')
        for ref in evidence:
            reader.raw(ref['path'])
            require(reader.reference(ref['path']) == ref, f'{name}: observation source changed')
        return item, run

    typing, run = reviewed_run('toDeskKeyboard')
    require(typing.get('inputSource') == 'ToDesk', 'Input provenance must state the actually observed ToDesk source')
    row = run['report']['reactionObservations'][typing['reactionIndex']]
    reaction(row)
    keyboard = row.get('providerStatus', {}).get('keyboard', {})
    require(row.get('keyboardRecent') is True and finite(row.get('typing'), .20000001)
            and keyboard.get('state') == 'ready' and keyboard.get('lowLevelHook') is True
            and keyboard.get('hookActivityCount', 0) > 0
            and run['start'] <= stamp(row['at']) <= run['end']
            and abs((stamp(row['at'])-stamp(typing['observedAt'])).total_seconds()) <= 10,
            'ToDesk review lacks actual compatibility-hook and visible native typing evidence')
    device, run = reviewed_run('audioDeviceChange')
    history = run['report'].get('providerStatusHistory', [])
    before, after = (history[device[key]] for key in ('beforeHistoryIndex', 'afterHistoryIndex'))
    require(device['beforeHistoryIndex'] < device['afterHistoryIndex']
            and run['start'] <= stamp(before['at']) < stamp(after['at']) <= run['end']
            and stamp(before['at']) <= stamp(device['observedAt'])
            and (stamp(device['observedAt'])-stamp(after['at'])).total_seconds() <= 10,
            'Audio endpoint observations must be ordered within the same actual process')
    endpoints = []
    for row in (before, after):
        audio = row.get('status', {}).get('audio', {})
        require(audio.get('state') == 'ready' and audio.get('endpointId') and finite(audio.get('peak'), .000001),
                'Both actual audio endpoints must be ready and measure output; a device list is insufficient')
        endpoints.append(audio['endpointId'])
    require(endpoints[0] != endpoints[1], 'Audio device change requires two actually sampled distinct endpoints')
    def actual_listening(row):
        try:
            reaction(row)
            return (row.get('audioRecent') is True and finite(row.get('listening'), .20000001)
                and row.get('providerStatus', {}).get('audio', {}).get('endpointId') == endpoints[1]
                and stamp(after['at']) <= stamp(row['at']) <= run['end'])
        except (ValueError, TypeError, KeyError):
            return False
    require(any(actual_listening(r) for r in run['report'].get('reactionObservations', [])),
            'New audio endpoint needs a subsequent actual visible native listening response')
    return {'modelVersion': 'v5', 'passed': True, 'pausedDragChecks': {k: True for k in PAUSED_CHECKS},
            'observations': selected, 'interactionReview': reader.reference(review_path), 'audioEndpointIds': endpoints,
            'toDeskKeyboard': typing, 'audioDeviceChange': device}
