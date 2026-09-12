import json
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from tools.authoring.animation_specs import ANIMATIONS
from tools.validate_release import (validate, validate_desktop_evidence, validate_model_evidence,
                                    blocked_build, V4_TRANSITIONS, DESKTOP_WEB_HASHES)
from tools.qa_resource_snapshot import resource_snapshot


def frontend_bundle(root):
    files = ["web/dist/index.html", "web/dist/app.js", "web/dist/live2dcubismcore.min.js",
             "web/dist/licenses/Cubism-Core-LICENSE.md", "web/dist/licenses/Cubism-Framework-LICENSE.md",
             "web/dist/licenses/Cubism-SDK-NOTICE.md", "assets/icon.png", "assets/fonts/font.otf"]
    files += ["web/dist/shaders/vertshadersrc.vert", "web/dist/shaders/fragshadersrcpremultipliedalpha.frag"]
    files += [f"web/dist/shaders/fixture-{i}.frag" for i in range(11)]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test inventory")
    runtime = root / "web/dist"
    hashes = {str(path.relative_to(runtime)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in runtime.rglob("*") if path.is_file()}
    (runtime / "build-manifest.json").write_text(json.dumps({"files": hashes}))


def test_missing_model_always_fails(tmp_path):
    frontend_bundle(tmp_path)
    assert any("Incomplete Maple model" in error for error in validate(tmp_path))


def test_model_reference_cannot_escape_runtime_model_folder(tmp_path):
    frontend_bundle(tmp_path)
    folder = tmp_path / "assets/live2d/Maple"
    folder.mkdir(parents=True)
    outside = tmp_path / "assets/live2d/outside.moc3"
    outside.write_bytes(b"MOC3")
    (folder / "Maple.model3.json").write_text(json.dumps({
        "FileReferences": {"Moc": "../outside.moc3", "Textures": [], "Motions": {}},
    }))
    errors = validate(tmp_path)
    assert any("unsafe model reference" in error for error in errors)
    assert any("Missing native motion group: sleep_enter" in error for error in errors)


def test_package_guard_rejects_missing_chromium_and_authoring_sources(tmp_path):
    frontend_bundle(tmp_path)
    (tmp_path / "leaked-source.cmo3").write_bytes(b"source")
    errors = validate(tmp_path, packaged=True)
    assert any("QtWebEngineProcess.exe" in error for error in errors)
    assert any("Authoring sources must not be shipped" in error for error in errors)


def test_corrupt_shader_cannot_pass_compatibility_build_guard(tmp_path):
    frontend_bundle(tmp_path)
    (tmp_path / "web/dist/shaders/vertshadersrc.vert").write_bytes(b"corrupted")
    assert any("hash mismatch" in error for error in validate(tmp_path))


def test_known_detected_build_is_rejected_before_reading_inventory(tmp_path):
    revoked = tmp_path / "CuteMaple-Live2D-Directory-20260908-190759"
    assert blocked_build(revoked)
    assert "withdrawn" in validate(revoked, packaged=True)[0]


def test_offscreen_smoke_cannot_substitute_for_normal_desktop_evidence():
    errors = validate_desktop_evidence({"passed": True, "executableSha256": "abc",
                                       "offscreen": True, "durationSeconds": 1.6}, "abc")
    assert any("3600 seconds" in error for error in errors)
    assert any("Default-protection" in error for error in errors)


def synthetic_desktop_evidence(executable_hash="abc", assets=None):
    """Synthetic unit-test data only; never represents real hardware/release QA."""
    return {"syntheticTestFixture": True, "passed": True, "executableSha256": executable_hash,
              "profileIsolated": True, "nativeExceptionObserved": False,
              "runtimeActive": True, "nativeProvidersActive": True, "offscreen": False,
              "nativeProvidersObserved": True, "ordinaryPetWindow": True, "defaultGraphicsBackend": True,
              "completed": True, "audioChildExited": True, "candidateSmokeOnly": False, "scenario": "full",
              "qtPlatform": "windows", "exitCode": 0, "errors": [], "graphicsOverrides": {},
              "assetSha256": {key: value for key, value in (assets or {}).items()
                               if key.startswith("assets/live2d/Maple/") or key in DESKTOP_WEB_HASHES},
              "durationSeconds": 3600, "launchEvidence": {"successfulLaunches": 20,
                 "runs": [{"passed": True, "assetMatch": True, "executableSha256": executable_hash,
                           "exitCode": 0, "durationSeconds": 20, "report": f"launch-{i:02}/desktop-report.json"}
                          for i in range(20)]},
              "checks": {name: True for name in (
                  "repeatedLaunch", "cleanExit", "idleGaze", "keyboardReaction", "audioReaction",
                  "pauseResume", "dragDrop", "displayChange")},
              "manualOrExternalChecksPending": ["lockUnlock", "sleepResume", "audioDeviceChange",
                                                 "manualAutostart", "manualCleanupAuthorization"],
              "keyboardEvents": 1, "audioActiveEvents": 1, "gazeAlignedSamples": 2,
              "lastGazeObservation": {"cursor": [140, 300], "expected": [.2, .1], "actual": [.2, .1]},
              "screenEvents": [{"kind": "geometry", "repositionValidated": True}],
              "physicalDrags": [{"status": "passed", "moveEvents": 2, "pointerTravel": 50, "windowTravel": 50,
                                 "finalSettlement": {"contact": "ground"},
                                 "nativeActivity": [{"kind": "geometry", "at": at} for at in (1, 1.1, 1.2)]}],
              "securityObservation": {"protectionUnchanged": True, "status": "no-detections-observed",
                                      "checkedAt": "2026-09-08T21:05:00+08:00",
                                      "observationStart": "2026-09-08T20:00:00+08:00",
                                      "observationEnd": "2026-09-08T21:04:00+08:00", "detections": [],
                                      **{phase: {field: True for field in ("AntivirusEnabled", "RealTimeProtectionEnabled",
                                                                         "BehaviorMonitorEnabled", "OnAccessProtectionEnabled")}
                                         for phase in ("protectionBefore", "protectionAfter")}}}


def test_desktop_evidence_binds_hash_and_default_protection_observation():
    report = synthetic_desktop_evidence()
    assert validate_desktop_evidence(report, "abc") == []
    assert validate_desktop_evidence(report, "different")
    report["nativeExceptionObserved"] = True
    assert any('native exceptions' in error for error in validate_desktop_evidence(report, "abc"))
    report['nativeExceptionLogs'] = [{'sha256': 'recorded-log-hash', 'exceptionCodes': ['0x8001010d']}]
    report['nativeExceptionReview'] = {'status': 'reviewed-with-limitations',
        'evidence': ['report-continues-and-exits-zero.json', 'wer-review.json'],
        'limitations': ['Underlying native call remains unidentified.'],
        'executableSha256': 'abc', 'faultLogSha256': ['recorded-log-hash'],
        'continuedNativeReady': True, 'cleanExitVerified': True, 'werCrashMatches': 0}
    assert validate_desktop_evidence(report, "abc") == []
    report['nativeExceptionReview']['faultLogSha256'] = ['another-log']
    assert any('bind' in error for error in validate_desktop_evidence(report, "abc"))
    report['nativeExceptionReview']['faultLogSha256'] = ['recorded-log-hash']
    report['nativeExceptionLogs'][0]['exceptionCodes'] = ['0xc0000005']
    assert any('New or unclassified' in error for error in validate_desktop_evidence(report, "abc"))
    report['nativeExceptionLogs'][0]['exceptionCodes'] = ['0x8001010d']
    report["securityObservation"]["status"] = "detected"
    assert validate_desktop_evidence(report, "abc")


def test_cleanup_helper_does_not_inherit_qt_package(tmp_path):
    frontend_bundle(tmp_path)
    helper = tmp_path / "cleaner"
    helper.mkdir()
    (helper / "CuteMaple-Cleaner.exe").write_bytes(b"fixture")
    (helper / "Qt6Core.dll").write_bytes(b"fixture")
    assert any("Cleanup helper must not bundle Qt" in error
               for error in validate(tmp_path, packaged=True))


def test_finalizer_rejects_candidate_marked_for_recompile_before_binary_reads(tmp_path):
    from tools.finalize_release import finalize
    import pytest

    (tmp_path / 'BUILD-STATUS.json').write_text(json.dumps(
        {'compatibility': False, 'mode': 'Directory', 'requiresRecompile': True}), encoding='utf-8')
    with pytest.raises(ValueError, match='recompilation'):
        finalize(tmp_path, tmp_path / 'missing-bundle', tmp_path / 'missing-smoke', 1)


@pytest.fixture
def synthetic_release(tmp_path, request):
    """No real executable, model, artwork or evidence: never run or distribute these fixtures."""
    from tools.finalize_release import digest
    from tools.qa_model_inputs import verify_native_uv_evidence, audit_atlas_files
    from PIL import Image

    package = tmp_path / 'SYNTHETIC-TEST-ONLY'
    bundle = package / 'CuteMaple-Live2D'
    frontend_bundle(bundle)
    stage = tmp_path / 'compiler-stage'
    stage.mkdir()
    (stage/'main.py').write_text('# Synthetic unit-test fixture, not application source\n')
    (package/'BUILD-STATUS.json').write_text(json.dumps({'compatibility': False, 'mode': 'Directory',
                                                        'buildStage': str(stage)}))
    for name in ('CuteMaple-Live2D.exe', 'cleaner/CuteMaple-Cleaner.exe', 'QtWebEngineProcess.exe',
                 'Qt6WebEngineCore.dll', 'Qt6WebEngineWidgets.dll', 'icudtl.dat', 'qtwebengine_resources.pak',
                 'qtwebengine_resources_100p.pak', 'qtwebengine_resources_200p.pak', 'v8_context_snapshot.bin',
                 'en-US.pak', 'qwindows.dll'):
        path = bundle/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'SYNTHETIC TEST FIXTURE - NOT AN EXECUTABLE')
    model = bundle/'assets/live2d/Maple'
    (model/'motions').mkdir(parents=True)
    states = (*ANIMATIONS, *V4_TRANSITIONS)
    for state in states:
        (model/f'motions/{state}.motion3.json').write_text(json.dumps(
            {'Meta': {'Duration': 1}, 'Curves': [{'Target': 'Parameter', 'Id': 'SyntheticParameter'}]}))
    (model/'Maple.moc3').write_bytes(b'MOC3 SYNTHETIC TEST FIXTURE - NOT A NATIVE MODEL')
    texture = Image.new('RGBA', (2, 2), (80, 90, 100, 255))
    texture.putpixel((0, 0), (0, 0, 0, 0))
    texture.save(model/'texture.png')
    (model/'Maple.pet.json').write_text('{}')
    (model/'Maple.model3.json').write_text(json.dumps({'Version': 3,
        'FileReferences': {'Moc': 'Maple.moc3', 'Textures': ['texture.png'],
                           'Motions': {state: [{'File': f'motions/{state}.motion3.json'}] for state in states}},
        'CuteMaple': {'Metadata': 'Maple.pet.json'}}))
    assets = resource_snapshot(model, bundle/'web/dist')
    evidence = tmp_path/'synthetic-native-inputs'
    evidence.mkdir()
    source_cmo, packed_cmo, source_texture = [evidence/name for name in ('source.cmo3', 'packed.cmo3', 'atlas.png')]
    source_cmo.write_bytes(b'SYNTHETIC SOURCE PROJECT')
    packed_cmo.write_bytes(b'SYNTHETIC PACKED PROJECT')
    if getattr(request, 'param', False):
        texture.putpixel((0, 0), (255, 80, 100, 0))  # Invisible RGB differs; coordinates/alpha/opaque pixels do not.
    texture.save(source_texture)
    atlas = {'source': str(source_cmo), 'sourceSha256': digest(source_cmo),
             'output': str(packed_cmo), 'outputSha256': digest(packed_cmo), 'atlasSize': [2, 2], 'atlasCount': 1,
             'atlases': [{'path': str(source_texture), 'sha256': digest(source_texture)}]}
    atlas_path = evidence/'atlas-audit.json'
    atlas_path.write_text(json.dumps(atlas))
    moc = model/'Maple.moc3'
    core = bundle/'web/dist/live2dcubismcore.min.js'
    native_ids = [f'synthetic-mesh-{index}' for index in range(134)]
    core_inputs = {str(path): digest(path) for path in (moc, core)}
    geometry = {'tool': 'official-cubism-native-geometry-audit', 'passed': True, 'errors': [],
                'sourcesUnchanged': True, 'source': str(moc), 'sourceSha256': digest(moc),
                'sourceHashesAtStart': core_inputs, 'sourceHashesAtEnd': core_inputs.copy(),
                'drawables': [{'id': name} for name in native_ids]}
    geometry_path = evidence/'geometry.json'
    geometry_path.write_text(json.dumps(geometry))
    uv_inputs = {str(path): digest(path) for path in (moc, core, source_cmo, packed_cmo, source_texture, atlas_path, geometry_path)}
    raw_uv = {'tool': 'native-uv-pairing-audit', 'schemaVersion': 1, 'passed': True, 'errors': [],
              'sourcesUnchanged': True, 'sourceHashesAtStart': uv_inputs, 'sourceHashesAtEnd': uv_inputs.copy(),
              'nativeMoc': str(moc), 'meshCount': 134, 'matchedMeshCount': 134, 'pixelTolerance': .001,
              'maximumAtlasPixelError': 0, 'meshes': [{'id': name, 'passed': True, 'errors': [],
                  'maximumAtlasPixelError': 0, 'sameTriangleTopologyIgnoringOrder': True} for name in native_ids]}
    uv_path = evidence/'native-uv.json'
    uv_path.write_text(json.dumps(raw_uv))
    uv = verify_native_uv_evidence(uv_path, moc, atlas_path, digest(moc))
    mapping = audit_atlas_files(model/'texture.png', source_texture, digest(model/'texture.png'), digest(source_texture))
    native = {'syntheticTestFixture': True, 'ready': True, 'passed': True, 'refinement': True, 'errors': [],
              'reportKind': 'cutemaple-native-model-qa', 'actualNative': True, 'modelRevision': 'v4',
              'modelRevisionEvidence': {'metadataVersion': 4,
                  **{field: ['ParamHeadTurn', 'ParamBodyTurn', 'ParamHandLShape', 'ParamHandRShape',
                             'ParamTransitionProgress', 'ParamArmPoseMode', 'ParamPoseSwing', 'ParamSwing', 'ParamSwingVisible']
                     for field in ('requiredParameters', 'nativeParameters')}, 'nativeStates': list(states)},
              'finished': {state: True for state in states}, 'captures': {state: {} for state in states},
              'probes': {'synthetic': {'pixelsChanged': True}}, 'atlasPixelsMatch': mapping['rawPixelsIdentical'],
              'atlasCoordinateMappingVerified': True, 'atlasCoordinateMappingAudits': [dict(mapping, page=0)],
              'atlasAudit': atlas, 'atlasAuditPath': str(atlas_path), 'nativeUvAudit': uv,
              'resourceChangesDuringRun': [], 'modelSha256': assets['assets/live2d/Maple/Maple.model3.json'],
              **{field: assets.copy() for field in ('resourceHashesAtStart', 'resourceHashesLoaded', 'resourceHashesAtEnd')}}
    native_path = tmp_path/'synthetic-native-report.json'
    native_path.write_text(json.dumps(native))
    source = {'syntheticTestFixture': True, 'modelVersion': 'v4', 'actualNative': True,
              'technicalResults': {'passed': True, 'errors': []}, 'fullReportSha256': digest(native_path),
              'files': {name: {'sha256': value} for name, value in assets.items()},
              'visualReview': {'status': 'accepted', 'evidence': ['SYNTHETIC TEST VISUAL REVIEW - NOT REAL QA'],
                               'fullReportSha256': digest(native_path)}, 'limitations': ['Synthetic unit-test fixture.']}
    source_path = tmp_path/'synthetic-source-qa.json'
    source_path.write_text(json.dumps(source))
    desktop_path = tmp_path/'synthetic-desktop.json'
    desktop = synthetic_desktop_evidence(digest(bundle/'CuteMaple-Live2D.exe'), assets)
    desktop_path.write_text(json.dumps(desktop))
    smoke = tmp_path/'smoke'
    smoke.mkdir()
    summary = {'helperSmokeExit': 0, 'helperDiagnosticPassed': True,
               'helperSha256': digest(bundle/'cleaner/CuteMaple-Cleaner.exe'), 'live2dPassed': True,
               'applicationSmokeExit': 0, 'runtimeSmokeExit': 0, 'sha256': digest(bundle/'CuteMaple-Live2D.exe'),
               'states': list(states), 'nativeIdleCycles': 1, 'nativeLandingFinished': 1, 'visiblePixels': 200, 'errors': []}
    (smoke/'smoke-summary.json').write_text(json.dumps(summary))
    (smoke/'helper-runtime.json').write_text(json.dumps({'qtImported': False, 'privilegedOperationPerformed': False}))
    (smoke/'packaged-runtime.json').write_text(json.dumps({'passed': True, 'errors': [],
        'modelSha256': assets['assets/live2d/Maple/Maple.model3.json']}))
    (smoke/'packaged-runtime.png').write_bytes(b'SYNTHETIC TEST CAPTURE')
    return {'package': package, 'bundle': bundle, 'smoke': smoke, 'tests': 1, 'desktop_report': desktop_path,
            'source_qa_path': source_path, 'source_runtime_path': native_path}


