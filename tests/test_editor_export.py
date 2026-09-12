"""Install actual Editor bytes only after validating the complete runtime pairing."""
import json
from pathlib import Path
import shutil

import pytest
from tools.authoring import install_editor_export as installer

REPO = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, 'ROOT', tmp_path/'report-root')
    native = REPO/'assets/live2d/Maple'
    refs = installer.read_json(native/'Maple.model3.json')['FileReferences']
    exported = tmp_path/'editor'
    exported.mkdir()
    shutil.copyfile(native/refs['Moc'], exported/'Maple.moc3')
    shutil.copyfile(native/refs['Textures'][0], exported/'atlas.png')
    source = exported/'Maple.model3.json'
    write_json(source, {'Version': 3, 'FileReferences': {'Moc': 'Maple.moc3', 'Textures': ['atlas.png']}})
    runtime = tmp_path/'revision/runtime'
    runtime.mkdir(parents=True)
    (runtime/'motions').mkdir()
    shutil.copyfile(native/'motions/idle.motion3.json', runtime/'motions/idle.motion3.json')
    write_json(runtime/'Maple.pet.json', {'anchors': {'head': {'drawable': 'face_base'}}})
    write_json(runtime/'Maple.physics3.json', {'Version': 3, 'PhysicsSettings': []})
    write_json(runtime/'expressions/smile.exp3.json', {'Type': 'Live2D Expression', 'Parameters': [{'Id': 'ParamMouthForm', 'Value': 1, 'Blend': 'Add'}]})
    fragment = runtime.parent/'Maple.model3-fragment.json'
    additions = {'FileReferences': {'Motions': {'idle': [{'File': 'motions/idle.motion3.json'}]},
                 'Physics': 'Maple.physics3.json', 'Expressions': [{'Name': 'smile', 'File': 'expressions/smile.exp3.json'}]},
                 'Groups': [{'Target': 'Parameter', 'Name': 'EyeBlink', 'Ids': ['ParamEyeLOpen', 'ParamEyeROpen']}],
                 'CuteMaple': {'Metadata': 'Maple.pet.json'}}
    write_json(fragment, additions)
    destination = tmp_path/'installed'
    destination.mkdir()
    (destination/'previous.txt').write_text('preserve my previous model')
    return source, destination, fragment, runtime


def test_complete_pairing_installs_actual_bytes_and_keeps_previous_directory(package):
    source, destination, fragment, runtime = package
    report = installer.install(source, destination, runtime_source=runtime)
    assert report['nativeValidation']['valid'] and report['stagingValidated']
    assert Path(report['backup']).joinpath('previous.txt').read_text() == 'preserve my previous model'
    assert not (destination/'previous.txt').exists()
    expected = {'Maple.moc3', 'atlas.png', 'Maple.pet.json', 'Maple.physics3.json',
                'motions/idle.motion3.json', 'expressions/smile.exp3.json', 'Maple.model3.json'}
    assert set(report['files']) == expected
    for name, checksum in report['files'].items():
        assert installer.sha(destination/name) == checksum
    for name, record in report['resources'].items():
        assert record['sourceSha256'] == record['installedSha256'] == installer.sha(Path(record['source']))
    assert report['sourceInputs'][str(fragment)] == installer.sha(fragment)
    assert installer.read_json(destination/'Maple.model3.json')['FileReferences']['Expressions'][0]['Name'] == 'smile'
    assert installer.read_json(destination/'editor-export.json') == report


