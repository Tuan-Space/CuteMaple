"""Explicit synthetic evidence fixtures. No application or input is executed."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from tools.combine_desktop_evidence import reconstruct, resolve_combination, DESKTOP_WEB_HASHES


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf8")


@pytest.fixture
def evidence(tmp_path):
    """Synthetic raw reports include their own PID, time, logs and observations."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    exe = bundle / "CuteMaple-Live2D.exe"
    exe.write_bytes(b"SYNTHETIC TEST FILE, NEVER EXECUTE")
    identity = {"path": str(exe), "sha256": sha(exe)}
    names = [f"assets/live2d/Maple/{n}" for n in ("Maple.model3.json", "Maple.moc3", "Maple.pet.json")]
    names += list(DESKTOP_WEB_HASHES) + [f"web/dist/test-{n}.glsl" for n in range(47)]
    assets = {name: hashlib.sha256(name.encode()).hexdigest() for name in names}
    origin = datetime(2026, 9, 9, tzinfo=timezone.utc)
    iso = lambda seconds: (origin + timedelta(seconds=seconds)).isoformat()
    base_path = tmp_path / "base/NORMAL-DESKTOP-QA.json"
    supplement_path = tmp_path / "supplement/SUPPLEMENT-EVIDENCE.json"

    def security(start, end):
        return {"status": "no-detections-observed", "protectionUnchanged": True, "detections": [],
                "observationStart": iso(start), "observationEnd": iso(end), "checkedAt": iso(end+1),
                **{phase: {k: True for k in ("AntivirusEnabled", "RealTimeProtectionEnabled", "BehaviorMonitorEnabled", "OnAccessProtectionEnabled")}
                   for phase in ("protectionBefore", "protectionAfter")}}

    def make_run(folder, pid, start, duration, scenario, manual=False):
        at = iso(start+10)
        drag = {"status": "passed", "moveEvents": 2, "pointerTravel": 40, "windowTravel": 40,
                "pressedAt": 100, "releasedAt": 101, "settledAt": 102,
                "finalSettlement": {"contact": "ground", "window": [1, 2, 100, 200]},
                "nativeActivity": [{"at": n, "kind": "geometry"} for n in (101.2, 101.4, 101.6)]}
        observations = [{"at": iso(start+1), "event": "native_ready"}]
        if manual:
            observations += [{"at": at, "event": "keyboard_activity"}, {"at": at, "event": "audio_activity"},
                             {"at": at, "event": "physical_drag_validated", "evidence": drag}]
        checks = {k: manual for k in ("idleGaze", "keyboardReaction", "audioReaction", "dragDrop", "displayChange")}
        checks.update(cleanExit=True, pauseResume=scenario == "full", repeatedLaunch=False)
        report = {"syntheticTestFixture": True, "pid": pid, "executable": str(exe), "executableSha256": identity["sha256"],
                  "startedAt": iso(start), "finishedAt": iso(start+duration+2), "durationSeconds": duration,
                  "requestedDurationSeconds": duration, "scenario": scenario, "exitCode": 0, "profile": str(folder / "profile"),
                  "assetSha256": {name: assets[name] for name in names[:3]},
                  **{k: True for k in ("passed", "completed", "runtimeActive", "nativeProvidersActive", "nativeProvidersObserved",
                     "ordinaryPetWindow", "defaultGraphicsBackend", "audioChildExited")},
                  "offscreen": False, "qtPlatform": "windows", "graphicsOverrides": {}, "errors": [], "nativeCycles": 8,
                  "nativeFinished": 1, "checks": checks, "ownedChildPids": [pid+1000],
                  "observations": observations, "keyboardEvents": int(manual), "audioActiveEvents": int(manual),
                  "gazeAlignedSamples": 2 if manual else 0,
                  "lastGazeObservation": {"cursor": [10, 20], "expected": [.2, .3], "actual": [.2, .3]} if manual else {},
                  "screenEvents": [{"at": at, "kind": "geometry", "repositionValidated": True, "window": [1, 2, 100, 200]}] if manual else [],
                  "physicalDrags": [drag] if manual else [],
                  "manualOrExternalChecksPending": ["lockUnlock"] if manual else ["idleGazeMovement", "keyboardReaction", "audioReaction", "physicalDragDrop", "displayChange", "lockUnlock"],
                  "securityObservation": {"status": "pending-external-read-only-event-review"}}
        write(folder / "desktop-report.json", report)
        write(folder / "process-summary.json", {"pid": pid, "exitCode": 0, "reportProduced": True, "ownedOrphansTerminated": 0})
        logs = folder / "profile/logs"
        logs.mkdir(parents=True, exist_ok=True)
        events = [{"time": iso(start+1), "pid": pid, "event": e} for e in ("renderer_ready", "desktop_qa_native_ready")]
        if manual:
            events += [{"time": at, "pid": pid, "event": "desktop_qa_"+e} for e in ("keyboard_activity", "audio_activity")]
        events += [{"time": iso(start+duration+1), "pid": pid, "event": "audio_child_exit", "code": 0}]
        (logs / f"app-{pid}.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf8")
        (logs / f"app-{pid}.fault.log").write_bytes(b"")
        return report

    launches = []
    for n in range(20):
        name = f"launch-{n+1:02}"
        make_run(base_path.parent/name, n+1, n*30, 20, "startup")
        launches.append({"passed": True, "assetMatch": True, "executableSha256": identity["sha256"],
                         "exitCode": 0, "durationSeconds": 20, "report": f"{name}/desktop-report.json"})
    full = make_run(base_path.parent/"soak", 101, 700, 3600, "full")
    base = deepcopy(full)
    base.update(passed=False, technicalPassed=True, profileIsolated=True, candidateSmokeOnly=False,
                nativeExceptionObserved=False, nativeExceptionLogs=[], nativeExceptionReview={"status": "not-observed"},
                launchEvidence={"successfulLaunches": 20, "runs": launches},
                assetSha256={k: v for k, v in assets.items() if k.startswith("assets/live2d/Maple/") or k in DESKTOP_WEB_HASHES},
                securityObservation=security(-1, 4310))
    base["checks"]["repeatedLaunch"] = True
    write(base_path, base)
    supplement = make_run(supplement_path.parent, 201, 5000, 300, "startup", manual=True)

    def capture():
        raw = json.loads((supplement_path.parent/"desktop-report.json").read_text())
        files = {p.relative_to(supplement_path.parent).as_posix(): sha(p) for p in supplement_path.parent.rglob("*")
                 if p.is_file() and p != supplement_path}
        record = {"schemaVersion": 1, "reportKind": "cutemaple-desktop-supplement",
                  "status": "RECORDED-NOT-RELEASE", "errors": [],
                  "baseReport": {"path": str(base_path), "sha256": sha(base_path)},
                  "executable": {"path": str(exe), "sha256Before": identity["sha256"], "sha256After": identity["sha256"]},
                  "assetSha256Before": assets, "assetSha256After": assets, "reportRoot": str(supplement_path.parent),
                  "profileIsolated": True, "files": files,
                  "execution": {"pid": 201, "startedAt": iso(4999), "finishedAt": iso(5303), "exitCode": 0,
                                "requestedDurationSeconds": 300, "scenario": "startup", "ownedOrphansTerminated": 0},
                  "securityObservation": security(4998, 5304), "nativeExceptionObserved": False, "nativeExceptionLogs": [],
                  "applicationEvents": {"observationStart": iso(4998), "observationEnd": iso(5304), "checkedAt": iso(5305),
                                        "scopeExecutablePath": str(exe), "processIds": [201, 1201], "matchingEvents": []}}
        write(supplement_path, record)
        return record

    capture()
    def complete_base():
        original = json.loads(base_path.read_text())
        updated = make_run(base_path.parent/"soak", 101, 700, 3600, "full", manual=True)
        for key, value in updated.items():
            if key not in {"passed", "checks", "securityObservation", "assetSha256"}:
                original[key] = value
        original["checks"] = dict(updated["checks"], repeatedLaunch=True)
        write(base_path, original)
    return {"base": base_path, "supplement": supplement_path, "exe": identity, "assets": assets, "capture": capture,
            "complete_base": complete_base, "make": lambda: reconstruct(base_path, [supplement_path], None, identity, assets)}