def test_explicit_complete_synthetic_evidence_passes_and_hashes_final_metadata(synthetic_release):
    from tools.finalize_release import finalize, digest

    verification = finalize(**synthetic_release)
    assert verification['passed'] and verification['sourceModelQa']['modelVersion'] == 'v4'
    assert verification['sourceModelQa']['visualReview']['status'] == 'accepted'
    package = synthetic_release['package']
    assert digest(package/'RELEASE-VERIFICATION.json').upper() in (package/'SHA256.txt').read_text()


def test_finalizer_archives_candidate_and_updates_status_only_after_all_gates(synthetic_release):
    from tools.finalize_release import finalize, digest
    package = synthetic_release['package']
    previous = (package/'BUILD-STATUS.json').read_bytes()
    (package/'CANDIDATE-NOT-FINAL.txt').write_text('Synthetic original candidate')
    finalize(**synthetic_release)
    assert (package/'CANDIDATE-ARCHIVE/BUILD-STATUS.json').read_bytes() == previous
    assert (package/'CANDIDATE-ARCHIVE/CANDIDATE-NOT-FINAL.txt').read_text() == 'Synthetic original candidate'
    assert not (package/'CANDIDATE-NOT-FINAL.txt').exists()
    status = json.loads((package/'BUILD-STATUS.json').read_text())
    assert status['releaseStatus'] == 'VERIFIED-WITH-LIMITATIONS'
    assert status['normalDesktopRunVerified'] is True
    assert 'no-detections-observed' in status['securityReviewStatus']
    for line in (package/'SHA256.txt').read_text().splitlines():
        expected, relative = line.split('  ', 1)
        assert digest(package/relative).upper() == expected


