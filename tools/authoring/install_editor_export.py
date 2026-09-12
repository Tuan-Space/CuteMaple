"""Install genuine Editor data with its authored runtime through a verified staging directory.

No MOC data is generated. Official Core consistency checks validate the binary;
native rendering and visual acceptance are still required after installation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.qa_model_inputs import audit_atlas_files, verify_native_uv_evidence, recheck_input_hashes
from tools.native_model_contract import declared_native_parameters, declared_native_drawables, free_motion_contacts, brush_contract
MOC_INSPECTOR = Path(__file__).with_name('inspect_moc3.mjs')
SIMPLE_REFERENCES = ('Moc', 'Physics', 'Pose', 'DisplayInfo', 'UserData')
REFERENCE_KINDS = {*SIMPLE_REFERENCES, 'Textures', 'Motions', 'Expressions'}


def read_json(path: Path):
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(document, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return document


def safe_name(reference: str) -> str:
    if not isinstance(reference, str) or not reference or '\x00' in reference:
        raise ValueError('An exported resource reference is empty or invalid')
    name = reference.replace('\\', '/')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or ':' in name or not path.name or name.endswith('/'):
        raise ValueError(f'Unsafe exported resource: {reference}')
    return path.as_posix()


def contained_file(root: Path, reference: str) -> Path:
    root = root.resolve()
    path = (root / safe_name(reference)).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'Missing or unsafe exported resource: {reference}')
    return path


def references(document: dict):
    refs = document.get('FileReferences', {})
    if not isinstance(refs, dict) or set(refs)-REFERENCE_KINDS:
        raise ValueError('Unknown or malformed model FileReferences')
    for kind in SIMPLE_REFERENCES:
        if kind in refs:
            yield kind, safe_name(refs[kind])
    if 'Textures' in refs and not isinstance(refs['Textures'], list):
        raise ValueError('Textures must be an array')
    for name in refs.get('Textures', []):
        yield 'Textures', safe_name(name)
    motions = refs.get('Motions', {})
    if not isinstance(motions, dict):
        raise ValueError('Motions must be an object')
    for group, entries in motions.items():
        if not isinstance(entries, list) or not entries:
            raise ValueError(f'Empty or malformed motion group: {group}')
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Malformed motion entry')
            yield 'Motions', safe_name(entry.get('File'))
            if entry.get('Sound'):
                yield 'Motions', safe_name(entry['Sound'])
    expressions = refs.get('Expressions', [])
    if not isinstance(expressions, list):
        raise ValueError('Expressions must be an array')
    for entry in expressions:
        if not isinstance(entry, dict):
            raise ValueError('Malformed expression entry')
        yield 'Expressions', safe_name(entry.get('File'))
    if 'CuteMaple' in document:
        yield 'Metadata', safe_name(document['CuteMaple'].get('Metadata'))


def inspect_moc(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 64 or data[:4] != b'MOC3':
        raise ValueError('Expected a complete, actual Cubism MOC3 binary')
    node = shutil.which('node')
    if node is None:
        raise ValueError('Node.js is required for the official Cubism Core MOC validation')
    try:
        result = subprocess.run([node, str(MOC_INSPECTOR), str(path)], capture_output=True,
                                text=True, encoding='utf-8', timeout=20, check=False)
        if result.returncode:
            raise ValueError('Official Cubism Core rejected the MOC3 binary: ' + result.stderr[-600:])
        native = json.loads(result.stdout)
        if native.get('valid') is not True or not native.get('drawables'):
            raise ValueError('MOC3 did not contain a valid drawable model')
        return native
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise ValueError(f'Official Cubism Core validation failed: {error}') from error


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_material_anchors(metadata: dict, atlas: dict | None, native: dict, evidence: dict | None):
    """Convert authoring pixel materials only after complete native UV proof.

    Packing uses image-top-down V. Core's vertex UV V is bottom-up; the
    runtime descriptor uses that original Core convention without a second flip.
    Source metadata remains immutable and each converted descriptor is audited.
    """
    brush_contract(metadata)
    result, conversions = deepcopy(metadata), []
    definitions = [('anchors', result.get('anchors', {}))]
    definitions += [(f'states.{state}.anchors', value.get('anchors', {}))
                    for state, value in result.get('states', {}).items()]
    regions = {}
    if atlas is not None:
        for region in atlas.get('regions', []):
            for name in region.get('drawables', [region.get('name')]):
                if name in regions:
                    raise ValueError(f'Duplicate atlas region for material anchor {name}')
                regions[name] = region
    for context, anchors in definitions:
        for key, anchor in anchors.items():
            if 'sourceUv' not in anchor:
                continue
            if not evidence or evidence.get('verified') is not True or atlas is None:
                raise ValueError('Source material anchors require complete verified native UV evidence')
            name, point = anchor.get('drawable'), anchor['sourceUv']
            if (name not in native['drawables'] or name not in regions or
                    not isinstance(point, list) or len(point) != 2 or
                    not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1 for v in point)
                    or set(anchor)-{'drawable', 'sourceUv'}):
                raise ValueError(f'Invalid or ambiguous material anchor: {context}.{key}')
            region = regions[name]
            source_size, translation, size = region['sourceSize'], region['translationToCanvas'], atlas['atlasSize']
            pixels = [point[i]*source_size[i] for i in range(2)]
            crop = region['crop']
            if not crop[0] <= pixels[0] < crop[2] or not crop[1] <= pixels[1] < crop[3]:
                raise ValueError(f'Material anchor lies outside the packed source crop: {context}.{key}')
            packed = [(pixels[i]-translation[i])/size[i] for i in range(2)]
            if not all(math.isfinite(v) and 0 <= v <= 1 for v in packed):
                raise ValueError(f'Material anchor lies outside the atlas: {context}.{key}')
            native_index = native['drawables'].index(name)
            if native['textureIndices'][native_index] != region['page']:
                raise ValueError(f'Material anchor texture page differs from native Core: {name}')
            resolved = {'drawable': name, 'atlasUv': [packed[0], 1-packed[1]]}
            conversions.append({'anchor': context+'.'+key, 'source': deepcopy(anchor), 'installed': resolved,
                                'texturePage': region['page']})
            anchors[key] = resolved
    return result, conversions


def validate_native_bindings(model: dict, resources: dict, native: dict) -> None:
    parameters, drawables = set(native['parameters']), set(native['drawables'])
    def parameter(name, context):
        if name not in parameters:
            raise ValueError(f'Authored runtime does not match native MOC parameter {name!r} ({context})')
    for group in model.get('Groups', []):
        if group.get('Target') == 'Parameter':
            for name in group.get('Ids', []):
                parameter(name, group.get('Name'))
    for entries in model['FileReferences'].get('Motions', {}).values():
        for entry in entries:
            name = safe_name(entry['File'])
            motion = read_json(resources[name])
            if not motion.get('Curves') or motion.get('Meta', {}).get('Duration', 0) <= 0:
                raise ValueError(f'Missing authored motion curves/duration: {name}')
            for curve in motion['Curves']:
                if curve.get('Target') == 'Parameter':
                    parameter(curve.get('Id'), name)
    for entry in model['FileReferences'].get('Expressions', []):
        name = safe_name(entry['File'])
        for binding in read_json(resources[name]).get('Parameters', []):
            parameter(binding.get('Id'), name)
    physics = model['FileReferences'].get('Physics')
    if physics:
        for setting in read_json(resources[safe_name(physics)]).get('PhysicsSettings', []):
            for binding in setting.get('Input', []):
                parameter(binding.get('Source', {}).get('Id'), physics)
            for binding in setting.get('Output', []):
                parameter(binding.get('Destination', {}).get('Id'), physics)
    metadata = model.get('CuteMaple', {}).get('Metadata')
    if not metadata:
        raise ValueError('Missing CuteMaple.Metadata authoring contact/expression mapping')
    data = read_json(resources[safe_name(metadata)])
    brush_contract(data)
    refinement = data.get('refinement', {})
    if refinement.get('version') == 5:
        expected_parameters = declared_native_parameters(refinement)
        expected_drawables = declared_native_drawables(refinement)
        if ((expected_parameters is not None and expected_parameters != parameters)
                or (expected_drawables is not None and expected_drawables != drawables)):
            raise ValueError('Native MOC identities differ from the complete authored model inventory')
        free_motion_contacts(refinement)
    for name in data.get('capabilities', {}).get('parameters', []):
        parameter(name, metadata)
    for name in data.get('refinement', {}).get('requiredParameters', []):
        parameter(name, metadata)
    for definitions in [data.get('anchors', {}), *(state.get('anchors', {}) for state in data.get('states', {}).values())]:
        for anchor in definitions.values():
            if 'drawable' in anchor and anchor['drawable'] not in drawables:
                raise ValueError(f'Authored contact drawable does not exist in native MOC: {anchor["drawable"]}')
            if 'parameter' in anchor:
                parameter(anchor['parameter'], 'pose contact anchor')
    count = len(model['FileReferences'].get('Textures', []))
    if not count or any(index < 0 or index >= count for index in native['textureIndices']):
        raise ValueError('Native MOC texture indices do not match exported atlas inventory')
    from PIL import Image
    for name in model['FileReferences']['Textures']:
        texture = resources[safe_name(name)]
        with Image.open(texture) as atlas:
            if atlas.format not in {'PNG', 'JPEG'}:
                raise ValueError(f'Expected an actual PNG or JPEG atlas: {name}')
            atlas.verify()
        # verify() checks encoding and checksums, not whether the exported
        # image contains any visible texels. Reopen after verify, including
        # palette/tRNS transparency, without rejecting valid RGB/JPEG art.
        with Image.open(texture) as atlas:
            if atlas.format == 'PNG' and ('A' in atlas.getbands() or 'transparency' in atlas.info):
                if atlas.convert('RGBA').getchannel('A').getextrema()[1] == 0:
                    raise ValueError(f'Exported texture is fully transparent (all alpha values are zero): {name}')


def verified_atlas_restore(model: dict, source: Path, native: dict, atlas_audit: Path, native_uv_audit: Path):
    """Restore exact authored pixels only after the actual exported MOC's entire UV set is verified."""
    from PIL import Image
    atlas_audit, native_uv_audit = atlas_audit.resolve(strict=True), native_uv_audit.resolve(strict=True)
    moc = contained_file(source.parent, model['FileReferences']['Moc'])
    count = len(native['drawables'])
    evidence = verify_native_uv_evidence(native_uv_audit, moc, atlas_audit, sha(moc), expected_mesh_count=count,
                                        expected_drawable_ids=set(native['drawables']))
    atlas = read_json(atlas_audit)
    atlas_size, pages = atlas.get('atlasSize'), atlas.get('atlases')
    if (not isinstance(atlas_size, list) or len(atlas_size) != 2
            or not all(isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 16384 for value in atlas_size)
            or not isinstance(pages, list) or not pages or atlas.get('meshCount') != count
            or atlas.get('atlasCount') != len(pages) or len(model['FileReferences']['Textures']) != len(pages)
            or len(set(native['drawables'])) != count or len(native['textureIndices']) != count):
        raise ValueError('Verified packed atlas dimensions, mesh count or page inventory do not match actual MOC')
    if not all(atlas.get(field) is True for field in ('cropPixelsIdentical', 'noRegionCollisions',
            'noRotationOrScaling', 'allUvsValid', 'allReferencesResolve', 'noMoc3Generated')):
        raise ValueError('Packed atlas lacks complete original-pixel/layout provenance')
    regions = {}
    for region in atlas.get('regions', []):
        for name in region.get('drawables', [region.get('name')]):
            if not isinstance(name, str) or name in regions:
                raise ValueError('Packed atlas has duplicate or invalid drawable assignments')
            regions[name] = region
    if set(regions) != set(native['drawables']):
        raise ValueError('Packed atlas must assign every actual native drawable exactly once')
    for name, texture in zip(native['drawables'], native['textureIndices']):
        page = regions[name].get('page')
        if not isinstance(page, int) or isinstance(page, bool) or not 0 <= page < len(pages) or texture != page:
            raise ValueError(f'Actual native texture page differs from packed atlas: {name}')
    restored, rows = {}, []
    for page, (entry, reference) in enumerate(zip(pages, model['FileReferences']['Textures'])):
        reference = safe_name(reference)
        if reference in restored:
            raise ValueError('Multiple texture pages share an installed resource path')
        declared = Path(entry['path'])
        path = declared.resolve(strict=True) if declared.is_absolute() else contained_file(atlas_audit.parent, entry['path'])
        if evidence['inputHashesAtStart'].get(str(path)) != entry.get('sha256') or sha(path) != entry.get('sha256'):
            raise ValueError('Restored atlas page is not the exact source bound by the native UV audit')
        with Image.open(path) as image:
            if image.format != 'PNG' or list(image.size) != atlas_size:
                raise ValueError('Restored atlas page is not a PNG with the verified packed dimensions')
            image.verify()
        editor_texture = contained_file(source.parent, reference)
        comparison = audit_atlas_files(editor_texture, path, sha(editor_texture), entry['sha256'])
        # This comparison intentionally records Editor damage rather than
        # granting it a pass. Installed pixels come from the verified pack.
        rows.append({'textureIndex': page, 'runtimeReference': reference,
                     'originalEditorTexture': {'source': str(editor_texture), 'sha256': comparison['actualSha256']},
                     'installedTexture': {'source': str(path), 'sha256': entry['sha256'], 'size': atlas_size},
                     'editorVsPackedComparison': comparison})
        restored[reference] = path
    recheck_input_hashes(evidence['inputHashesAtStart'])
    return restored, {'mode': 'verified-packed-atlas', 'originalEditorFilesModified': False,
        'reason': 'Restore original authored texture bytes using the actual exported MOC and complete verified native UV coordinates',
        'nativeUvAudit': evidence, 'atlasAuditPath': str(atlas_audit), 'atlasAuditSha256': sha(atlas_audit),
        'sourceCmo3Sha256': atlas['sourceSha256'], 'packedCmo3Sha256': atlas['outputSha256'], 'pages': rows}


