"""Reopen actual GUI cleanup evidence; never perform cleanup or authorization."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from cleanup_protocol import STEPS, digest
from cleanup_process import identity_alive
from tools.validate_cleanup_evidence import validate_native_calls


def validate(executable: Path, report_path: Path):
    executable = executable.resolve(strict=True)
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    helper = executable.parent / 'cleaner/CuteMaple-Cleaner.exe'
    manifest = json.loads(helper.with_name('native-build.json').read_text(encoding='utf-8-sig'))
    assert manifest['testBuild'] is False and manifest['implementation'] == 'cpp-msvc'
    assert digest(helper) == manifest['sha256'] == report['helperSha256'] == report['helperSha256After']
    assert digest(executable) == report['mainSha256']
    assert report['observerElevated'] is False and report['realCleanupPerformed'] is True
    assert report['passed'] is True and report['errors'] == [] and report['exitCode'] == 0
    assert report['sessionExited'] is True and len(report['sessionIdentities']) == 1
    assert not identity_alive(report['sessionIdentities'][0])
    operations = report['operations']
    assert len(operations) == len({row['operation_id'] for row in operations}) == 3
    files = report['operationFiles']
    assert files
    for name, expected in files.items():
        assert digest(Path(name)) == expected, name
    for result in operations:
        assert result['ok'] and result['status'] == 'succeeded' and not result['guardTriggered']
        assert result['processExitVerified'] and result['exitCodeVerified'] and result['supervisorExitCode'] == 0
        assert result['duration_seconds'] < 60 and result['work_duration_seconds'] < 45
        assert set(result['steps']) == set(STEPS) and len(result['workers']) == 6
        assert not identity_alive(result['supervisor'])
        folder = next(Path(name).parent for name in files if Path(name).parent.name == result['operation_id'])
        request = json.loads((folder/'request.json').read_text(encoding='utf-8'))
        final = json.loads((folder/'result.json').read_text(encoding='utf-8'))
        assert digest(folder/'request.json') == result['request_sha256'] == final['request_sha256']
        assert request['client'] == report['client'] and request['helper_sha256'] == digest(helper)
        assert final['supervisor'] == result['supervisor'] and final['steps'] == result['steps']
        for step in STEPS:
            row = result['steps'][step]
            assert row['ok'] and row['exit_verified'] and row['exit_code'] == 0 and row['duration_seconds'] < 15
            assert row['processElevated'] is True and row['implementation'] == 'cpp-msvc'
            assert not identity_alive(row['worker'])
            validate_native_calls(step, row['nativeCalls'])
    return {'passed': True, 'operations': 3, 'nativeSteps': 18, 'executableSha256': digest(executable),
            'helperSha256': digest(helper), 'reportSha256': digest(report_path)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.executable, args.report)))
