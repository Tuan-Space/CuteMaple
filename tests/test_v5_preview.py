from tools.record_maple_preview import review_background, v5_cleanup_timeline
import json
import pytest
from tools.record_maple_preview import (PreviewDirector, PreviewStep, refined_preview_steps,
                                       planned_timeline, motion_durations, resting_tail_decision,
                                       capture_coverage)
from tools.record_maple_preview import preview_gaze_plan, send_preview_gaze


def test_cleanup_review_covers_complete_gestures_and_support_poses():
    timeline, duration = v5_cleanup_timeline()
    times = [row[0] for row in timeline]
    assert times == sorted(times) and duration > times[-1]
    for state in ('clean_ground', 'clean_climb_left', 'clean_climb_right', 'clean_top'):
        entry = next(row for row in timeline if row[1] == state + '_enter')
        loop = next(row for row in timeline if row[1] == state)
        exit_ = next(row for row in timeline if row[1] == state + '_exit')
        following = timeline[timeline.index(exit_) + 1]
        assert entry[2] == exit_[2] == 'one_shot'
        assert loop[0] - entry[0] >= .7 and following[0] - exit_[0] >= .65
        assert loop[2] == 'loop' and exit_[0] - loop[0] >= 1.5


def test_alpha_review_background_is_a_real_checkerboard():
    background = review_background((48, 48), 'checkerboard')
    assert background.getpixel((1, 1)) != background.getpixel((17, 1))
    assert background.getpixel((1, 1)) == background.getpixel((17, 17))
    assert background.getextrema()[3] == (255, 255)


def director_fixture(steps):
    plays, commands, completed = [], [], []
    def play(name, playback):
        plays.append((name, playback))
        return len(plays)
    director = PreviewDirector(steps, 7, play, lambda kind, **kw: commands.append({'type': kind, **kw}),
                               lambda: completed.append(True))
    director.advance(0)
    def event(kind, **fields):
        return {'type': kind, 'generation': 7, 'token': director.token,
                'name': steps[director.index].name, **fields}
    return director, plays, commands, completed, event


@pytest.mark.parametrize('next_state', ['climb_to_top_left', 'clean_climb_left_enter'])
def test_recorder_waits_for_real_endpoint_instead_of_switching_at_wall_time(next_state):
    steps = [PreviewStep('climb_left', dwell=.8), PreviewStep(next_state, 'one_shot', endpoint_before=True),
             PreviewStep('idle', dwell=1)]
    director, plays, commands, _, event = director_fixture(steps)
    director.poll(.8)
    assert commands == [{'type': 'climb-endpoint', 'name': 'climb_left', 'token': 1, 'request': 1, 'enabled': True}]
    director.poll(100)
    assert plays == [('climb_left', 'loop')]
    endpoint = event('geometry', climbPhase=1, climbCycle=2, climbEndpoint={'request': 1, 'cycle': 2},
                     bounds=[0, 0, 1, 1], anchors={'gripLeft': [.3, .4]})
    for patch in ({'generation': 6}, {'token': 0}, {'name': 'climb_right'}, {'climbPhase': .7},
                  {'climbCycle': 3}, {'climbEndpoint': {'request': 2, 'cycle': 2}}, {'bounds': [0, 0, 0, 0]}):
        director.event({**endpoint, **patch}, 100)
        assert len(plays) == 1
    director.event(endpoint, 101)
    assert plays[-1] == (next_state, 'one_shot')
    assert director.trace[-1]['time'] == 101
    director.event(endpoint, 102)
    assert len(plays) == 2


@pytest.mark.parametrize('name', ['land', 'sleep_enter', 'sleep_exit', 'happy'])
def test_motion_completion_cannot_be_replaced_by_elapsed_wall_time_or_wrong_native_token(name):
    director, plays, _, completed, event = director_fixture([
        PreviewStep(name, 'one_shot'), PreviewStep('idle', dwell=1)])
    director.poll(600)
    assert plays == [(name, 'one_shot')] and not completed
    valid = event('finished', cycle=1)
    for patch in ({'token': 999}, {'generation': 8}, {'type': 'cycle'}, {'cycle': True}):
        director.event({**valid, **patch}, 600)
        assert len(plays) == 1
    director.event(valid, 601)
    assert plays[-1] == ('idle', 'loop')
    director.poll(601.5)
    assert not completed
    director.poll(602)
    assert completed == [True] and director.completed


def test_cleanup_loop_waits_for_its_actual_required_cycles():
    director, plays, _, _, event = director_fixture([
        PreviewStep('clean_ground', cycles=2), PreviewStep('clean_ground_exit', 'one_shot')])
    director.poll(100)
    director.event(event('cycle', cycle=1), 101)
    assert len(plays) == 1
    director.event(event('cycle', cycle=2), 102)
    assert plays[-1] == ('clean_ground_exit', 'one_shot')