@pytest.fixture
def restoration(package):
    """Synthetic audit records exercise installation gates; the MOC itself is a real Core-checked fixture."""
    from PIL import Image
    source, destination, fragment, runtime = package
    root = source.parent/'verified'
    root.mkdir()
    native = installer.inspect_moc(source.parent/'Maple.moc3')
    names = native['drawables']
    packed_atlas = root/'original-atlas.png'
    image = Image.new('RGBA', (32, 32), (10, 20, 30, 255)); image.save(packed_atlas)
    image.putpixel((0, 0), (10, 20, 30, 120)); image.save(source.parent/'atlas.png')
    author, packed, core = [root/name for name in ('source.cmo3', 'packed.cmo3', 'core.js')]
    for path in (author, packed, core): path.write_bytes(('unit fixture '+path.name).encode())
    atlas = {'sourceSha256': installer.sha(author), 'outputSha256': installer.sha(packed),
             'atlasSize': [32, 32], 'atlasCount': 1, 'meshCount': len(names),
             **dict.fromkeys(('cropPixelsIdentical', 'noRegionCollisions', 'noRotationOrScaling',
                             'allUvsValid', 'allReferencesResolve', 'noMoc3Generated'), True),
             'atlases': [{'path': str(packed_atlas), 'sha256': installer.sha(packed_atlas)}],
             'regions': [{'name': name, 'page': index} for name, index in zip(names, native['textureIndices'])]}
    atlas_path = root/'atlas-audit.json'; write_json(atlas_path, atlas)
    moc = source.parent/'Maple.moc3'
    core_hashes = {str(path): installer.sha(path) for path in (moc, core)}
    geometry = {'tool': 'official-cubism-native-geometry-audit', 'passed': True, 'errors': [],
                'sourcesUnchanged': True, 'sourceSha256': installer.sha(moc), 'sourceHashesAtStart': core_hashes,
                'sourceHashesAtEnd': dict(core_hashes), 'drawables': [{'id': name} for name in names]}
    geometry_path = root/'geometry.json'; write_json(geometry_path, geometry)
    hashes = {str(path): installer.sha(path) for path in (author, packed, core, moc, packed_atlas, atlas_path, geometry_path)}
    uv = {'tool': 'native-uv-pairing-audit', 'schemaVersion': 1, 'passed': True, 'errors': [],
          'sourcesUnchanged': True, 'sourceHashesAtStart': hashes, 'sourceHashesAtEnd': dict(hashes),
          'nativeMoc': str(moc), 'meshCount': len(names), 'matchedMeshCount': len(names), 'pixelTolerance': .001,
          'meshes': [{'id': name, 'passed': True, 'errors': [], 'sameTriangleTopologyIgnoringOrder': True,
                      'maximumAtlasPixelError': .0007} for name in names]}
    uv_path = root/'uv.json'; write_json(uv_path, uv)
    return (*package, atlas_path, uv_path, packed_atlas)


def test_verified_recovery_installs_exact_original_png_and_keeps_editor_damage_auditable(restoration):
    source, destination, fragment, runtime, atlas, uv, original = restoration
    editor_bytes = (source.parent/'atlas.png').read_bytes()
    original_bytes = original.read_bytes()
    global_receipt = installer.ROOT/'assets/authoring/editor-export.json'
    write_json(global_receipt, {'unrelated': 'parallel installation'})
    report = installer.install(source, destination, fragment, runtime_source=runtime, atlas_audit=atlas, native_uv_audit=uv)
    assert (destination/'atlas.png').read_bytes() == original_bytes
    assert (source.parent/'atlas.png').read_bytes() == editor_bytes
    assert (destination/'Maple.moc3').read_bytes() == (source.parent/'Maple.moc3').read_bytes()
    material = report['textureInstallation']
    assert material['mode'] == 'verified-packed-atlas' and material['originalEditorFilesModified'] is False
    row = material['pages'][0]
    assert row['editorVsPackedComparison']['counts']['alphaChangedPixels'] == 1
    assert row['editorVsPackedComparison']['coordinateMappingVerified'] is False
    assert row['installedTexture']['sha256'] == installer.sha(original) == report['files']['atlas.png']
    assert report['resources']['atlas.png']['sourceSha256'] == installer.sha(original)
    assert report['resources']['atlas.png']['originalEditorTexture']['sha256'] == installer.sha(source.parent/'atlas.png')
    assert report['sourceInputs'][str(uv)] == installer.sha(uv)
    assert installer.read_json(global_receipt) == {'unrelated': 'parallel installation'}