def test_failed_finalization_does_not_change_package_metadata(synthetic_release):
    from tools.finalize_release import finalize
    package = synthetic_release['package']
    (package/'CANDIDATE-NOT-FINAL.txt').write_text('Still a candidate')
    before = {p.relative_to(package): p.read_bytes() for p in package.rglob('*') if p.is_file()}
    desktop_path = synthetic_release['desktop_report']
    report = json.loads(desktop_path.read_text())
    report['checks']['keyboardReaction'] = False
    desktop_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='Normal desktop'):
        finalize(**synthetic_release)
    assert {p.relative_to(package): p.read_bytes() for p in package.rglob('*') if p.is_file()} == before


def test_finalizer_reopens_combination_manifest_instead_of_using_passed_flag(synthetic_release):
    from tools.finalize_release import finalize
    path = synthetic_release['desktop_report']
    path.write_text(json.dumps({'schemaVersion': 1, 'reportKind': 'cutemaple-combined-desktop-evidence',
                                'passed': True, 'derivedReport': {'passed': True}}))
    before = (synthetic_release['package']/'BUILD-STATUS.json').read_bytes()
    with pytest.raises(ValueError, match='different compiled program/resources'):
        finalize(**synthetic_release)
    assert (synthetic_release['package']/'BUILD-STATUS.json').read_bytes() == before