def test_refined_plans_include_full_drop_and_wake_continuity_and_only_real_climb_entries():
    for scenario in ('locomotion', 'drop-land-idle'):
        plan = refined_preview_steps(scenario)
        names = [step.name for step in plan]
        at = names.index('fall_float')
        assert names[at:at + 3] == ['fall_float', 'land', 'idle']
        assert plan[at + 1].playback == 'one_shot' and plan[at + 1].dwell is None
    for scenario in ('locomotion', 'sleep-exit-idle'):
        plan = refined_preview_steps(scenario)
        names = [step.name for step in plan]
        at = names.index('sleep_exit')
        assert names[at + 1] == 'idle' and plan[at].dwell is None
        assert next(step for step in plan if step.name == 'sleep_enter').dwell is None
    assert next(step for step in refined_preview_steps('overview') if step.name == 'happy').playback == 'one_shot'
    cleanup = refined_preview_steps('cleanup')
    assert {step.name for step in cleanup if step.endpoint_before} == {'clean_climb_left_enter', 'clean_climb_right_enter'}


def test_nominal_timeline_uses_actual_referenced_motion_durations_but_never_drives_completion(tmp_path):
    plan = refined_preview_steps('sleep-exit-idle')
    actual = {'sleep_enter': 2.75, 'sleep_loop': 3.2, 'sleep_exit': 2.25, 'idle': 4.}
    settings = {'FileReferences': {'Motions': {}}}
    for name, seconds in actual.items():
        (tmp_path / f'{name}.json').write_text(json.dumps({'Meta': {'Duration': seconds}}))
        settings['FileReferences']['Motions'][name] = [{'File': name + '.json'}]
    durations = motion_durations(tmp_path, settings, plan)
    timeline, total = planned_timeline(plan, durations)
    assert timeline == [(0., 'sleep_enter', 'one_shot'), (2.75, 'sleep_loop', 'loop'),
                        (4.75, 'sleep_exit', 'one_shot'), (7., 'idle', 'loop')]
    assert total == 13.
    (tmp_path / 'sleep_exit.json').write_text(json.dumps({'Meta': {'Duration': 0}}))
    with pytest.raises(ValueError, match='Invalid actual motion duration'):
        motion_durations(tmp_path, settings, plan)


def coverage_fixture():
    """Synthetic unit fixture using the shape/counts/clocks of real native06.

    These generated observations are test inputs, never release evidence.
    """
    from tools.record_maple_preview import capture_coverage
    plan = refined_preview_steps('sleep-exit-idle')
    durations = {'sleep_enter': 1.34, 'sleep_loop': 3.1, 'sleep_exit': 1.05, 'idle': 2.92}
    trace, snapshots = [], []
    groups = [(0., 11, .15, 1.3832, True), (1.51794, 13, 1.4664, 3.2997, False),
              (3.52019, 9, 3.4664, 4.3831, True), (4.91556, 67, 4.5997, 13.8828, True)]
    for index, (step, group) in enumerate(zip(plan, groups)):
        at, count, first, last, initial_unevaluated = group
        token = index + 2
        trace.append({'kind': 'play', 'time': at, 'name': step.name, 'playback': step.playback,
                      'token': token, 'generation': 1})
        valid_count = count - int(initial_unevaluated)
        if initial_unevaluated:
            snapshots.append({'time': at + .1, 'visiblePixels': 48811, 'playback': {
                'token': token, 'name': step.name, 'evaluated': False,
                'nativeUpdateCount': 0, 'motionTimeSeconds': first}})
        for number in range(valid_count):
            clock = first + (last - first) * number / (valid_count - 1)
            snapshots.append({'time': at + .2 + clock - first, 'visiblePixels': 48811, 'playback': {
                'token': token, 'name': step.name, 'evaluated': True,
                'nativeUpdateCount': index * 1000 + number + 1, 'motionTimeSeconds': clock}})
    return capture_coverage, plan, durations, trace, snapshots


def test_each_native_play_has_its_own_capture_coverage():
    check, plan, durations, trace, snapshots = coverage_fixture()
    report = check(plan, durations, trace, snapshots)
    assert report['passed']
    assert [row['frameCount'] for row in report['segments']] == [10, 13, 8, 66]
    assert [row['nativeTimeSpanSeconds'] for row in report['segments']] == pytest.approx(
        [1.2332, 1.8333, .9167, 9.2831])
    assert [row['expectedDurationSeconds'] for row in report['segments']] == [1.34, 2, 1.05, 6]


