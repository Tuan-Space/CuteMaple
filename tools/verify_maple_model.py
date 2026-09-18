"""Verify the exported Maple rig through the real offline Qt/Cubism renderer.

Checks all 21 public motion completions and, with refinement, 23 v4 or 31 v5
native clips, nonempty frames, geometry contacts, and semantic parameter changes.
Screenshots and a JSON report are saved for visual review; a successful numeric
check does not replace the artist's review of pose quality or appearance.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-compositing --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader --ignore-gpu-blocklist")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_OPENGL", "software")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT));sys.path.insert(0,str(ROOT/"src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
import live2d_host
from tools.native_model_contract import (declared_native_parameters, declared_native_drawables, free_motion_contacts,
    motion_polish_enabled, brush_contract, physics_resource_contract, climb_drape_exchange)
from tools.native_material_geometry import native_uv_point, source_point, measure_brush_sweep, measure_brush_transitions, measure_free_brace, measure_climb_materials, continuous_swing_targets, cleanup_endpoint_parameters, brush_forearm
from live2d_host import Live2DHost, register_live2d_scheme
from pet_core import ANIMATIONS
from tools.qa_resource_snapshot import resource_snapshot, changed_resources
from tools.qa_model_inputs import (audit_atlas_files, atlas_coordinate_mapping_audit,
                                   verify_native_uv_evidence, recheck_input_hashes)

EXPRESSIONS = ("keyboard", "audio", "dizzy", "smile", "blush", "glasses", "fan", "maple")
PROBES = {
    "gaze": ({"ParamEyeBallX": -1, "ParamAngleX": -10}, {"ParamEyeBallX": 1, "ParamAngleX": 10}),
    "eyes": ({"ParamEyeLOpen": 1, "ParamEyeROpen": 1}, {"ParamEyeLOpen": 0, "ParamEyeROpen": 0}),
    "sleep": ({"ParamPoseSleep": 0}, {"ParamPoseSleep": 1}),
    "drag": ({"ParamBodyAngleZ": 0, "ParamArmLA": 0}, {"ParamBodyAngleZ": 12, "ParamArmLA": 15}),
    "keyboard": ({"ParamTyping": 0, "ParamTypingPulse": 0.5}, {"ParamTyping": 1, "ParamTypingPulse": 0.5}),
    "audio": ({"ParamListening": 0}, {"ParamListening": 1}),
    "dizzy": ({"ParamDizzy": 0}, {"ParamDizzy": 1}),
    "glasses": ({"ParamGlasses": 0}, {"ParamGlasses": 1}),
    "fan": ({"ParamFan": 0}, {"ParamFan": 1}),
    "maple": ({"ParamMaple": 0}, {"ParamMaple": 1}),
}


TRANSITIONS = ("climb_to_top_left", "climb_to_top_right")
V5_CLEAN_SEGMENTS = tuple(f'{state}_{phase}' for state in ANIMATIONS if state.startswith('clean_')
                        for phase in ('enter', 'exit'))
V5_PARAMETERS = {'ParamRibbonLX', 'ParamRibbonLY', 'ParamRibbonRX', 'ParamRibbonRY',
                 'ParamCleanFanL', 'ParamCleanFanR', 'ParamCleaningSweep', 'ParamClimbPhase',
                 'ParamClimbActive', 'ParamClimbDirection', 'ParamCleanGround'}
V5_DRAWABLES = {'skirt_sleep', 'leg_l_sleep', 'leg_r_sleep', 'leg_l_climb', 'leg_r_climb',
                'clean_fan_l', 'clean_fan_r'}
TRANSITION_MARKERS = ("top_grab", "wall_release", "settled")
REFINEMENT_PROBES = {
    "head_turn": ({"ParamHeadTurn": -1}, {"ParamHeadTurn": 1}),
    "body_turn": ({"ParamBodyTurn": -1}, {"ParamBodyTurn": 1}),
    # This probe checks the two open eyes in each 45-degree painted view.
    # Freeze those inputs explicitly instead of inheriting a random idle blink.
    "mid_head_turn": ({"ParamHeadTurn": -.5, "ParamEyeLOpen": 1, "ParamEyeROpen": 1},
                      {"ParamHeadTurn": .5, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
    "mid_body_turn": ({"ParamBodyTurn": -.5}, {"ParamBodyTurn": .5}),
    "neck_head_only": ({"ParamHeadTurn": -.5, "ParamBodyTurn": 0},
                       {"ParamHeadTurn": .5, "ParamBodyTurn": 0}),
    "neck_body_only": ({"ParamHeadTurn": 0, "ParamBodyTurn": -.5},
                       {"ParamHeadTurn": 0, "ParamBodyTurn": .5}),
    # This deliberately disagrees across head/body material boundaries; the
    # former disappearing-neck regression must not be hidden by synced motion.
    "neck_mixed_turn": ({"ParamHeadTurn": -.198, "ParamBodyTurn": -.358},
                        {"ParamHeadTurn": .198, "ParamBodyTurn": .358}),
    "seated": ({"ParamPoseSwing": 0, "ParamSwingVisible": 0, "ParamArmPoseMode": 0},
               {"ParamPoseSwing": 1, "ParamSwingVisible": 1, "ParamArmPoseMode": 1, "ParamHandLShape": 1, "ParamHandRShape": 1}),
    "hand_left": ({"ParamHandLShape": -1, "ParamArmPoseMode": 1}, {"ParamHandLShape": 1, "ParamArmPoseMode": 1}),
    "hand_right": ({"ParamHandRShape": -1, "ParamArmPoseMode": 1}, {"ParamHandRShape": 1, "ParamArmPoseMode": 1}),
    "suspension": ({"ParamPoseSwing": 1, "ParamSwingVisible": 1, "ParamSwing": -1, "ParamArmPoseMode": 1, "ParamHandLShape": 1, "ParamHandRShape": 1},
                   {"ParamPoseSwing": 1, "ParamSwingVisible": 1, "ParamSwing": 1, "ParamArmPoseMode": 1, "ParamHandLShape": 1, "ParamHandRShape": 1}),
}
VIEW_HANDOFFS = {
    'left_profile_join': (-.751, -.749, 'profile_l_', 'mid_l_'),
    'left_front_join': (-.251, -.249, 'mid_l_', ''),
    'right_front_join': (.249, .251, '', 'mid_r_'),
    'right_profile_join': (.749, .751, 'mid_r_', 'profile_r_'),
}
for _name, (_before, _after, _, _) in VIEW_HANDOFFS.items():
    REFINEMENT_PROBES[_name] = ({'ParamHeadTurn': _before, 'ParamBodyTurn': _before},
                              {'ParamHeadTurn': _after, 'ParamBodyTurn': _after})


def handoff_opacities(name: str, phase: str, contract: dict) -> dict[str, float]:
    first_turn, last_turn, first, last = VIEW_HANDOFFS[name]
    prefixes = ('', 'mid_l_', 'mid_r_', 'profile_l_', 'profile_r_')
    if not contract.get('materialBlend'):
        return {prefix: float(prefix == (first if phase == 'before' else last)) for prefix in prefixes}
    width = contract.get('materialBlendTurnWidth')
    if (contract['materialBlend'] != 'opaque-lower-surface-normal-over'
            or not isinstance(width, (float, int)) or not math.isfinite(width) or not 0 < width < .5):
        raise ValueError('Unsupported native view material blending contract')
    turn = first_turn if phase == 'before' else last_turn
    boundary = (first_turn+last_turn)/2
    t = min(1, max(0, (turn-boundary+width/2)/width))
    blend = t*t*(3-2*t)
    weights = {prefix: 0. for prefix in prefixes}
    weights[first], weights[last] = 1-blend, blend
    # Native normal-over compositing keeps the lower local surface opaque.
    order = {'': 0, 'profile_r_': 1, 'profile_l_': 2, 'mid_r_': 3, 'mid_l_': 4}
    weights[min((first, last), key=order.get)] = 1.
    return weights



def atlas_pixels_match(actual, expected) -> bool:
    """Ignore RGB hidden beneath alpha=0; preserve every visible RGBA value."""
    import numpy as np
    if actual.size != expected.size:
        return False
    left, right = np.asarray(actual.convert('RGBA')), np.asarray(expected.convert('RGBA'))
    visible = (left[:, :, 3] > 0) | (right[:, :, 3] > 0)
    return bool(np.array_equal(left[:, :, 3], right[:, :, 3]) and np.array_equal(left[visible], right[visible]))



def inspect_contacts(report: dict, captures: dict, contract=None, measurement_key='contactMeasurements') -> list[str]:
    errors = []
    contract, audit = report.get('contactContract', {}) if contract is None else contract, report.get('atlasAudit', {})
    regions = {name: region for region in audit.get('regions', []) for name in region.get('drawables', [region.get('name')])}
    atlas_size = audit.get('atlasSize', [])
    if len(atlas_size) != 2 or not contract or report.get('atlasCoordinateMappingVerified') is not True:
        return ['Missing verified atlas placement and native contact contract']
    def sample(items, name, wrist):
        region = regions[name]
        size, translation = region['sourceSize'], region['translationToCanvas']
        point = [(wrist[axis]*size[axis]-translation[axis])/atlas_size[axis] for axis in (0, 1)]
        if items[name].get('textureIndex') != region['page']:
            raise ValueError(f'Native texture page differs for {name}')
        return native_uv_point(items[name], point)
    measurements = report.setdefault(measurement_key, {})
    for capture_name, snapshot in captures.items():
        items = {item['id']: item for item in snapshot.get('drawables', [])}
        info = snapshot.get('canvasInfo', {})
        try:
            factor = info['pixelsPerUnit']/max(info['width'], info['height'])
            if not math.isfinite(factor) or factor <= 0:
                raise ValueError('Invalid native canvas scale')
            for side in ('l', 'r'):
                definition = contract[side]
                cuff = sample(items, definition['cuff'], definition.get('cuffSourceUv', definition['sourceUv']))
                for hand in definition['hands']:
                    if items[hand].get('opacity', 0) < .05:
                        continue
                    wrist = sample(items, hand, definition.get('handSourceUv', definition['sourceUv']))
                    distance = math.dist(cuff, wrist)*factor
                    measurements.setdefault(capture_name, {})[hand] = {'distance': distance, 'cuff': cuff, 'wrist': wrist}
                    if distance > .012:
                        errors.append(f'Native hand/cuff contact separates: {capture_name}/{hand} ({distance:.5f} canvas)')
        except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as error:
            errors.append(f'Native contact unavailable: {capture_name}: {error}')
    return errors


def record_native_identity(report: dict, metadata_refinement: dict) -> None:
    """Derive revision from loaded native parameters/states and authored metadata, never a CLI label."""
    required = metadata_refinement.get('requiredParameters', [])
    parameters = report.get('captures', {}).get('baseline', {}).get('parameters', {})
    native_parameters = sorted(name for name, value in parameters.items()
                               if isinstance(value, (int, float)) and math.isfinite(value))
    native_states = sorted(report.get('nativeStates', []))
    report['modelRevisionEvidence'] = {'metadataVersion': metadata_refinement.get('version'),
        'motionPolishVersion': metadata_refinement.get('motionPolishVersion', 0),
        'requiredParameters': required, 'nativeParameters': native_parameters, 'nativeStates': native_states}
    core_parameters = {'ParamHeadTurn', 'ParamBodyTurn', 'ParamHandLShape', 'ParamHandRShape',
                       'ParamTransitionProgress', 'ParamArmPoseMode', 'ParamPoseSwing', 'ParamSwing', 'ParamSwingVisible'}
    states = {*ANIMATIONS, *TRANSITIONS}
    version = metadata_refinement.get('version')
    additional = True
    if version == 5:
        core_parameters |= V5_PARAMETERS
        states.update(V5_CLEAN_SEGMENTS)
        drawables = report.get('captures', {}).get('baseline', {}).get('drawables', [])
        ids = [item.get('id') for item in drawables]
        try:
            expected = declared_native_parameters(metadata_refinement)
            expected_drawables = declared_native_drawables(metadata_refinement)
            parameter_identity = (set(native_parameters) == expected if expected is not None else len(native_parameters) == 67)
            drawable_identity = (set(ids) == expected_drawables if expected_drawables is not None else len(ids) == 141)
        except ValueError as error:
            expected, expected_drawables, parameter_identity, drawable_identity = None, None, False, False
            report['modelRevisionEvidence']['contractError'] = str(error)
        report['modelRevisionEvidence'].update(motionRevision=metadata_refinement.get('motionRevision'),
                                               expectedNativeParameters=sorted(expected) if expected else None,
                                               expectedNativeDrawables=sorted(expected_drawables) if expected_drawables else None)
        additional = (len(parameters) == len(native_parameters) and parameter_identity and drawable_identity and len(drawables) == len(set(ids))
                      and V5_DRAWABLES.issubset(ids) and set(native_states) == states and len(report.get('nativeStates', [])) == 31)
        report['modelRevisionEvidence'].update(nativeParameterCount=len(native_parameters), nativeDrawableCount=len(drawables),
                                               nativeDrawableIds=ids, expectedMotionCount=31)
    report['modelRevision'] = (f'v{version}' if report.get('ready') is True and report.get('actualNative') is True and
        version in (4, 5) and additional and core_parameters.issubset(required) and
        set(required).issubset(native_parameters) and states.issubset(native_states) and
        all(report.get('finished', {}).get(state) is True for state in states) else None)


def inspect_report(report: dict, refinement: bool = False) -> list[str]:
    """Fail on missing native behavior, absent rig parameters, or invisible output."""
    errors = list(report["errors"])
    captures = report["captures"]
    required_motions = [*ANIMATIONS]
    if report.get('modelRevisionEvidence', {}).get('metadataVersion') == 5:
        required_motions.extend(V5_CLEAN_SEGMENTS)
    for name in required_motions:
        if not report["finished"].get(name):
            errors.append(f"Native motion did not finish: {name}")
        if name not in captures:
            errors.append(f"Missing motion capture: {name}")
    for name, capture in captures.items():
        if capture.get("visiblePixels", 0) < 100:
            errors.append(f"Empty or almost empty frame: {name}")
        if capture.get("glError"):
            errors.append(f"WebGL error in {name}: {capture['glError']}")
        geometry = capture.get("geometry", {})
        if len(geometry.get("bounds", [])) != 4 or not all(math.isfinite(x) for x in geometry["bounds"]):
            errors.append(f"Invalid geometry: {name}")
    for name in ("idle", "walk_left", "walk_right"):
        capture = captures.get(name, {})
        bounds = capture.get("pixelBounds", [])
        ground = capture.get("geometry", {}).get("anchors", {}).get("ground", [])
        if len(bounds) == 4 and len(ground) == 2:
            if abs(ground[1] - bounds[1] - bounds[3]) > 0.08:
                errors.append(f"Standing ground anchor is far from visible feet: {name}")

    def parameter(capture: str, name: str) -> float:
        result = captures.get(capture, {}).get("parameters", {}).get(name)
        if result is None:
            errors.append(f"Missing {name} in {capture}")
            return float("nan")
        return result

    for capture, minimum, maximum in (("gaze_left", -1.01, -0.5), ("gaze_right", 0.5, 1.01), ("gaze_disabled", -0.15, 0.15)):
        value = parameter(capture, "ParamEyeBallX")
        if not minimum <= value <= maximum:
            errors.append(f"Gaze parameter does not reach expected target: {capture} = {value}")
    for eye in ("ParamEyeLOpen", "ParamEyeROpen"):
        value = parameter("sleep_loop", eye)
        if not 0 <= value <= 0.2:
            errors.append(f"Sleeping eyes did not close: {eye} = {value}")
    for capture, name in (("keyboard", "ParamTyping"), ("audio", "ParamListening"),
                          ("dizzy", "ParamDizzy"), ("glasses", "ParamGlasses"),
                          ("fan", "ParamFan"), ("maple", "ParamMaple")):
        value = parameter(capture, name)
        if not 0.8 <= value <= 1.01:
            errors.append(f"Expression did not activate: {capture}/{name} = {value}")
    for left, right in (("gaze_left", "gaze_right"), ("baseline", "sleep_loop"),
                        ("baseline", "drag_left"), ("baseline", "keyboard"),
                        ("baseline", "audio"), ("baseline", "glasses"),
                        ("baseline", "fan"), ("baseline", "maple")):
        if captures.get(left, {}).get("pngSha256") == captures.get(right, {}).get("pngSha256"):
            errors.append(f"Rendered frames are identical: {left}, {right}")
    for name in PROBES:
        probe = report["probes"].get(name)
        if not probe or not probe.get("pixelsChanged"):
            errors.append(f"Rig parameter did not deform rendered pixels with time held fixed: {name}")
    if refinement:
        errors.extend(inspect_refinement(report))
        if report.get('modelRevisionEvidence', {}).get('metadataVersion') == 5:
            errors.extend(inspect_v5(report))
    return errors


def inspect_necks(report: dict) -> list[str]:
    """Native material/geometry evidence; painted seam quality still needs review."""
    contract = report.get('neckContract', {})
    views = contract.get('views', {})
    if (contract.get('materialParameter') != 'ParamBodyTurn'
            or set(contract.get('geometryParameters', [])) != {'ParamHeadTurn', 'ParamBodyTurn'}
            or set(views) != {'front', 'left', 'right', 'leftMid', 'rightMid'}
            or not all(isinstance(value, str) and value for value in views.values())
            or len(set(views.values())) != 5):
        return ['Missing or invalid native neck registration contract']
    errors = []
    native = {item['id']: item for item in report.get('captures', {}).get('baseline', {}).get('drawables', [])}
    for name in views.values():
        if name not in native:
            errors.append(f'Missing native neck drawable: {name}')
    for probe, selected in (('neck_head_only', ('front', 'front')),
                            ('neck_body_only', ('leftMid', 'rightMid')),
                            ('neck_mixed_turn', ('leftMid', 'rightMid'))):
        for phase, view in zip(('before', 'after'), selected):
            snapshot = report.get('probes', {}).get(probe, {}).get(phase, {})
            items = {item['id']: item for item in snapshot.get('drawables', [])}
            for candidate, name in views.items():
                actual = items.get(name, {}).get('opacity')
                if not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(actual-int(candidate == view)) > .05:
                    errors.append(f'Native neck does not follow body material: {probe}/{phase}/{name}')
            shown = items.get(views[view], {})
            bounds = shown.get('bounds', [])
            if (shown.get('finiteVertices') is not True or shown.get('vertexCount', 0) < 3
                    or len(bounds) != 4 or not all(isinstance(value, (float, int)) and math.isfinite(value) for value in bounds)
                    or (len(bounds) == 4 and (bounds[2] <= 0 or bounds[3] <= 0))):
                errors.append(f'Invalid native visible neck geometry: {probe}/{phase}')
    return errors


def inspect_refinement(report: dict) -> list[str]:
    """Numeric export guards; screenshots remain subject to human art review."""
    errors = []
    captures, probes = report.get('captures', {}), report.get('probes', {})
    required = ('ParamHeadTurn', 'ParamBodyTurn', 'ParamHandLShape', 'ParamHandRShape',
                'ParamPoseSwing', 'ParamPoseClimb', 'ParamTransitionProgress', 'ParamSwingVisible', 'ParamArmPoseMode')
    baseline = captures.get('baseline', {})
    for name in required:
        value = baseline.get('parameters', {}).get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            errors.append(f'Missing or invalid native v4 parameter: {name}')
    def drawables(snapshot):
        return {item['id']: item for item in snapshot.get('drawables', []) if isinstance(item, dict) and 'id' in item}
    native = drawables(baseline)
    for name in ('hand_l', 'hand_r', 'rope_l', 'rope_r', 'seat', 'profile_l_face_base', 'profile_r_face_base',
                 'profile_l_torso', 'profile_r_torso', 'mid_l_face_base', 'mid_r_face_base',
                 'mid_l_torso', 'mid_r_torso', 'arm_l', 'arm_r'):
        if name not in native:
            errors.append(f'Missing native v4 drawable: {name}')
    pupils = [name for name in native if 'pupil_' in name]
    if not pupils:
        errors.append('No native pupil clipping meshes')
    for name in pupils:
        expected = name.replace('pupil_', 'eye_')
        if expected not in native or expected not in native[name].get('masks', []):
            errors.append(f'Native pupil clipping missing: {name} -> {expected}')
    for name, item in native.items():
        if item.get('vertexCount', 0) < 3 or len(item.get('bounds', [])) != 4 or not all(
                isinstance(n, (int, float)) and math.isfinite(n) for n in item.get('bounds', [])):
            errors.append(f'Invalid native drawable geometry: {name}')
    for name in TRANSITIONS:
        if not report.get('finished', {}).get(name):
            errors.append(f'Native transition did not finish: {name}')
        if report.get('markers', {}).get(name) != list(TRANSITION_MARKERS):
            errors.append(f'Missing, repeated or out-of-order native transition markers: {name}')
        end = captures.get(name + '-end', {})
        if name not in captures or not end:
            errors.append(f'Missing transition frames: {name}')
        for key, target in {'ParamHeadTurn': 0, 'ParamBodyTurn': 0, 'ParamPoseClimb': 0,
                            'ParamPoseSwing': 1, 'ParamHandLShape': 1, 'ParamHandRShape': 1,
                            'ParamTransitionProgress': 1, 'ParamArmPoseMode': 1}.items():
            value = end.get('parameters', {}).get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value-target) > .15:
                errors.append(f'Transition end pose does not join the seated front pose: {name}/{key}={value}')
        for anchor in ('gripLeft', 'gripRight', 'hang', 'hangerLeft', 'hangerRight'):
            value = end.get('geometry', {}).get('anchors', {}).get(anchor)
            if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
                errors.append(f'Missing transition contact anchor: {name}/{anchor}')
    attached = {name: snapshot for name, snapshot in captures.items() if name in
                ('climb_left', 'climb_right', 'clean_climb_left', 'clean_climb_right', 'swing_idle', 'swing_cycle', 'clean_top')
                or name.startswith(TRANSITIONS)}
    for name in TRANSITIONS:
        for marker in (*TRANSITION_MARKERS, 'end'):
            if name+'-'+marker not in attached:
                errors.append(f'Missing native attached contact frame: {name}-{marker}')
    for name, snapshot in attached.items():
        items = drawables(snapshot)
        mode = snapshot.get('parameters', {}).get('ParamArmPoseMode')
        if not isinstance(mode, (int, float)) or not math.isfinite(mode) or mode < .95:
            errors.append(f'Attached pose lost active sleeves: {name}')
        for side in ('l', 'r'):
            lower = f'sleeve_{side}_lower'
            if report.get('v5Contract', {}).get('motionPolishVersion') == 1 and name.startswith('clean_'):
                try:
                    definition = brush_contract({'refinement': report['v5Contract'], 'states': report['stateContracts']})[name]
                    lower = brush_forearm(report, snapshot, side, definition['workSide'] == side,
                                           definition['context'])['drawable']
                except (ValueError, KeyError, TypeError) as error:
                    errors.append(f'Invalid visible cleaning forearm: {name}/{side}: {error}')
            if items.get('arm_'+side, {}).get('opacity', 1) > .05 or any(
                    items.get(part, {}).get('opacity', 0) < .95 for part in (f'sleeve_{side}_upper', lower)):
                errors.append(f'Native rest/active sleeves are not exclusive: {name}/{side}')
    errors.extend(inspect_contacts(report, attached))
    errors.extend(inspect_necks(report))
    for name, (before_values, after_values) in REFINEMENT_PROBES.items():
        probe = probes.get(name, {})
        if not probe.get('pixelsChanged'):
            errors.append(f'No fixed-time native visual response: {name}')
        for side, expected in (('before', before_values), ('after', after_values)):
            snapshot = probe.get(side, {})
            for drawable, item in drawables(snapshot).items():
                if (item.get('finiteVertices') is not True or item.get('vertexCount', 0) < 3
                        or len(item.get('bounds', [])) != 4 or not all(
                            isinstance(value, (int, float)) and math.isfinite(value) for value in item['bounds'])):
                    errors.append(f'Invalid native probe vertex geometry: {name}/{side}/{drawable}')
            for parameter, target in expected.items():
                actual = snapshot.get('parameters', {}).get(parameter)
                if not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(actual-target) > .01:
                    errors.append(f'Native parameter target missing: {name}/{side}/{parameter}')
    for probe, family in (('head_turn', 'face_base'), ('body_turn', 'torso')):
        for phase, shown, hidden in (('before', 'l', 'r'), ('after', 'r', 'l')):
            items = drawables(probes.get(probe, {}).get(phase, {}))
            if items.get('profile_' + shown + '_' + family, {}).get('opacity', 0) < .95:
                errors.append(f'Authored profile is not visible at turn endpoint: {probe}/{phase}')
            if items.get(family, {}).get('opacity', 1) > .05 or items.get('profile_' + hidden + '_' + family, {}).get('opacity', 1) > .05:
                errors.append(f'Front/other profile is still visible at turn endpoint: {probe}/{phase}')
    # These observations distinguish the actual two-eye 45-degree artwork from
    # an endpoint-only rig. They are capture evidence, not visual certification.
    for probe, family in (('mid_head_turn', 'face_base'), ('mid_body_turn', 'torso')):
        for phase, shown in (('before', 'l'), ('after', 'r')):
            items = drawables(probes.get(probe, {}).get(phase, {}))
            visible = f'mid_{shown}_{family}'
            for prefix in ('', 'mid_l_', 'mid_r_', 'profile_l_', 'profile_r_'):
                name = prefix+family
                expected = int(name == visible)
                actual = items.get(name, {}).get('opacity')
                if not isinstance(actual, (int, float)) or abs(actual-expected) > .05:
                    errors.append(f'Native middle view visibility wrong: {probe}/{phase}/{name}')
            if family == 'face_base':
                for eye in ('l', 'r'):
                    for feature in ('eye_', 'pupil_'):
                        name = f'mid_{shown}_{feature}{eye}'
                        if items.get(name, {}).get('opacity', 0) < .95:
                            errors.append(f'Middle view lost a visible eye: {probe}/{phase}/{name}')
    for name, (_, _, before, after) in VIEW_HANDOFFS.items():
        for phase in ('before', 'after'):
            items = drawables(probes.get(name, {}).get(phase, {}))
            try:
                weights = handoff_opacities(name, phase, report.get('turnContract', {}))
            except ValueError as error:
                errors.append(str(error))
                continue
            for family in ('face_base', 'torso'):
                for candidate in ('', 'mid_l_', 'mid_r_', 'profile_l_', 'profile_r_'):
                    drawable = candidate+family
                    actual = items.get(drawable, {}).get('opacity')
                    if not isinstance(actual, (int, float)) or abs(actual-weights[candidate]) > .05:
                        errors.append(f'Native view handoff visibility wrong: {name}/{phase}/{drawable}')
                if report.get('turnContract', {}).get('materialBlend'):
                    lower = next(prefix for prefix in (before, after) if weights[prefix] == 1)
                    upper = after if lower == before else before
                    lower_order = items.get(lower+family, {}).get('renderOrder')
                    upper_order = items.get(upper+family, {}).get('renderOrder')
                    if (not isinstance(lower_order, int) or not isinstance(upper_order, int)
                            or lower_order >= upper_order):
                        errors.append(f'Native view handoff surface order wrong: {name}/{phase}/{family}')
    orders = [drawables(probes.get('body_turn', {}).get(phase, {})) for phase in ('before', 'after')]
    if not (orders[0].get('arm_l', {}).get('drawOrder', 0) < orders[0].get('arm_r', {}).get('drawOrder', 0)
            and orders[1].get('arm_l', {}).get('drawOrder', 0) > orders[1].get('arm_r', {}).get('drawOrder', 0)):
        errors.append('Native arm occlusion draw order does not reverse with body turn')
    suspension = probes.get('suspension', {})
    for anchor in ('hangerLeft', 'hangerRight', 'hang'):
        before = suspension.get('before', {}).get('geometry', {}).get('anchors', {}).get(anchor)
        after = suspension.get('after', {}).get('geometry', {}).get('anchors', {}).get(anchor)
        if not before or not after or math.dist(before, after) > .001:
            errors.append(f'Rope suspension point moves with swing: {anchor}')
    seated = probes.get('seated', {})
    for name in ('rope_l', 'rope_r', 'seat'):
        before, after = [drawables(seated.get(phase, {})).get(name, {}) for phase in ('before', 'after')]
        if before.get('opacity', 1) > .05 or after.get('opacity', 0) < .95:
            errors.append(f'Swing prop visibility incorrect: {name}')
    for side, probe_name in (('l', 'hand_left'), ('r', 'hand_right')):
        contract = report.get('handContract', {}).get(side, {})
        rest_names = contract.get('rest', [f'hand_{side}'])
        support_names = contract.get('support', [f'hand_{side}_support'])
        grip_names = contract.get('grip', [f'hand_{side}_grip_palm', f'hand_{side}_grip_fingers'])
        names = [*rest_names, *support_names, *grip_names]
        for name in names:
            if name not in native:
                errors.append(f'Missing independently authored hand drawable: {name}')
        for phase in ('before', 'after'):
            items = drawables(probes.get(probe_name, {}).get(phase, {}))
            for name in names:
                expected = int(name in (support_names if phase == 'before' else grip_names))
                actual = items.get(name, {}).get('opacity')
                if not isinstance(actual, (int, float)) or abs(actual-expected) > .05:
                    errors.append(f'Hand shape visibility wrong: {probe_name}/{phase}/{name}')
            if phase == 'after':
                orders = [items.get(name, {}).get('drawOrder', 0) for name in (grip_names[0], 'rope_' + side, grip_names[-1])]
                if not orders[0] < orders[1] < orders[2]:
                    errors.append(f'Grip palm/rope/finger native occlusion is incorrect: {side}')
        snapshots = list(captures.values()) + [phase for probe in probes.values() for phase in
                                              (probe.get('before', {}), probe.get('after', {}))]
        for snapshot in snapshots:
            items = drawables(snapshot)
            rest = sum(items.get(name, {}).get('opacity', 0) for name in rest_names)
            support = sum(items.get(name, {}).get('opacity', 0) for name in support_names)
            grip = max((items.get(name, {}).get('opacity', 0) for name in grip_names), default=0)
            # Grip palm/fingers are complementary pieces of one hand. Count their
            # shared opacity once; summing the two would invent a duplicate hand.
            total = rest + support + grip
            if report.get('v5Contract', {}).get('motionPolishVersion') == 1:
                total += max((items.get(f'clean_hand_{side}_{part}', {}).get('opacity', 0)
                              for part in ('palm', 'fingers')), default=0)
                total += max((items.get(f'clean_ground_hand_{side}_{part}', {}).get('opacity', 0)
                              for part in ('palm', 'fingers')), default=0)
                total += items.get(f'clean_ground_hand_{side}_relaxed', {}).get('opacity', 0)
            if total > 1.05:
                errors.append(f'Multiple hand forms remain visible simultaneously: {side}/{snapshot.get("screenshot")}')
    for standing, sitting in (('skirt', 'skirt_swing'), ('leg_l', 'leg_l_swing'), ('leg_r', 'leg_r_swing')):
        before, after = [drawables(seated.get(phase, {})) for phase in ('before', 'after')]
        if (before.get(standing, {}).get('opacity', 0) < .95 or before.get(sitting, {}).get('opacity', 1) > .05
                or after.get(standing, {}).get('opacity', 1) > .05 or after.get(sitting, {}).get('opacity', 0) < .95):
            errors.append(f'Standing/seated artwork replacement incorrect: {standing}/{sitting}')
    return errors


def v5_probe_definitions(metadata: dict, endpoints: dict) -> tuple[dict, dict]:
    """Use the loaded motion baselines and authored support table, not guessed poses."""
    idle = endpoints['idle']['start']
    probes = {'v5_sleep_proportion': ({**idle, 'ParamPoseSleep': 0}, {**idle, 'ParamPoseSleep': 1})}
    for side in ('L', 'R'):
        probes['v5_rest_'+side.lower()] = ({**idle, 'ParamArm'+side+'A': -8, 'ParamArm'+side+'B': -5},
                                         {**idle, 'ParamArm'+side+'A': 8, 'ParamArm'+side+'B': 5})
        for axis in ('X', 'Y'):
            parameter = 'ParamRibbon'+side+axis
            probes['v5_wind_'+side.lower()+axis.lower()] = ({**idle, parameter: -1}, {**idle, parameter: 1})
    climb = metadata['locomotion']['climb']
    refined = climb.get('refinementParameter') == 'ParamClimbRefine'
    polished = motion_polish_enabled(metadata.get('refinement', {}))
    expected_cycle, expected_rise = ((1.0 if polished else 1.6), .12) if refined else (.9, .16)
    if polished and not refined:
        raise ValueError('Motion polish requires refined climbing')
    if (climb['phaseParameter'] != 'ParamClimbPhase' or climb['cycleDuration'] != expected_cycle
            or climb['risePerCycle'] != expected_rise or climb['nearHand'] != {'left': 'r', 'right': 'l'}
            or climb['supportWindows'] != {'near': [[0, .1], [.35, 1]], 'far': [[0, .6], [.85, 1]]}):
        raise ValueError('Unknown v5 climbing support contract')
    table = climb['phaseTravel']
    if (not isinstance(table, list) or len(table) < 2 or table[0] != [0, 0] or table[-1] != [1, 1]
            or not all(len(row) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in row) for row in table)
            or any(b[0] <= a[0] or b[1] < a[1] for a, b in zip(table, table[1:]))):
        raise ValueError('Invalid v5 climb phase travel table')
    support = {}
    for direction in ('left', 'right'):
        baseline = endpoints['climb_'+direction]['start']
        if refined and baseline.get('ParamClimbRefine') != 1:
            raise ValueError('Refined support contract must use its actual authored pose')
        if polished and baseline.get(climb_drape_exchange(metadata['refinement'])['parameter']) != 1:
            raise ValueError('Climbing probe must use the independently authored drape material gate')
        for role, windows in climb['supportWindows'].items():
            near = climb['nearHand'][direction]
            side = near if role == 'near' else ('l' if near == 'r' else 'r')
            for index, (start, end) in enumerate(windows):
                middle = (start+end)/2
                for half, pair in enumerate(((start, middle), (middle, end))):
                    name = f'v5_climb_{direction}_{role}{index}_{half}'
                    probes[name] = tuple({**baseline, 'ParamClimbPhase': phase} for phase in pair)
                    support[name] = {'side': side, 'phases': list(pair), 'direction': direction, 'role': role}
    cleaning = brush_contract(metadata)
    for state in (name for name in ANIMATIONS if name.startswith('clean_')):
        baseline = endpoints[state]['start']
        if cleaning:
            side = cleaning[state]['workSide'].upper()
            other = 'R' if side == 'L' else 'L'
            required = {'ParamCleanContext': cleaning[state]['context'], 'ParamCleanPose'+side: 1,
                        'ParamCleanBrush'+side: 1, 'ParamCleanBrush'+other: 0,
                        'ParamArmPoseMode': cleaning[state].get('armPoseMode', 1)}
            required[climb_drape_exchange(metadata['refinement'])['parameter']] = int(cleaning[state]['context'] in (-1, 1))
            if any(baseline.get(key) != value for key, value in required.items()):
                raise ValueError(f'Brush probe must use the actual authored working pose: {state}')
            for index, pair in enumerate(((-1, 0), (0, 1))):
                probes[f'v51_brush_{state}_{index}'] = tuple({**baseline, 'ParamCleanStroke': value} for value in pair)
        else:
            probes['v5_fan_'+state] = ({**baseline, 'ParamCleaningSweep': -.65}, {**baseline, 'ParamCleaningSweep': 1})
    if polished:
        baseline = endpoints['fall_float']['start']
        if any(baseline.get(name) != 1 for name in ('ParamFreeArmActive', 'ParamFreeArmPose', 'ParamFreeArmBrace')):
            raise ValueError('Brace probe must use actual authored fall pose')
        recovery = [(1, 1), (1, .5), (1, 0), (.5, 0), (0, 0)]
        for index, pair in enumerate(zip(recovery, recovery[1:])):
            probes[f'v51_free_recovery_{index}'] = tuple({**baseline, 'ParamFreeArmPose': pose,
                                                        'ParamFreeArmBrace': brace} for pose, brace in pair)
    return probes, support


def capture_binding_valid(snapshot: dict, generation: int, token: int, name: str) -> bool:
    playback = snapshot.get('playback', {})
    return (snapshot.get('generation') == generation and playback.get('token') == token
            and playback.get('name') == name and playback.get('evaluated') is True
            and isinstance(playback.get('nativeUpdateCount'), int) and playback['nativeUpdateCount'] > 0)


def mesh_scale_ratio(before: dict, after: dict) -> float:
    """Translation/rotation invariant RMS extent; a screen AABB would misread the head nod as scaling."""
    import numpy as np
    a, b = [np.asarray(item.get('positions', []), dtype=float).reshape(-1, 2) for item in (before, after)]
    if len(a) < 3 or a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('Missing finite native mesh positions for proportion measurement')
    aa, bb = float(np.square(a-a.mean(axis=0)).sum()), float(np.square(b-b.mean(axis=0)).sum())
    if aa <= 1e-12 or bb <= 1e-12:
        raise ValueError('Collapsed native mesh')
    return math.sqrt(bb/aa)


def mesh_principal_scales(before: dict, after: dict) -> list[float]:
    """Best-fit affine singular scales catch stretching that preserves total RMS extent."""
    import numpy as np
    mesh_scale_ratio(before, after)  # Validate topology length, finite positions and non-collapse.
    a, b = [np.asarray(item['positions'], dtype=float).reshape(-1, 2) for item in (before, after)]
    a, b = a-a.mean(axis=0), b-b.mean(axis=0)
    transform, _, rank, _ = np.linalg.lstsq(a, b, rcond=None)
    if rank != 2:
        raise ValueError('Degenerate native mesh for proportion fit')
    return np.linalg.svd(transform, compute_uv=False).tolist()


def inspect_v5(report: dict) -> list[str]:
    """Actual native samples and source-UV contacts; these guards do not certify artwork quality."""
    errors, checks = [], {}
    report['v5Checks'] = checks
    measured = report.setdefault('v5Measurements', {})
    captures, probes = report.get('captures', {}), report.get('probes', {})
    contract = report.get('v5Contract', {})
    polished = contract.get('motionPolishVersion') == 1
    def items(snapshot):
        return {item['id']: item for item in snapshot.get('drawables', [])}
    def run(name, operation):
        before = len(errors)
        try:
            operation()
        except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError) as error:
            errors.append(f'v5 {name}: {error}')
        checks[name] = len(errors) == before
    def identity():
        if report.get('modelRevision') != 'v5':
            raise ValueError('Actual authored parameter/drawable sets and 31 completed states required')
        measured['nativeIdentity'] = dict(report['modelRevisionEvidence'])
    run('nativeIdentity', identity)
    def motion_bindings():
        rows = measured.setdefault('motionBindings', {})
        for name in (*ANIMATIONS, *TRANSITIONS, *V5_CLEAN_SEGMENTS):
            event = report.get('finishedEvents', {}).get(name, {})
            binding = report.get('motionBindings', {}).get(name, {})
            if (event.get('type') != 'finished' or event.get('cycle') != 1 or event.get('name') != name
                    or not binding or any(event.get(key) != binding.get(key) for key in ('name', 'token', 'generation'))):
                raise ValueError(f'Unbound native completion: {name}')
            snapshot = captures.get(name, {})
            if not capture_binding_valid(snapshot, binding['generation'], binding['token'], name):
                raise ValueError(f'Stale/unevaluated motion capture: {name}')
            rows[name] = {'binding': binding, 'finishedEvent': event,
                          'captureNativeUpdateCount': snapshot['playback']['nativeUpdateCount']}
    run('motionBindings', motion_bindings)
    def cleanup():
        rows = measured.setdefault('cleanupSegments', {})
        for name in V5_CLEAN_SEGMENTS:
            binding = report['motionBindings'][name]
            for phase in ('start', 'end'):
                snapshot = captures.get(name+'-'+phase, {})
                if not capture_binding_valid(snapshot, binding['generation'], binding['token'], name):
                    raise ValueError(f'Missing current native cleanup {phase}: {name}')
                rows.setdefault(name, {})[phase] = {**cleanup_endpoint_parameters(report, name, phase), 'binding': binding}
            if report.get('markers', {}).get(name):
                raise ValueError(f'Cleanup transition emitted a loop sweep: {name}')
        for name in (state for state in ANIMATIONS if state.startswith('clean_')):
            if report.get('markers', {}).get(name) != ['clean_sweep']:
                raise ValueError(f'Missing actual cleaning sweep marker: {name}')
    run('cleanupSegments', cleanup)
    def rest_contacts():
        rest = contract['restContacts']
        if set(rest) != {'l', 'r'}:
            raise ValueError('Missing both rest hand contact definitions')
        converted = {side: {'cuff': value['cuff'], 'hands': [value['hand']], 'sourceUv': value['sourceUv']}
                     for side, value in rest.items()}
        # Drag/fall now use the authored active relaxed hands. Checking the hidden
        # original resting hands there would reject the intended replacement.
        samples = {name: captures[name] for name in ('idle', 'happy', 'talk', 'petting', 'sleep_loop')}
        for side in ('l', 'r'):
            for phase in ('before', 'after'):
                samples[f'v5_rest_{side}-{phase}'] = probes['v5_rest_'+side][phase]
        errors.extend(inspect_contacts(report, samples, converted, 'restContactMeasurements'))
        measured['restContacts'] = report.get('restContactMeasurements', {})
        for name, sample in samples.items():
            mode = sample.get('parameters', {}).get('ParamArmPoseMode')
            if not isinstance(mode, (int, float)) or not math.isfinite(mode) or abs(mode) > .03:
                raise ValueError(f'Rest contact sample does not use the actual resting pose: {name}')
            actual = items(sample)
            for side, definition in rest.items():
                opacities = [actual[key]['opacity'] for key in (definition['hand'], definition['cuff'])]
                if not all(isinstance(value, (int, float)) and math.isfinite(value) and value >= .95 for value in opacities):
                    raise ValueError(f'Rest hand/cuff pixels are hidden: {name}/{side}')
        capture_keys = {name: name for name in ('drag_left', 'drag_right', 'fall_float')}
        capture_keys['land'] = 'land-start'  # Later landing frames intentionally blend back to rest.
        active_samples = {key: captures[key] for key in capture_keys.values()}
        free_contract = free_motion_contacts(contract)
        active_contract = free_contract or report['contactContract']
        errors.extend(inspect_contacts(report, active_samples, active_contract, 'activeMotionContactMeasurements'))
        rows = measured['activeMotionContacts'] = {}
        for name, capture_key in capture_keys.items():
            sample = active_samples[capture_key]
            binding = report['motionBindings'][name]
            if not capture_binding_valid(sample, binding['generation'], binding['token'], name):
                raise ValueError(f'Stale/unevaluated active hand capture: {name}/{capture_key}')
            rows[name] = {'captureKey': capture_key, 'binding': binding,
                          'contacts': report['activeMotionContactMeasurements'].get(capture_key, {})}
            actual = items(sample)
            parameters = sample.get('parameters', {})
            mode = parameters.get('ParamArmPoseMode')
            expected_mode = 0 if free_contract else 1
            if not isinstance(mode, (int, float)) or not math.isfinite(mode) or abs(mode-expected_mode) > .03:
                raise ValueError(f'Drag/fall contact sample does not use the actual active pose: {name}')
            if free_contract and abs(parameters.get('ParamFreeArmActive', -1)-1) > .03:
                raise ValueError(f'Free sleeve pose did not activate: {name}')
            for side, definition in active_contract.items():
                relaxed = set(definition['hands']) & set(report['handContract'][side]['rest'])
                if len(relaxed) != 1:
                    raise ValueError(f'Missing unique authored active relaxed hand: {side}')
                hand = relaxed.pop()
                shape = parameters.get('ParamHand'+side.upper()+'Shape')
                if not isinstance(shape, (int, float)) or not math.isfinite(shape) or abs(shape) > .03:
                    raise ValueError(f'Drag/fall sample is not the relaxed hand shape: {name}/{side}')
                opacities = [actual[key]['opacity'] for key in (hand, definition['cuff'])]
                if not all(isinstance(value, (int, float)) and math.isfinite(value) and value >= .95 for value in opacities):
                    raise ValueError(f'Active relaxed hand/cuff pixels are hidden: {name}/{side}')
    run('restContacts', rest_contacts)
    def climb_support():
        table = report['locomotion']['climb']['phaseTravel']
        rise = report['locomotion']['climb']['risePerCycle']
        cases = report['climbProbeContract']
        if len(cases) != 16:
            raise ValueError('All near/far planted windows on both walls require three phase samples')
        rows = measured.setdefault('climbSupport', {})
        for name, definition in cases.items():
            side = definition['side']
            uv = report['contactContract'][side].get('handSourceUv', report['contactContract'][side]['sourceUv'])
            points = []
            for phase_name, phase in zip(('before', 'after'), definition['phases']):
                snapshot = probes[name][phase_name]
                point = source_point(report, snapshot, 'hand_'+side+'_support', uv)
                info = snapshot['canvasInfo']; factor = info['pixelsPerUnit']/max(info['width'], info['height'])
                segment = next(((a, b) for a, b in zip(table, table[1:]) if a[0] <= phase <= b[0]), None)
                if segment is None:
                    raise ValueError(f'Climb phase is outside the authored travel table: {phase}')
                a, b = segment; travel = a[1]+(b[1]-a[1])*(phase-a[0])/(b[0]-a[0])
                points.append([point[0]*factor, -point[1]*factor-rise*travel])
            slip = max(abs(a-b) for a, b in zip(*points))
            rows[name] = {'worldPoints': points, 'maximumAxisSlip': slip, 'limit': .008}
            if not math.isfinite(slip) or slip > .008:
                raise ValueError(f'Planted hand slides in world space: {name} ({slip})')
            errors.extend(inspect_contacts(report, {name+'-'+phase: probes[name][phase] for phase in ('before', 'after')}))
    run('climbSupport', climb_support)
    def sleep_proportions():
        if contract['sleep'].get('upperBodyScale') != [1, 1] or contract['sleep'].get('pose') != 'curled ground sitting':
            raise ValueError('Unknown authored sleep proportion contract')
        before, after = [items(probes['v5_sleep_proportion'][phase]) for phase in ('before', 'after')]
        ratios = {name: mesh_scale_ratio(before[name], after[name]) for name in ('face_base', 'torso')}
        axes = {name: mesh_principal_scales(before[name], after[name]) for name in ratios}
        measured['sleepProportions'] = {'scaleRatios': ratios, 'principalScales': axes, 'tolerance': .03,
                                      'measurement': 'native centered vertex RMS extent and affine singular scales; rotation invariant'}
        if any(not .97 <= ratio <= 1.03 for ratio in [*ratios.values(), *(v for pair in axes.values() for v in pair)]):
            raise ValueError(f'Upper body or face changes scale during sleep: {ratios}')
        for name in contract['sleep']['seated']:
            if before[name]['opacity'] > .05 or after[name]['opacity'] < .95:
                raise ValueError(f'Curled sleeping artwork did not replace standing pose: {name}')
        for name in ('skirt', 'leg_l', 'leg_r'):
            if after[name]['opacity'] > .05:
                raise ValueError(f'Standing garment remains over sleeping pose: {name}')
    run('sleepProportions', sleep_proportions)
    def ribbon_wind():
        import numpy as np
        if contract['ribbons'].get('rootPinned') is not True or contract['ribbons'].get('range') != [-1, 1]:
            raise ValueError('Missing authored pinned ribbon wind contract')
        rows = measured.setdefault('ribbonWind', {})
        for side in ('l', 'r'):
            for axis in ('x', 'y'):
                name = 'v5_wind_'+side+axis
                before, after = [items(probes[name][phase]) for phase in ('before', 'after')]
                mesh = 'ribbon_'+side; other = 'ribbon_'+('r' if side == 'l' else 'l')
                a, b = [np.asarray(value[mesh]['positions']).reshape(-1, 2) for value in (before, after)]
                uv = np.asarray(before[mesh]['textureUvs']).reshape(-1, 2)
                info = probes[name]['before']['canvasInfo']; factor = info['pixelsPerUnit']/max(info['width'], info['height'])
                root = uv[:, 1] <= uv[:, 1].min()+1e-6
                delta = (b-a)*factor
                root_shift = float(np.linalg.norm(delta[root], axis=1).max())
                tail_shift = float(np.linalg.norm(delta, axis=1).max())
                other_shift = float(np.abs(np.asarray(after[other]['positions'])-np.asarray(before[other]['positions'])).max())*factor
                rows[name] = dict(rootShift=root_shift, tailShift=tail_shift, oppositeRibbonShift=other_shift)
                if not all(math.isfinite(v) for v in (root_shift, tail_shift, other_shift)) or root_shift > .003 or tail_shift < .02 or other_shift > .001:
                    raise ValueError(f'Ribbon root/tail/independence contract failed: {name}: {rows[name]}')
    run('ribbonWind', ribbon_wind)
    def fan_sweep():
        rows = measured.setdefault('fanSweep', {})
        for state in (name for name in ANIMATIONS if name.startswith('clean_')):
            probe = probes['v5_fan_'+state]
            side = 'l' if state == 'clean_climb_left' else 'r'
            other = 'r' if side == 'l' else 'l'
            tips, supports = [], []
            for phase in ('before', 'after'):
                snapshot = probe[phase]; actual = items(snapshot)
                if actual['clean_fan_'+side]['opacity'] < .95 or actual['clean_fan_'+other]['opacity'] > .05:
                    raise ValueError(f'Cleaning fan uses wrong hand: {state}/{phase}')
                info = snapshot['canvasInfo']; factor = info['pixelsPerUnit']/max(info['width'], info['height'])
                bounds = actual['clean_fan_'+side]['bounds']; tips.append([(bounds[0]+bounds[2]/2)*factor, (bounds[1]+bounds[3]/2)*factor])
                definition = report['contactContract'][other]
                shown = [name for name in definition['hands'] if actual[name]['opacity'] > .95]
                if len(shown) != 1:
                    raise ValueError(f'Cleaning support hand is ambiguous: {state}/{phase}')
                point = source_point(report, snapshot, shown[0], definition.get('handSourceUv', definition['sourceUv']))
                supports.append([value*factor for value in point])
                errors.extend(inspect_contacts(report, {state+'-sweep-'+phase: snapshot}))
            distance = math.dist(*tips)
            slip = math.dist(*supports)
            rows[state] = {'nativeFanCenterDistance': distance, 'supportHandMovement': slip}
            if not math.isfinite(distance) or distance < .01 or not math.isfinite(slip) or slip > .008:
                raise ValueError(f'Cleaning fan has no actual geometric sweep: {state}')
    if polished:
        def measure(name, operation):
            measured[name] = operation(report)
        run('brushSweep', lambda: measure('brushSweep', measure_brush_sweep))
        run('brushTransitions', lambda: measure('brushTransitions', measure_brush_transitions))
        run('freeBrace', lambda: measure('freeBrace', measure_free_brace))
        run('climbMaterials', lambda: measure('climbMaterials', measure_climb_materials))
    else:
        run('fanSweep', fan_sweep)
    definitions = report.get('v5ProbeDefinitions', {})
    if len(definitions) != (35 if polished else 27):
        errors.append('Missing complete native v5 probe definitions')
    for name, definition in definitions.items():
        probe = probes.get(name, {})
        for phase, expected in zip(('before', 'after'), definition):
            snapshot = probe.get(phase, {})
            if snapshot.get('visiblePixels', 0) < 100 or snapshot.get('glError'):
                errors.append(f'Invalid native v5 probe image: {name}/{phase}')
            for parameter, target in expected.items():
                value = snapshot.get('parameters', {}).get(parameter)
                if not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value-target) > .01:
                    errors.append(f'Native v5 probe target differs: {name}/{phase}/{parameter}')
            if not snapshot.get('drawables') or any(item.get('finiteVertices') is not True for item in snapshot['drawables']):
                errors.append(f'Nonfinite native v5 probe geometry: {name}/{phase}')
        if not name.startswith('v5_climb_') and not probe.get('pixelsChanged'):
            errors.append(f'No fixed-time native v5 pixel response: {name}')
    checks['probeTargets'] = not any('v5 probe' in error or 'v5 pixel' in error for error in errors)
    measured['probeTargets'] = {'definitions': len(definitions), 'capturedPairs': sum(name in probes for name in definitions)}
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", type=Path, default=ROOT / "assets/live2d/Maple/Maple.model3.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/maple-runtime")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--refinement", action="store_true", help="Require actual v4/v5 revision contracts, all internal clips and full native UV evidence")
    parser.add_argument("--atlas-audit", type=Path, help="Native pack audit for verified wrist/cuff UV registration (required with --refinement)")
    parser.add_argument("--native-uv-audit", type=Path, help="Passing verify_native_uv.py report for the complete model mesh set (required with --refinement)")
    options = parser.parse_args()
    source = options.model.resolve(strict=True)
    output = options.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new output directory to preserve earlier native QA evidence")
    output.mkdir(parents=True, exist_ok=True)
    resource_hashes = resource_snapshot(source.parent, ROOT / "web/dist")
    settings = json.loads(source.read_text(encoding="utf-8-sig"))
    atlas_audit, contact_contract, hand_contract, turn_contract, neck_contract = {}, {}, {}, {}, {}
    atlas_mapping, uv_evidence = [], {}
    refinement = {}
    metadata_path = source.parent / settings.get('CuteMaple', {}).get('Metadata', 'Maple.pet.json')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8-sig'))
    physics_outputs = (physics_resource_contract(source.parent, settings, metadata['refinement']['nativeParameterIds'])
                       if motion_polish_enabled(metadata.get('refinement', {})) else None)
    if options.refinement:
        refinement = metadata.get('refinement', {})
        if refinement.get('version') not in (4, 5):
            parser.error('Native refinement requires known v4 or v5 authored metadata')
        expected_drawables = declared_native_drawables(refinement) if refinement['version'] == 5 else None
        mesh_count = len(expected_drawables) if expected_drawables is not None else (141 if refinement['version'] == 5 else 134)
        if options.atlas_audit is None:
            parser.error('--refinement requires --atlas-audit from pack_native_atlas.py')
        if options.native_uv_audit is None:
            parser.error('--refinement requires --native-uv-audit with the complete verified mesh set')
        atlas_audit_bytes = options.atlas_audit.read_bytes()
        atlas_audit = json.loads(atlas_audit_bytes)
        try:
            moc_name = settings['FileReferences']['Moc']
            moc_path = (source.parent/moc_name).resolve(strict=True)
            moc_relative = moc_path.relative_to(source.parent).as_posix()
            uv_evidence = verify_native_uv_evidence(options.native_uv_audit, moc_path, options.atlas_audit,
                resource_hashes['assets/live2d/Maple/' + moc_relative], expected_mesh_count=mesh_count,
                expected_drawable_ids=expected_drawables)
            if uv_evidence['atlasAuditSha256'] != hashlib.sha256(atlas_audit_bytes).hexdigest():
                raise ValueError('Atlas placement audit changed during QA preflight')
        except (OSError, ValueError, KeyError, TypeError) as error:
            (output/'input-validation.json').write_text(json.dumps({
                'passed': False, 'stage': 'native-uv-evidence', 'errors': [str(error)]}, indent=2)+'\n', encoding='utf-8')
            parser.error(str(error))
        contact_contract, hand_contract = refinement.get('contacts', {}), refinement.get('hands', {})
        turn_contract = refinement.get('turn', {})
        neck_contract = refinement.get('neck', {})
        if set(contact_contract) != {'l', 'r'}:
            parser.error('Export metadata is missing the v4 wrist/cuff contact contract')
        textures = settings.get('FileReferences', {}).get('Textures', [])
        if len(textures) != len(atlas_audit.get('atlases', [])):
            parser.error('Native export texture count differs from packed atlas')
        try:
            for page, (exported, atlas) in enumerate(zip(textures, atlas_audit['atlases'])):
                actual_path = (source.parent/exported).resolve(strict=True)
                relative = actual_path.relative_to(source.parent).as_posix()
                reference_path = Path(atlas['path'])
                if not reference_path.is_absolute():
                    reference_path = options.atlas_audit.resolve().parent/reference_path
                mapping = audit_atlas_files(actual_path, reference_path,
                    resource_hashes['assets/live2d/Maple/' + relative], atlas['sha256'])
                mapping['page'] = page
                if mapping['sourceSize'] != atlas_audit.get('atlasSize'):
                    mapping['coordinateMappingVerified'] = False
                atlas_mapping.append(mapping)
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(str(error))
        (output/'atlas-coordinate-mapping.json').write_text(json.dumps(atlas_mapping, indent=2)+'\n', encoding='utf-8')
        if not atlas_mapping or not all(page['coordinateMappingVerified'] for page in atlas_mapping):
            parser.error('Export atlas coordinate mapping failed: alpha, opaque colors, dimensions or premultiplied content above alpha 8 changed')
    references = settings.get("FileReferences", {}).get("Motions", {})
    durations, motion_endpoints = {}, {}
    required_states = [*ANIMATIONS, *(TRANSITIONS if options.refinement else ())]
    if refinement.get('version') == 5:
        required_states.extend(V5_CLEAN_SEGMENTS)
    for name in required_states:
        if not references.get(name):
            raise SystemExit(f"Missing model motion group: {name}")
        motion_path = (source.parent / references[name][0]["File"]).resolve(strict=True)
        motion_path.relative_to(source.parent)
        motion_bytes = motion_path.read_bytes()
        data = json.loads(motion_bytes)
        durations[name] = data['Meta']['Duration']
        motion_endpoints[name] = {'start': {curve['Id']: curve['Segments'][1] for curve in data['Curves']},
                                 'end': {curve['Id']: curve['Segments'][-1] for curve in data['Curves']},
                                 'sha256': hashlib.sha256(motion_bytes).hexdigest()}
    register_live2d_scheme()
    live2d_host.APP_URL += "?diagnostics=1"
    app = QApplication([])
    report = {"reportKind": "cutemaple-native-model-qa", "actualNative": False, "modelRevision": None,
              "model": str(source), "ready": False, "finished": {}, "finishedEvents": {}, "motionBindings": {},
              "motionEndpoints": motion_endpoints, "captures": {}, "probes": {}, "errors": [],
              "refinement": options.refinement, "markers": {}, "visualReview": "pending-human-review",
              "modelSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "resourceHashesAtStart": resource_hashes}
    if physics_outputs is not None:
        report['physicsOutputs'] = physics_outputs
    if options.refinement:
        report.update(atlasAudit=atlas_audit, contactContract=contact_contract, handContract=hand_contract,
                      turnContract=turn_contract, neckContract=neck_contract,
                      atlasAuditPath=str(options.atlas_audit.resolve()), nativeUvAudit=uv_evidence,
                      atlasCoordinateMappingVerified=all(page['coordinateMappingVerified'] for page in atlas_mapping),
                      atlasCoordinateMappingAudits=atlas_mapping,
                      atlasPixelsMatch=all(page['rawPixelsIdentical'] for page in atlas_mapping))
    probe_definitions = {**PROBES, **(REFINEMENT_PROBES if options.refinement else {})}
    if refinement.get('version') == 5:
        v5_probes, climb_contract = v5_probe_definitions(metadata, motion_endpoints)
        probe_definitions.update(v5_probes)
        report.update(v5Contract=refinement, locomotion=metadata['locomotion'], stateContracts=metadata['states'],
                      v5ProbeDefinitions=v5_probes, climbProbeContract=climb_contract)
    token = 0
    active = ""
    render_name = ""
    motion_queue = required_states.copy()
    effects = ["baseline", "gaze_left", "gaze_right", "gaze_disabled", *EXPRESSIONS]
    probes = list(probe_definitions)

    with tempfile.TemporaryDirectory(prefix="cutemaple-maple-qa-") as work:
        bundle = Path(work)
        shutil.copytree(ROOT / "web" / "dist", bundle / "web" / "dist")
        destination = bundle / "assets" / "live2d" / "Maple"
        shutil.copytree(source.parent, destination)
        copied_hashes = resource_snapshot(destination, bundle / "web/dist")
        if changed_resources(resource_hashes, copied_hashes):
            raise ValueError("Copied renderer resources differ from the initial QA snapshot")
        if source.name != "Maple.model3.json":
            shutil.copyfile(destination / source.name, destination / "Maple.model3.json")
            alias_sha = hashlib.sha256((destination / "Maple.model3.json").read_bytes()).hexdigest()
            if alias_sha != resource_hashes["assets/live2d/Maple/" + source.name]:
                raise ValueError("Normalized model alias differs from the verified source snapshot")
        report["resourceHashesLoaded"] = resource_snapshot(destination, bundle / "web/dist")
        parent = QWidget()
        parent.resize(512, 512)
        host = Live2DHost(parent, bundle)
        host.view.resize(512, 512)

        def failed(message: str) -> None:
            report["errors"].append(message)
            print(f"FAIL {message}", flush=True)
            app.quit()

        def capture(name: str, done=None) -> None:
            expected_token, expected_name, expected_generation = token, render_name, host._generation

            def receive(raw) -> None:
                if expected_token != token:
                    return
                try:
                    value = json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(value, dict) or not value.get("png"):
                        raise ValueError("No renderer diagnostics")
                    if not capture_binding_valid(value, expected_generation, expected_token, expected_name):
                        raise ValueError('Native capture has a stale token/generation, wrong motion or unevaluated pose')
                    value.pop('compositePng', None)
                    png = base64.b64decode(value.pop("png").split(",", 1)[1])
                    path = output / f"{name}.png"
                    path.write_bytes(png)
                    value["pngSha256"] = hashlib.sha256(png).hexdigest()
                    value["screenshot"] = str(path)
                    report["captures"][name] = value
                    print(f"FRAME {name}: {value['visiblePixels']} visible pixels", flush=True)
                    if done:
                        done()
                except (ValueError, KeyError, TypeError) as error:
                    failed(f"Capture {name}: {error}")

            host.page.runJavaScript("JSON.stringify(window.__cutemapleCapture())", receive)

        def effect_next() -> None:
            nonlocal active, token, render_name
            if not effects:
                host.send("pause", paused=True)
                probe_next()
                return
            active = effects.pop(0)
            render_name = 'idle'
            token += 1
            host.send("play", name="idle", token=token, playback="loop", fade=0.12)
            for expression in EXPRESSIONS:
                host.send("expression", name=expression, active=False, intensity=1)
            if active in ("gaze_left", "gaze_right"):
                host.send("gaze", x=-1 if active == "gaze_left" else 1, y=0.3, enabled=True)
            else:
                host.send("gaze", x=0, y=0, enabled=False)
                if active in EXPRESSIONS:
                    host.send("expression", name=active, active=True, intensity=1)
            name = active
            QTimer.singleShot(750, lambda: capture(name, effect_next))

        def probe_next() -> None:
            if not probes:
                app.quit()
                return
            name = probes.pop(0)
            before, after = probe_definitions[name]

            def receive(raw) -> None:
                try:
                    value = json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(value, dict):
                        raise ValueError("Missing rig comparison")
                    for key in ("before", "after"):
                        snapshot = value[key]
                        snapshot.pop('compositePng', None)
                        png = base64.b64decode(snapshot.pop("png").split(",", 1)[1])
                        snapshot["pngSha256"] = hashlib.sha256(png).hexdigest()
                        path = output / f"probe-{name}-{key}.png"
                        path.write_bytes(png)
                        snapshot["screenshot"] = str(path)
                        if snapshot.get("visiblePixels", 0) < 100 or snapshot.get("glError"):
                            raise ValueError(f"Empty/invalid {key} probe image")
                    value["pixelsChanged"] = value["before"]["pngSha256"] != value["after"]["pngSha256"]
                    report["probes"][name] = value
                    print(f"PROBE {name}: pixelsChanged={value['pixelsChanged']}", flush=True)
                    QTimer.singleShot(0, probe_next)
                except (ValueError, KeyError, TypeError) as error:
                    failed(f"Rig probe {name}: {error}")

            command = f"JSON.stringify(window.__cutemapleCompare({json.dumps(before)}, {json.dumps(after)}))"
            host.page.runJavaScript(command, receive)

        def motion_next() -> None:
            nonlocal active, token, render_name
            if not motion_queue:
                effect_next()
                return
            active = motion_queue.pop(0)
            render_name = active
            token += 1
            report['motionBindings'][active] = {'generation': host._generation, 'token': token, 'name': active}
            start_sample = active in V5_CLEAN_SEGMENTS or (refinement.get('version') == 5 and active == 'land')
            if start_sample:
                host.send('pause', paused=True)
            host.send("play", name=active, token=token, playback="one_shot", fade=0.12)
            name, expected_token = active, token
            delay = max(80, min(500, int(durations[active] * 450)))
            def resume_after_start():
                if token != expected_token:
                    return
                host.send('pause', paused=False)
                QTimer.singleShot(delay, lambda: capture(name) if token == expected_token else None)
            if start_sample:
                QTimer.singleShot(80, lambda: capture(name+'-start', resume_after_start))
            else:
                QTimer.singleShot(delay, lambda: capture(name) if token == expected_token else None)

        def event(value: dict) -> None:
            if value.get("type") == "ready":
                report["ready"] = True
                report["actualNative"] = True
                report["nativeStates"] = value.get("states", [])
                report["sdk"] = value.get("sdk")
                missing = set(required_states) - set(value.get("states", []))
                if missing:
                    failed(f"Missing renderer states: {sorted(missing)}")
                    return
                host.view.show()
                host.send("gaze", x=0, y=0, enabled=False)
                motion_next()
            elif value.get("type") == "marker" and value.get("token") == token and value.get("name") == active and value.get('generation') == host._generation:
                report["markers"].setdefault(active, []).append(value.get("marker"))
                if options.refinement and active in TRANSITIONS:
                    capture(active + "-" + str(value.get("marker")))
            elif value.get("type") == "finished" and value.get("token") == token and value.get("name") == active and value.get('generation') == host._generation:
                report["finished"][active] = True
                report['finishedEvents'][active] = dict(value)
                print(f"FINISHED {active}", flush=True)
                if active in (*TRANSITIONS, *V5_CLEAN_SEGMENTS) or (motion_polish_enabled(refinement) and active == 'land'):
                    capture(active + "-end", motion_next)
                elif active not in report["captures"]:
                    capture(active, motion_next)
                else:
                    QTimer.singleShot(0, motion_next)
            elif value.get("type") == "error":
                failed(value.get("message", "Unknown runtime error"))

        host.event.connect(event)
        QTimer.singleShot(options.timeout * 1000, lambda: failed("Maple runtime QA timed out"))
        parent.show()
        host.load()
        app.exec()
        host.stop()
        parent.close()
        app.processEvents()
    report["resourceHashesAtEnd"] = resource_snapshot(source.parent, ROOT / "web/dist")
    report["resourceChangesDuringRun"] = changed_resources(resource_hashes, report["resourceHashesAtEnd"])
    if report["resourceChangesDuringRun"]:
        report["errors"].append("Renderer resources changed during native QA; evidence cannot verify current files")
    if options.refinement:
        try:
            report['nativeUvAudit']['inputHashesAtEnd'] = recheck_input_hashes(uv_evidence['inputHashesAtStart'])
        except (OSError, ValueError) as error:
            report['nativeUvAudit']['verified'] = False
            report['nativeUvAudit']['sourcesUnchanged'] = False
            report['nativeUvAudit']['inputHashesAtEnd'] = {}
            report['errors'].append(str(error))
    record_native_identity(report, refinement)
    if options.refinement and report['modelRevision'] != f"v{refinement['version']}":
        report['errors'].append('Loaded native parameters/states do not establish the authored model revision')
    report["errors"] = inspect_report(report, options.refinement)
    report["passed"] = report["ready"] and not report["errors"]
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "motions": len(report["finished"]),
                      "captures": len(report["captures"]), "errors": report["errors"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