@pytest.mark.parametrize('kind', ['recording-success', 'failed-native', 'old-v3', 'diagnostic-only',
                                 'known-invalid-neck', 'unreviewed-visual', 'wrong-report-hash',
                                 'changed-motion', 'incomplete-source-hashes', 'claimed-version-without-native-identity'])
def test_finalizer_rejects_diagnostic_old_or_unbound_source_evidence(synthetic_release, kind):
    from tools.finalize_release import finalize, read, digest

    source_path, native_path = synthetic_release['source_qa_path'], synthetic_release['source_runtime_path']
    source, native = read(source_path), read(native_path)
    if kind == 'recording-success':
        native = {'passed': True, 'actualNative': True, 'frames': 195, 'errors': [],
                  'modelVersion': 'v4', 'validationScope': 'Native model motion appearance.'}
    elif kind == 'failed-native':
        native.update(passed=False, errors=['Native neck has invalid UV'])
    elif kind == 'old-v3':
        source['modelVersion'] = 'v3'
    elif kind == 'diagnostic-only':
        native['diagnosticOnly'] = True
    elif kind == 'known-invalid-neck':
        source['knownInvalidNeckUV'] = True
    elif kind == 'unreviewed-visual':
        source['visualReview'] = {'status': 'pending-human-review'}
    elif kind == 'wrong-report-hash':
        source['fullReportSha256'] = 'hash-of-another-report'
    elif kind == 'changed-motion':
        path = synthetic_release['bundle']/'assets/live2d/Maple/motions/idle.motion3.json'
        motion = read(path)
        motion['Meta']['Duration'] = 2
        path.write_text(json.dumps(motion))
    elif kind == 'incomplete-source-hashes':
        del source['files']['assets/live2d/Maple/motions/idle.motion3.json']
    elif kind == 'claimed-version-without-native-identity':
        native['modelRevisionEvidence']['nativeParameters'] = []
    native_path.write_text(json.dumps(native))
    # Keep report bindings valid except in the explicit mismatch case, so the
    # rejection exercises the semantic defect rather than incidental changed JSON.
    if kind != 'wrong-report-hash':
        source['fullReportSha256'] = digest(native_path)
    source['visualReview']['fullReportSha256'] = digest(native_path)
    source_path.write_text(json.dumps(source))
    expected = {'recording-success': 'recording success is insufficient',
                'failed-native': 'passing verify_maple_model', 'old-v3': 'actual native v4 model',
                'diagnostic-only': 'Diagnostic-only', 'known-invalid-neck': 'known-invalid',
                'unreviewed-visual': 'accepted visual review', 'wrong-report-hash': 'exact full native report',
                'changed-motion': 'complete packaged model/frontend inventory',
                'incomplete-source-hashes': 'every packaged model/frontend file',
                'claimed-version-without-native-identity': 'actual metadata, loaded parameters and 23 states'}[kind]
    with pytest.raises(ValueError, match=expected):
        finalize(**synthetic_release)
    assert not (synthetic_release['package']/'RELEASE-VERIFICATION.json').exists()