def install(source: Path, destination: Path, fragment: Path | None = None,
            *, runtime_source: Path | None = None, atlas_audit: Path | None = None,
            native_uv_audit: Path | None = None) -> dict:
    if (atlas_audit is None) != (native_uv_audit is None):
        raise ValueError('Packed atlas restoration requires both --atlas-audit and --native-uv-audit')
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination == destination.parent or destination == ROOT or destination in ROOT.parents:
        raise ValueError('Destination must be a dedicated model asset directory')
    if source.is_relative_to(destination):
        raise ValueError('Installation destination must not contain the original Editor export')
    if destination.exists() and not destination.is_dir():
        raise ValueError('Destination exists and is not a directory')
    runtime_root = runtime_source.resolve(strict=True) if runtime_source else destination
    fragment = (fragment or (runtime_root.parent/'Maple.model3-fragment.json' if runtime_source else
                            ROOT/'assets/authoring/Maple.model3-fragment.json')).resolve(strict=True)
    settings_checksums = {str(path): sha(path) for path in (source, fragment)}
    model, additions = read_json(source), read_json(fragment)
    if model.get('Version') != 3 or not model.get('FileReferences', {}).get('Moc'):
        raise ValueError('Expected an Editor-exported version 3 model settings file')
    if not model['FileReferences'].get('Textures'):
        raise ValueError('The Editor export has no texture atlas')
    if set(additions.get('FileReferences', {})) & {'Moc', 'Textures'}:
        raise ValueError('Authored fragment may not replace actual Editor MOC3/atlas references')
    if not additions.get('Groups') or not additions.get('CuteMaple'):
        raise ValueError('Authored fragment is missing Groups/CuteMaple')
    exported = {(kind, name): contained_file(source.parent, name) for kind, name in references(model)}
    authored = {(kind, name): contained_file(runtime_root, name) for kind, name in references(additions)}
    source_inputs = {str(path): sha(path) for path in {*exported.values(), *authored.values()}}
    source_inputs.update(settings_checksums)
    moc = contained_file(source.parent, model['FileReferences']['Moc'])
    native = inspect_moc(moc)
    merged = deepcopy(model)
    merged['FileReferences'].update(additions['FileReferences'])
    for key in ('Groups', 'CuteMaple'):
        merged[key] = deepcopy(additions[key])
    resources, names_lower = {}, {}
    for kind, name in references(merged):
        path = authored[(kind, name)] if kind in additions['FileReferences'] or kind == 'Metadata' else exported[(kind, name)]
        folded = name.casefold()
        if folded in {'maple.model3.json', 'editor-export.json'}:
            raise ValueError(f'Resource collides with generated settings/audit: {name}')
        if folded in names_lower and (names_lower[folded] != name or resources[name] != path):
            raise ValueError(f'Conflicting installed resource path: {name}')
        names_lower[folded], resources[name] = name, path
    texture_installation = {'mode': 'editor-export', 'originalEditorFilesModified': False}
    if atlas_audit is not None:
        restored, texture_installation = verified_atlas_restore(model, source, native, atlas_audit, native_uv_audit)
        resources.update(restored)
        source_inputs.update(texture_installation['nativeUvAudit']['inputHashesAtStart'])
        if any(Path(name).resolve().is_relative_to(destination) for name in source_inputs):
            raise ValueError('Restoration destination must not contain any frozen source or UV evidence input')
    validate_native_bindings(merged, resources, native)
    metadata_name = safe_name(merged['CuteMaple']['Metadata'])
    resolved_metadata, anchor_conversions = resolve_material_anchors(read_json(resources[metadata_name]),
        read_json(atlas_audit) if atlas_audit else None, native, texture_installation.get('nativeUvAudit'))
    derived_payloads = {}
    if anchor_conversions:
        derived_payloads[metadata_name] = (json.dumps(resolved_metadata, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
    payloads = {name: {'source': str(path), 'sourceSha256': sha(path)} for name, path in resources.items()}
    for name, data in derived_payloads.items():
        payloads[name].update(origin='verified-native-material-anchors', derivedSha256=hashlib.sha256(data).hexdigest())
    for page in texture_installation.get('pages', []):
        payloads[page['runtimeReference']].update(origin='verified-packed-atlas', originalEditorTexture=page['originalEditorTexture'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f'.{destination.name}.install-{uuid.uuid4().hex}'
    # All rename/remove targets remain siblings of the explicit model folder.
    # Source trees and previous backups are never deleted.
    assert staging.parent == destination.parent and staging.name.startswith(f'.{destination.name}.install-')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = destination.with_name(destination.name + '.backup-' + timestamp + '-' + uuid.uuid4().hex[:8])
    backup_created = False
    switched = False
    # Runtime art is shared with the ordinary desktop user. Python 3.13's
    # mkdtemp uses mode 0700, which installs a private Windows DACL that survives
    # rename. Default mkdir inherits the parent ACL instead. Creation is atomic
    # and must succeed before this invocation owns or may clean up the path.
    staging.mkdir()
    try:
        for name, path in resources.items():
            target = staging/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if name in derived_payloads:
                target.write_bytes(derived_payloads[name])
        (staging/'Maple.model3.json').write_text(json.dumps(merged, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        for path, checksum in source_inputs.items():
            if sha(Path(path)) != checksum:
                raise ValueError(f'Input changed during installation: {path}')
        staged = {name: contained_file(staging, name) for _, name in references(read_json(staging/'Maple.model3.json'))}
        for name, path in staged.items():
            if sha(path) != payloads[name].get('derivedSha256', payloads[name]['sourceSha256']):
                raise ValueError(f'Staged payload differs from source: {name}')
        validate_native_bindings(merged, staged, native)
        files = {name: sha(path) for name, path in staged.items()}
        files['Maple.model3.json'] = sha(staging/'Maple.model3.json')
        audit = {'status': 'editor-export-installed-runtime-visual-verification-pending',
                 'installedAt': datetime.now(timezone.utc).isoformat(), 'editorExport': source.name,
                 'destination': str(destination), 'runtimeSource': str(runtime_root), 'fragment': str(fragment),
                 'backup': str(backup) if destination.exists() else None, 'files': files,
                 'resources': {name: {**payloads[name], 'installedSha256': files[name]} for name in payloads},
                 'sourceInputs': source_inputs, 'motionGroups': len(merged['FileReferences'].get('Motions', {})),
                 'nativeValidation': native, 'noMocGenerated': True, 'stagingValidated': True,
                 'textureInstallation': texture_installation,
                 'materialAnchorConversion': {'conversions': anchor_conversions,
                     'mocSha256': sha(moc), 'nativeUvAuditSha256': (texture_installation.get('nativeUvAudit') or {}).get('sha256'),
                     'sourceMetadataUnchanged': True, 'nativeUvConvention': 'Core U/V; V equals one minus packed PNG V'}}
        (staging/'editor-export.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        if destination.exists():
            assert backup.parent == destination.parent and not backup.exists()
            destination.replace(backup)
            backup_created = True
        try:
            staging.replace(destination)
            switched = True
        except OSError:
            if backup_created and backup.exists() and not destination.exists():
                backup.replace(destination)
            raise
    finally:
        if not switched and staging.exists():
            assert staging.parent == destination.parent and staging.name.startswith(f'.{destination.name}.install-')
            assert staging.resolve().parent == destination.parent
            shutil.rmtree(staging)
    # Isolated/runtime-source installs own only their destination receipt.
    # Preserve the original default CLI's legacy receipt without a shared temp name.
    if runtime_source is None and atlas_audit is None and destination == ROOT/'assets/live2d/Maple':
        legacy_audit = ROOT/'assets/authoring/editor-export.json'
        legacy_audit.parent.mkdir(parents=True, exist_ok=True)
        temporary = legacy_audit.with_name('.editor-export-'+uuid.uuid4().hex+'.json.tmp')
        temporary.write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temporary.replace(legacy_audit)
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path, default=ROOT/'assets/authoring/Maple.model3.json')
    parser.add_argument('--destination', type=Path, default=ROOT/'assets/live2d/Maple')
    parser.add_argument('--fragment', type=Path)
    parser.add_argument('--runtime-source', type=Path, help='Matching authored runtime folder; defaults to existing destination for legacy calls')
    parser.add_argument('--atlas-audit', type=Path, help='Restore exact packed atlas bytes; requires matching complete --native-uv-audit')
    parser.add_argument('--native-uv-audit', type=Path, help='Frozen full native UV evidence for this actual exported MOC3')
    args = parser.parse_args()
    print(json.dumps(install(args.source, args.destination, args.fragment, runtime_source=args.runtime_source,
                             atlas_audit=args.atlas_audit, native_uv_audit=args.native_uv_audit), ensure_ascii=False, indent=2))