@pytest.mark.parametrize('failure', ['missing-exit', 'duplicate-updates', 'narrow-span', 'not-evaluated', 'invisible'])
def test_idle_padding_cannot_replace_missing_short_motion_coverage(failure):
    check, plan, durations, trace, snapshots = coverage_fixture()
    if failure == 'missing-exit':
        snapshots = [row for row in snapshots if row['playback']['name'] != 'sleep_exit']
    for row in snapshots:
        if row['playback']['name'] == 'sleep_exit':
            if failure == 'duplicate-updates':
                row['playback']['nativeUpdateCount'] = 2001
            elif failure == 'narrow-span':
                row['playback']['motionTimeSeconds'] = 3.5 + row['playback']['nativeUpdateCount'] * .00001
            elif failure == 'not-evaluated':
                row['playback']['evaluated'] = False
            elif failure == 'invisible':
                row['visiblePixels'] = 0
    snapshots.extend([snapshots[-1]] * 100)
    report = check(plan, durations, trace, snapshots)
    assert len(snapshots) >= 100 and not report['passed']
    assert not report['segments'][2]['passed'] and report['segments'][3]['passed']
    assert all(row['passed'] for row in report['segments'][:2])


def test_delayed_qwebchannel_callback_is_attributed_to_native_token_not_receipt_time():
    check, plan, durations, trace, snapshots = coverage_fixture()
    before = check(plan, durations, trace, snapshots)
    for row in snapshots:
        # All short-state replies arrive after idle started, even out of order.
        row['time'] += 20 if row['playback']['name'] != 'idle' else 0
    snapshots.reverse()
    after = check(plan, durations, trace, snapshots)
    assert after['passed']
    assert [row['frameCount'] for row in after['segments']] == [row['frameCount'] for row in before['segments']]
    assert [row['nativeTimeSpanSeconds'] for row in after['segments']] == pytest.approx(
        [row['nativeTimeSpanSeconds'] for row in before['segments']])


@pytest.mark.parametrize('change', ['mixed-generation', 'reused-token', 'missing-play', 'wrong-play-name'])
def test_ambiguous_native_play_trace_cannot_borrow_capture_frames(change):
    check, plan, durations, trace, snapshots = coverage_fixture()
    if change == 'mixed-generation':
        trace[2]['generation'] = 2
    elif change == 'reused-token':
        trace[2]['token'] = trace[0]['token']
    elif change == 'missing-play':
        trace.pop(2)
    else:
        trace[2]['name'] = 'idle'
    assert not check(plan, durations, trace, snapshots)['passed']


def test_cycle_coverage_requires_the_whole_requested_number_of_cycles():
    check, _, _, _, snapshots = coverage_fixture()
    plan = [PreviewStep('sleep_loop', cycles=2)]
    trace = [{'kind': 'play', 'name': 'sleep_loop', 'playback': 'loop', 'token': 3, 'generation': 1}]
    report = check(plan, {'sleep_loop': 3.1}, trace, snapshots)
    assert report['segments'][0]['expectedDurationSeconds'] == 6.2
    assert not report['passed']  # The fixture captures less than a single 3.1s cycle.


def test_identically_named_idle_steps_cannot_share_later_token_frames():
    check, _, durations, _, snapshots = coverage_fixture()
    plan = [PreviewStep('idle', dwell=.6), PreviewStep('idle', dwell=6)]
    trace = [{'kind': 'play', 'name': 'idle', 'playback': 'loop', 'token': token, 'generation': 1}
             for token in (1, 5)]
    report = check(plan, durations, trace, snapshots)
    assert not report['passed']
    assert report['segments'][0]['frameCount'] == 0 and report['segments'][1]['passed']


def short_tail_fixture():
    """Synthetic replay of the native10 callback timing; not release evidence."""
    steps = [PreviewStep('clean_top_exit', 'one_shot'), PreviewStep('swing_idle', dwell=.6)]
    durations = {'clean_top_exit': .65, 'swing_idle': 2.4}
    trace = [{'kind': 'play', 'time': at, 'name': step.name, 'playback': step.playback,
              'token': token, 'generation': 1}
             for step, at, token in zip(steps, (18.2313, 19.0614), (21, 22))]
    snapshots = []
    for token, name, times, first_update in (
        (21, 'clean_top_exit', [17.29927, 17.49927, 17.83247], 1142),
        (22, 'swing_idle', [18.06587, 18.18247, 18.31587, 18.41587], 1185)):
        for number, native_time in enumerate(times):
            snapshots.append({'time': native_time+1.24, 'visiblePixels': 48000,
                              'playback': {'token': token, 'name': name, 'evaluated': True,
                                           'nativeUpdateCount': first_update+number,
                                           'motionTimeSeconds': native_time}})
    return steps, durations, trace, snapshots


