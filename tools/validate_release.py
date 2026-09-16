"""Fail closed on incomplete Live2D release data and missing QtWebEngine files."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet_core import ANIMATIONS
from tools.authoring.animation_specs import ANIMATIONS as AUTHORING_ANIMATIONS
from tools.native_model_contract import (declared_native_parameters, declared_native_drawables, free_motion_contacts,
    REFINED_MOTION_REVISION, motion_polish_enabled)
from tools.native_material_geometry import (measure_brush_sweep, measure_brush_transitions, measure_free_brace, measure_climb_materials,
                                            continuous_swing_targets, cleanup_endpoint_parameters)

BLOCKED_BUILD_IDS = ("20260908-190153", "20260908-190759")
V4_TRANSITIONS = ("climb_to_top_left", "climb_to_top_right")
V5_CLEAN_SEGMENTS = tuple(f"{state}_{phase}" for state in AUTHORING_ANIMATIONS if state.startswith("clean_")
                          for phase in ("enter", "exit"))
V5_PARAMETERS = {"Param" + name for name in ("RibbonLX", "RibbonLY", "RibbonRX", "RibbonRY",
    "CleanFanL", "CleanFanR", "CleaningSweep", "ClimbPhase", "ClimbActive", "ClimbDirection", "CleanGround")}
V5_DRAWABLES = {"skirt_sleep", "leg_l_sleep", "leg_r_sleep", "leg_l_climb", "leg_r_climb", "clean_fan_l", "clean_fan_r"}
V5_NATIVE_CHECKS = ('nativeIdentity', 'motionBindings', 'cleanupSegments', 'restContacts', 'climbSupport',
                    'sleepProportions', 'ribbonWind', 'fanSweep', 'probeTargets')
V5_REST_CONTACT_POSES = ('idle', 'happy', 'talk', 'petting', 'sleep_loop')
V5_REST_CONTACT_PROBES = tuple(f'v5_rest_{side}-{phase}' for side in ('l', 'r') for phase in ('before', 'after'))
V5_ACTIVE_CONTACT_CAPTURES = {'drag_left': 'drag_left', 'drag_right': 'drag_right',
                              'fall_float': 'fall_float', 'land': 'land-start'}
DESKTOP_WEB_HASHES = ("web/dist/app.js", "web/dist/build-manifest.json", "web/dist/live2dcubismcore.min.js")


def blocked_build(path: Path) -> bool:
    """Known detected artifacts must not be revalidated by reading/running them."""
    return any(build in str(path) for build in BLOCKED_BUILD_IDS)


def validate_v5_measurements(native: dict, expected_mesh_count: int = 141, expected_drawable_ids: set[str] | None = None) -> list[str]:
    """Bind measured invariants to original current-token captures, not nine booleans."""
    errors = []
    measured, checks = native.get('v5Measurements', {}), native.get('v5Checks', {})
    try:
        polished = motion_polish_enabled(native.get('v5Contract', {}))
    except ValueError as error:
        return [str(error)]
    required_checks = tuple(name for name in V5_NATIVE_CHECKS if not polished or name != 'fanSweep')
    if polished:
        required_checks += ('brushSweep', 'brushTransitions', 'freeBrace', 'climbMaterials')
    for name in required_checks:
        if checks.get(name) is not True or not measured.get(name):
            errors.append(f'Actual v5 native measurement pending: {name}')
    if errors:
        return errors
    def require(value, message):
        if not value:
            raise ValueError(message)
    def number(value, low=0, high=float('inf')):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high
    def binding_valid(capture, binding):
        play = capture.get('playback', {})
        return (capture.get('generation') == binding.get('generation') and play.get('name') == binding.get('name')
                and play.get('token') == binding.get('token') and play.get('evaluated') is True
                and isinstance(play.get('nativeUpdateCount'), int) and play['nativeUpdateCount'] > 0)
    def contact_valid(row, capture):
        cuff, wrist = row.get('cuff', []), row.get('wrist', [])
        info = capture.get('canvasInfo', {})
        if (len(cuff) != 2 or len(wrist) != 2 or
                not all(number(v, -float('inf')) for v in (*cuff, *wrist)) or
                not all(number(info.get(key), 1e-12) for key in ('pixelsPerUnit', 'width', 'height'))):
            return False
        distance = math.dist(cuff, wrist) * info['pixelsPerUnit'] / max(info['width'], info['height'])
        return number(row.get('distance'), 0, .012) and abs(distance-row['distance']) < 1e-9
    def contact_items(capture):
        rows = capture.get('drawables', [])
        result = {row['id']: row for row in rows}
        require(len(result) == len(rows), 'Contact capture contains duplicate native drawables')
        return result
    try:
        require(measured['nativeIdentity'] == native.get('modelRevisionEvidence'), 'v5 measured native identity differs')
        states = (*AUTHORING_ANIMATIONS, *V4_TRANSITIONS, *V5_CLEAN_SEGMENTS)
        require(set(measured['motionBindings']) == set(states), 'All 31 measured native motion bindings are required')
        for name in states:
            binding = native.get('motionBindings', {}).get(name, {})
            event = native.get('finishedEvents', {}).get(name, {})
            capture = native.get('captures', {}).get(name, {})
            row = measured['motionBindings'][name]
            require(binding and binding.get('name') == name and event.get('type') == 'finished' and event.get('cycle') == 1
                    and all(binding.get(k) == event.get(k) for k in ('generation', 'token', 'name'))
                    and binding_valid(capture, binding) and row.get('binding') == binding and row.get('finishedEvent') == event
                    and row.get('captureNativeUpdateCount') == capture['playback']['nativeUpdateCount'],
                    f'v5 motion completion/capture is stale or unbound: {name}')
        require(set(measured['cleanupSegments']) == set(V5_CLEAN_SEGMENTS), 'Missing eight actual cleanup transitions')
        for name, phases in measured['cleanupSegments'].items():
            require(set(phases) == {'start', 'end'} and not native.get('markers', {}).get(name), 'Cleanup transition endpoints/markers differ')
            for phase, row in phases.items():
                require(number(row.get('maximumParameterError'), 0, .03) and number(row.get('parametersCompared'), 1)
                        and row.get('binding') == native['motionBindings'][name]
                        and binding_valid(native['captures'].get(name+'-'+phase, {}), row['binding']),
                        f'Cleanup transition lacks current native {phase} measurement: {name}')
                if polished:
                    actual = cleanup_endpoint_parameters(native, name, phase)
                    require(row == {**actual, 'binding': native['motionBindings'][name]},
                            f'Native cleanup endpoint parameter evidence differs: {name}/{phase}')
        require(all(native.get('markers', {}).get(name) == ['clean_sweep'] for name in AUTHORING_ANIMATIONS if name.startswith('clean_')),
                'Each cleaning loop must emit exactly its actual sweep marker')
        rest = measured['restContacts']
        require(set(rest) == set((*V5_REST_CONTACT_POSES, *V5_REST_CONTACT_PROBES)),
                'Incomplete measured rest hand/cuff contact poses')
        contract = native.get('v5Contract', {})
        rest_contract = contract.get('restContacts', {})
        rest_hands = {value['hand'] for value in rest_contract.values()}
        require(set(rest_contract) == {'l', 'r'} and len(rest_hands) == 2, 'Both authored resting hand contacts are required')
        for name, row in rest.items():
            if name in V5_REST_CONTACT_POSES:
                capture = native['captures'][name]
            else:
                probe, phase = name.rsplit('-', 1)
                capture = native['probes'][probe][phase]
            items = contact_items(capture)
            require(number(capture.get('parameters', {}).get('ParamArmPoseMode'), -.03, .03),
                    f'Rest contact capture does not use its actual resting pose: {name}')
            require(set(row) == rest_hands and all(contact_valid(hand, capture) for hand in row.values()),
                    f'Native resting hands separate from their cuffs or measured coordinates differ: {name}')
            require(all(number(items[value['hand']].get('opacity'), .95) and
                        number(items[value['cuff']].get('opacity'), .95) for value in rest_contract.values()),
                    f'Rest hand/cuff pixels are hidden: {name}')
        active = measured.get('activeMotionContacts', {})
        active_contract, hand_contract = contract.get('contacts', {}), contract.get('hands', {})
        require(set(active) == set(V5_ACTIVE_CONTACT_CAPTURES), 'All four actual active drag/fall/land contact measurements are required')
        require(set(active_contract) == set(hand_contract) == {'l', 'r'} and
                native.get('contactContract') == active_contract and native.get('handContract') == hand_contract,
                'Active hand contact definitions differ from the actual metadata contract')
        free_contract = free_motion_contacts(contract)
        if free_contract:
            active_contract = free_contract
        active_hands = {}
        for side, definition in active_contract.items():
            relaxed = set(definition['hands']) & set(hand_contract[side]['rest'])
            require(len(relaxed) == 1, f'Missing unique authored active relaxed hand: {side}')
            active_hands[side] = relaxed.pop()
        require(len(set(active_hands.values())) == 2, 'Both actual active relaxed hands are required')
        for name, key in V5_ACTIVE_CONTACT_CAPTURES.items():
            row, binding = active[name], native['motionBindings'][name]
            capture = native['captures'].get(key, {})
            require(row.get('captureKey') == key and row.get('binding') == binding and binding_valid(capture, binding),
                    f'Active hand contact capture is stale or unbound: {name}')
            items, parameters = contact_items(capture), capture.get('parameters', {})
            expected_mode = 0 if free_contract else 1
            require(number(parameters.get('ParamArmPoseMode'), expected_mode-.03, expected_mode+.03) and
                    all(number(parameters.get('ParamHand'+side.upper()+'Shape'), -.03, .03) for side in ('l', 'r')),
                    f'Active drag/fall/land capture does not use actual relaxed active hands: {name}')
            if free_contract:
                require(number(parameters.get('ParamFreeArmActive'), .97, 1.03),
                        f'Actual free sleeve pose did not activate: {name}')
            contacts = row.get('contacts', {})
            require(set(contacts) == set(active_hands.values()) and all(contact_valid(hand, capture) for hand in contacts.values()),
                    f'Active native hand/cuff gap exceeds .012 or measured coordinates differ: {name}')
            require(all(number(items[hand].get('opacity'), .95) and
                        number(items[active_contract[side]['cuff']].get('opacity'), .95) for side, hand in active_hands.items()),
                    f'Active relaxed hand/cuff pixels are hidden: {name}')
        climbs = measured['climbSupport']
        require(len(climbs) == 16, 'All 16 planted climbing phase pairs are required')
        for row in climbs.values():
            points = row.get('worldPoints', [])
            require(len(points) == 2 and all(len(p) == 2 and all(number(v, -float('inf')) for v in p) for p in points)
                    and row.get('limit') == .008 and number(row.get('maximumAxisSlip'), 0, .008)
                    and abs(max(abs(a-b) for a, b in zip(*points))-row['maximumAxisSlip']) < 1e-9,
                    'Climbing support world-position slip exceeds the actual .008 limit')
        sleep = measured['sleepProportions']
        require(set(sleep.get('scaleRatios', {})) == set(sleep.get('principalScales', {})) == {'face_base', 'torso'}
                and sleep.get('tolerance') == .03 and sleep.get('measurement')
                and all(number(v, .97, 1.03) for v in sleep['scaleRatios'].values())
                and all(len(pair) == 2 and all(number(v, .97, 1.03) for v in pair) for pair in sleep['principalScales'].values()),
                'Actual sleeping face/torso must preserve native scale within three percent')
        wind = measured['ribbonWind']
        require(set(wind) == {'v5_wind_'+side+axis for side in ('l', 'r') for axis in ('x', 'y')}
                and all(number(r.get('rootShift'), 0, .003) and number(r.get('tailShift'), .02)
                        and number(r.get('oppositeRibbonShift'), 0, .001) for r in wind.values()),
                'Independent ribbons need actual pinned roots and moving tails')
        if polished:
            # Recompute from native topology and verified material UVs; measured booleans/numbers alone cannot approve.
            for name, operation in [('brushSweep', measure_brush_sweep), ('brushTransitions', measure_brush_transitions), ('freeBrace', measure_free_brace),
                                    ('climbMaterials', measure_climb_materials)]:
                require(json.loads(json.dumps(measured[name])) == json.loads(json.dumps(operation(native))),
                        f'Actual native {name} measurements differ from recorded geometry')
        else:
            fans = measured['fanSweep']
            require(set(fans) == {n for n in AUTHORING_ANIMATIONS if n.startswith('clean_')}
                    and all(number(r.get('nativeFanCenterDistance'), .01) and number(r.get('supportHandMovement'), 0, .008)
                            for r in fans.values()), 'Four cleanup poses need actual fan sweep and preserved support')
        count = 35 if polished else 27
        require(measured['probeTargets'] == {'definitions': count, 'capturedPairs': count}
                and len(native.get('v5ProbeDefinitions', {})) == count
                and all(name in native.get('probes', {}) for name in native['v5ProbeDefinitions']),
                f'All {count} v5 actual native probe pairs are required')
        for name, targets in native['v5ProbeDefinitions'].items():
            require(len(targets) == 2 and all(isinstance(t, dict) and t for t in targets), 'Missing actual v5 probe targets')
            for phase, target in zip(('before', 'after'), targets):
                snapshot = native['probes'][name].get(phase, {})
                drawables = snapshot.get('drawables', [])
                require(number(snapshot.get('visiblePixels'), 100) and not snapshot.get('glError')
                        and len(drawables) == expected_mesh_count and len({r.get('id') for r in drawables}) == expected_mesh_count
                        and (expected_drawable_ids is None or {r.get('id') for r in drawables} == expected_drawable_ids)
                        and all(r.get('finiteVertices') is True for r in drawables)
                        and all(number(snapshot.get('parameters', {}).get(p), -float('inf'))
                            and number(v, -float('inf')) and abs(snapshot['parameters'][p]-v) <= .01 for p, v in target.items()),
                        f'v5 probe lacks visible finite native geometry or actual target parameters: {name}/{phase}')
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, ZeroDivisionError) as error:
        errors.append(str(error))
    return errors


def validate_model_evidence(source: dict, native: dict, assets: dict[str, str], native_report_sha256: str,
                            metadata_version: int | None = None, metadata_refinement: dict | None = None,
                            metadata_states: dict | None = None) -> list[str]:
    """Version-bound native QA; recording success never establishes model identity."""
    errors = []
    version = metadata_version if metadata_version is not None else (5 if source.get("modelVersion") == "v5" else 4)
    revision, mesh_count = f"v{version}", 141 if version == 5 else 134
    expected_drawables = None
    identity = native.get('modelRevisionEvidence', {})
    if version == 5:
        if (native.get('v5Contract', {}).get('motionPolishVersion', 0) != (metadata_refinement or {}).get('motionPolishVersion', 0)
                or identity.get('motionPolishVersion', 0) != (metadata_refinement or {}).get('motionPolishVersion', 0)):
            errors.append('Native motion-polish revision differs from the packaged model')
        if (metadata_refinement or {}).get('motionPolishVersion') == 1:
            if native.get('v5Contract') != metadata_refinement or not metadata_states or native.get('stateContracts') != metadata_states:
                errors.append('Native motion-polish material/state contract differs from packaged metadata')
        try:
            expected_drawables = declared_native_drawables(metadata_refinement or {})
            if expected_drawables is not None:
                mesh_count = len(expected_drawables)
        except ValueError as error:
            errors.append(str(error))
    states = (*AUTHORING_ANIMATIONS, *V4_TRANSITIONS, *(V5_CLEAN_SEGMENTS if version == 5 else ()))
    if version not in (4, 5) or source.get("modelVersion") != revision or source.get("actualNative") is not True:
        errors.append(f"Source QA must explicitly identify the actual native {revision} model")
    if any(document.get("diagnosticOnly") is True or document.get("knownInvalidNeckUV") is True
           for document in (source, native)):
        errors.append("Diagnostic-only or known-invalid model evidence cannot approve a release")
    technical = source.get("technicalResults", {})
    if technical.get("passed") is not True or technical.get("errors") != []:
        errors.append("Source technical QA must pass without errors")
    if (native.get("passed") is not True or native.get("ready") is not True or
            native.get("refinement") is not True or native.get("errors") != [] or
            native.get("reportKind") != "cutemaple-native-model-qa" or native.get("actualNative") is not True or
            native.get("modelRevision") != revision):
        errors.append("A passing verify_maple_model --refinement report is required; recording success is insufficient")
    identity = native.get("modelRevisionEvidence", {})
    required_parameters = set(identity.get("requiredParameters", []))
    core_parameters = {'ParamHeadTurn', 'ParamBodyTurn', 'ParamHandLShape', 'ParamHandRShape',
                       'ParamTransitionProgress', 'ParamArmPoseMode', 'ParamPoseSwing', 'ParamSwing', 'ParamSwingVisible'}
    core_parameters |= V5_PARAMETERS if version == 5 else set()
    if (identity.get("metadataVersion") != version or not core_parameters.issubset(required_parameters) or
            not required_parameters.issubset(identity.get("nativeParameters", [])) or
            not set(states).issubset(identity.get("nativeStates", []))):
        errors.append(f"Native revision must be established by actual metadata, loaded parameters and {len(states)} states")
    if version == 5:
        if any(document.get(key) for document in (source, native)
               for key in ('synthetic', 'syntheticTestFixture', 'mock', 'fixture', 'unitTest')):
            errors.append('Synthetic or mock native QA cannot approve v5')
        baseline = native.get("captures", {}).get("baseline", {})
        parameters, drawables = baseline.get("parameters", {}), baseline.get("drawables", [])
        ids = [row.get("id") for row in drawables if isinstance(row, dict)]
        try:
            expected = declared_native_parameters(metadata_refinement or {})
        except ValueError as error:
            expected = None
            errors.append(str(error))
        expected_count = len(expected) if expected is not None else 67
        if expected is not None and (set(parameters) != expected
                or identity.get('expectedNativeParameters') != sorted(expected)
                or identity.get('motionRevision') != metadata_refinement.get('motionRevision')):
            errors.append('Native parameters must match the exact packaged refined model inventory')
        if expected_drawables is not None and (set(ids) != expected_drawables
                or identity.get('expectedNativeDrawables') != sorted(expected_drawables)):
            errors.append('Native drawables must match the exact packaged refined model inventory')
        if (len(parameters) != expected_count or not V5_PARAMETERS.issubset(parameters) or
                any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in parameters.values()) or
                len(identity.get("nativeParameters", [])) != expected_count or set(identity["nativeParameters"]) != set(parameters) or
                len(ids) != mesh_count or len(set(ids)) != mesh_count or not V5_DRAWABLES.issubset(ids) or
                len(identity.get("nativeStates", [])) != len(states) or set(identity["nativeStates"]) != set(states)):
            errors.append(f"v5 requires actual unique {mesh_count} drawables, {expected_count} finite native parameters and all 31 states")
        errors.extend(validate_v5_measurements(native, mesh_count, expected_drawables))
    for state in states:
        if native.get("finished", {}).get(state) is not True or state not in native.get("captures", {}):
            errors.append(f"Missing verified {revision} native motion evidence: {state}")
    uv = native.get("nativeUvAudit", {})
    if (uv.get("verified") is not True or uv.get("tool") != "native-uv-pairing-audit" or
            uv.get("meshCount") != mesh_count or uv.get("matchedMeshCount") != mesh_count or
            uv.get("mocSha256") != assets.get("assets/live2d/Maple/Maple.moc3") or
            uv.get("sourcesUnchanged") is not True or not uv.get("inputHashesAtStart") or
            uv.get("inputHashesAtStart") != uv.get("inputHashesAtEnd") or
            uv.get("inputHashesAtStart", {}).get(uv.get("path")) != uv.get("sha256") or
            not uv.get("sha256") or not uv.get("atlasAuditSha256") or
            uv.get("inputHashesAtStart", {}).get(uv.get("atlasAuditPath")) != uv.get("atlasAuditSha256")):
        errors.append(f"A frozen, hash-bound full {mesh_count}-mesh native UV audit is required for this MOC3")
    tolerance, maximum = uv.get("pixelTolerance"), uv.get("maximumAtlasPixelError")
    if (not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or not 0 < tolerance <= .001 or
            not isinstance(maximum, (int, float)) or not math.isfinite(maximum) or not 0 <= maximum <= tolerance):
        errors.append("Native UV evidence must satisfy the strict .001 atlas pixel tolerance")
    atlas_verified = native.get("atlasPixelsMatch") is True
    if not atlas_verified and native.get("atlasCoordinateMappingVerified") is True:
        pages = native.get("atlasCoordinateMappingAudits", [])
        expected_pages = native.get("atlasAudit", {}).get("atlases", [])
        atlas_verified = bool(pages) and len(pages) == len(expected_pages)
        for index, page in enumerate(pages):
            atlas_verified = atlas_verified and (page.get("page") == index and all(page.get(key) is True for key in
                ("sizesIdentical", "alphaIdentical", "opaqueRGBIdentical", "premulAbove8Identical", "coordinateMappingVerified"))
                and page.get("lowAlphaThreshold") == 8 and page.get("premultiplication") == '(rgb * alpha + 127) // 255'
                and page.get("actualSha256") in assets.values() and index < len(expected_pages)
                and page.get("sourceSha256") == expected_pages[index].get("sha256"))
    if not native.get("probes") or not atlas_verified:
        errors.append("Native refinement probes and matching atlas evidence are required")
    if native.get("resourceChangesDuringRun") != []:
        errors.append("Native QA must record an unchanged resource snapshot")
    for field in ("resourceHashesAtStart", "resourceHashesLoaded", "resourceHashesAtEnd"):
        if not assets or native.get(field) != assets:
            errors.append(f"Native QA {field} differs from the complete packaged model/frontend inventory")
    if native.get("modelSha256") != assets.get("assets/live2d/Maple/Maple.model3.json"):
        errors.append("Native QA model settings hash differs from the package")
    source_hashes = {name: row.get("sha256") for name, row in source.get("files", {}).items()
                     if isinstance(row, dict)}
    if not assets or source_hashes != assets:
        errors.append("Source QA must bind every packaged model/frontend file, including motions and shaders")
    if source.get("fullReportSha256") != native_report_sha256:
        errors.append("Source QA is not bound to this exact full native report")
    visual = source.get("visualReview", {})
    if (not isinstance(visual, dict) or visual.get("status") != "accepted" or not visual.get("evidence") or
            visual.get("fullReportSha256") != native_report_sha256):
        errors.append("An independent accepted visual review bound to the full native report is required")
    return errors


def validate_desktop_evidence(report: dict, executable_sha256: str,
                              asset_sha256: dict[str, str] | None = None) -> list[str]:
    errors = []
    if report.get("executableSha256", "").lower() != executable_sha256.lower():
        errors.append("Desktop evidence belongs to a different executable")
    for field in ("passed", "profileIsolated", "runtimeActive", "nativeProvidersActive",
                  "nativeProvidersObserved", "ordinaryPetWindow", "defaultGraphicsBackend", "completed", "audioChildExited"):
        if report.get(field) is not True:
            errors.append(f"Desktop evidence missing: {field}")
    if (report.get("candidateSmokeOnly") is not False or report.get("scenario") != "full" or
            report.get("qtPlatform") != "windows" or report.get("exitCode") != 0 or
            report.get("errors") != [] or report.get("graphicsOverrides") != {}):
        errors.append("Final desktop evidence must be a complete ordinary Windows run without overrides/errors")
    if asset_sha256 is not None:
        expected = {name: value for name, value in asset_sha256.items()
                    if name.startswith("assets/live2d/Maple/") or name in DESKTOP_WEB_HASHES}
        if not expected or report.get("assetSha256") != expected:
            errors.append("Desktop evidence belongs to different or incomplete model/frontend assets")
    if not isinstance(report.get("nativeExceptionObserved"), bool):
        errors.append("Desktop evidence must record whether native fault logs were observed")
    elif report["nativeExceptionObserved"]:
        review = report.get("nativeExceptionReview", {})
        fault_logs = report.get("nativeExceptionLogs", [])
        if (review.get("status") != "reviewed-with-limitations" or not review.get("evidence") or
                not review.get("limitations") or not fault_logs):
            errors.append("Observed native exceptions require documented review and explicit limitations")
        elif (review.get("executableSha256", "").lower() != executable_sha256.lower() or
              sorted(review.get("faultLogSha256", [])) != sorted(row.get("sha256", "") for row in fault_logs) or
              review.get("continuedNativeReady") is not True or review.get("cleanExitVerified") is not True or
              review.get("werCrashMatches") != 0):
            errors.append("Native exception review must bind this executable/fault hashes and verified recovery evidence")
        if any(not row.get("exceptionCodes") or set(row["exceptionCodes"]) != {"0x8001010d"} for row in fault_logs):
            errors.append("New or unclassified native exceptions cannot use the recovered initialization COM review")
    if report.get("offscreen") is not False or report.get("durationSeconds", 0) < 3600:
        errors.append("Normal Windows desktop must run for at least 3600 seconds")
    launches = report.get("launchEvidence", {})
    runs = launches.get("runs", [])
    if (launches.get("successfulLaunches", 0) < 20 or len(runs) < 20 or
            sum(run.get("passed") is True and run.get("assetMatch") is True and
                run.get("executableSha256", "").lower() == executable_sha256.lower() and
                run.get("exitCode") == 0 and run.get("durationSeconds", 0) >= 20 and run.get("report")
                is not None for run in runs) < 20 or
            len({run.get("report") for run in runs if run.get("report")}) < 20):
        errors.append("At least 20 successful launches of identical executable/assets are required")
    checks = report.get("checks", {})
    for name in ("repeatedLaunch", "cleanExit", "idleGaze", "keyboardReaction", "audioReaction",
                 "pauseResume", "dragDrop", "displayChange"):
        if checks.get(name) is not True:
            errors.append(f"Desktop check pending: {name}")
    pending = report.get("manualOrExternalChecksPending")
    required_external = {"idleGazeMovement", "keyboardReaction", "audioReaction", "physicalDragDrop", "displayChange"}
    if not isinstance(pending, list) or required_external.intersection(pending):
        errors.append("Required physical interaction checks remain pending or unrecorded")
    if report.get("keyboardEvents", 0) < 1 or report.get("audioActiveEvents", 0) < 1:
        errors.append("Actual keyboard and audio observations are required, not only check booleans")
    if report.get("gazeAlignedSamples", 0) < 2 or not report.get("lastGazeObservation"):
        errors.append("Actual cursor movement and native gaze observations are required")
    if not any(row.get("repositionValidated") is True for row in report.get("screenEvents", [])):
        errors.append("An observed display change with verified window repositioning is required")
    if not any(row.get("status") == "passed" and row.get("moveEvents", 0) > 0 and
               row.get("pointerTravel", 0) >= 6 and row.get("windowTravel", 0) >= 6 and
               row.get("finalSettlement") and len(row.get("nativeActivity", [])) >= 3
               for row in report.get("physicalDrags", [])):
        errors.append("A physical drag, valid settlement and subsequent native activity are required")
    security = report.get("securityObservation", {})
    if (security.get("protectionUnchanged") is not True or
            security.get("status") != "no-detections-observed" or
            not security.get("checkedAt") or not security.get("observationStart") or
            not security.get("observationEnd")):
        errors.append("Default-protection observation is missing or reports a detection")
    else:
        try:
            start, end, checked = (datetime.fromisoformat(security[key].replace("Z", "+00:00"))
                                   for key in ("observationStart", "observationEnd", "checkedAt"))
            if (any(stamp.tzinfo is None for stamp in (start, end, checked)) or
                    not start <= end <= checked or (end - start).total_seconds() < report.get("durationSeconds", 0)):
                errors.append("Security observation must cover the documented desktop run with zoned timestamps")
        except (ValueError, TypeError, AttributeError):
            errors.append("Invalid security observation timestamps")
    for field in ("AntivirusEnabled", "RealTimeProtectionEnabled", "BehaviorMonitorEnabled", "OnAccessProtectionEnabled"):
        if any(security.get(phase, {}).get(field) is not True for phase in ("protectionBefore", "protectionAfter")):
            errors.append(f"Actual default-protection state was not recorded as enabled: {field}")
    if security.get("detections") != []:
        errors.append("Desktop observation must contain an explicit empty detection inventory")
    return errors


def validate_refined_release_evidence(root: Path, metadata: dict, report_path: Path | None = None,
                                     *, finalized: bool = False) -> list[str]:
    """Candidate inventory stays independent; finalized refined packages reopen the numeric evidence."""
    from tools.refined_motion_evidence import verify_refined_motion_evidence
    required = metadata.get('refinement', {}).get('motionRevision') == REFINED_MOTION_REVISION
    if not finalized and report_path is None:
        return []  # The report is produced after installation/build, before finalize().
    try:
        claimed = None
        if finalized:
            verification = json.loads((root.parent/'RELEASE-VERIFICATION.json').read_text(encoding='utf-8-sig'))
            claimed = verification.get('sourceModelQa', {}).get('refinedMotionQa')
            if not required and claimed is None and report_path is None:
                return []  # A genuinely legacy finalized package has no refined receipt.
            if not isinstance(claimed, dict):
                raise ValueError('Finalized refined release has no supplemental motion evidence')
            original = Path(claimed['reportPath'])
            if report_path is not None and report_path.resolve(strict=True) != original.resolve(strict=True):
                raise ValueError('Explicit refined report differs from finalized evidence')
            report_path = original
        measured = verify_refined_motion_evidence(root, report_path)
        if finalized:
            if measured != claimed:
                raise ValueError('Finalized refined evidence differs from its recomputed original inputs')
            copy = (root.parent/'REFINED-MOTION-QA.json').read_bytes()
            if hashlib.sha256(copy).hexdigest() != measured['reportSha256']:
                raise ValueError('Packaged refined report differs from its original evidence')
        return []
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        return [f'Refined motion release evidence failed: {error}']


def validate(root: Path, *, packaged: bool = False,
             refined_motion_report: Path | None = None) -> list[str]:
    if blocked_build(root):
        return ["This build was detected by Windows protection and is withdrawn; do not read or run its binaries"]
    errors: list[str] = []
    required = ["web/dist/index.html", "web/dist/app.js", "web/dist/live2dcubismcore.min.js",
                "web/dist/build-manifest.json", "web/dist/shaders/vertshadersrc.vert",
                "web/dist/shaders/fragshadersrcpremultipliedalpha.frag",
                "web/dist/licenses/Cubism-Core-LICENSE.md", "web/dist/licenses/Cubism-Framework-LICENSE.md",
                "web/dist/licenses/Cubism-SDK-NOTICE.md", "assets/icon.png"]
    for name in required:
        path = root / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"Missing runtime file: {name}")
    if not list((root / "assets" / "fonts").glob("*.otf")):
        errors.append("Missing bundled font")
    web_root = (root / "web" / "dist").resolve()
    try:
        files = json.loads((web_root / "build-manifest.json").read_text(encoding="utf-8"))["files"]
        if sum(name.startswith("shaders/") for name in files) < 13:
            errors.append("Incomplete Cubism R5 shader inventory")
        for name, checksum in files.items():
            path = (web_root / name).resolve()
            if (not path.is_relative_to(web_root) or not path.is_file() or
                    hashlib.sha256(path.read_bytes()).hexdigest() != checksum):
                errors.append(f"Renderer build hash mismatch: {name}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(f"Invalid renderer build manifest: {exc}")
    folder = root / "assets" / "live2d" / "Maple"
    model_path = folder / "Maple.model3.json"
    try:
        model = json.loads(model_path.read_text(encoding="utf-8-sig"))
        refs = model["FileReferences"]

        def check_file(name: object) -> Path | None:
            if not isinstance(name, str) or not name:
                errors.append("Empty or invalid model file reference")
                return None
            path = (folder / name).resolve()
            if not path.is_relative_to(folder.resolve()) or not path.is_file() or path.stat().st_size == 0:
                errors.append(f"Missing/unsafe model reference: {name}")
                return None
            return path

        moc = check_file(refs.get("Moc"))
        if moc is not None and moc.read_bytes()[:4] != b"MOC3":
            errors.append("Maple moc file is not a Cubism MOC3 export")
        textures = refs.get("Textures", [])
        if not textures:
            errors.append("Model has no textures")
        for name in textures:
            check_file(name)
        for kind in ("Physics", "Pose", "DisplayInfo", "UserData"):
            if refs.get(kind):
                check_file(refs[kind])
        for expression in refs.get("Expressions", []):
            check_file(expression.get("File"))
        metadata_name = model.get("CuteMaple", {}).get("Metadata")
        if not metadata_name:
            errors.append("Missing CuteMaple.Metadata contact/expression mapping")
        metadata = {}
        if metadata_name:
            metadata_path = check_file(metadata_name)
            if metadata_path:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        motions = refs.get("Motions", {})
        version = metadata.get("refinement", {}).get("version")
        states = (*ANIMATIONS, *V4_TRANSITIONS)
        if version == 5 and set(motions) != set(states):
            errors.append("Live2D runtime must reference exactly 19 motion groups")
        for state in states:
            entries = motions.get(state, [])
            if not entries:
                errors.append(f"Missing native motion group: {state}")
                continue
            for entry in entries:
                path = check_file(entry.get("File"))
                if path:
                    motion = json.loads(path.read_text(encoding="utf-8-sig"))
                    if not motion.get("Curves") or not motion.get("Meta", {}).get("Duration", 0) > 0:
                        errors.append(f"Motion has no authored curves/duration: {entry.get('File')}")
                if entry.get("Sound"):
                    check_file(entry["Sound"])
        errors.extend(validate_refined_release_evidence(root, metadata, refined_motion_report,
                      finalized=packaged and (root.parent/'RELEASE-VERIFICATION.json').is_file()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(f"Incomplete Maple model: {exc}")
    for obsolete in (root / "assets/sprites_v2", root / "desktop_decorations.py"):
        if obsolete.exists():
            errors.append(f"Obsolete sprite runtime payload: {obsolete.name}")
    if packaged:
        inventory = {path.name.lower() for path in root.rglob("*") if path.is_file()}
        for name in ("QtWebEngineProcess.exe", "Qt6WebEngineCore.dll", "Qt6WebEngineWidgets.dll",
                     "icudtl.dat", "qtwebengine_resources.pak", "qtwebengine_resources_100p.pak",
                     "qtwebengine_resources_200p.pak", "v8_context_snapshot.bin", "en-US.pak", "qwindows.dll"):
            if name.lower() not in inventory:
                errors.append(f"Missing packaged QtWebEngine dependency: {name}")
        forbidden = [str(path.relative_to(root)) for path in root.rglob("*")
                     if path.suffix.lower() in {".cmo3", ".can3", ".psd", ".ora"}]
        if forbidden:
            errors.append("Authoring sources must not be shipped: " + ", ".join(forbidden))
        helper = root / "cleaner/CuteMaple-Cleaner.exe"
        if not helper.is_file() or helper.stat().st_size == 0:
            errors.append("Missing independent privileged cleanup helper: cleaner/CuteMaple-Cleaner.exe")
        else:
            try:
                manifest = json.loads((root / 'cleaner/native-build.json').read_text(encoding='utf-8-sig'))
                if (manifest.get('implementation') != 'cpp-msvc' or manifest.get('testBuild') is not False
                        or manifest.get('sha256', '').lower() != hashlib.sha256(helper.read_bytes()).hexdigest()):
                    errors.append('Native cleaner build manifest does not match the production executable')
            except (OSError, ValueError):
                errors.append('Missing or invalid native cleaner build manifest')
        if any(path.name.lower() not in {'cutemaple-cleaner.exe', 'native-build.json'}
               for path in (root / 'cleaner').rglob('*') if path.is_file()):
            errors.append('Native cleaner directory contains unexpected dependencies or test files')
        if any("pyside" in path.name.lower() or path.name.lower().startswith("qt6")
               for path in (root / "cleaner").rglob("*")):
            errors.append("Cleanup helper must not bundle Qt or PySide")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--packaged", action="store_true")
    parser.add_argument("--refined-motion-report", type=Path,
                        help="Reopen supplemental actual Core evidence; automatically required on finalized refined packages")
    options = parser.parse_args()
    errors = validate(options.root.resolve(), packaged=options.packaged,
                      refined_motion_report=options.refined_motion_report)
    print(json.dumps({"ok": not errors, "mode": "live2d-inventory",
                      "errors": errors}, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
