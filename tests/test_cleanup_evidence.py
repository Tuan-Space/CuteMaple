"""Synthetic schema fixtures only; no helper, native operation, task or UAC runs."""
from copy import deepcopy
import json

import pytest

from cleanup_protocol import STEPS, digest, write_json
from tools import validate_cleanup_evidence as gate


def ident(pid, seconds):
    return {'pid': pid, 'creationFiletime': int((seconds+11644473600)*10_000_000)}


def native(step):
    if step == 'system_file_cache':
        return [{'api': 'SetSystemFileCacheSize', 'dll': 'kernel32', 'returnValue': 1, 'lastError': 0, 'succeeded': True}]
    cls, payload = {'empty_working_sets': (80, 2), 'flush_modified_pages': (80, 3),
                    'purge_standby_list': (80, 4), 'registry_cache': (155, None), 'combine_memory': (130, None)}[step]
    return [{'api': 'NtSetSystemInformation', 'dll': 'ntdll', 'informationClass': cls,
             'inputInteger': payload, 'inputSize': 4 if cls == 80 else 0 if cls == 155 else 24,
             'ntstatus': 0, 'ntstatusHex': '0x00000000', 'succeeded': True}]


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    bundle = tmp_path/'package'/'CuteMaple-Live2D'
    (bundle/'cleaner').mkdir(parents=True)
    helper = bundle/'cleaner/CuteMaple-Cleaner.exe'
    helper.write_bytes(b'SYNTHETIC TEST ONLY - NOT EXECUTABLE - NEVER RELEASE')
    helper_sha = digest(helper)
    root, profile = tmp_path/'observation', tmp_path/'profile'
    root.mkdir()
    script = tmp_path/'observe-fixture.py'
    script.write_text('# SYNTHETIC TEST ONLY\n')
    sources = dict.fromkeys(gate.SOURCE_NAMES, 'a'*64)
    write_json(bundle.parent/'SOURCE-SNAPSHOT.json', {'sourceModulesSha256': sources})
    start = 1789000000.
    observer = ident(50000, start-.1)
    report = {'synthetic': True, 'schema': 1, 'evidenceVersion': 1, 'reportKind': 'cleanup-operation-observation',
              'executionMode': 'native-compiled-helper', 'observerElevated': False, 'privilegedCallsInObserver': False,
              'explicitSchedulingRequested': True, 'passed': True, 'requestedRuns': 3,
              'profile': str(profile), 'helper': str(helper), 'helperSha256': helper_sha, 'helperSha256After': helper_sha,
              'sourceClientHashes': sources, 'sourceClientHashesAfter': sources.copy(), 'observer': observer,
              'observerScript': {'path': str(script), 'sha256': digest(script)},
              'startedAtUnix': start, 'finishedAtUnix': start+20, 'operations': []}
    for index in range(3):
        op, requested = f'{index+1:032x}', start+1+index*5
        folder = profile/'cleanup/operations'/op
        folder.mkdir(parents=True)
        supervisor = ident(50100+index, requested+.01)
        request = {'schema': 4, 'operation_id': op, 'steps': list(STEPS), 'client': observer,
                   'helper_sha256': helper_sha, 'helper': str(helper), 'profile': str(profile), 'requested_at': requested}
        write_json(folder/'request.json', request)
        write_json(folder/'client-observed.json', {'operation_id': op, 'client': observer, 'supervisor': supervisor})
        write_json(folder/'started.json', {'schema': 4, 'operation_id': op, 'supervisor': supervisor})
        write_json(folder/'exit-observed.json', {'operation_id': op, 'supervisor': supervisor,
                    'method': 'process-handle', 'exit_code': 0, 'observed_at': requested+2.1})
        final = {'schema': 4, 'operation_id': op, 'supervisor': supervisor, 'client_observed': True,
                 'evidenceKind': 'windows-cleanup-supervisor', 'evidenceVersion': 1, 'status': 'succeeded',
                 'expected_exit_code': 0, 'request_sha256': digest(folder/'request.json'),
                 'guardTriggered': False, 'guards': [], 'work_duration_seconds': 1.8, 'duration_seconds': 2.,
                 'handshake_duration_seconds': .05,
                 'limits': {'stepSeconds': 15., 'workSeconds': 45., 'supervisorSeconds': 60., 'handshakeSeconds': 10.},
                 'completed_at': requested+2, 'steps': {}, 'workers': []}
        identities = [supervisor]
        for position, step in enumerate(STEPS):
            worker = ident(51000+index*10+position, requested+.1+position*.2)
            identities.append(worker)
            token = f'{position+10:032x}'
            prefix = f'step-{position:02d}-{step}-'
            launch = {'schema': 4, 'operation_id': op, 'step': step, 'worker': worker, 'token': token,
                      'supervisor': supervisor, 'request_sha256': final['request_sha256']}
            result = {'schema': 4, 'operation_id': op, 'step': step, 'worker': worker, 'token': token,
                      'supervisor': supervisor, 'evidenceKind': 'windows-native-cleanup-step', 'evidenceVersion': 1,
                      'processElevated': True, 'ok': True, 'status': 'succeeded', 'exit_code': 0,
                      'started_at': requested+.15+position*.2, 'completed_at': requested+.25+position*.2,
                      'duration_seconds': .1, 'nativeCalls': native(step)}
            result['privileges'] = ({'SeIncreaseQuotaPrivilege': True} if step == 'system_file_cache' else
                                    {} if step == 'registry_cache' else {'SeProfileSingleProcessPrivilege': True})
            write_json(folder/(prefix+'launch.json'), launch)
            write_json(folder/(prefix+'started.json'), {**result, 'status': 'running'})
            write_json(folder/(prefix+'result.json'), result)
            final['steps'][step] = {**result, 'duration_seconds': .15, 'exit_verified': True}
            final['workers'].append({'step': step, 'worker': worker, 'exit_code': 0, 'exit_verified': True})
        write_json(folder/'result.json', final)
        row = {'index': index+1, 'operation_id': op, 'passed': True, 'ownedProcessesStillAlive': [],
               'observedStartedAtUnix': requested-.05, 'observedFinishedAtUnix': requested+2.3,
               'result': {**final, 'terminal': True, 'processExitVerified': True, 'exitCodeVerified': True,
                          'supervisorExitCode': 0, 'ok': True},
               'exitChecks': [{'identity': value, 'alive': False, 'observedAtUnix': requested+2.2} for value in identities]}
        report['operations'].append(row)
    def refresh():
        for row in report['operations']:
            folder = profile/'cleanup/operations'/row['operation_id']
            row['operationFiles'] = {str(path): digest(path) for path in sorted(folder.glob('*.json'))}
        stream = root/'observations.jsonl'
        stream.write_text('\n'.join(json.dumps({'operation_id': row['operation_id'], 'value': row['result'],
            'observedAtUnix': row['result']['completed_at']+.15}) for row in report['operations']), encoding='utf-8')
        report['observationsSha256'] = digest(stream)
        write_json(root/'report.json', report)
        write_json(root/'OBSERVATION-MANIFEST.json', {'schema': 1, 'reportKind': 'cleanup-observation-manifest',
            'report': {'path': str(root/'report.json'), 'sha256': digest(root/'report.json')}, 'helperSha256': helper_sha,
            'operationFiles': {path: checksum for row in report['operations'] for path, checksum in row['operationFiles'].items()}})
    refresh()
    # A schema-positive fixture is intentionally marked synthetic on disk and
    # the production reader must reject it. Only these unit tests strip that
    # marker locally to exercise all deeper checks. There is no CLI bypass.
    actual_reader = gate.document
    def schema_reader(path):
        if path == root/'report.json':
            value = json.loads(path.read_text(encoding='utf-8'))
            value.pop('synthetic')
            return value
        return actual_reader(path)
    monkeypatch.setattr(gate, 'document', schema_reader)
    monkeypatch.setattr(gate, 'identity_alive', lambda _: False)
    return bundle, root, report, profile, refresh, actual_reader