def test_verified_material_metadata_is_derived_without_changing_authored_input(restoration):
    source, destination, fragment, runtime, atlas_path, uv_path, _ = restoration
    atlas = installer.read_json(atlas_path)
    name = atlas['regions'][0]['name']
    atlas['regions'][0].update(sourceSize=[32, 32], translationToCanvas=[0, 0], crop=[0, 0, 32, 32])
    write_json(atlas_path, atlas)
    uv = installer.read_json(uv_path)
    for field in ('sourceHashesAtStart', 'sourceHashesAtEnd'):
        uv[field][str(atlas_path)] = installer.sha(atlas_path)
    write_json(uv_path, uv)
    metadata_path = runtime/installer.read_json(fragment)['CuteMaple']['Metadata']
    metadata = installer.read_json(metadata_path)
    metadata.setdefault('states', {})['climb_right'] = {'anchors': {'nearGrip': {'drawable': name, 'sourceUv': [.5, .25]}}}
    write_json(metadata_path, metadata)
    before = metadata_path.read_bytes()
    report = installer.install(source, destination, fragment, runtime_source=runtime, atlas_audit=atlas_path, native_uv_audit=uv_path)
    installed = installer.read_json(destination/metadata_path.name)
    assert installed['states']['climb_right']['anchors']['nearGrip'] == {'drawable': name, 'atlasUv': [.5, .75]}
    assert metadata_path.read_bytes() == before
    resource = report['resources'][metadata_path.name]
    assert resource['sourceSha256'] == installer.sha(metadata_path)
    assert resource['derivedSha256'] == resource['installedSha256']
    assert resource['sourceSha256'] != resource['installedSha256']
    assert len(report['materialAnchorConversion']['conversions']) == 1


