"""Archive a committed source tree with materialized LFS objects, never local edits."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def package(root, output, version=None, source=False, committed_files=(), revision='HEAD'):
    root, output = root.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(output)
    if committed_files and not source:
        raise ValueError('--committed-file requires --source')
    commit = git(root, 'rev-parse', '--verify', revision + '^{commit}').decode().strip() if source else None
    actual_version = (git(root, 'show', commit + ':VERSION').decode().strip() if source
                      else (root / 'VERSION').read_text().strip())
    if version and version != actual_version:
        raise ValueError('Version must match the archived source commit')
    version = actual_version
    prefix = f'CuteMaple-{version}' + ('-Source' if source else '')
    manifest = {}

    def write(archive, name, stream):
        parts = PurePosixPath(name)
        if parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
            raise ValueError(f'Unsafe archive path: {name}')
        if name in manifest:
            raise ValueError(f'Duplicate archive path: {name}')
        hasher = hashlib.sha256()
        with archive.open(prefix + '/' + name, 'w', force_zip64=True) as destination:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
                destination.write(chunk)
        manifest[name] = hasher.hexdigest()

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='cutemaple-package-') as temp:
            with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                if source:
                    tree = Path(temp) / 'source.tar'
                    subprocess.run(['git', '-C', str(root), '-c', 'core.autocrlf=false', 'archive', '--format=tar', '-o', str(tree), commit], check=True)
                    common = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip())
                    with tarfile.open(tree) as inputs:
                        for entry in inputs:
                            if entry.isdir():
                                continue
                            if not entry.isfile():
                                raise ValueError(f'Unsupported source entry: {entry.name}')
                            name = entry.name
                            if name.startswith(('artifacts/', 'dist/', '.build/', 'docs/verification/')):
                                raise ValueError(f'Generated evidence must not be committed: {name}')
                            with inputs.extractfile(entry) as stream:
                                if entry.size < 1024:
                                    data = stream.read()
                                    if data.startswith(b'version https://git-lfs.github.com/spec/v1'):
                                        values = dict(line.split(' ', 1) for line in data.decode().splitlines())
                                        oid = values['oid'].removeprefix('sha256:')
                                        if len(oid) != 64 or any(c not in '0123456789abcdef' for c in oid):
                                            raise ValueError(f'Invalid LFS pointer: {name}')
                                        path = common / 'lfs/objects' / oid[:2] / oid[2:4] / oid
                                        if not path.is_file() or path.stat().st_size != int(values['size']) or digest(path) != oid:
                                            raise ValueError(f'Missing/corrupt LFS object; run git lfs pull: {name}')
                                        with path.open('rb') as materialized:
                                            write(archive, name, materialized)
                                    else:
                                        write(archive, name, io.BytesIO(data))
                                else:
                                    write(archive, name, stream)
                    if set(committed_files) - manifest.keys():
                        raise ValueError('Requested committed file does not exist in this revision')
                    write(archive, 'SOURCE-REVISION.json', io.BytesIO(json.dumps({'commit': commit, 'version': version}, indent=2).encode()))
                else:
                    for path in sorted(root.rglob('*')):
                        if path.is_symlink() or not path.resolve().is_relative_to(root):
                            raise ValueError(f'Nonlocal input: {path}')
                        if path.is_file() and path != output:
                            with path.open('rb') as stream:
                                write(archive, path.relative_to(root).as_posix(), stream)
                archive.writestr(prefix + '/FILE-HASHES.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            with zipfile.ZipFile(output) as archive:
                for name, expected in manifest.items():
                    with archive.open(prefix + '/' + name) as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                            raise ValueError(f'Archive hash mismatch: {name}')
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    result = {'file': output.name, 'sha256': digest(output), 'bytes': output.stat().st_size,
              'files': len(manifest) + 1, 'source': source, 'commit': commit}
    output.with_suffix(output.suffix + '.sha256.txt').write_text(result['sha256'] + '  ' + output.name + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--source', action='store_true')
    parser.add_argument('--version')
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--committed-file', action='append', default=[], help='Compatibility option; all source files now come from the commit')
    args = parser.parse_args()
    print(json.dumps(package(args.root, args.output, args.version, args.source, args.committed_file, args.revision)))
