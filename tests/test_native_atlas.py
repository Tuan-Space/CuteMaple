"""Atlas conversion preserves source artwork and all animation/mesh structure."""
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/authoring'))
from pack_native_atlas import field, identities, intersects, merge_prefix, run, shelf_pack, validate_type_declarations
from native_caff import read_project
from image2live2d.backends.live2d.cmo3 import pack_caff


def test_shelf_layout_is_deterministic_unrotated_and_padded_across_pages():
    rectangles = [('b', 30, 20), ('a', 30, 30), ('c', 40, 20), ('d', 40, 20)]
    result = shelf_pack(rectangles, size=64, padding=4)
    assert result == shelf_pack(reversed(rectangles), size=64, padding=4)
    assert len({item.page for item in result}) > 1
    for item in result:
        assert (item.width, item.height) == dict((name, (w, h)) for name, w, h in rectangles)[item.name]
        bounds = item.padded(4)
        assert min(bounds) >= 0 and max(bounds) <= 64
        assert not any(item != other and item.page == other.page and intersects(bounds, other.padded(4)) for other in result)
    with pytest.raises(ValueError, match='larger'):
        shelf_pack([('oversize', 64, 10)], size=64, padding=4)


def test_prefix_adds_only_missing_types_without_upgrading_source_versions():
    source = b'<?xml version="1.0"?>\n<?version CArtMeshSource:4?>\n<?import old.CArtMeshSource?>\n'
    template = b'<?version CArtMeshSource:5?>\n<?version CTextureAtlas:2?>\n<?import native.CTextureAtlas?>\n<?import irrelevant.Type?>\n'
    merged = merge_prefix(source, template, {'CArtMeshSource', 'CTextureAtlas'})
    assert merged.startswith(source)
    assert b'CArtMeshSource:5' not in merged
    assert b'CTextureAtlas:2' in merged and b'native.CTextureAtlas' in merged
    assert b'irrelevant' not in merged
    assert merge_prefix(merged, template, {'CArtMeshSource', 'CTextureAtlas'}) == merged


def test_xml_validation_rejects_duplicate_ids_indices_and_missing_references():
    for xml in ('<root><X xs.id="#1"/><X xs.id="#1"/></root>',
                '<root><X xs.idx="1"/><X xs.idx="1"/></root>',
                '<root><X xs.ref="#2"/></root>'):
        with pytest.raises(ValueError):
            identities(ET.fromstring(xml))


def test_native_inner_class_import_is_required_for_atlas_model_image_entries():
    version=b'<?version com.live2d.cubism.doc.model.texture.textureAtlas.ModelImageEntry:2?>\n'
    declaration=b'<?import com.live2d.cubism.doc.model.texture.textureAtlas.CTextureAtlas$ModelImageEntry?>\n'
    template=version+declaration
    with pytest.raises(ValueError,match="ModelImageEntry"):
        validate_type_declarations(version,template,{'ModelImageEntry','f','carray_list'})
    merged=merge_prefix(version,template,{'ModelImageEntry'})
    assert declaration.strip() in merged
    assert merged.count(declaration.strip())==1
    assert merge_prefix(merged,template,{'ModelImageEntry'})==merged
    assert validate_type_declarations(merged,template,{'ModelImageEntry','f','carray_list'})['importDeclarationsVerified']
    with pytest.raises(ValueError,match='versions='):
        validate_type_declarations(declaration,template,{'ModelImageEntry'})


@pytest.fixture(scope='module')
def packed(tmp_path_factory):
    directory = tmp_path_factory.mktemp('native-atlas')
    source = directory / 'source.cmo3'
    # Snapshot the editable fixture so parallel authoring cannot change this test.
    source.write_bytes((ROOT / 'assets/authoring/model/Maple.cmo3').read_bytes())
    template = ROOT / 'assets/authoring/model/Maple.cmo3'
    output = directory / 'packed.cmo3'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    audit = run(source, template, output)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    return source, template, output, audit


def test_native_fixture_preserves_original_payloads_and_exact_rgba_crops(packed):
    source, _, output, audit = packed
    original = {entry.path: entry for entry in read_project(source)}
    converted = {entry.path: entry for entry in read_project(output)}
    assert audit['unrelatedXmlStructurePreserved'] and audit['caffPayloadAndMetadataRoundtrip']
    assert audit['meshCount'] >= 75 and audit['noMoc3Generated']
    assert audit['typeDeclarations']['importDeclarationsVerified']
    assert audit['typeDeclarations']['nativeVersionDeclarationsVerified']
    assert audit['nativeUvRebuildFromStaticPositionsVerified']
    assert audit['maximumNativeUvRebuildPixelError'] <= .001
    for name, entry in original.items():
        got = converted[name]
        assert (got.tag, got.obfuscated, got.compress) == (entry.tag, entry.obfuscated, entry.compress)
        if name != 'main.xml':
            assert got.content == entry.content
    atlas_images = [Image.open(io.BytesIO(converted[item['entry']].content)).convert('RGBA') for item in audit['atlases']]
    for region in audit['regions']:
        original_image = Image.open(io.BytesIO(original[region['sourceImage']].content)).convert('RGBA')
        assert region['sourceSize'] == list(original_image.size) and region['drawables']
        x, y, width, height = region['destination']
        actual = atlas_images[region['page']].crop((x, y, x+width, y+height))
        assert np.array_equal(np.asarray(actual), np.asarray(original_image.crop(region['crop'])))
    tree = ET.fromstring(converted['main.xml'].content)
    ids = identities(tree)
    assert field(tree, 'isTextureInputModelImageMode').text == 'false'
    for extension in (node for node in tree.iter('CTextureInputExtension') if node.get('xs.id')):
        inputs = field(extension, '_textureInputs')
        assert len(inputs) == 2 and inputs[0].tag == 'CTextureInput_ModelImage'
        current = field(extension, 'currentTextureInputData')
        assert current.tag == 'CTextureInput_TextureAtlasRegion'
        assert field(ids[current.get('xs.ref')], '_owner').get('xs.ref') == extension.get('xs.id')