def test_complete_schema_fixture_passes_only_under_explicit_test_reader(evidence):
    bundle, root, *_ = evidence
    result = gate.validate_cleanup_evidence(bundle, root)
    assert result['passed'], result['errors']
    assert len(result['operations']) == 3


def test_actual_reader_rejects_our_synthetic_test_report(evidence, monkeypatch):
    bundle, root, _, _, _, reader = evidence
    monkeypatch.setattr(gate, 'document', reader)
    result = gate.validate_cleanup_evidence(bundle, root)
    assert not result['passed'] and 'Synthetic' in result['errors'][0]


@pytest.mark.parametrize('case', ['two_runs', 'same_operation', 'source_changed', 'wrong_helper', 'missing_raw_code',
                                  'old_registry_class', 'nt_failure', 'guard_triggered', 'worker_unverified',
                                  'missing_started', 'worker_alive', 'outside_process_interval'])
def test_incomplete_or_mismatched_native_evidence_rejected(evidence, monkeypatch, case):
    bundle, root, report, profile, refresh, _ = evidence
    row = report['operations'][0]
    folder = profile/'cleanup/operations'/row['operation_id']
    if case == 'two_runs': report['operations'].pop()
    elif case == 'same_operation': report['operations'][1] = deepcopy(row)
    elif case == 'source_changed': report['sourceClientHashesAfter'] = {**report['sourceClientHashes'], 'cleanup_session.py': 'b'*64}
    elif case == 'wrong_helper': (bundle/'cleaner/CuteMaple-Cleaner.exe').write_bytes(b'changed final helper')
    elif case == 'missing_started': (folder/'step-04-registry_cache-started.json').unlink()
    elif case == 'worker_alive': monkeypatch.setattr(gate, 'identity_alive', lambda _: True)
    else:
        final = json.loads((folder/'result.json').read_text(encoding='utf-8'))
        if case == 'guard_triggered': final['guardTriggered'] = True
        elif case == 'worker_unverified': final['workers'][0]['exit_verified'] = False
        elif case == 'outside_process_interval': final['supervisor']['creationFiletime'] = 1000
        else:
            step_path = folder/'step-04-registry_cache-result.json'
            step = json.loads(step_path.read_text(encoding='utf-8'))
            if case == 'missing_raw_code': step.pop('nativeCalls')
            elif case == 'old_registry_class': step['nativeCalls'][0]['informationClass'] = 143
            elif case == 'nt_failure': step['nativeCalls'][0].update(ntstatus=0xC0000003, ntstatusHex='0xC0000003', succeeded=False)
            write_json(step_path, step)
            final['steps']['registry_cache'] = {**step, 'duration_seconds': .15, 'exit_verified': True}
        write_json(folder/'result.json', final)
        row['result'] = {**row['result'], **final}
    refresh()
    assert not gate.validate_cleanup_evidence(bundle, root)['passed']


@pytest.mark.parametrize('change', ['report', 'operation'])
def test_changed_original_hash_cannot_be_replaced_by_success_boolean(evidence, change):
    bundle, root, report, profile, *_ = evidence
    path = root/'report.json' if change == 'report' else profile/'cleanup/operations'/report['operations'][0]['operation_id']/'result.json'
    path.write_bytes(path.read_bytes()+b' ')
    assert not gate.validate_cleanup_evidence(bundle, root)['passed']


@pytest.mark.parametrize('name', gate.BLOCKED_BUILD_IDS)
def test_blocked_build_is_rejected_before_any_file_read(tmp_path, monkeypatch, name):
    monkeypatch.setattr(gate, 'document', lambda _: pytest.fail('must not read blocked build'))
    result = gate.validate_cleanup_evidence(tmp_path/name, tmp_path/'unneeded.json')
    assert not result['passed'] and 'blocked' in result['errors'][0]