def test_missing_required_metadata_is_rejected_before_staging(package):
    source, destination, fragment, runtime = package
    additions = installer.read_json(fragment)
    additions['CuteMaple'].pop('Metadata')
    additions['CuteMaple']['TestRetainsObject'] = True
    write_json(fragment, additions)
    with pytest.raises(ValueError, match='resource reference is empty or invalid'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert not list(destination.parent.glob('.installed.install-*'))


@pytest.mark.parametrize('fault', ['moc-hash', 'uv-failed', 'atlas-bytes', 'atlas-size', 'atlas-page', 'pixel-provenance'])
def test_wrong_moc_uv_or_atlas_cannot_trigger_original_texture_restoration(restoration, fault):
    source, destination, fragment, runtime, atlas_path, uv_path, original = restoration
    uv, atlas = installer.read_json(uv_path), installer.read_json(atlas_path)
    if fault == 'moc-hash':
        uv['sourceHashesAtStart'][str(source.parent/'Maple.moc3')] = '0'*64
        uv['sourceHashesAtEnd'] = dict(uv['sourceHashesAtStart'])
    elif fault == 'uv-failed': uv['meshes'][0]['passed'] = False
    elif fault == 'atlas-bytes': original.write_bytes(original.read_bytes()+b'changed')
    else:
        if fault == 'atlas-size': atlas['atlasSize'] = [64, 64]
        elif fault == 'atlas-page': atlas['regions'][0]['page'] = 1
        elif fault == 'pixel-provenance': atlas['cropPixelsIdentical'] = False
        write_json(atlas_path, atlas)
        for phase in ('sourceHashesAtStart', 'sourceHashesAtEnd'):
            uv[phase][str(atlas_path)] = installer.sha(atlas_path)
    write_json(uv_path, uv)
    with pytest.raises(ValueError):
        installer.install(source, destination, fragment, runtime_source=runtime, atlas_audit=atlas_path, native_uv_audit=uv_path)
    assert (destination/'previous.txt').is_file()
    assert not list(destination.parent.glob('.installed.install-*'))
    assert not list(destination.parent.glob('installed.backup-*'))


@pytest.mark.parametrize('option', ['atlas_audit', 'native_uv_audit'])
def test_restoration_requires_both_explicit_evidence_inputs(package, option):
    source, destination, fragment, runtime = package
    with pytest.raises(ValueError, match='requires both'):
        installer.install(source, destination, fragment, runtime_source=runtime, **{option: Path('missing.json')})
    assert (destination/'previous.txt').is_file()


def test_destination_cannot_replace_original_editor_export_or_frozen_restoration_sources(restoration):
    source, destination, fragment, runtime, atlas, uv, original = restoration
    editor_before, original_before = (source.parent/'atlas.png').read_bytes(), original.read_bytes()
    for target in (source.parent, original.parent):
        with pytest.raises(ValueError, match='must not contain'):
            installer.install(source, target, fragment, runtime_source=runtime, atlas_audit=atlas, native_uv_audit=uv)
    assert (source.parent/'atlas.png').read_bytes() == editor_before
    assert original.read_bytes() == original_before
    assert (destination/'previous.txt').is_file()


def test_legacy_call_reads_existing_authored_destination_before_backup(package):
    source, destination, fragment, runtime = package
    shutil.copytree(runtime, destination, dirs_exist_ok=True)
    report = installer.install(source, destination, fragment)
    assert report['runtimeSource'] == str(destination)
    assert (destination/'motions/idle.motion3.json').read_bytes() == (runtime/'motions/idle.motion3.json').read_bytes()


def test_missing_authored_asset_does_not_touch_existing_target(package):
    source, destination, fragment, runtime = package
    (runtime/'motions/idle.motion3.json').unlink()
    with pytest.raises(ValueError, match='Missing or unsafe'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert sorted(path.name for path in destination.iterdir()) == ['previous.txt']
    assert not list(destination.parent.glob('installed.backup-*'))


@pytest.mark.parametrize('escape', ['../outside.motion3.json', '..\\outside.motion3.json', 'C:/private/model.json', '/absolute/model.json'])
def test_authored_reference_escape_rejected_before_staging(package, escape):
    source, destination, fragment, runtime = package
    additions = installer.read_json(fragment)
    additions['FileReferences']['Motions']['idle'][0]['File'] = escape
    write_json(fragment, additions)
    with pytest.raises(ValueError, match='Unsafe'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert sorted(path.name for path in destination.iterdir()) == ['previous.txt']
    assert not list(destination.parent.glob('.installed.install-*'))


def test_editor_reference_escape_rejected_before_staging(package):
    source, destination, fragment, runtime = package
    model = installer.read_json(source)
    model['FileReferences']['Textures'] = ['../private.png']
    write_json(source, model)
    with pytest.raises(ValueError, match='Unsafe'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert (destination/'previous.txt').is_file()


@pytest.mark.parametrize('mode', ['RGBA', 'LA', 'P'])
def test_fully_transparent_real_png_is_rejected_before_target_changes(package, mode):
    from PIL import Image

    source, destination, fragment, runtime = package
    empty = Image.new(mode, (16, 16), (255, 255, 255, 0) if mode == 'RGBA' else (255, 0) if mode == 'LA' else 0)
    save = {'transparency': 0} if mode == 'P' else {}
    empty.save(source.parent/'atlas.png', **save)
    with pytest.raises(ValueError, match='fully transparent.*atlas.png'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert sorted(path.name for path in destination.iterdir()) == ['previous.txt']
    assert not list(destination.parent.glob('installed.backup-*'))
    assert not list(destination.parent.glob('.installed.install-*'))


def test_every_referenced_texture_is_checked_for_empty_alpha(package):
    from PIL import Image

    source, destination, fragment, runtime = package
    Image.new('RGBA', (8, 8), (120, 130, 140, 0)).save(source.parent/'empty-second.png')
    model = installer.read_json(source)
    model['FileReferences']['Textures'].append('empty-second.png')
    write_json(source, model)
    with pytest.raises(ValueError, match='fully transparent.*empty-second.png'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert (destination/'previous.txt').is_file()


@pytest.mark.parametrize('mode,extension', [('RGB', '.png'), ('RGB', '.jpg'), ('RGBA', '.png')])
def test_nonalpha_or_partially_visible_textures_remain_supported(package, mode, extension):
    from PIL import Image

    source, destination, fragment, runtime = package
    atlas = Image.new(mode, (16, 16), (80, 90, 100) if mode == 'RGB' else (0, 0, 0, 0))
    if mode == 'RGBA':
        atlas.putpixel((7, 7), (255, 255, 255, 1))
    name = 'supported' + extension
    atlas.save(source.parent/name)
    model = installer.read_json(source)
    model['FileReferences']['Textures'] = [name]
    write_json(source, model)
    report = installer.install(source, destination, fragment, runtime_source=runtime)
    assert report['stagingValidated']
    assert (destination/name).read_bytes() == (source.parent/name).read_bytes()


def test_header_only_fabricated_moc_is_rejected_by_official_core(package):
    source, destination, fragment, runtime = package
    (source.parent/'Maple.moc3').write_bytes(b'MOC3' + b'\x00'*1020)
    with pytest.raises(ValueError, match='Official Cubism Core rejected'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert (destination/'previous.txt').is_file()


def test_authored_parameters_must_exist_in_actual_model(package):
    source, destination, fragment, runtime = package
    motion = installer.read_json(runtime/'motions/idle.motion3.json')
    motion['Curves'][0]['Id'] = 'ParamDoesNotExistInExport'
    write_json(runtime/'motions/idle.motion3.json', motion)
    with pytest.raises(ValueError, match='does not match native MOC parameter'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert (destination/'previous.txt').is_file()


def test_failed_final_switch_restores_previous_target(package, monkeypatch):
    source, destination, fragment, runtime = package
    original = Path.replace
    def fail_staging(path, target):
        if path.name.startswith('.installed.install-') and target == destination:
            raise PermissionError('simulated directory in use')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', fail_staging)
    with pytest.raises(PermissionError, match='in use'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert (destination/'previous.txt').read_text() == 'preserve my previous model'
    assert not list(destination.parent.glob('.installed.install-*'))


def test_staging_is_created_atomically_with_inherited_directory_permissions(package, monkeypatch):
    source, destination, fragment, runtime = package
    original = Path.mkdir
    creations = []

    def observe_mkdir(path, mode=0o777, parents=False, exist_ok=False):
        if path.parent == destination.parent and path.name.startswith('.installed.install-'):
            creations.append((path, mode, parents, exist_ok))
        return original(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, 'mkdir', observe_mkdir)
    installer.install(source, destination, fragment, runtime_source=runtime)
    path, mode, parents, exist_ok = creations[0]
    assert mode == 0o777 and parents is False and exist_ok is False
    suffix = path.name.removeprefix('.installed.install-')
    assert installer.uuid.UUID(hex=suffix).hex == suffix and len(suffix) == 32
    assert not path.exists()
    assert (destination/'Maple.model3.json').is_file()


def test_staging_collision_is_rejected_without_removing_existing_directory(package, monkeypatch):
    source, destination, fragment, runtime = package
    collision_id = installer.uuid.UUID('4b6d04f2-3e96-4e54-b893-4bc44a06a667')
    collision = destination.parent / ('.installed.install-' + collision_id.hex)
    collision.mkdir()
    (collision/'unrelated.txt').write_text('belongs to another operation')
    monkeypatch.setattr(installer.uuid, 'uuid4', lambda: collision_id)
    with pytest.raises(FileExistsError):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert (collision/'unrelated.txt').read_text() == 'belongs to another operation'
    assert (destination/'previous.txt').read_text() == 'preserve my previous model'
    assert not list(destination.parent.glob('installed.backup-*'))


def test_copy_failure_removes_only_owned_staging_directory(package, monkeypatch):
    source, destination, fragment, runtime = package
    unrelated = destination.parent / '.installed.install-unrelated'
    unrelated.mkdir()
    (unrelated/'unrelated.txt').write_text('keep this sibling')

    def fail_copy(source_path, target_path):
        raise OSError('simulated resource copy failure')

    monkeypatch.setattr(installer.shutil, 'copy2', fail_copy)
    with pytest.raises(OSError, match='resource copy failure'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert (unrelated/'unrelated.txt').read_text() == 'keep this sibling'
    assert list(destination.parent.glob('.installed.install-*')) == [unrelated]
    assert (destination/'previous.txt').read_text() == 'preserve my previous model'
    assert not list(destination.parent.glob('installed.backup-*'))


def test_failed_install_to_absent_target_does_not_restore_an_unowned_backup(package, monkeypatch):
    source, existing_destination, fragment, runtime = package
    destination = existing_destination.with_name('not-yet-installed')
    instant = installer.datetime(2026, 9, 9, tzinfo=installer.timezone.utc)
    identifier = installer.uuid.UUID('802b29d8-d563-49ad-893a-b83094a6b793')

    class FixedClock:
        @staticmethod
        def now(zone=None):
            return instant

    monkeypatch.setattr(installer, 'datetime', FixedClock)
    monkeypatch.setattr(installer.uuid, 'uuid4', lambda: identifier)
    unrelated = destination.with_name(destination.name + '.backup-' +
                                      instant.strftime('%Y%m%dT%H%M%S.%fZ') + '-' + identifier.hex[:8])
    unrelated.mkdir()
    (unrelated/'unrelated.txt').write_text('never moved by this installation')
    original = Path.replace

    def fail_staging(path, target):
        if path.name.startswith('.not-yet-installed.install-') and target == destination:
            raise PermissionError('simulated final switch failure')
        return original(path, target)

    monkeypatch.setattr(Path, 'replace', fail_staging)
    with pytest.raises(PermissionError, match='final switch failure'):
        installer.install(source, destination, fragment, runtime_source=runtime)
    assert not destination.exists()
    assert (unrelated/'unrelated.txt').read_text() == 'never moved by this installation'
    assert not list(destination.parent.glob('.not-yet-installed.install-*'))
    assert (existing_destination/'previous.txt').read_text() == 'preserve my previous model'
