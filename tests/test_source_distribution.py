"""Release archives must be reproducible from commits, not developer worktrees."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest
from tools.package_release import package


def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'VERSION').write_text('2.2.7\n')
    (root / 'main.py').write_bytes(b'committed source\n')
    return root


def commit(root):
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    '-c', 'core.hooksPath=NUL', 'commit', '-qm', 'fixture'], check=True)


def test_source_ignores_dirty_files_and_records_revision(tmp_path):
    root = repo(tmp_path)
    commit(root)
    (root / 'main.py').write_text('uncommitted edits\n')
    (root / 'private.txt').write_text('not for release')
    output = tmp_path / 'source.zip'
    result = package(root, output, source=True)
    with zipfile.ZipFile(output) as archive:
        prefix = 'CuteMaple-2.2.7-Source/'
        assert archive.read(prefix + 'main.py') == b'committed source\n'
        assert not any('private.txt' in n for n in archive.namelist())
        assert json.loads(archive.read(prefix + 'SOURCE-REVISION.json'))['commit'] == result['commit']
        manifest = json.loads(archive.read(prefix + 'FILE-HASHES.json'))
        for name, sha in manifest.items():
            assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == sha
    assert (root / 'main.py').read_text() == 'uncommitted edits\n'


def test_lfs_materializes_committed_object_and_rejects_missing_data(tmp_path):
    root = repo(tmp_path)
    original = b'original editable model'
    oid = hashlib.sha256(original).hexdigest()
    (root / 'model.cmo3').write_text(f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(original)}\n')
    commit(root)
    (root / 'model.cmo3').write_bytes(b'private draft')
    output = tmp_path / 'source.zip'
    with pytest.raises(ValueError, match='Missing/corrupt LFS'):
        package(root, output, source=True)
    assert not output.exists()
    obj = root / '.git/lfs/objects' / oid[:2] / oid[2:4] / oid
    obj.parent.mkdir(parents=True)
    obj.write_bytes(original)
    package(root, output, source=True)
    with zipfile.ZipFile(output) as archive:
        assert archive.read('CuteMaple-2.2.7-Source/model.cmo3') == original
    assert (root / 'model.cmo3').read_bytes() == b'private draft'


def test_generated_evidence_and_wrong_version_are_rejected(tmp_path):
    root = repo(tmp_path)
    (root / 'docs/verification').mkdir(parents=True)
    (root / 'docs/verification/old.txt').write_text('generated evidence')
    commit(root)
    with pytest.raises(ValueError, match='Version must match'):
        package(root, tmp_path / 'wrong.zip', version='0.0.0', source=True)
    with pytest.raises(ValueError, match='Generated evidence'):
        package(root, tmp_path / 'old.zip', source=True)
    assert not (tmp_path / 'old.zip').exists()


def test_absolute_launcher_resolves_resources_outside_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    import sys
    code = ('import sys;sys.path.insert(0,sys.argv[1]);import main;from pet_core import resource_root,stable_application_path;'
            'assert (resource_root()/"assets/live2d/Maple/Maple.model3.json").is_file();'
            'assert stable_application_path()==resource_root()/"main.py";print(resource_root())')
    result = subprocess.run([sys.executable, '-I', '-c', code, str(root)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == root
