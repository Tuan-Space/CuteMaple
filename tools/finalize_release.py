"""Attach verified source-model and actual packaged-executable QA to a release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

try:
    from .validate_release import validate, blocked_build, validate_desktop_evidence, validate_model_evidence
    from .qa_resource_snapshot import resource_snapshot
    from .qa_model_inputs import verify_native_uv_evidence, audit_atlas_files
    from .combine_desktop_evidence import KIND as COMBINED_DESKTOP_KIND, resolve_combination, bundle_snapshot
    from .validate_cleanup_evidence import validate_cleanup_evidence
    from .finalize_metadata import commit_metadata
except ImportError:
    from validate_release import validate, blocked_build, validate_desktop_evidence, validate_model_evidence
    from qa_resource_snapshot import resource_snapshot
    from qa_model_inputs import verify_native_uv_evidence, audit_atlas_files
    from combine_desktop_evidence import KIND as COMBINED_DESKTOP_KIND, resolve_combination, bundle_snapshot
    from validate_cleanup_evidence import validate_cleanup_evidence
    from finalize_metadata import commit_metadata

ROOT = Path(__file__).resolve().parents[1]
from tools.native_model_contract import declared_native_drawables, REFINED_MOTION_REVISION, physics_resource_contract
from tools.refined_motion_evidence import verify_refined_motion_evidence


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def limitation_items(value):
    """Preserve a review paragraph as one limitation, rather than split characters."""
    return [value] if isinstance(value, str) else list(value or [])


def recheck_model_inputs(native, bundle):
    """Reopen the exact raw UV audit and atlases, not just its claimed pass summary."""
    claimed = native["nativeUvAudit"]
    folder = bundle / "assets/live2d/Maple"
    references = read(folder / "Maple.model3.json")["FileReferences"]
    try:
        refinement = read(folder / 'Maple.pet.json').get('refinement', {})
        expected_drawables = declared_native_drawables(refinement) if native.get('modelRevision') == 'v5' else None
        expected_mesh_count = len(expected_drawables) if expected_drawables else (141 if native.get('modelRevision') == 'v5' else 134)
        verified = verify_native_uv_evidence(Path(claimed["path"]), folder / references["Moc"],
                                            Path(claimed["atlasAuditPath"]), digest(folder / references["Moc"]),
                                            expected_mesh_count=expected_mesh_count,
                                            expected_drawable_ids=expected_drawables)
        if verified != claimed:
            raise ValueError("Raw native UV audit differs from its native QA summary")
        atlas = read(Path(verified["atlasAuditPath"]))
        if native.get("atlasAudit") != atlas:
            raise ValueError("Native QA embeds a different packed atlas reference")
        textures, sources = references["Textures"], atlas["atlases"]
        if len(textures) != len(sources):
            raise ValueError("Packaged texture pages differ from the verified atlas inventory")
        pages = native.get("atlasCoordinateMappingAudits", [])
        if native.get("atlasCoordinateMappingVerified") is True and len(pages) != len(textures):
            raise ValueError("Incomplete per-page atlas coordinate evidence")
        for index, (texture, source) in enumerate(zip(textures, sources)):
            actual = folder / texture
            measured = audit_atlas_files(actual, Path(source["path"]), digest(actual), source["sha256"])
            if native.get("atlasPixelsMatch") is True and measured["rawPixelsIdentical"] is not True:
                raise ValueError("Claimed exact atlas pixel match does not hold for the packaged texture")
            if native.get("atlasCoordinateMappingVerified") is True:
                page = pages[index]
                if (page.get("page") != index or measured["coordinateMappingVerified"] is not True or
                        any(page.get(key) != value for key, value in measured.items() if key != "actualPath")):
                    raise ValueError("Atlas coordinate audit differs from its actual packaged/source pixels")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"Full native UV/atlas evidence recheck failed: {error}") from error
    return verified


def finalize(package, bundle, smoke, tests, desktop_report=None, source_qa_path=None, source_runtime_path=None,
             cleanup_evidence_path=None, refined_motion_report=None):
    package, bundle, smoke = package.resolve(), bundle.resolve(), smoke.resolve()
    if any(blocked_build(path) for path in (package, bundle, smoke)):
        raise ValueError("Detected builds are withdrawn and must not be finalized or read")
    manifest = read(package / "BUILD-STATUS.json")
    if manifest["compatibility"]:
        raise ValueError("Compatibility builds cannot be finalized as a Live2D release")
    if manifest["mode"] != "Directory":
        raise ValueError("Only the installed directory release is supported")
    if manifest.get("requiresRecompile"):
        raise ValueError("This candidate is explicitly marked for recompilation before release")
    if bundle != package / "CuteMaple-Live2D":
        raise ValueError("The verified resource bundle must be adjacent to the actual packaged executable")
    if source_qa_path is None or source_runtime_path is None:
        raise ValueError("Explicit v4/v5 source QA and full native refinement report paths are required; no v3 defaults")
    if desktop_report is None:
        raise ValueError("Normal desktop and default-protection evidence is required; offscreen smoke is insufficient")
    errors = validate(bundle, packaged=True)
    if errors:
        raise ValueError("Packaged inventory failed: " + "; ".join(errors))
    assets = resource_snapshot(bundle / "assets/live2d/Maple", bundle / "web/dist")
    source_qa = read(source_qa_path)
    full_qa = read(source_runtime_path)
    metadata = read(bundle / 'assets/live2d/Maple/Maple.pet.json')
    refinement = metadata.get('refinement', {})
    version = refinement.get('version', 4)
    model_errors = validate_model_evidence(source_qa, full_qa, assets, digest(source_runtime_path), version, refinement,
                                           metadata.get('states', {}))
    if refinement.get('motionPolishVersion') == 1:
        model_root = bundle / 'assets/live2d/Maple'
        physics = physics_resource_contract(model_root, read(model_root / 'Maple.model3.json'), refinement['nativeParameterIds'])
        if full_qa.get('physicsOutputs') != physics:
            model_errors.append('Native physics output declaration differs from the actual packaged physics file')
    if version == 5 and refinement.get('requiredParameters') != full_qa.get('modelRevisionEvidence', {}).get('requiredParameters'):
        model_errors.append('v5 native identity must preserve the exact packaged metadata parameter contract')
    if version == 5:
        metadata = read(bundle / 'assets/live2d/Maple/Maple.pet.json')
        if full_qa.get('v5Contract') != refinement or full_qa.get('locomotion') != metadata.get('locomotion'):
            model_errors.append('v5 measured support/sleep/wind contracts differ from actual packaged metadata')
        references = read(bundle / 'assets/live2d/Maple/Maple.model3.json')['FileReferences']['Motions']
        for name, entries in references.items():
            motion_path = bundle / 'assets/live2d/Maple' / entries[0]['File']
            motion = read(motion_path)
            try:
                endpoints = {'sha256': digest(motion_path),
                    'start': {curve['Id']: curve['Segments'][1] for curve in motion['Curves']},
                    'end': {curve['Id']: curve['Segments'][-1] for curve in motion['Curves']}}
            except (KeyError, TypeError, IndexError):
                model_errors.append(f'v5 actual motion endpoint curves are invalid: {name}')
                continue
            if endpoints != full_qa.get('motionEndpoints', {}).get(name):
                model_errors.append(f'v5 measured motion baselines/endpoints differ from actual packaged curves: {name}')
    if model_errors:
        raise ValueError(f"Native v{version} release approval incomplete: " + "; ".join(model_errors))
    native_uv = recheck_model_inputs(full_qa, bundle)
    refined_evidence = None
    if refinement.get('motionRevision') == REFINED_MOTION_REVISION or refined_motion_report is not None:
        refined_evidence = verify_refined_motion_evidence(bundle, refined_motion_report, native_uv=native_uv)
    executable = package / "CuteMaple-Live2D/CuteMaple-Live2D.exe"
    summary = read(smoke / "smoke-summary.json")
    runtime = read(smoke / "packaged-runtime.json")
    helper = bundle / "cleaner/CuteMaple-Cleaner.exe"
    helper_qa = read(smoke / "helper-runtime.json")
    if (summary.get("helperSmokeExit") != 0 or summary.get("helperDiagnosticPassed") is not True or
            summary.get("helperSha256", "").lower() != digest(helper) or
            helper_qa.get("qtImported") is not False or helper_qa.get("privilegedOperationPerformed") is not False):
        raise ValueError("The independent helper has not passed a matching non-privileged diagnostic")
    if (not summary["live2dPassed"] or not runtime["passed"] or runtime["errors"] or
            summary["applicationSmokeExit"] != 0 or summary["runtimeSmokeExit"] != 0 or
            summary["sha256"].lower() != digest(executable)):
        raise ValueError("The actual executable has not passed matching packaged smoke checks")
    desktop_input = read(desktop_report)
    combined = desktop_input.get("reportKind") == COMBINED_DESKTOP_KIND
    if version == 5 and not combined:
        raise ValueError('v5 requires re-opened original 20-launch/full/supplement desktop evidence, not standalone booleans')
    whole_bundle = bundle_snapshot(bundle) if version == 5 else None
    desktop = (resolve_combination(desktop_input, digest(executable), assets, model_version=version,
                                   package_hashes=whole_bundle) if combined else desktop_input)
    desktop_errors = validate_desktop_evidence(desktop, summary["sha256"], assets)
    if desktop_errors:
        raise ValueError("Normal desktop verification incomplete: " + "; ".join(desktop_errors))
    cleanup_evidence = None
    if version == 5:
        if cleanup_evidence_path is None:
            raise ValueError('v5 requires three actual successful cleanup operations of the final bundled helper')
        cleanup_evidence = validate_cleanup_evidence(bundle, cleanup_evidence_path)
        if cleanup_evidence.get('passed') is not True:
            raise ValueError('v5 cleanup evidence incomplete: '+'; '.join(cleanup_evidence.get('errors', [])))
    verified_hashes = assets
    if runtime["modelSha256"] != verified_hashes["assets/live2d/Maple/Maple.model3.json"]:
        raise ValueError("Runtime diagnostic loaded another model settings file")
    # These sources are already identified as the exact compiler staging input.
    stage = Path(manifest["buildStage"])
    runtime_sources = {path.name: digest(path) for path in stage.glob("*.py")}
    verification = {
        "schemaVersion": 3, "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "product": "CuteMaple-Live2D", "mode": manifest["mode"], "passed": True,
        "executable": str(executable.relative_to(package)), "executableBytes": executable.stat().st_size,
        "executableSha256": digest(executable), "pythonTestsPassed": tests,
        "sourceModelQa": {
            "actualNative": source_qa["actualNative"], "modelVersion": source_qa["modelVersion"],
            "matchedPackagedAssetSha256": verified_hashes, "technicalResults": source_qa["technicalResults"],
            "fullReportSha256": digest(source_runtime_path),
            "visualReview": source_qa["visualReview"],
            "nativeUvAudit": native_uv,
            "atlasPixelsMatch": full_qa.get("atlasPixelsMatch") is True,
            "atlasCoordinateMappingVerified": full_qa.get("atlasCoordinateMappingVerified") is True,
        },
        "packagedExecutableQa": {
            "offscreenStartupExit": summary["applicationSmokeExit"],
            "runtimeCheckExit": summary["runtimeSmokeExit"], "states": summary["states"],
            "nativeIdleCycles": summary["nativeIdleCycles"], "nativeLandingFinished": summary["nativeLandingFinished"],
            "visiblePixels": summary["visiblePixels"], "errors": summary["errors"],
            "isolatedAppData": True, "startupAndCleanupTaskCreationDisabled": True, "sensorsDisabled": True,
            "runtimeReportSha256": digest(smoke / "packaged-runtime.json"),
        },
        "normalDesktopQa": desktop,
        "normalDesktopQaReportSha256": digest(desktop_report),
        "cleanupHelperQa": {"sha256": digest(helper), "noQtDiagnosticPassed": True,
                            "administratorActionsExercised": version == 5,
                            **({'actualCleanupEvidence': cleanup_evidence} if cleanup_evidence else {})},
        "securityConclusion": "No detections observed only during the documented interval; not a malware-free guarantee",
        "packagedQtWebEngineAndAssetInventoryPassed": True,
        "compiledRuntimeSourceSha256": runtime_sources,
        "limitations": limitation_items(source_qa.get("limitations")) + limitation_items(desktop.get("nativeExceptionReview", {}).get("limitations")) + ["The application is unsigned.",
            ("Startup registration is not covered by the cleanup and desktop observations." if version == 5 else
             "Administrator cleanup and startup registration are not covered by non-mutating desktop validation.")]
    }
    if refined_evidence is not None:
        verification['sourceModelQa']['refinedMotionQa'] = refined_evidence
    # Prepare every byte only after all release gates pass. No package write
    # occurs until the final metadata transaction, which restores originals on
    # an I/O failure. Original candidate labels remain available in the archive.
    encode = lambda value: (json.dumps(value, ensure_ascii=False, indent=2)+"\n").encode("utf8")
    files = {"SOURCE-MODEL-QA.json": source_qa_path.read_bytes(),
             "NATIVE-UV-QA.json": Path(native_uv["path"]).read_bytes(),
             "NORMAL-DESKTOP-QA.json": desktop_report.read_bytes(),
             "PACKAGED-RUNTIME-QA.json": (smoke / "packaged-runtime.json").read_bytes(),
             "PACKAGED-RUNTIME.png": (smoke / "packaged-runtime.png").read_bytes()}
    if refined_evidence is not None:
        data = Path(refined_evidence['reportPath']).read_bytes()
        if hashlib.sha256(data).hexdigest() != refined_evidence['reportSha256']:
            raise ValueError('Refined motion report changed before metadata preparation')
        files['REFINED-MOTION-QA.json'] = data
    if combined:
        originals = []
        for index, (original, checksum) in enumerate(sorted(desktop_input["inputSha256"].items())):
            data = Path(original).read_bytes()
            if hashlib.sha256(data).hexdigest() != checksum:
                raise ValueError("Combined original evidence changed before metadata preparation")
            target = f"DESKTOP-EVIDENCE/inputs/{index:04}-{Path(original).name}"
            files[target] = data
            originals.append({"originalPath": original, "sha256": checksum, "packagedPath": target})
        verification["desktopEvidenceComposition"] = {"reportKind": COMBINED_DESKTOP_KIND,
            "manifestSha256": digest(desktop_report), "originalFiles": originals,
            "baseAndSupplementIntervals": desktop["evidenceComposition"]}
    if cleanup_evidence:
        originals = []
        for index, (original, checksum) in enumerate(sorted(cleanup_evidence['inputSha256'].items())):
            data = Path(original).read_bytes()
            if hashlib.sha256(data).hexdigest() != checksum:
                raise ValueError('Cleanup original evidence changed before metadata preparation')
            target = None
            if Path(original).suffix.lower() != '.exe':
                target = f'CLEANUP-EVIDENCE/inputs/{index:04}-{Path(original).name}'
                files[target] = data
            originals.append({'originalPath': original, 'sha256': checksum, 'packagedPath': target})
        verification['cleanupEvidenceOriginals'] = originals
    archived = {}
    for name in ("BUILD-STATUS.json", "CANDIDATE-NOT-FINAL.txt", "SHA256.txt"):
        source = package / name
        target = "CANDIDATE-ARCHIVE/" + name
        if (package / target).exists():
            raise ValueError("Original candidate archive already exists; do not overwrite prior release evidence")
        if source.is_file():
            data = source.read_bytes()
            files[target] = data
            archived[name] = {"path": target, "sha256": hashlib.sha256(data).hexdigest()}
    verification["candidateStateArchive"] = archived
    status = dict(manifest)
    status.update(releaseStatus="VERIFIED-WITH-LIMITATIONS", normalDesktopRunVerified=True,
                  securityReviewStatus="no-detections-observed-during-verified-intervals",
                  verifiedAt=verification["verifiedAt"], candidateLabel="",
                  modelValidation=f"Actual native v{version} model, full desktop and required physical observations verified; see recorded limitations")
    files["BUILD-STATUS.json"] = encode(status)
    files["RELEASE-VERIFICATION.json"] = encode(verification)
    instructions = ("美腻枫 Live2D 桌宠\n\n"
                    "保留整个 CuteMaple-Live2D 文件夹，双击其中 CuteMaple-Live2D.exe 启动。\n"
                    "默认开启鼠标视线跟随，关闭自主走动；右键菜单可独立调整互动选项。\n"
                    "本包已核验同一程序的20次启动、至少60分钟普通桌面运行及必需的真实交互。\n"
                    "如使用独立补测，各段原始报告、观察区间和SHA均分别保存，时长没有相加。\n"
                    "RELEASE-VERIFICATION.json记录精确程序、模型和验收证据。\n"
                    "原候选状态保存在CANDIDATE-ARCHIVE，仅供追溯。\n\n"
                    "已记录的限制：\n" + "".join("- " + str(item) + "\n" for item in verification["limitations"]) +
                    "程序未签名；防护结论只覆盖记录的观察区间，不代表永久无检测保证。\n")
    files["READ-ME.txt"] = instructions.encode("utf8")
    remove = {"CANDIDATE-NOT-FINAL.txt"} if (package / "CANDIDATE-NOT-FINAL.txt").is_file() else set()
    hashes = {path.relative_to(package).as_posix(): digest(path) for path in package.rglob("*")
              if path.is_file() and path.name != "SHA256.txt" and path.relative_to(package).as_posix() not in remove}
    hashes.update({name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    files["SHA256.txt"] = ("\n".join(f"{value.upper()}  {name}" for name, value in sorted(hashes.items()))+"\n").encode("utf8")
    if digest(executable) != verification["executableSha256"] or resource_snapshot(bundle / "assets/live2d/Maple", bundle / "web/dist") != assets:
        raise ValueError("Executable/resources changed before final metadata commit")
    if version == 5 and bundle_snapshot(bundle) != whole_bundle:
        raise ValueError('Complete v5 bundle changed before final metadata commit')
    if cleanup_evidence and any(digest(path) != checksum for path, checksum in cleanup_evidence['inputSha256'].items()):
        raise ValueError('Cleanup evidence changed before final metadata commit')
    if refined_evidence and any(digest(path) != checksum for path, checksum in refined_evidence['inputSha256'].items()):
        raise ValueError('Refined motion evidence changed before final metadata commit')
    commit_metadata(package, files, remove)
    return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--tests", type=int, required=True)
    parser.add_argument("--desktop-report", type=Path, required=True)
    parser.add_argument("--source-qa", type=Path, required=True)
    parser.add_argument("--source-runtime-qa", type=Path, required=True)
    parser.add_argument("--cleanup-evidence", type=Path, help="v5 required: observe_cleanup three-run directory/report JSON")
    parser.add_argument("--refined-motion-report", type=Path,
                        help="Required for the refined motion revision: actual verify_refined_motion report JSON")
    args = parser.parse_args()
    result = finalize(args.package, args.bundle, args.smoke, args.tests,
                      args.desktop_report, args.source_qa, args.source_runtime_qa, args.cleanup_evidence,
                      args.refined_motion_report)
    print(json.dumps({k: result[k] for k in ("mode", "passed", "executableBytes", "executableSha256")}, indent=2))
