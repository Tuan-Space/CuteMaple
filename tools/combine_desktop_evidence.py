"""Bind a complete desktop soak to later, genuinely observed interaction checks.

This tool never runs the pet or changes a source report. The one-hour duration,
twenty launches, lifecycle checks and all errors remain owned by the base run.
Only five external-interaction observations can come from an isolated supplement.
The finalizer reopens every input and reconstructs the result rather than trusting
the cached derived report in the combination manifest.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

try:
    from .validate_release import blocked_build, validate_desktop_evidence, DESKTOP_WEB_HASHES
    from .qa_resource_snapshot import resource_snapshot
except ImportError:
    from validate_release import blocked_build, validate_desktop_evidence, DESKTOP_WEB_HASHES
    from qa_resource_snapshot import resource_snapshot

KIND = "cutemaple-combined-desktop-evidence"
MANUAL = {"idleGaze": "idleGazeMovement", "keyboardReaction": "keyboardReaction",
          "audioReaction": "audioReaction", "dragDrop": "physicalDragDrop", "displayChange": "displayChange"}
MODEL_ENTRIES = {f"assets/live2d/Maple/{name}" for name in
                 ("Maple.model3.json", "Maple.moc3", "Maple.pet.json")}
PROTECTION = ("AntivirusEnabled", "RealTimeProtectionEnabled", "BehaviorMonitorEnabled", "OnAccessProtectionEnabled")
REPORT_TIMESTAMPS = {("startedAt",), ("finishedAt",), ("observations", "*", "at"), ("screenEvents", "*", "at")}
REPORT_TIMESTAMPS |= {("providerStatusHistory", "*", "at"), ("reactionObservations", "*", "at")}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Evidence timestamps must include a timezone")
    return result


def same_original_value(aggregate, original, path):
    """PowerShell may reserialize known DateTime fields in its local timezone.

    Match those explicit schema locations by instant, including fractional-second
    formatting. Never normalize arbitrary strings, nested payloads, monotonic drag
    times, observation values, order or membership.
    """
    if path in REPORT_TIMESTAMPS:
        try:
            def exact_instant(value):
                moment = stamp(value)
                match = re.search(r"T\d{2}:\d{2}:\d{2}(?:\.(\d+))?(?:Z|[+-]\d{2}:\d{2})$", value)
                require(match is not None, "Unknown timestamp representation")
                # DateTime's optional seventh digit must not be silently lost
                # by Python datetime's six-digit microsecond precision.
                fraction = (match.group(1) or "").rstrip("0")
                return moment.replace(microsecond=0).astimezone(timezone.utc), fraction
            return isinstance(aggregate, str) and isinstance(original, str) and exact_instant(aggregate) == exact_instant(original)
        except (ValueError, TypeError, AttributeError):
            return False
    if isinstance(original, dict):
        return (isinstance(aggregate, dict) and aggregate.keys() == original.keys() and
                all(same_original_value(aggregate[key], value, path + (key,)) for key, value in original.items()))
    if isinstance(original, list):
        return (isinstance(aggregate, list) and len(aggregate) == len(original) and
                all(same_original_value(a, b, path + ("*",)) for a, b in zip(aggregate, original)))
    return aggregate == original


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def child(folder, relative):
    path = (folder / relative).resolve()
    require(not Path(relative).is_absolute() and path.is_relative_to(folder.resolve()) and path != folder.resolve(),
            "Evidence reference escapes its report directory")
    return path


class Reader:
    def __init__(self):
        self.hashes = {}

    def raw(self, path):
        path = Path(path).resolve()
        require(not blocked_build(path), "Withdrawn build evidence cannot be reopened")
        data = path.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        require(str(path) not in self.hashes or self.hashes[str(path)] == checksum, "Input changed while reading evidence")
        self.hashes[str(path)] = checksum
        return data

    def json(self, path):
        return json.loads(self.raw(path).decode("utf-8-sig"))

    def reference(self, path):
        return {"path": str(Path(path).resolve()), "sha256": self.hashes[str(Path(path).resolve())]}


def security_ok(security, start, end):
    require(security.get("status") == "no-detections-observed" and security.get("protectionUnchanged") is True
            and security.get("detections") == [], "Evidence lacks a clean default-protection observation")
    require(all(security.get(phase, {}).get(key) is True for phase in ("protectionBefore", "protectionAfter")
                for key in PROTECTION), "Default protection must stay enabled in each separate session")
    first, last, checked = (stamp(security.get(key, "")) for key in ("observationStart", "observationEnd", "checkedAt"))
    require(first <= start <= end <= last <= checked, "Security interval does not cover its actual process interval")


def read_run(reader, report_path, exe, assets, minimum, scenario):
    path = Path(report_path).resolve()
    report, process = reader.json(path), reader.json(path.parent / "process-summary.json")
    start, end = stamp(report["startedAt"]), stamp(report["finishedAt"])
    require(start < end and isinstance(report.get("durationSeconds"), (int, float))
            and math.isfinite(report["durationSeconds"]) and minimum <= report["durationSeconds"] <= (end-start).total_seconds(),
            "Run duration is insufficient or contradicts its original interval")
    require(report.get("scenario") == scenario and report.get("executableSha256") == exe["sha256"]
            and Path(report["executable"]).resolve() == Path(exe["path"]).resolve(), "Run belongs to another executable or scenario")
    require(report.get("exitCode") == process.get("exitCode") == 0 and process.get("reportProduced") is True
            and report.get("pid") == process.get("pid") and process.get("ownedOrphansTerminated") == 0,
            "Run lacks a matching clean process exit/PID or has orphaned children")
    require(isinstance(report["pid"], int) and report["pid"] > 0, "Invalid process identity")
    require(all(report.get(key) is True for key in ("passed", "completed", "runtimeActive", "nativeProvidersActive",
                "nativeProvidersObserved", "ordinaryPetWindow", "defaultGraphicsBackend", "audioChildExited"))
            and report.get("offscreen") is False and report.get("qtPlatform") == "windows"
            and report.get("graphicsOverrides") == {} and report.get("errors") == [],
            "A supplement cannot repair an incomplete, overridden, failed, or non-native run")
    require(report.get("checks", {}).get("cleanExit") is True and report.get("nativeCycles", 0) > 0,
            "Run did not demonstrate clean exit and subsequent native activity")
    require(Path(report["profile"]).resolve() == (path.parent / "profile").resolve(), "Run profile is not independently isolated")
    recorded = report.get("assetSha256", {})
    require(set(recorded) == MODEL_ENTRIES and all(assets.get(k) == v for k, v in recorded.items()),
            "Run's actually loaded model hashes do not match the complete resource baseline")
    logs = path.parent / "profile/logs"
    app_path = logs / f"app-{report['pid']}.jsonl"
    events = [json.loads(line) for line in reader.raw(app_path).decode("utf8").splitlines() if line.strip()]
    require(all(event.get("pid") == report["pid"] for event in events), "Diagnostic log PID does not match the run")
    for kind in ("renderer_ready", "desktop_qa_native_ready", "audio_child_exit"):
        require(any(event.get("event") == kind and start <= stamp(event["time"]) <= end and
                    (kind != "audio_child_exit" or event.get("code") == 0) for event in events),
                f"Missing in-interval diagnostic evidence: {kind}")
    require(not any(event.get("event") in ("unhandled_python_exception", "unhandled_thread_exception") for event in events),
            "Unhandled Python failure in original diagnostic log")
    faults = []
    require((logs / f"app-{report['pid']}.fault.log").is_file(), "Missing main native fault log")
    for fault in sorted(logs.glob("*.fault.log")):
        data = reader.raw(fault)
        if data:
            codes = re.findall(r"Windows fatal exception: code (0x[0-9a-fA-F]+)", data.decode("utf8", errors="replace"))
            require(codes and set(codes) == {"0x8001010d"}, "New/unclassified native exception cannot be supplemented away")
            faults.append({"path": str(fault.resolve()), "sha256": digest(fault), "bytes": len(data),
                           "exceptionCodes": sorted(set(codes))})
    return {"path": path, "report": report, "process": process, "start": start, "end": end,
            "events": events, "faults": faults}


def actual_manual(run, name):
    report = run["report"]
    if report.get("checks", {}).get(name) is not True or MANUAL[name] in report.get("manualOrExternalChecksPending", []):
        return False
    observations = [row for row in report.get("observations", []) if "at" in row and
                    run["start"] <= stamp(row["at"]) <= run["end"]]
    if name in ("keyboardReaction", "audioReaction"):
        key, kind = (("keyboardEvents", "keyboard_activity") if name == "keyboardReaction" else
                     ("audioActiveEvents", "audio_activity"))
        return report.get(key, 0) >= 1 and any(row.get("event") == kind for row in observations) and any(
            e.get("event") == "desktop_qa_" + kind and run["start"] <= stamp(e["time"]) <= run["end"] for e in run["events"])
    if name == "idleGaze":
        gaze = report.get("lastGazeObservation", {})
        return report.get("gazeAlignedSamples", 0) >= 2 and all(
            isinstance(gaze.get(key), list) and len(gaze[key]) == 2 and
            all(isinstance(v, (int, float)) and math.isfinite(v) for v in gaze[key]) for key in ("cursor", "expected", "actual"))
    if name == "displayChange":
        return any(row.get("repositionValidated") is True and row.get("kind") in
                   {"added", "removed", "primary", "geometry", "availableGeometry"}
                   and run["start"] <= stamp(row["at"]) <= run["end"] and len(row.get("window", [])) == 4
                   for row in report.get("screenEvents", []))
    for row in report.get("physicalDrags", []):
        activity = row.get("nativeActivity", [])
        if (row.get("status") == "passed" and row.get("moveEvents", 0) > 0 and row.get("pointerTravel", 0) >= 6
                and row.get("windowTravel", 0) >= 6 and row.get("finalSettlement") and len(activity) >= 3
                and activity[-1].get("at", 0)-activity[0].get("at", 0) >= .1
                and 0 < row.get("releasedAt", 0)-row.get("pressedAt", 0)
                and 0 <= row.get("settledAt", 0)-row.get("releasedAt", 0) <= 30
                and any(o.get("event") == "physical_drag_validated" and o.get("evidence") == row for o in observations)):
            return True
    return False


def bundle_snapshot(bundle):
    bundle = Path(bundle).resolve()
    require(not blocked_build(bundle), "Withdrawn bundle cannot be reopened")
    result = {}
    for path in sorted(bundle.rglob('*')):
        require(not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction()),
                "A release bundle cannot traverse linked files/directories")
        if path.is_file():
            result[path.relative_to(bundle).as_posix()] = digest(path)
    require(result, "Missing complete release bundle inventory")
    return result


def reconstruct(base_path, supplement_paths, native_review_path, exe, assets, *,
                model_version=4, interaction_review_path=None, package_hashes=None):
    reader = Reader()
    base_path = Path(base_path).resolve()
    base = reader.json(base_path)
    require(base.get("technicalPassed") is True and base.get("candidateSmokeOnly") is False and
            base.get("errors") == [] and base.get("profileIsolated") is True, "Base full desktop run did not finish technically")
    full = read_run(reader, base_path.parent / "soak/desktop-report.json", exe, assets, 3600, "full")
    require(base.get("executableSha256") == exe["sha256"], "Base aggregate executable differs")
    if model_version == 5:
        require(package_hashes and base.get('packageUnchanged') is True and base.get('packageSha256') == package_hashes,
                'v5 base must bind the complete unchanged bundle, including cleaner and dependencies')
    wrapper_fields = {"passed", "checks", "securityObservation", "assetSha256"}
    require(all(same_original_value(base.get(key), value, (key,)) for key, value in full["report"].items() if key not in wrapper_fields),
            "Base aggregate rewrote observations or duration from its original full report")
    require(all(base.get("checks", {}).get(k) == v for k, v in full["report"]["checks"].items() if k != "repeatedLaunch"),
            "Base aggregate rewrote original physical check values")
    expected_subset = {k: v for k, v in assets.items() if k.startswith("assets/live2d/Maple/") or k in DESKTOP_WEB_HASHES}
    require(base.get("assetSha256") == expected_subset, "Base aggregate has different/incomplete model/frontend assets")
    security_ok(base["securityObservation"], full["start"], full["end"])
    launches = base.get("launchEvidence", {})
    require(launches.get("successfulLaunches", 0) >= 20 and len(launches.get("runs", [])) >= 20,
            "The original base must independently include at least twenty launches")
    paths, runs, previous_end = set(), [], None
    for claimed in launches["runs"]:
        path = child(base_path.parent, claimed["report"])
        require(path not in paths and path.parent.name.startswith("launch-"), "Duplicate/non-launch raw evidence")
        paths.add(path)
        run = read_run(reader, path, exe, assets, 20, "startup")
        require(claimed.get("passed") is True and claimed.get("assetMatch") is True and claimed.get("exitCode") == 0
                and claimed.get("durationSeconds") == run["report"]["durationSeconds"]
                and claimed.get("executableSha256") == exe["sha256"], "Launch aggregate differs from its raw process report")
        require(run["end"] <= full["start"] and (previous_end is None or previous_end <= run["start"]),
                "Launch intervals overlap or occur after the base soak")
        security_ok(base["securityObservation"], run["start"], run["end"])
        previous_end = run["end"]
        runs.append(run)
    faults = [f for run in runs + [full] for f in run["faults"]]
    claimed_faults = base.get("nativeExceptionLogs", [])
    require(base.get("nativeExceptionObserved") is bool(faults) and
            sorted(row.get("sha256") for row in claimed_faults) == sorted(f["sha256"] for f in faults),
            "Base aggregate native-fault inventory differs from actual launch/full logs")
    require(full["report"]["checks"].get("pauseResume") is True, "Supplement cannot replace base pause/resume lifecycle checks")
    supplements = []
    previous_end = stamp(base["securityObservation"]["observationEnd"])
    require(0 <= len(supplement_paths) <= 5, "Use at most five bounded supplementary sessions")
    for evidence_path in supplement_paths:
        evidence_path = Path(evidence_path).resolve()
        evidence = reader.json(evidence_path)
        require(evidence.get("reportKind") == "cutemaple-desktop-supplement" and evidence.get("schemaVersion") == 1
                and evidence.get("status") == "RECORDED-NOT-RELEASE" and evidence.get("errors") == [],
                "Only a dedicated raw supplement capture is accepted")
        require(evidence.get("baseReport") == reader.reference(base_path), "Supplement belongs to another original base report")
        require(Path(evidence["reportRoot"]).resolve() == evidence_path.parent, "Supplement root differs from its actual location")
        binding = evidence.get("executable", {})
        require(Path(binding.get("path", "")).resolve() == Path(exe["path"]).resolve() and
                binding.get("sha256Before") == binding.get("sha256After") == exe["sha256"], "Supplement executable changed/differs")
        require(evidence.get("assetSha256Before") == evidence.get("assetSha256After") == assets,
                "Supplement must bind every model/frontend resource before and after")
        if model_version == 5:
            require(evidence.get('packageUnchanged') is True and evidence.get('packageSha256Before') ==
                    evidence.get('packageSha256After') == package_hashes,
                    'v5 supplement must use the exact full base bundle before and after its session')
        files = evidence.get("files", {})
        require(files and all(isinstance(v, str) for v in files.values()), "Supplement input manifest missing")
        for relative, checksum in files.items():
            raw = reader.raw(child(evidence_path.parent, relative))
            require(hashlib.sha256(raw).hexdigest() == checksum, "Supplement raw file hash changed")
        run = read_run(reader, evidence_path.parent / "desktop-report.json", exe, assets, 20, "startup")
        needed = {str(run["path"]), str(run["path"].parent / "process-summary.json"),
                  str(run["path"].parent / "profile/logs" / f"app-{run['report']['pid']}.jsonl")}
        needed.update(f["path"] for f in run["faults"])
        needed.update(path for path in reader.hashes if Path(path).is_relative_to(evidence_path.parent) and Path(path) != evidence_path)
        require(needed.issubset({str(child(evidence_path.parent, p)) for p in files}), "Supplement leaves primary raw inputs unbound")
        execution = evidence.get("execution", {})
        first, last = stamp(execution["startedAt"]), stamp(execution["finishedAt"])
        require(previous_end <= first <= run["start"] < run["end"] <= last and
                execution.get("pid") == run["report"]["pid"] and execution.get("exitCode") == 0 and
                execution.get("ownedOrphansTerminated") == 0 and execution.get("scenario") == "startup" and
                execution.get("requestedDurationSeconds") == run["report"].get("requestedDurationSeconds") and
                20 <= execution["requestedDurationSeconds"] <= run["report"]["durationSeconds"] and
                evidence.get("profileIsolated") is True, "Supplement PID/time/exit isolation binding is invalid")
        security_ok(evidence["securityObservation"], first, last)
        application = evidence.get("applicationEvents", {})
        require(application.get("matchingEvents") == [] and
                Path(application.get("scopeExecutablePath", "")).resolve() == Path(exe["path"]).resolve() and
                run["report"]["pid"] in application.get("processIds", []) and
                set(run["report"].get("ownedChildPids", [])).issubset(application.get("processIds", [])) and
                stamp(application["observationStart"]) <= first <= last <= stamp(application["observationEnd"]) <= stamp(application["checkedAt"]),
                "Supplement Application/WER observation lacks exact process/interval binding or reports an error")
        require(evidence.get("nativeExceptionObserved") is bool(run["faults"]) and
                sorted(row.get("sha256") for row in evidence.get("nativeExceptionLogs", [])) == sorted(f["sha256"] for f in run["faults"]),
                "Supplement native fault inventory differs from the raw logs")
        previous_end = stamp(evidence["securityObservation"]["observationEnd"])
        run.update(evidencePath=evidence_path, evidence=evidence)
        supplements.append(run)
        faults.extend(run["faults"])
    derived, contributions = deepcopy(base), {}
    for name, pending in MANUAL.items():
        chosen = next((run for run in [full] + supplements if actual_manual(run, name)), None)
        require(chosen is not None, f"Actual external observation is still missing: {name}")
        report = chosen["report"]
        derived["checks"][name] = True
        if pending in derived["manualOrExternalChecksPending"]:
            derived["manualOrExternalChecksPending"].remove(pending)
        fields = {"keyboardReaction": ["keyboardEvents"], "audioReaction": ["audioActiveEvents"],
                  "idleGaze": ["lastGazeObservation", "gazeAlignedSamples"], "dragDrop": ["physicalDrags"],
                  "displayChange": ["screenEvents"]}[name]
        for key in fields:
            derived[key] = deepcopy(report[key])
        contributions[name] = {"role": "base-full" if chosen is full else "supplement", **reader.reference(chosen["path"]),
                               "pid": report["pid"], "startedAt": report["startedAt"], "finishedAt": report["finishedAt"],
                               "observedFields": {key: deepcopy(report[key]) for key in fields}}
    derived["nativeExceptionObserved"] = bool(faults)
    derived["nativeExceptionLogs"] = faults
    review_document = reader.json(native_review_path) if native_review_path else None
    if review_document is not None:
        for path, checksum in review_document.get("inputSha256", {}).items():
            require(hashlib.sha256(reader.raw(path)).hexdigest() == checksum, "Native review input changed after its actual audit")
        review = review_document.get("nativeExceptionReview", review_document)
        for item in review.get("evidence", []):
            if isinstance(item, dict) and "path" in item:
                require(hashlib.sha256(reader.raw(item["path"])).hexdigest() == item.get("sha256"),
                        "Native review evidence reference changed")
    if faults:
        require(native_review_path is not None, "Actual native exceptions require a separate explicit review of all session fault hashes")
        derived["nativeExceptionReview"] = review
    else:
        derived["nativeExceptionReview"] = {"status": "not-observed"}
    derived["passed"] = True  # Derived only after raw independent evidence above; never written into the base.
    derived["allRequiredChecksPassed"] = all(derived["checks"].get(key) is True for key in
                                             (*MANUAL, "repeatedLaunch", "cleanExit", "pauseResume"))
    derived["releaseStatus"] = "DESKTOP-CHECKS-PASSED-WITH-LIMITATIONS" if faults else "DESKTOP-CHECKS-PASSED"
    derived["reportKind"] = "cutemaple-desktop-derived-evidence"
    if model_version == 5:
        try:
            from .validate_v5_desktop import validate_sessions
        except ImportError:
            from validate_v5_desktop import validate_sessions
        derived['v5InteractionEvidence'] = validate_sessions(reader, [full] + supplements,
                                                            interaction_review_path, package_hashes)
    derived["evidenceComposition"] = {"durationAndLaunchesFrom": reader.reference(base_path), "externalObservations": contributions,
        "supplementIntervals": [{"pid": s["report"]["pid"], "startedAt": s["report"]["startedAt"],
                                 "finishedAt": s["report"]["finishedAt"], "securityObservation": s["evidence"]["securityObservation"]}
                                for s in supplements]}
    errors = validate_desktop_evidence(derived, exe["sha256"], assets)
    require(not errors, "Combined desktop checks remain incomplete: " + "; ".join(errors))
    require(all(digest(path) == checksum for path, checksum in reader.hashes.items()), "Original evidence changed during reconstruction")
    result = {"schemaVersion": 1, "reportKind": KIND, "baseReport": reader.reference(base_path),
            "supplements": [reader.reference(path) for path in supplement_paths],
            "nativeReview": reader.reference(native_review_path) if native_review_path else None,
            "executable": exe, "assetSha256": assets, "inputSha256": reader.hashes, "derivedReport": derived}
    if model_version == 5:
        result.update(modelVersion='v5', packageSha256=package_hashes,
                      interactionReview=reader.reference(interaction_review_path))
    return result


def resolve_combination(manifest, executable_sha256, assets, *, model_version=4, package_hashes=None):
    require(manifest.get("schemaVersion") == 1 and manifest.get("reportKind") == KIND, "Invalid combination manifest")
    require(manifest.get("executable", {}).get("sha256") == executable_sha256 and manifest.get("assetSha256") == assets,
            "Combination belongs to different compiled program/resources")
    if model_version == 5:
        require(manifest.get('modelVersion') == 'v5' and manifest.get('packageSha256') == package_hashes,
                'v5 requires complete bundle-bound combined evidence, not older desktop approval')
    for path, checksum in manifest.get("inputSha256", {}).items():
        require(not blocked_build(Path(path)) and digest(path) == checksum, "Combined evidence input was changed after review")
    fresh = reconstruct(manifest["baseReport"]["path"], [row["path"] for row in manifest["supplements"]],
                        manifest["nativeReview"]["path"] if manifest.get("nativeReview") else None,
                        manifest["executable"], assets, model_version=model_version, package_hashes=package_hashes,
                        interaction_review_path=manifest.get('interactionReview', {}).get('path'))
    require(fresh == manifest, "Cached combined evidence differs from reconstruction of the actual original reports")
    return fresh["derivedReport"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, action="append", default=[])
    parser.add_argument("--native-review", type=Path)
    parser.add_argument("--interaction-review", type=Path, help="v5 only: independently recorded ToDesk/device observations")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not blocked_build(args.bundle), "Withdrawn executable cannot be reopened")
    exe = (args.bundle / "CuteMaple-Live2D.exe").resolve()
    assets = resource_snapshot(args.bundle / "assets/live2d/Maple", args.bundle / "web/dist")
    metadata = json.loads((args.bundle / 'assets/live2d/Maple/Maple.pet.json').read_text(encoding='utf-8-sig'))
    version = metadata.get('refinement', {}).get('version', 4)
    require(version in (4, 5), 'Unsupported model revision')
    result = reconstruct(args.base_report, args.supplement, args.native_review, {"path": str(exe), "sha256": digest(exe)}, assets,
                         model_version=version, interaction_review_path=args.interaction_review,
                         package_hashes=bundle_snapshot(args.bundle) if version == 5 else None)
    require(not args.output.exists(), "Use a new combination output; preserve previous evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps({"reportKind": KIND, "baseDurationSeconds": result["derivedReport"]["durationSeconds"],
                      "successfulLaunches": result["derivedReport"]["launchEvidence"]["successfulLaunches"],
                      "output": str(args.output), "sha256": digest(args.output)}, indent=2))


if __name__ == "__main__":
    main()