def test_schedule_completion_keeps_capturing_the_short_native_final_tail():
    steps, durations, trace, snapshots = short_tail_fixture()
    original = capture_coverage(steps, durations, trace, snapshots)
    assert original['minimumSpanFraction'] == .6
    assert original['segments'][0]['passed'] and not original['segments'][1]['passed']
    assert original['segments'][1]['nativeTimeSpanSeconds'] == pytest.approx(.35)
    assert not resting_tail_decision(steps, durations, trace, snapshots, 146)['stop']
    # One more real frame passes the existing 60% gate, but retain the full
    # requested .6s native resting observation before ending the review.
    def captured(native_time, update):
        return {'time': 20, 'visiblePixels': 48000, 'playback': {
            'token': 22, 'name': 'swing_idle', 'evaluated': True,
            'nativeUpdateCount': update, 'motionTimeSeconds': native_time}}
    snapshots.append(captured(18.51587, 1215))
    assert capture_coverage(steps, durations, trace, snapshots)['passed']
    assert not resting_tail_decision(steps, durations, trace, snapshots, 147)['stop']
    snapshots.append(captured(18.68247, 1225))
    decision = resting_tail_decision(steps, durations, trace, snapshots, 148)
    assert decision['stop'] and decision['reason'] == 'complete-native-resting-observation'
    assert decision['finalCapturedNativeSpanSeconds'] == pytest.approx(.6166)
    assert not resting_tail_decision(steps, durations, trace, snapshots, 99)['stop']


@pytest.mark.parametrize('invalid', ['wrong-token', 'wrong-generation', 'duplicate-update', 'unevaluated'])
def test_late_unrelated_capture_cannot_complete_final_observation(invalid):
    steps, durations, trace, snapshots = short_tail_fixture()
    late = {'visiblePixels': 48000, 'playback': {
        'token': 22, 'generation': 1, 'name': 'swing_idle', 'evaluated': True,
        'nativeUpdateCount': 1300, 'motionTimeSeconds': 99.}}
    if invalid == 'wrong-token':
        late['playback']['token'] = 21
    elif invalid == 'wrong-generation':
        late['playback']['generation'] = 2
    elif invalid == 'duplicate-update':
        late['playback']['nativeUpdateCount'] = 1185
    else:
        late['playback']['evaluated'] = False
    snapshots.append(late)
    assert not resting_tail_decision(steps, durations, trace, snapshots, 200)['stop']


def test_final_idle_extension_cannot_hide_missing_earlier_motion_captures():
    steps, durations, trace, snapshots = short_tail_fixture()
    snapshots = [s for s in snapshots if s['playback']['token'] == 22]
    decision = resting_tail_decision(steps, durations, trace, snapshots, 146)
    assert decision['stop'] and decision['reason'] == 'earlier-capture-coverage-failed'
    assert not capture_coverage(steps, durations, trace, snapshots)['passed']


def test_cleanup_final_rest_has_room_to_review_the_returned_hand_and_fan():
    steps = refined_preview_steps('cleanup')
    assert steps[-2].name == 'clean_top_exit' and steps[-2].playback == 'one_shot'
    assert steps[-1].name == 'swing_idle' and steps[-1].playback == 'loop'
    assert steps[-1].dwell >= 2


def test_default_climb_gaze_stays_disabled_and_overview_keeps_its_original_script():
    for scenario in ('climb-left', 'climb-right', 'cleanup', 'sleep-exit-idle'):
        assert preview_gaze_plan(scenario) == [{'time': 0., 'x': 0., 'y': 0., 'enabled': False}]
    overview = preview_gaze_plan('overview')
    assert [(c['time'], c['x'], c['y']) for c in overview] == [(0, 0, 0), (1, -.9, .2), (2.5, .9, .2), (8, 0, 0)]
    assert all(c['enabled'] for c in overview)


@pytest.mark.parametrize('target', [(1, -1), (-1, -1), (.2, .4)])
def test_explicit_gaze_is_one_persistent_production_command_without_later_reset(target):
    for scenario in ('climb-left', 'climb-right', 'overview'):
        commands = preview_gaze_plan(scenario, list(target))
        assert len(commands) == 1 and commands[0]['enabled']
        sent, trace = [], []
        send_preview_gaze(lambda kind, **values: sent.append((kind, values)), commands[0], 7, .031, trace)
        assert sent == [('gaze', {'x': target[0], 'y': target[1], 'enabled': True})]
        assert trace == [{'time': .031, 'generation': 7, 'type': 'gaze',
                          'x': target[0], 'y': target[1], 'enabled': True}]
        assert commands[0]['time'] == 0  # Planned time never masquerades as send time.


@pytest.mark.parametrize('target', [[float('nan'), 0], [0, float('inf')], [-1.01, 0], [0, 1.01], [True, 0], [0]])
def test_invalid_gaze_target_is_rejected_instead_of_silently_clipped(target):
    with pytest.raises(ValueError, match='finite coordinates'):
        preview_gaze_plan('climb-left', target)