def test_combination_reopens_raw_observations_and_preserves_independent_duration(evidence):
    original = evidence["base"].read_bytes()
    result = evidence["make"]()
    assert result["derivedReport"]["passed"] is True
    assert result["derivedReport"]["allRequiredChecksPassed"] is True
    assert result["derivedReport"]["releaseStatus"] == "DESKTOP-CHECKS-PASSED"
    assert result["derivedReport"]["durationSeconds"] == 3600
    assert len(result["derivedReport"]["launchEvidence"]["runs"]) == 20
    assert all(row["role"] == "supplement" for row in result["derivedReport"]["evidenceComposition"]["externalObservations"].values())
    assert evidence["base"].read_bytes() == original
    assert resolve_combination(result, evidence["exe"]["sha256"], evidence["assets"]) == result["derivedReport"]


def test_complete_original_base_does_not_need_an_unnecessary_supplement(evidence):
    evidence["complete_base"]()
    result = reconstruct(evidence["base"], [], None, evidence["exe"], evidence["assets"])
    assert result["supplements"] == []
    assert all(row["role"] == "base-full" for row in result["derivedReport"]["evidenceComposition"]["externalObservations"].values())
    assert resolve_combination(result, evidence["exe"]["sha256"], evidence["assets"])['durationSeconds'] == 3600