@pytest.mark.parametrize('field,value', [('keyboardEvents', 0), ('audioActiveEvents', 0),
    ('gazeAlignedSamples', 0), ('screenEvents', []), ('physicalDrags', []), ('durationSeconds', 3599),
    ('candidateSmokeOnly', True), ('manualOrExternalChecksPending', ['physicalDragDrop'])])
def test_true_check_booleans_do_not_replace_physical_or_full_duration_evidence(field, value):
    report = synthetic_desktop_evidence()
    report[field] = value
    assert validate_desktop_evidence(report, 'abc')


def test_twenty_copies_of_one_launch_do_not_satisfy_repeated_start_requirement():
    report = synthetic_desktop_evidence()
    report['launchEvidence']['runs'] = [deepcopy(report['launchEvidence']['runs'][0]) for _ in range(20)]
    assert any('20 successful launches' in error for error in validate_desktop_evidence(report, 'abc'))


def test_finalizer_binds_desktop_evidence_to_actual_model_and_frontend(synthetic_release):
    from tools.finalize_release import finalize, read

    path = synthetic_release['desktop_report']
    report = read(path)
    report['assetSha256']['assets/live2d/Maple/motions/idle.motion3.json'] = 'hash-from-old-v3-run'
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='different or incomplete model/frontend assets'):
        finalize(**synthetic_release)


