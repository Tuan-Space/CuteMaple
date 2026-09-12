"""Exercise process orchestration with tiny children, never real model fixtures."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import run_regression as runner


FAKE_PYTEST = '''import json, os, sys
from pathlib import Path
args = sys.argv[2:]
behavior = sys.argv[1]
root = Path.cwd()
directory = Path(args[args.index('--junitxml') + 1]).parent
output = directory.parent
summary = json.loads((output / 'summary.json').read_text())
assert summary['status'] == 'running' and not summary['complete']
assert (output / 'source-inputs.json').is_file()
assert (output / 'plan.json').is_file()
assert json.loads((directory / 'result.json').read_text())['status'] == 'running'
trace = root / 'children.jsonl'
previous = trace.read_text().splitlines() if trace.exists() else []
if previous:
    last = json.loads(previous[-1])
    prior = Path(last['directory']) / 'result.json'
    assert json.loads(prior.read_text())['status'] == 'passed'
with trace.open('a') as stream:
    stream.write(json.dumps({'args': args, 'pid': os.getpid(), 'directory': str(directory)}) + '\\n')
print('child stdout', flush=True)
print('child stderr', file=sys.stderr, flush=True)
second = len(previous) == 1
if behavior == 'crash' and second:
    os._exit(7)
report = Path(args[args.index('--junitxml') + 1])
if behavior != 'missing' or not second:
    failure = int(behavior == 'failure' and second)
    report.write_text('<testsuites><testsuite tests="3" failures="%s" errors="0" skipped="1" /></testsuites>' % failure)
if behavior == 'malformed' and second:
    report.write_text('<broken')
if behavior == 'drift' and second:
    (root / 'source.py').write_text('changed = True')
sys.exit(1 if behavior == 'failure' and second else 0)
'''


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    # These children only write flat logs, not deeply nested cleanup fixtures.
    monkeypatch.setattr(runner, 'MAX_WINDOWS_BASETEMP_LENGTH', 1000)
    root = tmp_path / 'checkout'
    root.mkdir()
    (root / 'tests' / 'fixtures').mkdir(parents=True)
    for module in runner.ISOLATED_MODULES:
        (root / module).write_text('def test_small(): pass\n')
    (root / 'tests' / 'test_other.py').write_text('def test_other(): pass\n')
    (root / 'tests' / 'fixtures' / 'contact.json').write_text('{"value": 1}')
    (root / 'source.py').write_text('original = True\n')
    (root / 'pytest.ini').write_text('[pytest]\n')
    (root / 'fake_pytest.py').write_text(FAKE_PYTEST)
    subprocess.run(['git', 'init', '--quiet', str(root)], check=True, capture_output=True)
    return root, tmp_path / 'evidence'


def run_fake(checkout, behavior='pass'):
    root, output = checkout
    code = runner.run_regression(root, output,
        pytest_command=[sys.executable, str(root / 'fake_pytest.py'), behavior])
    summary = json.loads((output / 'summary.json').read_text())
    children = [json.loads(line) for line in (root / 'children.jsonl').read_text().splitlines()]
    return code, summary, children


def test_every_group_runs_once_with_separate_evidence_and_checked_temps(checkout, monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k silently_omit_every_test')
    code, summary, children = run_fake(checkout)
    count = len(runner.ISOLATED_MODULES) + 1
    assert code == 0 and summary['status'] == 'passed' and summary['complete']
    assert summary['completedGroups'] == len(children) == count
    assert summary['totals'] == {'tests': count * 3, 'passed': count * 2,
                                  'failures': 0, 'errors': 0, 'skipped': count}
    assert [child['args'][0] for child in children] == [*runner.ISOLATED_MODULES, 'tests']
    assert set(arg for arg in children[-1]['args'] if arg.startswith('--ignore=')) == {
        f'--ignore={module}' for module in runner.ISOLATED_MODULES}
    assert len({group['basetemp'] for group in summary['groups']}) == count
    for group in summary['groups']:
        assert group['sourceUnchanged'] and group['sourceSha256'] == summary['sourceSha256']
        assert Path(group['basetemp']).is_relative_to(checkout[1])
        assert 'child stdout' in Path(group['stdout']).read_text()
        assert 'child stderr' in Path(group['stderr']).read_text()
        assert '-p' in group['command'] and 'no:cacheprovider' in group['command']
        assert '-o' in group['command'] and 'addopts=' in group['command']


@pytest.mark.parametrize('behavior', ['failure', 'missing', 'crash', 'malformed', 'drift'])
def test_failed_or_unverifiable_group_stops_and_keeps_prior_results(checkout, behavior):
    code, summary, children = run_fake(checkout, behavior)
    assert code == 1 and summary['status'] == 'failed' and not summary['complete']
    assert len(children) == 2 and summary['completedGroups'] == 1
    assert summary['groups'][0]['status'] == 'passed'
    assert summary['groups'][1]['status'] == 'failed'
    assert all(group['status'] == 'pending' for group in summary['groups'][2:])
    assert (checkout[1] / 'source-inputs.json').is_file()
    if behavior == 'drift':
        assert summary['groups'][1]['changedSourceInputs'] == ['source.py']
    if behavior == 'crash':
        assert summary['groups'][1]['returncode'] == 7
        assert 'reportError' in summary['groups'][1]


def test_existing_output_is_never_reused_or_deleted(checkout):
    root, output = checkout
    output.mkdir()
    marker = output / 'keep.txt'
    marker.write_text('user data')
    with pytest.raises(FileExistsError):
        runner.run_regression(root, output)
    assert marker.read_text() == 'user data'
    assert not (root / 'children.jsonl').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows legacy path limit')
def test_overlong_output_rejected_before_any_child_or_evidence_directory(checkout, monkeypatch):
    root, output = checkout
    monkeypatch.setattr(runner, 'MAX_WINDOWS_BASETEMP_LENGTH', 80)
    output = output / ('long-output-' * 8)
    with pytest.raises(ValueError, match='Choose a shorter --output-dir'):
        runner.run_regression(root, output)
    assert not output.exists() and not (root / 'children.jsonl').exists()


def test_snapshot_includes_untracked_fixture_and_never_reads_build_artifacts(checkout, monkeypatch):
    root, _ = checkout
    calibration = root / 'tools/authoring/climb_contact_native08.json'
    calibration.parent.mkdir(parents=True, exist_ok=True)
    calibration.write_text('{"nativeMaterial": 1}')
    snapshot = runner.source_snapshot(root)
    assert 'tests/fixtures/contact.json' in snapshot['files']
    assert 'source.py' in snapshot['files'] and 'pytest.ini' in snapshot['files']
    assert 'tools/authoring/climb_contact_native08.json' in snapshot['files']
    actual = subprocess.run

    def listed_names(*args, **kwargs):
        result = actual(*args, **kwargs)
        result.stdout += b'.build/withdrawn/must-not-open.py\0dist/withdrawn/must-not-open.py\0'
        return result

    monkeypatch.setattr(subprocess, 'run', listed_names)
    assert runner.source_snapshot(root) == snapshot
    calibration.write_text('{"nativeMaterial": 2}')
    assert runner.source_snapshot(root)['sha256'] != snapshot['sha256']


def test_interrupt_preserves_incomplete_evidence(checkout, monkeypatch):
    root, output = checkout
    actual = subprocess.run

    def interrupt_child(command, **kwargs):
        if command[:3] == [sys.executable, '-m', 'pytest']:
            raise KeyboardInterrupt
        return actual(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', interrupt_child)
    assert runner.run_regression(root, output) == 1
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['status'] == 'interrupted' and not summary['complete']
    assert summary['groups'][0]['status'] == 'interrupted'
    assert all(group['status'] == 'pending' for group in summary['groups'][1:])
    assert (output / 'plan.json').is_file() and (output / 'source-inputs.json').is_file()
