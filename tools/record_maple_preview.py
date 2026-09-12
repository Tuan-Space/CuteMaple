"""Record continuous review clips from the real production Cubism renderer.

Every frame is captured from one running native model. State changes go through
the same QWebChannel commands and Cubism fades as the pet; no pose screenshots
are cross-cut to simulate motion. Requires the existing Pillow dependency only.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-compositing --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader --ignore-gpu-blocklist")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_OPENGL", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
import live2d_host
from live2d_host import Live2DHost, register_live2d_scheme
from tools.qa_resource_snapshot import resource_snapshot, changed_resources


def review_background(size: tuple[int, int], kind: str) -> Image.Image:
    colors = {'light': (247, 246, 243, 255), 'black': (0, 0, 0, 255),
              'white': (255, 255, 255, 255), 'checkerboard': (232, 232, 232, 255)}
    result = Image.new('RGBA', size, colors[kind])
    if kind == 'checkerboard':
        draw = ImageDraw.Draw(result)
        for y in range(0, size[1], 16):
            for x in range(0, size[0], 16):
                if (x // 16 + y // 16) % 2:
                    draw.rectangle((x, y, x + 15, y + 15), fill=(184, 184, 184, 255))
    return result


def preview_gaze_plan(scenario: str, sustained: list[float] | None = None) -> list[dict]:
    """Explicit production gaze commands; never write model parameters here."""
    if sustained is not None:
        if (len(sustained) != 2 or any(isinstance(v, bool) or not isinstance(v, (float, int))
                or not math.isfinite(v) or not -1 <= v <= 1 for v in sustained)):
            raise ValueError('Sustained gaze requires two finite coordinates in [-1, 1]')
        return [{'time': 0., 'x': float(sustained[0]), 'y': float(sustained[1]), 'enabled': True}]
    if scenario != 'overview':
        return [{'time': 0., 'x': 0., 'y': 0., 'enabled': False}]
    return [{'time': at, 'x': x, 'y': y, 'enabled': True}
            for at, x, y in ((0., 0., 0.), (1., -.9, .2), (2.5, .9, .2), (8., 0., 0.))]


def send_preview_gaze(send, command: dict, generation: int, elapsed: float, trace: list[dict]) -> None:
    """Retain what was actually sent through the normal QWebChannel path."""
    payload = {key: command[key] for key in ('x', 'y', 'enabled')}
    send('gaze', **payload)
    trace.append({'time': elapsed, 'generation': generation, 'type': 'gaze', **payload})


def v5_cleanup_timeline() -> tuple[list[tuple[float, str, str]], float]:
    schedule = [(0., 'idle', 'loop')]
    at = .6
    for pose, state in (('idle', 'clean_ground'), ('climb_left', 'clean_climb_left'),
                        ('climb_right', 'clean_climb_right'), ('swing_idle', 'clean_top')):
        schedule.extend(((at, pose, 'loop'), (at + .8, state + '_enter', 'one_shot'),
                         (at + 1.55, state, 'loop'), (at + 3.35, state + '_exit', 'one_shot'),
                         (at + 4.05, pose, 'loop')))
        at += 4.65
    return schedule, at + .5


@dataclass(frozen=True)
class PreviewStep:
    name: str
    playback: str = 'loop'
    dwell: float | None = None
    cycles: int | None = None
    endpoint_before: bool = False


def refined_preview_steps(scenario: str) -> list[PreviewStep]:
    """Only loop dwell uses wall time; semantic completions use native events."""
    loop = lambda name, seconds: PreviewStep(name, dwell=seconds)
    once = lambda name, endpoint=False: PreviewStep(name, 'one_shot', endpoint_before=endpoint)
    if scenario == 'overview':
        return [loop('idle', 4), once('happy'), loop('swing_cycle', 2), loop('idle', 2)]
    if scenario.startswith('climb-'):
        side = scenario.removeprefix('climb-')
        # Allow the first native captures to settle before reviewing movement.
        # Keep capture coverage strict; a short startup dwell can lose most of
        # its observation to renderer startup and the initial motion fade.
        return [loop('idle', 1.5), loop('climb_' + side, 4.9), once('climb_to_top_' + side, True),
                loop('swing_cycle', 9), loop('swing_idle', 2)]
    if scenario == 'cleanup':
        steps = [loop('idle', 1.5)]
        for pose, clean in [('idle', 'clean_ground'), ('climb_left', 'clean_climb_left'),
                            ('climb_right', 'clean_climb_right'), ('swing_idle', 'clean_top')]:
            # Keep the complete return pose observable even when PNG capture
            # takes several renderer updates. Native transition/cycle timing
            # and the capture-coverage threshold remain unchanged.
            steps.extend([loop(pose, 2.0), once(clean + '_enter', pose.startswith('climb_')),
                          PreviewStep(clean, cycles=2 if clean == 'clean_ground' else 1),
                          once(clean + '_exit'), loop(pose, 2.0)])
        # Leave enough final rest for a person to inspect the returned hand
        # and brush. Actual capture completion also waits for native observation.
        steps[-1] = loop('swing_idle', 2)
        return steps
    if scenario == 'swing':
        return [loop('swing_cycle', 5), loop('swing_idle', 2), once('clean_top_enter'),
                PreviewStep('clean_top', cycles=1), once('clean_top_exit'), loop('swing_idle', 2)]
    if scenario == 'swing-petting':
        return [PreviewStep('swing_cycle', cycles=3), loop('swing_idle', 12),
                once('clean_top_enter'), PreviewStep('clean_top', cycles=1),
                once('clean_top_exit'), loop('swing_idle', 4)]
    if scenario == 'supported-drag':
        # Explicit interruptions, not synthetic completion of the interrupted
        # gesture. Every following landing still waits for its native finish.
        drop = [loop('drag_right', 2), loop('fall_float', 1.5), once('land'), loop('idle', 1.5)]
        steps = [loop('idle', 1.5), loop('swing_cycle', 3), *drop]
        for pose, clean in [('idle', 'clean_ground'), ('climb_left', 'clean_climb_left'),
                            ('climb_right', 'clean_climb_right'), ('swing_idle', 'clean_top')]:
            steps.extend([loop(pose, 1), once(clean + '_enter', pose.startswith('climb_')),
                          loop(clean, 1.15), *drop])
        steps[-1] = loop('idle', 6)
        return steps
    if scenario == 'locomotion':
        return [loop('walk_right', 2), loop('walk_left', 2), loop('drag_right', 1.5), loop('drag_left', 1.5),
                loop('fall_float', 1), once('land'), loop('idle', .4), once('sleep_enter'),
                loop('sleep_loop', 2), once('sleep_exit'), loop('idle', 2)]
    if scenario == 'interactions':
        return [loop('idle', 1), loop('talk', 4.2), loop('idle', .8),
                PreviewStep('petting', 'counted_loop', cycles=2), loop('idle', 1), once('happy'), loop('idle', 3)]
    if scenario == 'drop-land-idle':
        return [loop('idle', 1.5), loop('drag_right', 2), loop('fall_float', 2), once('land'), loop('idle', 6)]
    if scenario == 'sleep-exit-idle':
        return [once('sleep_enter'), loop('sleep_loop', 2), once('sleep_exit'), loop('idle', 6)]
    raise ValueError(f'Unknown preview scenario: {scenario}')


def motion_durations(directory: Path, settings: dict, steps: list[PreviewStep]) -> dict[str, float]:
    durations = {}
    for name in {step.name for step in steps}:
        entries = settings.get('FileReferences', {}).get('Motions', {}).get(name, [])
        if not entries or not isinstance(entries[0].get('File'), str):
            raise ValueError(f'Missing native motion reference: {name}')
        path = (directory / entries[0]['File']).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError(f'Motion reference escapes the model folder: {name}')
        duration = json.loads(path.read_text(encoding='utf-8-sig')).get('Meta', {}).get('Duration')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError(f'Invalid actual motion duration: {name}')
        durations[name] = float(duration)
    return durations


def planned_timeline(steps: list[PreviewStep], durations: dict[str, float]) -> tuple[list[tuple[float, str, str]], float]:
    at = 0.
    timeline = []
    for index, step in enumerate(steps):
        if step.endpoint_before:
            # This is a budget, not a callback timer. The actual endpoint may
            # arrive sooner; the following schedule moves with its real event.
            at += durations[steps[index - 1].name]
        timeline.append((at, step.name, step.playback))
        at += step.dwell if step.dwell is not None else durations[step.name] * (step.cycles or 1)
    return timeline, at


def capture_coverage(steps: list[PreviewStep], durations: dict[str, float],
                     trace: list[dict], snapshots: list[dict]) -> dict:
    """Measure each native play separately, regardless of callback arrival time.

    Diagnostic captures currently omit generation, so their token/name may only
    be attributed through a single-generation trace with unique native tokens.
    This checks captured motion coverage, not visual quality or world movement.
    """
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    plays = [row for row in trace if row.get('kind') == 'play']
    errors, segments = [], []
    generations = [row.get('generation') for row in plays]
    tokens = [row.get('token') for row in plays]
    identity_valid = (bool(plays) and all(isinstance(x, int) and not isinstance(x, bool) and x > 0
                                        for x in generations + tokens)
                      and len(set(generations)) == 1 and len(set(tokens)) == len(tokens))
    if not identity_valid:
        errors.append('Capture coverage requires one native generation and unique play tokens')
    if len(plays) != len(steps):
        errors.append('Capture coverage requires every scheduled play exactly once')
    for index, step in enumerate(steps):
        play = plays[index] if index < len(plays) else {}
        reasons = []
        binding_valid = (identity_valid and play.get('name') == step.name
                         and play.get('playback') == step.playback)
        if not binding_valid:
            reasons.append('Missing or ambiguous native play binding')
        expected = step.dwell if step.dwell is not None else durations.get(step.name, 0) * (step.cycles or 1)
        if not finite(expected) or expected <= 0:
            reasons.append('Missing valid expected presentation duration')
            expected = None
        matched, invalid, duplicates = 0, 0, 0
        updates = {}
        if binding_valid:
            for snapshot_index, snapshot in enumerate(snapshots):
                playback = snapshot.get('playback') or {}
                if playback.get('token') != play['token'] or playback.get('name') != step.name:
                    continue
                matched += 1
                update, native_time = playback.get('nativeUpdateCount'), playback.get('motionTimeSeconds')
                pixels = snapshot.get('visiblePixels')
                if (playback.get('generation', play['generation']) != play['generation']
                        or playback.get('evaluated') is not True
                        or not isinstance(update, int) or isinstance(update, bool) or update <= 0
                        or not finite(native_time) or native_time < 0
                        or not finite(pixels) or pixels <= 0):
                    invalid += 1
                    continue
                if update in updates:
                    duplicates += 1
                    continue
                updates[update] = (float(native_time), snapshot_index)
        times = [row[0] for row in updates.values()]
        span = max(times) - min(times) if times else 0.
        minimum = expected * .6 if expected is not None else None
        if len(updates) < 3:
            reasons.append('Fewer than 3 visible evaluated frames with distinct native updates')
        if minimum is not None and span + 1e-9 < minimum:
            reasons.append('Captured native time span is below 60% of expected presentation duration')
        row = {'stepIndex': index, 'name': step.name, 'token': play.get('token'),
               'generation': play.get('generation'), 'expectedDurationSeconds': expected,
               'minimumNativeSpanSeconds': minimum, 'frameCount': len(updates),
               'matchedFrameCount': matched, 'invalidFrameCount': invalid,
               'duplicateUpdateFrameCount': duplicates,
               'nativeTimeStart': min(times) if times else None,
               'nativeTimeEnd': max(times) if times else None, 'nativeTimeSpanSeconds': span,
               'snapshotIndices': [row[1] for row in updates.values()],
               'passed': not reasons, 'reasons': reasons}
        segments.append(row)
        if reasons:
            errors.append(f"Capture coverage failed for step {index} {step.name} token {play.get('token')}: "
                          + '; '.join(reasons))
    return {'passed': not errors, 'minimumFramesPerPlay': 3, 'minimumSpanFraction': .6,
            'bindingMethod': 'native-token-name-through-single-generation-trace',
            'segments': segments, 'errors': errors}


def resting_tail_decision(steps: list[PreviewStep], durations: dict[str, float],
                          trace: list[dict], snapshots: list[dict], frame_count: int) -> dict:
    """Stop only after real final-loop observation; never repair earlier gaps.

    The director's wall-time dwell can finish before asynchronous capture
    callbacks cover the final native loop. Keep recording that same token
    until its full intended native span is present. The existing overall
    timeout and unchanged coverage validator still fail incomplete evidence.
    """
    coverage = capture_coverage(steps, durations, trace, snapshots)
    segments = coverage['segments']
    final = segments[-1] if segments else None
    result = {'stop': False, 'frameCount': frame_count, 'minimumFrames': 100,
              'finalExpectedNativeSpanSeconds': final['expectedDurationSeconds'] if final else None,
              'finalCapturedNativeSpanSeconds': final['nativeTimeSpanSeconds'] if final else None,
              'finalCapturedFrameCount': final['frameCount'] if final else 0}
    # Remaining at the last pose cannot recreate a missing earlier transition.
    # Preserve failure immediately instead of adding unrelated idle padding.
    if not segments or any(not row['passed'] for row in segments[:-1]):
        return {**result, 'stop': True, 'reason': 'earlier-capture-coverage-failed'}
    if steps[-1].playback != 'loop' or steps[-1].dwell is None:
        return {**result, 'stop': True, 'reason': 'no-extendable-final-resting-loop'}
    expected, captured = final['expectedDurationSeconds'], final['nativeTimeSpanSeconds']
    if not final['passed'] or expected is None or captured + 1e-9 < expected:
        return {**result, 'reason': 'awaiting-complete-native-resting-observation'}
    if frame_count < 100:
        return {**result, 'reason': 'awaiting-minimum-frame-count'}
    return {**result, 'stop': True, 'reason': 'complete-native-resting-observation'}


class PreviewDirector:
    """Small event-driven recorder; never substitutes a timeout for completion."""
    def __init__(self, steps, generation, play, send, done):
        self.steps, self.generation = steps, generation
        self.play, self.send, self.done = play, send, done
        self.index, self.token, self.request_serial = -1, None, 0
        self.pending_endpoint = None
        self.started_at = 0.
        self.completed = False
        self.trace = []

    def advance(self, now):
        self.index += 1
        self.pending_endpoint = None
        if self.index == len(self.steps):
            self.completed = True
            self.done()
            return
        step = self.steps[self.index]
        self.token = self.play(step.name, step.playback)
        self.started_at = now
        self.trace.append({'kind': 'play', 'time': now, 'name': step.name,
                           'playback': step.playback, 'token': self.token, 'generation': self.generation})

    def poll(self, now):
        if self.completed or self.index < 0 or self.pending_endpoint is not None:
            return
        step = self.steps[self.index]
        if step.dwell is None or now - self.started_at < step.dwell:
            return
        following = self.steps[self.index + 1] if self.index + 1 < len(self.steps) else None
        if following and following.endpoint_before:
            expected = {'climb_to_top_' + step.name.removeprefix('climb_'), 'clean_' + step.name + '_enter'}
            if step.name not in {'climb_left', 'climb_right'} or following.name not in expected:
                raise ValueError('Endpoint entry must follow its matching native climb motion')
            self.request_serial += 1
            self.pending_endpoint = self.request_serial
            self.send('climb-endpoint', name=step.name, token=self.token, request=self.request_serial, enabled=True)
            self.trace.append({'kind': 'endpoint-request', 'time': now, 'name': step.name,
                'token': self.token, 'generation': self.generation, 'request': self.request_serial})
        else:
            self.advance(now)

    def event(self, value, now):
        if self.completed or self.index < 0:
            return
        step = self.steps[self.index]
        if (value.get('generation') != self.generation or value.get('token') != self.token
                or value.get('name') != step.name):
            return
        kind, cycle = value.get('type'), value.get('cycle')
        valid_cycle = isinstance(cycle, int) and not isinstance(cycle, bool) and cycle > 0
        if ((kind == 'finished' and step.playback == 'one_shot' and valid_cycle)
                or (kind == 'cycle' and step.cycles is not None and valid_cycle and cycle >= step.cycles)):
            self.trace.append({'kind': 'native-completion', 'time': now, 'event': dict(value)})
            self.advance(now)
        elif kind == 'geometry' and self.pending_endpoint is not None:
            endpoint, phase = value.get('climbEndpoint'), value.get('climbPhase')
            bounds, anchors = value.get('bounds'), value.get('anchors')
            if (isinstance(endpoint, dict) and isinstance(endpoint.get('request'), int)
                    and not isinstance(endpoint['request'], bool) and endpoint['request'] == self.pending_endpoint
                    and isinstance(endpoint.get('cycle'), int) and not isinstance(endpoint['cycle'], bool)
                    and endpoint['cycle'] >= 0 and endpoint['cycle'] == value.get('climbCycle')
                    and isinstance(phase, (int, float)) and not isinstance(phase, bool)
                    and math.isfinite(phase) and abs(phase - 1) <= 1e-5
                    and isinstance(bounds, list) and len(bounds) == 4 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in bounds)
                    and bounds[2] > 0 and bounds[3] > 0 and isinstance(anchors, dict) and anchors):
                self.trace.append({'kind': 'native-endpoint', 'time': now, 'event': dict(value)})
                self.advance(now)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/maple-preview.gif")
    parser.add_argument("--scenario", choices=("overview", "climb-left", "climb-right", "swing", "locomotion", "interactions", "cleanup",
                                               "drop-land-idle", "sleep-exit-idle", "swing-petting", "supported-drag"), default="overview")
    parser.add_argument("--model-version", default="v3")
    parser.add_argument('--background', choices=('light', 'black', 'white', 'checkerboard'), default='light')
    parser.add_argument('--sustained-gaze', nargs=2, type=float, metavar=('X', 'Y'),
                        help='Hold a normalized production gaze target throughout the recording (x right, y up; -1..1)')
    parser.add_argument("--model-directory", type=Path, default=ROOT / "assets/live2d/Maple",
                        help="Runtime folder containing Maple.model3.json; allows isolated export review")
    options = parser.parse_args()
    try:
        gaze_plan = preview_gaze_plan(options.scenario, options.sustained_gaze)
    except ValueError as error:
        parser.error(str(error))
    model_directory = options.model_directory.resolve()
    if not (model_directory / "Maple.model3.json").is_file():
        parser.error("Model directory must contain Maple.model3.json")
    settings = json.loads((model_directory / 'Maple.model3.json').read_text(encoding='utf-8-sig'))
    metadata_path = model_directory / settings.get('CuteMaple', {}).get('Metadata', 'Maple.pet.json')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8-sig'))
    revision = metadata.get('refinement', {}).get('version')
    refined = metadata.get('locomotion', {}).get('climb', {}).get('refinementParameter') == 'ParamClimbRefine'
    if revision in (4, 5) and options.model_version != f'v{revision}':
        parser.error('The recording version label must match the authored model metadata')
    output = options.output.resolve()
    frame_directory = output.parent / (output.stem + "-frames")
    if any(path.exists() for path in (output, output.with_suffix(".json"), frame_directory)):
        parser.error("Use a new output path so earlier review frames and evidence are preserved")
    resource_hashes = resource_snapshot(model_directory, ROOT / "web/dist")
    frames: list[Image.Image] = []
    timestamps: list[float] = []
    errors: list[str] = []
    snapshots: list[dict] = []
    events: list[dict] = []
    gaze_commands: list[dict] = []
    expression_commands: list[dict] = []
    petting_preview_started = False
    gaze_evidence = {
        'mode': 'sustained' if options.sustained_gaze is not None else 'overview-scripted' if options.scenario == 'overview' else 'disabled',
        'coordinateSystem': 'Normalized renderer target: x right, y up, range [-1, 1]',
        'path': 'Production Live2DHost/QWebChannel gaze command; normal smoothing and head response',
        'hardwareInputVerified': False,
        'plannedCommands': gaze_plan, 'sentCommands': gaze_commands,
        'actualModelParameters': 'snapshots[].parameters retains each captured native parameter value',
    }
    started = 0.0
    recording = False
    token = 1
    duration = 10.0
    schedule = [(0., "idle", "loop"), (4., "happy", "one_shot"), (6., "swing_cycle", "loop"), (8., "idle", "loop")]
    if options.scenario.startswith("climb-"):
        side = options.scenario.removeprefix("climb-")
        transition = f"climb_to_top_{side}"
        path = model_directory / "motions" / f"{transition}.motion3.json"
        transition_duration = json.loads(path.read_text(encoding="utf-8-sig"))["Meta"]["Duration"]
        # Record several complete weight-transfer and swing cycles. Software
        # rendering can capture below the requested FPS; retain the original
        # minimum-frame check and give these actual moving phases enough time.
        schedule = [(0., "idle", "loop"), (.6, f"climb_{side}", "loop"),
                    (5.5, transition, "one_shot"), (5.5 + transition_duration, "swing_cycle", "loop"),
                    (14.5 + transition_duration, "swing_idle", "loop")]
        duration = 16.5 + transition_duration
    elif options.scenario == "swing":
        schedule = [(0., "swing_cycle", "loop"), (5., "swing_idle", "loop"),
                    (7., "clean_top", "loop"), (10., "swing_idle", "loop")]
        duration = 12.
    elif options.scenario == "locomotion":
        schedule = [(0., "walk_right", "loop"), (2., "walk_left", "loop"),
                    (4., "drag_right", "loop"), (5.5, "drag_left", "loop"), (7., "fall_float", "loop"),
                    (8., "land", "one_shot"), (9.5, "sleep_enter", "one_shot"),
                    (11., "sleep_loop", "loop"), (13., "sleep_exit", "one_shot"), (14.5, "idle", "loop")]
        duration = 16.
    elif options.scenario == "interactions":
        schedule = [(0., "idle", "loop"), (1., "talk", "loop"), (5.2, "idle", "loop"),
                    (6., "petting", "counted_loop"), (10., "idle", "loop"),
                    (11., "happy", "one_shot"), (17., "idle", "loop")]
        duration = 20.
    elif options.scenario == "cleanup":
        schedule = [(0., "idle", "loop"), (1., "clean_ground", "loop"), (4., "idle", "loop"),
                    (5., "climb_left", "loop"), (6.5, "clean_climb_left", "loop"),
                    (9.5, "climb_left", "loop"), (11., "climb_right", "loop"),
                    (12.5, "clean_climb_right", "loop"), (15.5, "climb_right", "loop")]
        duration = 17.
        if revision == 5:
            schedule, duration = v5_cleanup_timeline()
    steps = None
    director = None
    durations = {}
    if refined or options.scenario in {'drop-land-idle', 'sleep-exit-idle'}:
        steps = refined_preview_steps(options.scenario)
        durations = motion_durations(model_directory, settings, steps)
        schedule, duration = planned_timeline(steps, durations)
    planned_duration = duration
    fps = 20
    temporary_bundle = tempfile.TemporaryDirectory(prefix="cutemaple-native-review-")
    bundle = Path(temporary_bundle.name)
    shutil.copytree(ROOT / "web/dist", bundle / "web/dist")
    copied_model = bundle / "assets/live2d/Maple"
    shutil.copytree(model_directory, copied_model)
    loaded_resource_hashes = resource_snapshot(copied_model, bundle / "web/dist")
    if changed_resources(resource_hashes, loaded_resource_hashes):
        raise ValueError("Copied recording resources differ from the initial QA snapshot")
    register_live2d_scheme()
    live2d_host.APP_URL += "?diagnostics=1"
    app = QApplication([])
    parent = QWidget()
    parent.resize(384, 384)
    host = Live2DHost(parent, bundle)
    host.view.resize(384, 384)

    def play(name: str, playback: str = "loop") -> int:
        nonlocal token, petting_preview_started
        token += 1
        host.send("play", name=name, token=token, playback=playback,
                  cycles=2 if playback == "counted_loop" else 0, fade=0.25)
        attached = name.startswith(('climb_', 'swing_', 'clean_climb_', 'clean_top'))
        falling = name == 'fall_float'
        host.send('context', token=token, grounded=not attached and not falling and not name.startswith('drag_'),
                  attached=attached, dragging=name.startswith('drag_'), falling=falling,
                  vx=0, vy=.4 if falling else 0, visibleRect=[0, 0, 1, 1], effectsEnabled=True)
        if ((options.scenario == 'swing-petting' and name == 'swing_idle')
                or (options.scenario == 'supported-drag' and name == 'swing_cycle')) and not petting_preview_started:
            petting_preview_started = True
            expected_token = token
            petting_plan = ((1000, 'swing_petting_left'), (8000, 'swing_petting_right')) if options.scenario == 'swing-petting' else ((1000, 'swing_petting_left'),)
            for delay, expression_name in petting_plan:
                def send_petting(expression_name=expression_name):
                    if not recording or token != expected_token:
                        return
                    host.send('expression', name=expression_name, active=True, intensity=1., token=expected_token)
                    expression_commands.append({'time': time.perf_counter() - started,
                        'token': expected_token, 'name': expression_name, 'active': True,
                        'source': 'explicit-preview-command', 'hardwareInputVerified': False})
                QTimer.singleShot(delay, send_petting)
        return token

    def stop() -> None:
        nonlocal recording, duration
        if recording and director is not None:
            duration = time.perf_counter() - started
        recording = False
        director_timer.stop()
        host.send("pause", paused=True)
        QTimer.singleShot(100, app.quit)

    review_tail = {}

    def finish_if_tail_observed() -> bool:
        if director is None or not director.completed:
            return False
        decision = resting_tail_decision(steps, durations, director.trace, snapshots, len(frames))
        review_tail['observation'] = decision
        if decision['stop']:
            review_tail['captureFinishedAt'] = time.perf_counter() - started
            stop()
            return True
        return False

    def finish_sequence() -> None:
        # Slow software capture must retain the complete motion sequence and
        # its subsequent resting animation. Capture more real resting frames,
        # with the existing timeout, rather than duplicating images or lowering
        # the review's 100-frame minimum.
        review_tail.update(scheduleFinishedAt=time.perf_counter() - started,
                           framesAtScheduleFinish=len(frames))
        director_timer.stop()
        # Let any pending JavaScript capture return before deciding to stop;
        # a wall-time completion must not discard an in-flight native frame.

    def capture() -> None:
        if not recording:
            return
        elapsed = time.perf_counter() - started
        if director is None and elapsed >= duration:
            stop()
            return

        def receive(raw) -> None:
            if not recording:
                return
            try:
                value = json.loads(raw)
                if value.get("visiblePixels", 0) < 100 or value.get("glError"):
                    raise ValueError("Captured an empty frame or WebGL error")
                png = base64.b64decode(value["png"].split(",", 1)[1])
                frame = Image.open(io.BytesIO(png)).convert("RGBA")
                frame_directory.mkdir(parents=True, exist_ok=True)
                frame.save(frame_directory / f"{len(frames):04d}.png")
                snapshots.append({"time": time.perf_counter() - started, "geometry": value.get("geometry"),
                                  "parameters": value.get("parameters"), "visiblePixels": value.get("visiblePixels"),
                                  "playback": value.get("playback"), "nativeVertexSignature": value.get("nativeVertexSignature")})
                # A constant neutral backdrop makes the transparent pet readable
                # in GIF viewers; it does not alter model content or animation.
                background = review_background(frame.size, options.background)
                background.alpha_composite(frame)
                frames.append(background.convert("RGB"))
                timestamps.append(time.perf_counter() - started)
                if finish_if_tail_observed():
                    return
                target = started + len(frames) / fps
                QTimer.singleShot(max(1, round((target - time.perf_counter()) * 1000)), capture)
            except (ValueError, TypeError, KeyError) as error:
                errors.append(str(error))
                stop()

        host.page.runJavaScript("JSON.stringify(window.__cutemapleCapture())", receive)

    def event(value: dict) -> None:
        nonlocal started, recording, director
        if value.get("type") == "ready":
            host.view.show()
            required = {name for _, name, _ in schedule}
            missing = required - set(value.get("states", []))
            if missing:
                errors.append(f"Missing native review motions: {sorted(missing)}")
                stop()
                return
            if steps and any(step.endpoint_before for step in steps) and 'climb-endpoint-v1' not in value.get('capabilities', []):
                errors.append('Refined climb recording requires the production native endpoint handshake')
                stop()
                return
            started = time.perf_counter()
            recording = True
            def gaze(command):
                if recording:
                    send_preview_gaze(host.send, command, value.get('generation'), time.perf_counter() - started, gaze_commands)
            for command in gaze_plan:
                if command['time'] == 0:
                    gaze(command)
                else:
                    QTimer.singleShot(round(command['time'] * 1000), lambda command=command: gaze(command))
            QTimer.singleShot(30, capture)
            if steps:
                director = PreviewDirector(steps, value.get('generation'), play, host.send, finish_sequence)
                director.advance(0.)
                director_timer.start(25)
            else:
                for at, name, playback in schedule:
                    QTimer.singleShot(round(at * 1000), lambda name=name, playback=playback: play(name, playback))
            if director is None:
                QTimer.singleShot(round(duration * 1000), stop)
        elif value.get("type") == "error":
            errors.append(value.get("message", "Native renderer error"))
            stop()
        elif value.get("type") in ("cycle", "finished", "marker") or value.get('climbEndpoint'):
            events.append({"time": time.perf_counter() - started, **value})
        if director is not None and recording:
            director.event(value, time.perf_counter() - started)

    def poll_director():
        if recording and director is not None:
            try:
                director.poll(time.perf_counter() - started)
            except ValueError as error:
                errors.append(str(error)); stop()

    director_timer = QTimer(parent)
    director_timer.timeout.connect(poll_director)

    host.event.connect(event)
    parent.show()
    host.load()
    QTimer.singleShot(round(max(45., planned_duration * 4 + 20) * 1000) if steps else 45000,
                      lambda: (errors.append("Recording timed out before native completion"), stop()))
    app.exec()
    host.stop()
    parent.close()
    app.processEvents()
    temporary_bundle.cleanup()
    resource_hashes_end = resource_snapshot(model_directory, ROOT / "web/dist")
    resource_changes = changed_resources(resource_hashes, resource_hashes_end)
    if resource_changes:
        errors.append("Renderer resources changed during recording; rerun with frozen inputs")
    if steps and (director is None or not director.completed):
        errors.append('Native recording schedule did not complete all motion and endpoint gates')
    schedule_evidence = {'mode': 'native-event-driven' if steps else 'legacy-wall-clock',
        'motionDurations': durations, 'plannedDurationSeconds': planned_duration,
        'completed': director.completed if director else None,
        'actualRestingTail': review_tail,
        'trace': director.trace if director else []}
    if steps:
        coverage = capture_coverage(steps, durations, director.trace if director else [], snapshots)
        schedule_evidence['captureCoverage'] = coverage
        errors.extend(coverage['errors'])
    if errors or len(frames) < 100:
        if len(frames) < 100:
            errors.append("Too few frames for a continuous native review")
        failure = {"passed": False, "errors": errors, "frames": len(frames),
                   "reportKind": "native-motion-recording", "schedule": schedule_evidence,
                   "scenario": options.scenario, "modelDirectory": str(model_directory),
                   "resourceHashesAtStart": resource_hashes,
                   "resourceHashesLoaded": loaded_resource_hashes,
                   "resourceHashesAtEnd": resource_hashes_end, "resourceChangesDuringRun": resource_changes,
                   "events": events, "snapshots": snapshots, "gazeInput": gaze_evidence,
                   "expressionCommands": expression_commands}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix(".json").write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": False, "errors": errors, "frames": len(frames)}), flush=True)
        return 1
    delays = [max(10, round((b - a) * 1000 / 10) * 10) for a, b in zip(timestamps, timestamps[1:])]
    delays.append(max(10, round((duration - timestamps[-1]) * 1000 / 10) * 10))
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=delays,
                   loop=0, optimize=True, disposal=2)
    report = {
        "passed": True, "actualNative": True, "modelVersion": options.model_version, "modelDirectory": str(model_directory),
        "reportKind": "native-motion-recording", "schedule": schedule_evidence,
        "modelMotionRevision": metadata.get('refinement', {}).get('motionRevision'),
        "scenario": options.scenario, "frames": len(frames),
        "width": 384, "height": 384, "durationMs": sum(delays), "nominalFps": fps,
        "method": "Continuous native Cubism RAF playback through production Qt host; each actual rendered frame captured, no cross-cut pose sequence",
        "timelineSeconds": ([{'time': row['time'], 'state': row['name'], 'playback': row['playback'],
                              'token': row['token'], 'generation': row['generation']}
                             for row in director.trace if row['kind'] == 'play'] if director else
                            [{"time": at, "state": name, "playback": playback} for at, name, playback in schedule]),
        "plannedTimelineSeconds": [{"time": at, "state": name, "playback": playback} for at, name, playback in schedule],
        "events": events, "snapshots": snapshots, "allFramesSavedAsPng": True,
        "gazeInput": gaze_evidence,
        "expressionCommands": expression_commands,
        "validationScope": "Fixed-canvas native model motion appearance only; passed means recording completed, not visual acceptance. This does not move PetWindow or prove world-space attachment, falling/landing travel, window contact continuity, or hardware activity.",
        "presentation": f"{options.background} background composited behind native transparent canvas for review",
        "moc3Sha256": resource_hashes["assets/live2d/Maple/Maple.moc3"],
        "gifSha256": hashlib.sha256(output.read_bytes()).hexdigest(), "errors": errors,
        "resourceHashesAtStart": resource_hashes, "resourceHashesAtEnd": resource_hashes_end,
        "resourceHashesLoaded": loaded_resource_hashes,
        "resourceChangesDuringRun": resource_changes,
    }
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "frames": len(frames), "durationMs": sum(delays), "bytes": output.stat().st_size}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