def test_conversion_refuses_overwriting_source_or_previous_output(packed):
    source, template, output, _ = packed
    for target in (source, template, output):
        with pytest.raises(ValueError, match='new, separate'):
            run(source, template, target)


def test_packed_mesh_render_texture_state_and_resource_match_native_atlas_input(packed):
    _,template,output,audit=packed
    converted={entry.path:entry.content for entry in read_project(output)}
    root=ET.fromstring(converted['main.xml']);ids=identities(root)
    native=ET.fromstring(next(e.content for e in read_project(template) if e.path=='main.xml'))
    native_mesh=next(n for n in native.iter('CArtMeshSource') if n.get('xs.id'))
    atlas_by_guid={field(n,'guid').get('xs.ref'):n for n in root.iter('CTextureAtlas') if n.get('xs.id')}
    meshes=[m for m in root.iter('CArtMeshSource') if m.get('xs.id')]
    assert len(meshes)==audit['meshCount']
    checked_resources=set()
    for mesh in meshes:
        assert field(mesh,'textureState').get('v')==field(native_mesh,'textureState').get('v')=='TEXTURE_ATLAS'
        extension=ids[next(mesh.iter('CTextureInputExtension')).get('xs.ref')]
        region=ids[field(extension,'currentTextureInputData').get('xs.ref')]
        atlas=atlas_by_guid[field(region,'textureAtlasGuid').get('xs.ref')]
        resource_ref=field(atlas,'cachedAtlasImage').get('xs.ref')
        texture=ids[field(mesh,'texture').get('xs.ref')]
        assert field(texture,'srcImageResource').get('xs.ref')==resource_ref
        assert field(texture,'owner').get('xs.ref')==texture.get('xs.id')
        resource=ids[resource_ref]
        assert (int(resource.get('width')),int(resource.get('height')))==tuple(audit['atlasSize'])
        if resource_ref not in checked_resources:
            with Image.open(io.BytesIO(converted[field(resource,'imageFileBuf').get('path')])) as png:
                assert png.size==tuple(audit['atlasSize']) and png.getchannel('A').getbbox() is not None
            checked_resources.add(resource_ref)


def test_nonidentity_material_transform_is_rejected_without_output(packed, tmp_path):
    source, template, _, _ = packed
    entries = read_project(source)
    main = next(entry for entry in entries if entry.path == 'main.xml')
    tree = ET.fromstring(main.content)
    field(tree, '_materialLocalToCanvasTransform').set('m02', '1.0')
    changed = main.content.split(b'<root', 1)[0] + ET.tostring(tree)
    bad = tmp_path / 'translated.cmo3'
    bad.write_bytes(pack_caff([replace(entry, content=changed) if entry.path == 'main.xml' else entry for entry in entries], key=42))
    output = tmp_path / 'must-not-exist.cmo3'
    with pytest.raises(ValueError, match='Nonidentity material'):
        run(bad, template, output)
    assert not output.exists()


def test_source_geometry_cannot_silently_reproject_texture_uvs(packed, tmp_path):
    source,template,_,_=packed
    entries=read_project(source)
    main=next(entry for entry in entries if entry.path=='main.xml')
    tree=ET.fromstring(main.content)
    mesh=next(node for node in tree.iter('CArtMeshSource') if node.get('xs.id'))
    static=next(child for child in mesh if child.get('xs.n')=='positions')
    values=np.fromstring(static.text,sep=' ')
    values[0]+=10  # UVs and every animated form are deliberately unchanged.
    static.text=' '.join(map(str,values))
    bad=tmp_path/'wrong-static-texture-point.cmo3'
    changed=main.content.split(b'<root',1)[0]+ET.tostring(tree)
    bad.write_bytes(pack_caff([replace(entry,content=changed) if entry.path=='main.xml' else entry
                              for entry in entries],key=42))
    output=tmp_path/'must-not-pack.cmo3'
    with pytest.raises(ValueError,match='texture-input UV rebuild differs'):
        run(bad,template,output)
    assert not output.exists()