def test_finalizer_has_no_implicit_v3_source_qa_default(synthetic_release):
    from tools.finalize_release import finalize

    synthetic_release.pop('source_qa_path')
    synthetic_release.pop('source_runtime_path')
    with pytest.raises(ValueError, match='no v3 defaults'):
        finalize(**synthetic_release)


def write_bound_synthetic_native(case, native):
    """Keep the outer fixture bindings valid when exercising a specific inner audit defect."""
    from tools.finalize_release import digest, read
    path = case['source_runtime_path']
    path.write_text(json.dumps(native))
    source = read(case['source_qa_path'])
    source['fullReportSha256'] = source['visualReview']['fullReportSha256'] = digest(path)
    case['source_qa_path'].write_text(json.dumps(source))


@pytest.mark.parametrize('synthetic_release', [True], indirect=True)
def test_coordinate_verified_transparent_rgb_difference_is_not_claimed_as_exact_pixels(synthetic_release):
    from tools.finalize_release import finalize
    result = finalize(**synthetic_release)
    assert result['sourceModelQa']['atlasPixelsMatch'] is False
    assert result['sourceModelQa']['atlasCoordinateMappingVerified'] is True
    assert result['sourceModelQa']['nativeUvAudit']['matchedMeshCount'] == 134
    assert (synthetic_release['package']/'NATIVE-UV-QA.json').is_file()


