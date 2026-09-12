"""Read-only final-bundle validation of three recorded native cleanup operations.

This checks recorded provenance and original files; it is not remote attestation.
It never starts a helper, requests elevation, or repairs incomplete evidence.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cleanup_protocol import SCHEMA, STEPS, digest
from cleanup_process import identity_alive, valid_identity

BLOCKED_BUILD_IDS = ('20260908-190153', '20260908-190759')
SOURCE_NAMES = ('cleanup_helper.py', 'cleanup_protocol.py', 'cleanup_process.py', 'memory_cleaner.py',
                'pet_core.py', 'resource_monitor.py', 'diagnostics.py')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_path(value):
    path = Path(value)
    require(not any(part in str(path) for part in BLOCKED_BUILD_IDS), 'Known blocked build must not be inspected')
    require(path.is_absolute(), 'Evidence paths must be absolute')
    for item in (path, *path.parents):
        require(not item.is_symlink() and not (hasattr(item, 'is_junction') and item.is_junction()),
                'Evidence cannot traverse a link or junction')
    return path


def document(path):
    path = safe_path(path)
    require(path.stat().st_size <= 4_000_000, 'Unexpectedly large cleanup JSON')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    require(isinstance(value, dict), 'Cleanup evidence must be a JSON object')
    require(not any(value.get(key) for key in ('synthetic', 'mock', 'fixture', 'unitTest', 'diagnosticOnly')),
            'Synthetic, mock, fixture or diagnostic evidence cannot approve cleanup')
    return value


def finite(value, minimum=0, maximum=float('inf')):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and minimum <= value <= maximum


def identity_key(value):
    require(valid_identity(value), 'Missing PID and creation-time identity')
    return (value['pid'], value['creationFiletime'])


def identity_in_interval(value, start, end):
    identity_key(value)
    created = value['creationFiletime']/10_000_000-11644473600
    require(start-.1 <= created <= end+.1, 'Process creation time is outside its actual observation interval')


def validate_native_calls(step, calls):
    require(isinstance(calls, list) and calls, f'{step}: missing raw native API returns')
    primary = calls[0]
    require(isinstance(primary, dict), f'{step}: malformed native call')
    if step == 'system_file_cache':
        require(len(calls) == 1 and primary.get('api') == 'SetSystemFileCacheSize' and primary.get('dll') == 'kernel32'
                and isinstance(primary.get('returnValue'), int) and primary['returnValue'] != 0
                and primary.get('lastError') == 0 and primary.get('succeeded') is True,
                'File cache must have a successful actual Kernel32 BOOL return')
        return
    cls, integer = {'empty_working_sets': (80, 2), 'flush_modified_pages': (80, 3),
                    'purge_standby_list': (80, 4), 'registry_cache': (155, None), 'combine_memory': (130, None)}[step]
    status = primary.get('ntstatus')
    sizes = {80: {4}, 155: {0}, 130: {12, 24}}
    require(primary.get('api') == 'NtSetSystemInformation' and primary.get('dll') == 'ntdll'
            and primary.get('informationClass') == cls and primary.get('inputInteger') == integer
            and primary.get('inputSize') in sizes[cls]
            and isinstance(status, int) and not isinstance(status, bool) and 0 <= status <= 0xffffffff
            and primary.get('ntstatusHex', '').lower() == f'0x{status:08x}', f'{step}: wrong API/class/raw NTSTATUS')
    if status < 0x80000000:
        require(len(calls) == 1 and primary.get('succeeded') is True, f'{step}: inconsistent native success')
        return
    require(step == 'empty_working_sets' and len(calls) == 2 and primary.get('succeeded') is False,
            f'{step}: native NTSTATUS indicates failure')
    fallback = calls[1]
    rows = fallback.get('returns', [])
    require(fallback.get('api') == 'EmptyWorkingSet' and fallback.get('dll') == 'psapi'
            and fallback.get('succeeded') is True and isinstance(rows, list)
            and all(isinstance(row, dict) and isinstance(row.get('returnValue'), int)
                    and isinstance(row.get('lastError'), int) and (row['returnValue'] == 0 or row['lastError'] == 0) for row in rows)
            and fallback.get('succeededCount') == sum(row['returnValue'] != 0 for row in rows)
            and fallback.get('attempted') == len(rows)+fallback.get('accessDeniedCount', -1)
            and (fallback.get('succeededCount', 0) > 0 or fallback.get('attempted') == 0),
            'Working-set fallback lacks matching per-call Win32 results')


def _validate(bundle, evidence):
    bundle, evidence = safe_path(bundle), safe_path(evidence)
    report_path = evidence / 'report.json' if evidence.is_dir() else evidence
    report = document(report_path)
    manifest = document(report_path.parent / 'OBSERVATION-MANIFEST.json')
    require(manifest.get('schema') == 1 and manifest.get('reportKind') == 'cleanup-observation-manifest'
            and manifest.get('report') == {'path': str(report_path), 'sha256': digest(report_path)},
            'Missing or changed original observation report SHA')
    require(report.get('schema') == 1 and report.get('evidenceVersion') == 1
            and report.get('reportKind') == 'cleanup-operation-observation'
            and report.get('executionMode') == 'native-compiled-helper'
            and report.get('observerElevated') is False and report.get('privilegedCallsInObserver') is False
            and report.get('explicitSchedulingRequested') is True and report.get('passed') is True,
            'A completed ordinary-user native observation is required; unit/diagnostic results are insufficient')
    helper = bundle / 'cleaner/CuteMaple-Cleaner.exe'
    actual_hash = digest(safe_path(helper))
    require(report.get('helperSha256') == report.get('helperSha256After') == manifest.get('helperSha256') == actual_hash,
            'Observed helper does not match the final bundle before and after execution')
    observed_helper = safe_path(report['helper'])
    require(digest(observed_helper) == actual_hash, 'Original observed helper has changed')
    sources, after = report.get('sourceClientHashes', {}), report.get('sourceClientHashesAfter', {})
    snapshot = document(bundle.parent / 'SOURCE-SNAPSHOT.json')
    require(set(sources) == set(SOURCE_NAMES) and sources == after
            and all(sources[name].lower() == snapshot.get('sourceModulesSha256', {}).get(name, '').lower() for name in SOURCE_NAMES),
            'Observer source hashes changed or differ from the final compiled source snapshot')
    script = report.get('observerScript', {})
    require(script.get('sha256') == digest(safe_path(script['path'])), 'Observation runner source has changed')
    observations_path = report_path.parent / 'observations.jsonl'
    require(digest(observations_path) == report.get('observationsSha256'), 'Raw observation stream is missing or changed')
    observations = [json.loads(line) for line in observations_path.read_text(encoding='utf-8').splitlines() if line]
    start, end = report.get('startedAtUnix'), report.get('finishedAtUnix')
    require(finite(start) and finite(end, start), 'Invalid observation interval')
    observer = report.get('observer')
    identity_key(observer)
    observer_created = observer['creationFiletime']/10_000_000-11644473600
    require(0 < observer_created <= start+.1, 'Invalid observer creation timestamp')
    operations = report.get('operations')
    require(report.get('requestedRuns') == 3 and isinstance(operations, list) and len(operations) == 3,
            'Exactly three complete operations are required')
    ids = [row.get('operation_id') for row in operations]
    require(len(set(ids)) == 3, 'Three distinct operation IDs are required')
    inventory = {}
    previous_exit = start
    details = []
    for index, row in enumerate(operations):
        op, files = row['operation_id'], row.get('operationFiles', {})
        require(row.get('index') == index+1 and row.get('passed') is True and not row.get('observationEndedBeforeCompletion'),
                'An operation did not complete successfully')
        profile = safe_path(report['profile'])
        require(isinstance(op, str) and len(op) == 32 and all(c in '0123456789abcdef' for c in op), 'Invalid operation ID')
        folder = profile / 'cleanup/operations' / op
        require(files and set(files) == {str(path) for path in folder.glob('*.json') if path.is_file()},
                'Operation inventory does not include all original JSON files')
        for path, checksum in files.items():
            require(safe_path(path).parent == folder and digest(Path(path)) == checksum, 'Operation original is missing, changed or outside its folder')
        inventory.update(files)
        def original(name):
            path = folder / name
            require(str(path) in files, f'Missing original operation file: {name}')
            return document(path)
        request, begun, final = original('request.json'), original('started.json'), original('result.json')
        ack, exited = original('client-observed.json'), original('exit-observed.json')
        requested, completed = request.get('requested_at'), final.get('completed_at')
        require(finite(requested, previous_exit, end) and finite(completed, requested, end), 'Operations overlap or have invalid timestamps')
        require(finite(row.get('observedStartedAtUnix'), start, requested)
                and finite(row.get('observedFinishedAtUnix'), completed, end), 'Missing individual operation observation interval')
        require(request.get('schema') == SCHEMA and request.get('operation_id') == op and request.get('steps') == list(STEPS)
                and request.get('client') == observer and request.get('helper_sha256') == actual_hash
                and request.get('helper') == str(observed_helper) and request.get('profile') == str(profile),
                'Request is not the exact schema-4 helper/client/profile operation')
        supervisor = final.get('supervisor')
        identity_in_interval(supervisor, requested, completed)
        require(begun.get('supervisor') == supervisor and begun.get('operation_id') == op
                and ack.get('client') == observer and ack.get('supervisor') == supervisor and ack.get('operation_id') == op
                and exited.get('operation_id') == op and exited.get('supervisor') == supervisor
                and exited.get('method') == 'process-handle' and exited.get('exit_code') == 0
                and finite(exited.get('observed_at'), completed, row['observedFinishedAtUnix']), 'Missing actual supervisor acknowledgement or handle exit')
        require(final.get('schema') == SCHEMA and final.get('operation_id') == op
                and final.get('evidenceKind') == 'windows-cleanup-supervisor' and final.get('evidenceVersion') == 1
                and final.get('client_observed') is True and final.get('status') == 'succeeded'
                and final.get('expected_exit_code') == 0 and final.get('request_sha256') == files[str(folder/'request.json')]
                and final.get('guardTriggered') is False and final.get('guards') == []
                and final.get('limits') == {'stepSeconds': 15., 'workSeconds': 45., 'supervisorSeconds': 60., 'handshakeSeconds': 10.}
                and finite(final.get('handshake_duration_seconds'), 0, 10.05)
                and finite(final.get('duration_seconds'), 0, 60) and finite(final.get('work_duration_seconds'), 0, 45),
                'Supervisor did not meet native provenance, complete success and all deadlines')
        derived = row.get('result', {})
        require(all(derived.get(key) == value for key, value in final.items())
                and derived.get('terminal') is True and derived.get('processExitVerified') is True
                and derived.get('exitCodeVerified') is True and derived.get('supervisorExitCode') == 0
                and derived.get('ok') is True and set(final.get('steps', {})) == set(STEPS),
                'Terminal UI/observer result does not match raw successful supervisor evidence')
        workers = final.get('workers', [])
        require(len(workers) == 6 and [item.get('step') for item in workers] == list(STEPS), 'Six ordered owned workers are required')
        identities = [supervisor]
        for step, owned in zip(STEPS, workers):
            identity = owned.get('worker')
            identity_in_interval(identity, requested, completed)
            identities.append(identity)
            require(owned.get('exit_code') == 0 and owned.get('exit_verified') is True, f'{step}: worker exit was not verified')
            prefix = f'step-{STEPS.index(step):02d}-{step}-'
            launch, started_step, result = (original(prefix+suffix+'.json') for suffix in ('launch', 'started', 'result'))
            require(launch.get('worker') == identity and launch.get('supervisor') == supervisor
                    and launch.get('request_sha256') == final['request_sha256'], f'{step}: launch ownership mismatch')
            for record in (started_step, result):
                require(record.get('schema') == SCHEMA and record.get('operation_id') == op and record.get('step') == step
                        and record.get('worker') == identity and record.get('supervisor') == supervisor
                        and record.get('token') == launch.get('token') and isinstance(record.get('token'), str)
                        and len(record['token']) == 32 and record.get('evidenceKind') == 'windows-native-cleanup-step'
                        and record.get('evidenceVersion') == 1 and record.get('processElevated') is True,
                        f'{step}: not a matching actual privileged native worker')
            require(result.get('ok') is True and result.get('status') == 'succeeded' and result.get('exit_code') == 0
                    and started_step.get('started_at') == result.get('started_at')
                    and finite(result.get('started_at'), requested, completed)
                    and finite(result.get('completed_at'), result['started_at'], completed)
                    and finite(result.get('duration_seconds'), 0, 15)
                    and finite(final['steps'][step].get('duration_seconds'), 0, 15)
                    and final['steps'][step].get('exit_verified') is True
                    and all(final['steps'][step].get(key) == value for key, value in result.items() if key != 'duration_seconds'),
                    f'{step}: raw result, native duration or observed worker exit is inconsistent')
            required_privileges = ({'SeIncreaseQuotaPrivilege': True} if step == 'system_file_cache' else
                                   {} if step == 'registry_cache' else {'SeProfileSingleProcessPrivilege': True})
            require(result.get('privileges') == required_privileges, f'{step}: required privilege acquisition was not recorded')
            validate_native_calls(step, result.get('nativeCalls'))
        keys = {identity_key(value) for value in identities}
        require(len(keys) == 7, 'Worker and supervisor process identities must be distinct')
        checks = row.get('exitChecks', [])
        require(len(checks) == 7 and {identity_key(check.get('identity')) for check in checks} == keys
                and all(check.get('alive') is False and finite(check.get('observedAtUnix'), completed, row['observedFinishedAtUnix']) for check in checks)
                and row.get('ownedProcessesStillAlive') == []
                and not any(identity_alive(identity) for identity in identities), 'Owned processes were not all observed exited')
        require(any(record.get('operation_id') == op and record.get('value') == derived
                    and finite(record.get('observedAtUnix'), completed, end) for record in observations),
                'Raw observation stream lacks this exact terminal result')
        previous_exit = exited['observed_at']
        details.append({'operation_id': op, 'supervisor': supervisor, 'nativeSteps': list(STEPS),
                        'workDurationSeconds': final['work_duration_seconds'], 'processExitVerified': True})
    require(inventory == manifest.get('operationFiles'), 'Manifest operation inventory differs from the original reports')
    inputs = {str(path): digest(path) for path in (report_path, report_path.parent/'OBSERVATION-MANIFEST.json',
        observations_path, bundle.parent/'SOURCE-SNAPSHOT.json', Path(script['path']), observed_helper, helper)}
    inputs.update(inventory)
    return {'helperSha256': actual_hash, 'reportSha256': digest(report_path), 'operations': details, 'inputSha256': inputs,
            'observationInterval': [start, end]}


def validate_cleanup_evidence(bundle, evidence):
    result = {'reportKind': 'cleanup-evidence-validation', 'passed': False, 'errors': []}
    try:
        result.update(_validate(bundle, evidence))
        result['passed'] = True
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result['errors'].append(str(exc))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    result = validate_cleanup_evidence(args.bundle, args.evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
