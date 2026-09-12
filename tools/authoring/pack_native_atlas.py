"""Pack ModelImage inputs into native Editor atlases in a NEW editable CMO3.

No MOC compiler is used. Atlas/region schemas are cloned from a real Editor
project; Editor open/export and visual inspection remain mandatory afterwards.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import io
import json
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from native_caff import read_project
from image2live2d.backends.live2d.cmo3 import pack_caff

ROOT = Path(__file__).resolve().parents[2]
SIZE = 4096
PADDING = 16


def field(node, name):
    return next(child for child in node.iter() if child.get('xs.n') == name)


def identities(root):
    result, indices = {}, set()
    for node in root.iter():
        key, index = node.get('xs.id'), node.get('xs.idx')
        if key:
            if key in result:
                raise ValueError(f'Duplicate XML object ID: {key}')
            result[key] = node
        if index:
            if index in indices:
                raise ValueError(f'Duplicate XML index: {index}')
            indices.add(index)
    missing = {node.get('xs.ref') for node in root.iter() if node.get('xs.ref')} - result.keys()
    if missing:
        raise ValueError(f'Unresolved XML references: {sorted(missing)[:5]}')
    return result


def resolve(node, ids):
    return ids[node.get('xs.ref')] if node.get('xs.ref') else node


def affine(node):
    return tuple(float(node.get(name)) for name in ('m00', 'm01', 'm02', 'm10', 'm11', 'm12'))


def set_affine(node, tx=0, ty=0):
    node.attrib.update(m00='1.0', m01='0.0', m02=str(float(tx)),
                       m10='0.0', m11='1.0', m12=str(float(ty)))


def require_identity(node, label):
    if not np.allclose(affine(node), (1, 0, 0, 0, 1, 0), rtol=0, atol=1e-9):
        raise ValueError(f'Nonidentity {label}; resampling/rotating source inputs is not supported')


def encode_png(image):
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


@dataclass(frozen=True)
class Placement:
    name: str
    page: int
    x: int
    y: int
    width: int
    height: int

    def padded(self, padding=PADDING):
        return (self.x-padding, self.y-padding, self.x+self.width+padding, self.y+self.height+padding)


def intersects(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def shelf_pack(rectangles, *, size=SIZE, padding=PADDING):
    """Deterministic height-first, unrotated shelves; allocate another page if needed."""
    if size <= 0 or padding < 0:
        raise ValueError('Invalid atlas size/padding')
    placements = []
    page = x = y = shelf_height = 0
    names = set()
    for name, width, height in sorted(rectangles, key=lambda item: (-item[2], -item[1], item[0])):
        if name in names or width <= 0 or height <= 0:
            raise ValueError('Duplicate region name or empty crop')
        names.add(name)
        padded_width, padded_height = width + 2*padding, height + 2*padding
        if padded_width > size or padded_height > size:
            raise ValueError(f'Region is larger than the atlas: {name}')
        if x + padded_width > size:
            x, y, shelf_height = 0, y+shelf_height, 0
        if y + padded_height > size:
            page, x, y, shelf_height = page+1, 0, 0, 0
        placement = Placement(name, page, x+padding, y+padding, width, height)
        if any(old.page == page and intersects(old.padded(padding), placement.padded(padding)) for old in placements):
            raise ValueError('Atlas shelf collision')
        placements.append(placement)
        x += padded_width
        shelf_height = max(shelf_height, padded_height)
    return placements


def merge_prefix(source, template, tags):
    """Add missing declarations for cloned types, preserving every source version."""
    pattern = rb'<\?(version|import)\s+([^?]+)\?>'
    def identity(kind, value):
        return kind, value.split(b':', 1)[0] if kind == b'version' else value
    found = {identity(kind, value.strip()) for kind, value in re.findall(pattern, source)}
    additions = []
    for match in re.finditer(pattern, template):
        kind, value = match.group(1), match.group(2).strip()
        key = identity(kind, value)
        # Java nested classes use '$' in the import path, while Cubism's
        # serialized element uses the inner class's simple name. Without
        # this mapping Editor logs "Class not found: ModelImageEntry" and
        # discards every atlas layout entry when rebuilding its cache.
        class_name = value.split(b':', 1)[0].split(b'.')[-1].split(b'$')[-1].decode()
        if key not in found and class_name in tags:
            additions.append(match.group(0))
            found.add(key)
    return source + (b'\n'.join(additions) + b'\n' if additions else b'')


def validate_type_declarations(prefix, template, tags, *, source_version_defaults=frozenset()):
    """All native object tags need imports; known versioned types need versions.

    Cubism scalar/collection primitives have lowercase tags and are built in.
    Preserve source version numbers instead of upgrading them to the template.
    """
    def simple(value):
        return value.split(b'.')[-1].split(b'$')[-1].decode()
    imports={simple(value.strip()) for value in re.findall(rb'<\?import\s+([^?]+)\?>',prefix)}
    versions={simple(value.strip()) for value in re.findall(rb'<\?version\s+([^:]+):[^?]+\?>',prefix)}
    native_versions={simple(value.strip()) for value in re.findall(rb'<\?version\s+([^:]+):[^?]+\?>',template)}
    missing_imports={tag for tag in tags if tag[:1].isupper()}-imports
    inherited_defaults=((tags & native_versions)-versions) & source_version_defaults
    missing_versions=(tags & native_versions)-versions-source_version_defaults
    if missing_imports or missing_versions:
        raise ValueError(f'Missing native type declarations: imports={sorted(missing_imports)}, versions={sorted(missing_versions)}')
    return {'nativeObjectTags':len({tag for tag in tags if tag[:1].isupper()}),
            'importDeclarationsVerified':True,'nativeVersionDeclarationsVerified':True,
            'inheritedSourceVersionDefaults':sorted(inherited_defaults)}


def run(source: Path, template: Path, output: Path, *, size=SIZE, padding=PADDING):
    source, template, output = source.resolve(strict=True), template.resolve(strict=True), output.resolve()
    if source == output or template == output or output.exists():
        raise ValueError('Output must be a new, separate project path')
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    template_hash = hashlib.sha256(template.read_bytes()).hexdigest()
    original_entries, template_entries = read_project(source), read_project(template)
    files = {entry.path: entry.content for entry in original_entries}
    template_files = {entry.path: entry.content for entry in template_entries}
    if len(files) != len(original_entries) or len(template_files) != len(template_entries):
        raise ValueError('Duplicate CAFF entry path')
    root, native = ET.fromstring(files['main.xml']), ET.fromstring(template_files['main.xml'])
    ids, native_ids = identities(root), identities(native)
    original_structure = ET.tostring(root)
    original_objects = {key: ET.tostring(node) for key, node in ids.items()}
    shared = root.find('shared')
    if shared is None:
        raise ValueError('Missing XML shared-object collection')
    atlas_list = field(root, '_textureAtlases')
    mode = field(root, 'isTextureInputModelImageMode')
    if len(atlas_list) or atlas_list.get('count') != '0' or mode.text != 'true':
        raise ValueError('Source must use ModelImage inputs and contain no atlases')
    old_atlas_list, old_mode = deepcopy(atlas_list), deepcopy(mode)
    atlas_template = next(node for node in native.iter('CTextureAtlas') if node.get('xs.id'))
    entry_template = next(atlas_template.iter('ModelImageEntry'))
    region_template = next(node for node in native.iter('CTextureInput_TextureAtlasRegion') if node.get('xs.id'))
    resource_template = resolve(field(atlas_template, 'cachedAtlasImage'), native_ids)
    texture_template = next(node for node in native.iter('GTexture2D') if node.get('xs.id') and
                            field(node,'srcImageResource').get('xs.ref')==resource_template.get('xs.id'))
    guid_template = resolve(field(atlas_template, 'guid'), native_ids)
    resource_entry = next(entry for entry in template_entries if entry.path == field(resource_template, 'imageFileBuf').get('path'))
    model_images = {field(node, 'guid').get('xs.ref'): node for node in root.iter('CModelImage') if len(node)}
    groups, meshes, extensions, old_uvs, old_texture_fields = {}, [], {}, {}, {}
    for mesh in root.iter('CArtMeshSource'):
        if not mesh.get('xs.id'):
            continue
        name = field(mesh, 'localName').text
        extension = resolve(next(mesh.iter('CTextureInputExtension')), ids)
        original_input = resolve(field(extension, 'currentTextureInputData'), ids)
        if original_input.tag != 'CTextureInput_ModelImage':
            raise ValueError(f'Mesh is not using a ModelImage input: {name}')
        require_identity(field(original_input, 'optionalTransformOnCanvas'), f'input transform for {name}')
        guid = field(original_input, '_modelImageGuid').get('xs.ref')
        model_image = model_images[guid]
        require_identity(field(model_image, '_materialLocalToCanvasTransform'), f'material transform for {name}')
        resource = resolve(field(model_image, '_filteredImage'), ids)
        image_path = field(resource, 'imageFileBuf').get('path')
        uv_node = next(child for child in mesh if child.get('xs.n') == 'uvs')
        values = np.fromstring(uv_node.text or '', sep=' ')
        if len(values) != int(uv_node.get('count')) or len(values) % 2 or len(values) < 6 or not np.isfinite(values).all():
            raise ValueError(f'Invalid original mesh UVs: {name}')
        uv = values.reshape(-1, 2)
        if np.any(uv < -1e-8) or np.any(uv > 1+1e-8):
            raise ValueError(f'Original mesh UVs outside the source image: {name}')
        if guid not in groups:
            with Image.open(io.BytesIO(files[image_path])) as raw_image:
                pixels = raw_image.convert('RGBA')
            if (pixels.width, pixels.height) != (int(resource.get('width')), int(resource.get('height'))):
                raise ValueError(f'Image resource dimensions disagree with PNG: {name}')
            groups[guid] = {'name': field(model_image, 'name').text, 'path': image_path, 'pixels': pixels,
                            'min': np.array([np.inf, np.inf]), 'max': np.array([-np.inf, -np.inf])}
        group = groups[guid]
        points = uv * [group['pixels'].width, group['pixels'].height]
        # Editor's updateArtMeshSourceUvs rebuilds UVs from static source
        # positions through the texture-input inverse, even in atlas mode.
        # With the identity source transforms enforced above, these must be
        # the same texture points. Animated keyform coordinates are separate.
        static_node=next(child for child in mesh if child.get('xs.n')=='positions')
        static=np.fromstring(static_node.text or '',sep=' ')
        if static.shape != values.shape or not np.isfinite(static).all():
            raise ValueError(f'Invalid static texture coordinates: {name}')
        rebuild_error=float(np.linalg.norm(static.reshape(-1,2)-points,axis=1).max())
        if rebuild_error > .001:
            raise ValueError(f'Editor texture-input UV rebuild differs from source UVs: {name}: {rebuild_error:.9g} pixels')
        group.setdefault('nativeRebuildErrors',[]).append(rebuild_error)
        group['min'], group['max'] = np.minimum(group['min'], points.min(axis=0)), np.maximum(group['max'], points.max(axis=0))
        if extension.get('xs.id') in extensions:
            raise ValueError('Multiple meshes share a texture extension; explicit conversion is required')
        extensions[extension.get('xs.id')] = deepcopy(extension)
        old_uvs[mesh.get('xs.id')] = deepcopy(uv_node)
        old_texture_fields[mesh.get('xs.id')] = {name:deepcopy(field(mesh,name)) for name in ('texture','textureState')}
        meshes.append((name, mesh, extension, guid, uv_node, points))
    if not meshes:
        raise ValueError('Source contains no mesh texture inputs')
    for group in groups.values():
        left, top = np.floor(group['min'] + 1e-7).astype(int)
        right, bottom = np.ceil(group['max'] - 1e-7).astype(int)
        if left < 0 or top < 0 or right > group['pixels'].width or bottom > group['pixels'].height:
            raise ValueError('Source crop exceeds PNG bounds')
        group['crop'] = (int(left), int(top), int(right), int(bottom))
    placements = shelf_pack([(guid, group['crop'][2]-group['crop'][0], group['crop'][3]-group['crop'][1])
                             for guid, group in groups.items()], size=size, padding=padding)
    placement_by_guid = {placement.name: placement for placement in placements}
    atlas_count = max(placement.page for placement in placements) + 1
    atlas_images = [Image.new('RGBA', (size, size), (0, 0, 0, 0)) for _ in range(atlas_count)]
    for placement in placements:
        group = groups[placement.name]
        crop = group['pixels'].crop(group['crop'])
        atlas_images[placement.page].paste(crop, (placement.x, placement.y))
        if not np.array_equal(np.asarray(crop), np.asarray(atlas_images[placement.page].crop(
                (placement.x, placement.y, placement.x+placement.width, placement.y+placement.height)))):
            raise ValueError('Atlas crop pixels differ from the original source')
    atlas_bytes = [encode_png(image) for image in atlas_images]
    atlas_paths = [f'cutemaple-native-atlas-{page:02d}.png' for page in range(atlas_count)]
    if any(path in files for path in atlas_paths):
        raise ValueError('Generated atlas resource path already exists')
    next_id = max(int(key.removeprefix('#')) for key in ids) + 1
    next_index = max(int(node.get('xs.idx')) for node in root.iter() if node.get('xs.idx')) + 1
    new_objects = []
    def allocate(node):
        nonlocal next_id, next_index
        node.set('xs.id', f'#{next_id}')
        node.set('xs.idx', str(next_index))
        next_id, next_index = next_id+1, next_index+1
        new_objects.append(node)
        return node.get('xs.id')
    pages, page_textures = [], []
    for page in range(atlas_count):
        atlas, resource, atlas_guid = deepcopy(atlas_template), deepcopy(resource_template), deepcopy(guid_template)
        atlas_id, resource_id, guid_id = allocate(atlas), allocate(resource), allocate(atlas_guid)
        remap = {atlas_template.get('xs.id'): atlas_id, resource_template.get('xs.id'): resource_id,
                 guid_template.get('xs.id'): guid_id}
        model_entries = field(atlas, 'modelImages')
        model_entries[:] = []
        model_entries.set('count', '0')
        for node in (atlas, resource, atlas_guid):
            for child in node.iter():
                if child.get('xs.ref'):
                    if child.get('xs.ref') not in remap:
                        raise ValueError(f'Unrecognized atlas-template dependency: {child.attrib}')
                    child.set('xs.ref', remap[child.get('xs.ref')])
        field(atlas, 'name').text = f'Texture{page}'
        field(atlas, 'width').text = field(atlas, 'height').text = str(size)
        for cached_size in atlas.iter('CSize'):
            cached_size.set('width', str(size))
            cached_size.set('height', str(size))
        resource.attrib.update(width=str(size), height=str(size), imageFileBuf_size=str(len(atlas_bytes[page])), previewFileBuf_size='0')
        field(resource, 'imageFileBuf').set('path', atlas_paths[page])
        atlas_guid.set('uuid', str(uuid.uuid5(uuid.NAMESPACE_URL, f'cutemaple-atlas:{source_hash}:{size}:{padding}:{page}')))
        atlas_guid.set('note', f'CuteMaple atlas {page}')
        # The mesh's render texture and texture state must agree with its
        # selected atlas input. Editor additionally rebuilds UVs from static
        # source positions; that independent invariant is checked above.
        texture=deepcopy(texture_template)
        texture_id=allocate(texture)
        texture_remap={texture_template.get('xs.id'):texture_id,
                       resource_template.get('xs.id'):resource_id}
        for child in texture.iter():
            if child.get('xs.ref'):
                if child.get('xs.ref') not in texture_remap:
                    raise ValueError(f'Unrecognized native atlas texture dependency: {child.attrib}')
                child.set('xs.ref',texture_remap[child.get('xs.ref')])
        field(texture,'name').text=f'Texture{page}'
        field(texture,'guid').set('uuid',str(uuid.uuid5(uuid.NAMESPACE_URL,f'cutemaple-atlas-texture:{source_hash}:{size}:{padding}:{page}')))
        page_textures.append(texture_id)
        atlas_list.append(ET.Element('CTextureAtlas', {'xs.ref': atlas_id}))
        pages.append((atlas, atlas_id, guid_id))
    atlas_list.set('count', str(atlas_count))
    mode.text = 'false'
    for guid, group in groups.items():
        placement = placement_by_guid[guid]
        tx, ty = group['crop'][0] - placement.x, group['crop'][1] - placement.y
        group['translation'] = (tx, ty)
        atlas, atlas_id, _ = pages[placement.page]
        entry = deepcopy(entry_template)
        field(entry, 'atlas').set('xs.ref', atlas_id)
        field(entry, 'modelImageGuid').set('xs.ref', guid)
        set_affine(field(entry, 'atlasLocalToCanvasTransform'), tx, ty)
        transform = field(entry, 'materialLocalToAtlasTransform')
        position, scale = field(transform, 'position'), field(transform, 'scale')
        field(position, 'x').text, field(position, 'y').text = str(float(-tx)), str(float(-ty))
        field(scale, 'x').text = field(scale, 'y').text = '1.0'
        field(transform, 'eulerAngle').text = '0.0'
        field(atlas, 'modelImages').append(entry)
    for atlas, _, _ in pages:
        field(atlas, 'modelImages').set('count', str(len(field(atlas, 'modelImages'))))
    for name, mesh, extension, guid, uv_node, points in meshes:
        group, placement = groups[guid], placement_by_guid[guid]
        tx, ty = group['translation']
        new_uv = (points - [tx, ty]) / size
        if not np.isfinite(new_uv).all() or np.any(new_uv < 0) or np.any(new_uv > 1):
            raise ValueError(f'Packed UV outside the atlas: {name}')
        uv_node.text = ' '.join(f'{value:.12g}' for value in new_uv.flat)
        actual = np.fromstring(uv_node.text, sep=' ').reshape(-1, 2)
        if np.max(np.abs(actual*size + [tx, ty] - points)) > 1e-6:
            raise ValueError(f'UV reconstruction mismatch: {name}')
        field(mesh,'textureState').set('v','TEXTURE_ATLAS')
        field(mesh,'texture').set('xs.ref',page_textures[placement.page])
        region = deepcopy(region_template)
        region_id = allocate(region)
        field(region, '_owner').set('xs.ref', extension.get('xs.id'))
        field(region, 'textureAtlasGuid').set('xs.ref', pages[placement.page][2])
        set_affine(field(region, 'optionalTransformOnCanvas'))
        set_affine(field(region, 'inputImageLocalToCanvasTransform'), tx, ty)
        inputs = field(extension, '_textureInputs')
        inputs.append(ET.Element('CTextureInput_TextureAtlasRegion', {'xs.ref': region_id}))
        inputs.set('count', str(len(inputs)))
        current = field(extension, 'currentTextureInputData')
        current.tag = 'CTextureInput_TextureAtlasRegion'
        current.attrib.clear()
        current.attrib.update({'xs.n': 'currentTextureInputData', 'xs.ref': region_id})
        current[:] = []
        current.text = None
    shared.extend(new_objects)
    identities(root)
    # Revert only the explicitly permitted edits in a copy. Every other XML
    # subtree, including all keyforms, GUIDs, topology, masks and draw order, must
    # serialize byte-for-byte identically to the source structure.
    restored = deepcopy(root)
    restored_ids = identities(restored)
    restored_shared = restored.find('shared')
    for node in list(restored_shared):
        if node.get('xs.id') not in ids:
            restored_shared.remove(node)
    for mesh_id, old_uv in old_uvs.items():
        mesh = restored_ids[mesh_id]
        current = next(child for child in mesh if child.get('xs.n') == 'uvs')
        mesh[list(mesh).index(current)] = deepcopy(old_uv)
        for name,original in old_texture_fields[mesh_id].items():
            current=field(mesh,name)
            mesh[list(mesh).index(current)]=deepcopy(original)
    for key, original in extensions.items():
        parent = next(node for node in restored.iter() if restored_ids[key] in list(node))
        parent[list(parent).index(restored_ids[key])] = deepcopy(original)
    for name, original in (('_textureAtlases', old_atlas_list), ('isTextureInputModelImageMode', old_mode)):
        current = field(restored, name)
        parent = next(node for node in restored.iter() if current in list(node))
        parent[list(parent).index(current)] = deepcopy(original)
    if ET.tostring(restored) != original_structure:
        raise ValueError('An unrelated original XML object changed')
    prefix = merge_prefix(files['main.xml'].split(b'<root', 1)[0], template_files['main.xml'].split(b'<root', 1)[0],
                          {node.tag for obj in new_objects for node in obj.iter()})
    declarations = validate_type_declarations(prefix, template_files['main.xml'].split(b'<root',1)[0],
                                             {node.tag for node in root.iter()},
                                             # Preserve these two original writer types' implicit
                                             # versions; atlas conversion does not migrate parts.
                                             source_version_defaults={'CPartForm','CPartSource'})
    modified_xml = prefix + ET.tostring(root, encoding='utf-8')
    entries = [replace(entry, content=modified_xml) if entry.path == 'main.xml' else replace(entry) for entry in original_entries]
    entries.extend(replace(resource_entry, path=path, content=data) for path, data in zip(atlas_paths, atlas_bytes))
    result = pack_caff(entries, key=42)
    external_atlases = [output.with_name(output.stem + f'.atlas-{page:02d}.png') for page in range(atlas_count)]
    audit_path = output.with_suffix('.atlas-audit.json')
    if audit_path.exists() or any(path.exists() for path in external_atlases):
        raise ValueError('Output audit/atlas files already exist')
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash or hashlib.sha256(template.read_bytes()).hexdigest() != template_hash:
        raise ValueError('Source/template changed while packing; retry from a stable authoring revision')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(result)
    saved = read_project(output)
    if [(entry.path, entry.content, entry.tag, entry.obfuscated, entry.compress) for entry in saved] != [
            (entry.path, entry.content, entry.tag, entry.obfuscated, entry.compress) for entry in entries]:
        raise ValueError('CAFF roundtrip changed payloads or entry metadata')
    saved_files = {entry.path: entry.content for entry in saved}
    identities(ET.fromstring(saved_files['main.xml']))
    if any(saved_files[path] != data for path, data in files.items() if path != 'main.xml'):
        raise ValueError('An original image/resource was not preserved completely')
    for path, data in zip(external_atlases, atlas_bytes):
        with path.open('xb') as stream:
            stream.write(data)
    audit = {'status': 'editable-project-packed-native-editor-open-export-still-required',
        'source': str(source), 'sourceSha256': source_hash, 'template': str(template),
        'templateSha256': template_hash, 'output': str(output),
        'outputSha256': hashlib.sha256(result).hexdigest(), 'atlasSize': [size, size], 'padding': padding,
        'atlasCount': atlas_count, 'meshCount': len(meshes), 'modelImageCount': len(groups),
        'originalResourceEntriesPreserved': len(files)-1, 'originalObjectsVerified': len(original_objects),
        'unrelatedXmlStructurePreserved': True, 'allReferencesResolve': True, 'allUvsValid': True,
        'cropPixelsIdentical': True, 'noRegionCollisions': True, 'noRotationOrScaling': True,
        'caffPayloadAndMetadataRoundtrip': True, 'noMoc3Generated': True,
        'typeDeclarations': declarations,
        'meshTextureState':'TEXTURE_ATLAS','meshRenderTexturesUseAtlasResources':True,
        'nativeUvRebuildFromStaticPositionsVerified':True,
        'maximumNativeUvRebuildPixelError':max(max(group['nativeRebuildErrors']) for group in groups.values()),
        'atlases': [{'path': str(path), 'entry': entry, 'sha256': hashlib.sha256(data).hexdigest()}
                    for path, entry, data in zip(external_atlases, atlas_paths, atlas_bytes)],
        'regions': [{'guid': placement.name, 'name': groups[placement.name]['name'],
                     'drawables': [name for name, _, _, guid, _, _ in meshes if guid == placement.name],
                     'sourceSize': list(groups[placement.name]['pixels'].size),
                     'sourceImage': groups[placement.name]['path'], 'crop': groups[placement.name]['crop'],
                     'page': placement.page, 'destination': [placement.x, placement.y, placement.width, placement.height],
                     'translationToCanvas': groups[placement.name]['translation']} for placement in placements]}
    with audit_path.open('x', encoding='utf-8') as stream:
        json.dump(audit, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'assets/authoring/revisions/v4/Maple.cmo3')
    parser.add_argument('--template', type=Path, default=ROOT/'assets/authoring/revisions/v3/Maple.cmo3')
    parser.add_argument('--output', type=Path, default=ROOT/'assets/authoring/revisions/v4/Maple-packed.cmo3')
    args = parser.parse_args()
    report = run(args.source, args.template, args.output)
    print(json.dumps({key: report[key] for key in ('output', 'atlasCount', 'meshCount', 'cropPixelsIdentical', 'noMoc3Generated')}, ensure_ascii=False))