def test_missing_actual_checks_are_still_rejected_without_supplements(evidence):
    with pytest.raises(ValueError, match="Actual external observation"):
        reconstruct(evidence["base"], [], None, evidence["exe"], evidence["assets"])


def test_powershell_timezone_reserialization_preserves_actual_instants(evidence):
    evidence["complete_base"]()
    def localize(d):
        convert = lambda value: datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).isoformat()
        for key in ("startedAt", "finishedAt"):
            d[key] = convert(d[key])
        for rows in (d["observations"], d["screenEvents"]):
            for row in rows:
                row["at"] = convert(row["at"])
    mutate(evidence["base"], localize)
    original = evidence["base"].read_bytes()
    result = reconstruct(evidence["base"], [], None, evidence["exe"], evidence["assets"])
    assert result["derivedReport"]["passed"]
    assert evidence["base"].read_bytes() == original


@pytest.mark.parametrize("change", [
    lambda d: d.update(startedAt=(datetime.fromisoformat(d["startedAt"])+timedelta(seconds=1)).isoformat()),
    lambda d: d["observations"][0].update(at=(datetime.fromisoformat(d["observations"][0]["at"])+timedelta(microseconds=1)).isoformat()),
    lambda d: d["observations"][0].update(event="changed-observation"),
    lambda d: d["observations"][0].update(at=d["observations"][0]["at"].replace("+00:00", ".0000001+00:00")),
])
def test_real_time_or_observation_rewrite_remains_rejected(evidence, change):
    mutate(evidence["base"], change)
    evidence["capture"]()
    with pytest.raises(ValueError, match="rewrote observations"):
        evidence["make"]()


def test_timezone_equivalence_does_not_normalize_unrelated_payload_strings(evidence):
    raw = evidence["base"].parent/"soak/desktop-report.json"
    for path in (raw, evidence["base"]):
        mutate(path, lambda d: d["observations"][0].update(payload={"note": "2026-09-09T02:25:54.677310+00:00"}))
    mutate(evidence["base"], lambda d: d["observations"][0]["payload"].update(note="2026-09-09T10:25:54.67731+08:00"))
    evidence["capture"]()
    with pytest.raises(ValueError, match="rewrote observations"):
        evidence["make"]()


def mutate(path, function):
    data = json.loads(path.read_text()); function(data); write(path, data)


@pytest.mark.parametrize("seconds", [20, 3599])
def test_supplement_time_cannot_make_short_base_reach_one_hour(evidence, seconds):
    for path in (evidence["base"], evidence["base"].parent/"soak/desktop-report.json"):
        mutate(path, lambda d: d.update(durationSeconds=seconds))
    evidence["capture"]()
    with pytest.raises(ValueError, match="duration"):
        evidence["make"]()


def test_nineteen_launches_cannot_be_repaired_by_supplement(evidence):
    mutate(evidence["base"], lambda d: d["launchEvidence"].update(successfulLaunches=19, runs=d["launchEvidence"]["runs"][:19]))
    evidence["capture"]()
    with pytest.raises(ValueError, match="twenty"):
        evidence["make"]()


@pytest.mark.parametrize("field,value", [("pid", 999), ("executableSha256", "different"), ("graphicsOverrides", {"QT_OPENGL": "software"}),
                                        ("audioChildExited", False), ("errors", ["renderer failed"])])
def test_supplement_cannot_hide_pid_binary_provider_or_runtime_errors(evidence, field, value):
    mutate(evidence["supplement"].parent/"desktop-report.json", lambda d: d.update({field: value}))
    evidence["capture"]()
    with pytest.raises(ValueError):
        evidence["make"]()


def test_matching_three_model_entries_does_not_allow_changed_shader(evidence):
    mutate(evidence["supplement"], lambda d: d["assetSha256After"].update({"web/dist/test-0.glsl": "changed"}))
    with pytest.raises(ValueError, match="every model/frontend"):
        evidence["make"]()