@pytest.mark.parametrize('damage', ['missing', 'partial', 'wrong-moc', 'changed-inputs', 'loose-tolerance'])
def test_passing_motion_qa_requires_full_bound_native_uv_evidence(synthetic_release, damage):
    from tools.finalize_release import finalize, read
    native = read(synthetic_release['source_runtime_path'])
    uv = native['nativeUvAudit']
    if damage == 'missing':
        del native['nativeUvAudit']
    elif damage == 'partial':
        uv['matchedMeshCount'] = 130
    elif damage == 'wrong-moc':
        uv['mocSha256'] = 'unrelated-moc-hash'
    elif damage == 'changed-inputs':
        uv['inputHashesAtEnd'] = {}
    elif damage == 'loose-tolerance':
        uv['pixelTolerance'] = 100
    write_bound_synthetic_native(synthetic_release, native)
    with pytest.raises(ValueError, match='native UV|Native UV'):
        finalize(**synthetic_release)


def test_raw_uv_topology_is_rechecked_even_when_claimed_summary_passes(synthetic_release):
    from tools.finalize_release import finalize, read, digest
    native = read(synthetic_release['source_runtime_path'])
    claimed = native['nativeUvAudit']
    path = Path(claimed['path'])
    raw = read(path)
    raw['meshes'][0]['sameTriangleTopologyIgnoringOrder'] = False
    path.write_text(json.dumps(raw))
    claimed['sha256'] = digest(path)
    claimed['inputHashesAtStart'][str(path)] = claimed['inputHashesAtEnd'][str(path)] = digest(path)
    write_bound_synthetic_native(synthetic_release, native)
    with pytest.raises(ValueError, match='Unverified native UV mesh'):
        finalize(**synthetic_release)


def test_real_previous_four_neck_uv_failure_cannot_hide_behind_passing_motion_qa(synthetic_release):
    from tools.finalize_release import finalize, read, digest
    # Exact archived failed audit from the real 2026-09-09 09:29 Editor export.
    # No referenced MOC/EXE is read or run: the raw audit is rejected before input access.
    path = Path(__file__).parent/'fixtures/native_uv_failed_necks.json'
    failed = read(path)
    assert failed['meshCount'] == 134 and failed['matchedMeshCount'] == 130 and failed['passed'] is False
    assert {row['id'] for row in failed['meshes'] if not row['passed']} == {
        'profile_l_neck', 'profile_r_neck', 'mid_l_neck', 'mid_r_neck'}
    native = read(synthetic_release['source_runtime_path'])
    claim = native['nativeUvAudit']
    claim['path'], claim['sha256'] = str(path), digest(path)
    claim['inputHashesAtStart'][str(path)] = claim['inputHashesAtEnd'][str(path)] = digest(path)
    write_bound_synthetic_native(synthetic_release, native)
    with pytest.raises(ValueError, match='Native UV audit is failed'):
        finalize(**synthetic_release)


def test_atlas_page_measurements_are_recomputed_instead_of_trusting_flags(synthetic_release):
    from tools.finalize_release import finalize, read
    native = read(synthetic_release['source_runtime_path'])
    # The actual source/package images still match; inconsistent claimed page data must fail.
    native['atlasCoordinateMappingAudits'][0]['counts']['opaqueRGBChangedPixels'] = 1
    write_bound_synthetic_native(synthetic_release, native)
    with pytest.raises(ValueError, match='coordinate audit differs'):
        finalize(**synthetic_release)


def test_exact_pixel_report_remains_supported_with_complete_raw_uv_audit(synthetic_release):
    from tools.finalize_release import finalize, read
    native = read(synthetic_release['source_runtime_path'])
    assert native['atlasPixelsMatch'] is True
    del native['atlasCoordinateMappingVerified']
    del native['atlasCoordinateMappingAudits']
    write_bound_synthetic_native(synthetic_release, native)
    result = finalize(**synthetic_release)
    assert result['sourceModelQa']['atlasPixelsMatch'] is True
    assert result['sourceModelQa']['nativeUvAudit']['verified'] is True