@pytest.mark.parametrize("kind", ["keyboardReaction", "dragDrop", "displayChange"])
def test_copied_boolean_without_original_physical_observation_is_rejected(evidence, kind):
    def change(d):
        if kind == "keyboardReaction": d["observations"] = [r for r in d["observations"] if r["event"] != "keyboard_activity"]
        if kind == "dragDrop": d["observations"] = [r for r in d["observations"] if r["event"] != "physical_drag_validated"]
        if kind == "displayChange": d["screenEvents"][0]["at"] = "2026-09-01T00:00:00Z"
    mutate(evidence["supplement"].parent/"desktop-report.json", change)
    evidence["capture"]()
    with pytest.raises(ValueError, match="Actual external observation"):
        evidence["make"]()


def test_supplement_cannot_rewrite_base_manual_flags(evidence):
    mutate(evidence["base"], lambda d: d["checks"].update(keyboardReaction=True))
    evidence["capture"]()
    with pytest.raises(ValueError, match="rewrote original physical"):
        evidence["make"]()


def test_overlapping_supplement_is_not_an_independent_followup(evidence):
    mutate(evidence["supplement"], lambda d: d["execution"].update(startedAt="2026-09-09T00:30:00Z"))
    with pytest.raises(ValueError, match="PID/time"):
        evidence["make"]()


@pytest.mark.parametrize("change", [lambda d: d["securityObservation"]["protectionAfter"].update(RealTimeProtectionEnabled=False),
                                   lambda d: d["securityObservation"].update(detections=[{"threat": "actual detection"}]),
                                   lambda d: d["applicationEvents"].update(processIds=[999])])
def test_each_supplement_requires_its_own_process_and_protection_observation(evidence, change):
    mutate(evidence["supplement"], change)
    with pytest.raises(ValueError):
        evidence["make"]()


def test_final_recheck_rejects_modified_raw_input_even_if_cached_pass_remains(evidence):
    result = evidence["make"]()
    mutate(evidence["supplement"].parent/"desktop-report.json", lambda d: d.update(keyboardEvents=0))
    with pytest.raises(ValueError, match="input was changed"):
        resolve_combination(result, evidence["exe"]["sha256"], evidence["assets"])


def test_final_recheck_reconstructs_instead_of_trusting_cached_bool(evidence):
    result = evidence["make"]()
    result["derivedReport"]["durationSeconds"] += 300
    with pytest.raises(ValueError, match="Cached combined"):
        resolve_combination(result, evidence["exe"]["sha256"], evidence["assets"])


def test_fault_log_cannot_be_removed_from_combined_review(evidence):
    log = evidence["supplement"].parent/"profile/logs/app-201.fault.log"
    log.write_text("Windows fatal exception: code 0x8001010d\n", encoding="utf8")
    evidence["capture"]()
    with pytest.raises(ValueError, match="fault inventory"):
        evidence["make"]()


def test_new_supplement_fault_requires_its_own_bound_review_and_original_review_inputs(evidence):
    log = evidence["supplement"].parent/"profile/logs/app-201.fault.log"
    log.write_text('Windows fatal exception: code 0x8001010d\n  File "live2d_host.py", line 70 in __init__\n')
    evidence["capture"]()
    mutate(evidence["supplement"], lambda d: d.update(nativeExceptionObserved=True, nativeExceptionLogs=[{'sha256': sha(log)}]))
    with pytest.raises(ValueError, match="separate explicit review"):
        evidence["make"]()
    events = evidence["base"].parent.parent/"synthetic-wer.json"
    write(events, {"syntheticTestFixture": True, "matchingEvents": []})
    path = events.with_name("synthetic-review.json")
    review = {"nativeExceptionReview": {"status": "reviewed-with-limitations", "executableSha256": evidence["exe"]["sha256"],
        "faultLogSha256": [sha(log)], "continuedNativeReady": True, "cleanExitVerified": True, "werCrashMatches": 0,
        "evidence": [{"path": str(events), "sha256": sha(events)}], "limitations": "Synthetic recovered native exception, not real QA."}}
    write(path, review)
    result = reconstruct(evidence["base"], [evidence["supplement"]], path, evidence["exe"], evidence["assets"])
    assert result["derivedReport"]["nativeExceptionReview"]["faultLogSha256"] == [sha(log)]
    write(events, {"matchingEvents": ["changed"]})
    with pytest.raises(ValueError, match="input was changed"):
        resolve_combination(result, evidence["exe"]["sha256"], evidence["assets"])
