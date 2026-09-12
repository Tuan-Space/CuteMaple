"""Coordinate compatibility is independent of raw pixels and mesh UV correctness."""
from copy import deepcopy
import json

from PIL import Image
import pytest

from tools.qa_model_inputs import (atlas_coordinate_mapping_audit, audit_atlas_files,
                                   digest, recheck_input_hashes, verify_native_uv_evidence)


def images():
    source = Image.new('RGBA', (4, 1))
    # The alpha-8 pair is a measured Editor export pixel (466,315). Other
    # partial-alpha values exercise actual 8-bit premultiply/unpremultiply.
    source.putdata([(14, 60, 150, 255), (100, 150, 200, 128), (255, 255, 255, 8), (255, 0, 255, 0)])
    actual = source.copy()
    actual.putdata([(14, 60, 150, 255), (100, 149, 199, 128), (57, 158, 232, 8), (0, 0, 0, 0)])
    return actual, source


def test_measured_editor_edge_and_premultiplied_roundtrip_verify_coordinates_not_raw_pixels():
    actual, source = images()
    result = atlas_coordinate_mapping_audit(actual, source)
    assert result['coordinateMappingVerified']
    assert result['alphaIdentical'] and result['opaqueRGBIdentical'] and result['premulAbove8Identical']
    assert not result['rawPixelsIdentical']
    assert result['counts']['lowAlphaPremulChangedPixels'] == 1
    assert result['lowAlphaMaxContribution8Bit'] == 6
    assert result['lowAlphaMaxContributionNormalized'] == pytest.approx(6/255)
    assert result['visualReview'] == 'pending-human-review'


def test_exact_and_hidden_rgb_cases_remain_distinct():
    _, source = images()
    assert atlas_coordinate_mapping_audit(source, source)['rawPixelsIdentical']
    actual = source.copy()
    actual.putpixel((3, 0), (0, 0, 0, 0))
    result = atlas_coordinate_mapping_audit(actual, source)
    assert result['coordinateMappingVerified'] and not result['rawPixelsIdentical']


@pytest.mark.parametrize('index,pixel,flag', [
    (2, (255, 255, 255, 7), 'alphaIdentical'),
    (0, (15, 60, 150, 255), 'opaqueRGBIdentical'),
    (1, (0, 150, 200, 128), 'premulAbove8Identical'),
])
def test_alpha_opaque_or_body_premultiplication_changes_are_rejected(index, pixel, flag):
    actual, source = images()
    actual.putpixel((index, 0), pixel)
    result = atlas_coordinate_mapping_audit(actual, source)
    assert not result['coordinateMappingVerified'] and not result[flag]


def test_alpha_nine_has_no_edge_color_exception():
    actual, source = images()
    source.putpixel((2, 0), (255, 255, 255, 9))
    actual.putpixel((2, 0), (57, 158, 232, 9))
    result = atlas_coordinate_mapping_audit(actual, source)
    assert result['alphaIdentical'] and not result['premulAbove8Identical']
    assert not result['coordinateMappingVerified']


def test_empty_or_resized_images_do_not_establish_coordinate_registration():
    actual, source = images()
    assert not atlas_coordinate_mapping_audit(actual.resize((8, 2)), source)['coordinateMappingVerified']
    empty = Image.new('RGBA', (4, 1))
    assert not atlas_coordinate_mapping_audit(empty, empty)['coordinateMappingVerified']


def test_file_comparison_binds_the_exact_decoded_bytes(tmp_path):
    actual, source = images()
    a, b = tmp_path/'actual.png', tmp_path/'source.png'
    actual.save(a)
    source.save(b)
    result = audit_atlas_files(a, b, digest(a), digest(b))
    assert result['coordinateMappingVerified'] and result['actualSha256'] == digest(a)
    with pytest.raises(ValueError, match='SHA256'):
        audit_atlas_files(a, b, digest(b), digest(b))


def evidence(tmp_path, mesh_count=134):
    """Synthetic evidence validates the gate; it is never called native QA."""
    paths = {name: tmp_path/name for name in ('model.moc3', 'source.cmo3', 'packed.cmo3', 'atlas.png', 'core.js')}
    for name, path in paths.items():
        path.write_bytes(('unit fixture '+name).encode())
    atlas = {'sourceSha256': digest(paths['source.cmo3']), 'outputSha256': digest(paths['packed.cmo3']),
             'atlases': [{'sha256': digest(paths['atlas.png'])}]}
    atlas_path = tmp_path/'atlas-audit.json'
    atlas_path.write_text(json.dumps(atlas))
    core_hashes = {str(paths[name]): digest(paths[name]) for name in ('model.moc3', 'core.js')}
    geometry = {'tool': 'official-cubism-native-geometry-audit', 'passed': True, 'errors': [], 'sourcesUnchanged': True,
                'sourceSha256': digest(paths['model.moc3']), 'sourceHashesAtStart': core_hashes,
                'sourceHashesAtEnd': core_hashes, 'drawables': [{'id': f'mesh_{i}'} for i in range(mesh_count)]}
    geometry_path = tmp_path/'geometry.json'
    geometry_path.write_text(json.dumps(geometry))
    hashes = {str(path): digest(path) for path in [*paths.values(), atlas_path, geometry_path]}
    report = {'tool': 'native-uv-pairing-audit', 'schemaVersion': 1, 'passed': True, 'errors': [],
              'sourcesUnchanged': True, 'sourceHashesAtStart': hashes, 'sourceHashesAtEnd': dict(hashes),
              'nativeMoc': str(paths['model.moc3']), 'meshCount': mesh_count, 'matchedMeshCount': mesh_count, 'pixelTolerance': .001,
              'meshes': [{'id': f'mesh_{i}', 'passed': True, 'errors': [], 'sameTriangleTopologyIgnoringOrder': True,
                          'maximumAtlasPixelError': .0007} for i in range(mesh_count)]}
    audit_path = tmp_path/'uv.json'
    audit_path.write_text(json.dumps(report))
    return audit_path, paths['model.moc3'], atlas_path, report


@pytest.mark.parametrize('mesh_count', [134, 141])
def test_native_uv_revision_count_cannot_be_substituted_by_another_revision(tmp_path, mesh_count):
    path, moc, atlas, report = evidence(tmp_path, mesh_count)
    assert verify_native_uv_evidence(path, moc, atlas, digest(moc), expected_mesh_count=mesh_count)['meshCount'] == mesh_count
    with pytest.raises(ValueError, match='exactly'):
        verify_native_uv_evidence(path, moc, atlas, digest(moc), expected_mesh_count=141 if mesh_count == 134 else 134)
    report['meshes'][0] = None
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='mesh records'):
        verify_native_uv_evidence(path, moc, atlas, digest(moc), expected_mesh_count=mesh_count)


def test_complete_native_uv_summary_rechecks_all_inputs_and_is_bound_to_current_moc(tmp_path):
    path, moc, atlas, report = evidence(tmp_path)
    result = verify_native_uv_evidence(path, moc, atlas, digest(moc))
    assert result['verified'] and result['meshCount'] == result['matchedMeshCount'] == 134
    assert result['sha256'] == digest(path) and result['atlasAuditPath'] == str(atlas)
    assert result['inputHashesAtStart'] == result['inputHashesAtEnd']
    assert result['inputHashesAtStart'][str(path)] == digest(path)
    (tmp_path/'core.js').write_bytes(b'changed')
    with pytest.raises(ValueError, match='SHA256'):
        recheck_input_hashes(result['inputHashesAtStart'])


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(passed=False),
    lambda r: r.update(matchedMeshCount=130),
    lambda r: r.update(sourcesUnchanged=False),
    lambda r: r.update(pixelTolerance=.01),
    lambda r: r['meshes'][0].update(id='mesh_1'),
    lambda r: r['meshes'][0].update(maximumAtlasPixelError=float('nan')),
    lambda r: r['meshes'][0].update(sameTriangleTopologyIgnoringOrder=False),
    lambda r: r['meshes'][0].update(passed=False),
    lambda r: r['sourceHashesAtEnd'].clear(),
])
def test_failed_missing_or_inconsistent_uv_evidence_cannot_pass(tmp_path, mutate):
    path, moc, atlas, report = evidence(tmp_path)
    mutate(report)
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        verify_native_uv_evidence(path, moc, atlas, digest(moc))


def test_replaced_moc_and_omitted_bound_authoring_source_are_rejected(tmp_path):
    path, moc, atlas, report = evidence(tmp_path)
    with pytest.raises(ValueError, match='different loaded MOC3'):
        verify_native_uv_evidence(path, moc, atlas, '0'*64)
    for field in ('sourceHashesAtStart', 'sourceHashesAtEnd'):
        report[field].pop(str(tmp_path/'source.cmo3'))
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='omits an authored source'):
        verify_native_uv_evidence(path, moc, atlas, digest(moc))


def test_refined_uv_requires_the_exact_drawable_set_in_addition_to_count(tmp_path):
    path, moc, atlas, report = evidence(tmp_path, 143)
    names = {mesh['id'] for mesh in report['meshes']}
    with pytest.raises(ValueError, match='Unsupported'):
        verify_native_uv_evidence(path, moc, atlas, digest(moc), expected_mesh_count=143)
    assert verify_native_uv_evidence(path, moc, atlas, digest(moc), expected_mesh_count=143,
                                    expected_drawable_ids=names)['verified']
    substituted = (names - {'mesh_0'}) | {'different_drawable'}
    with pytest.raises(ValueError, match='drawable identity'):
        verify_native_uv_evidence(path, moc, atlas, digest(moc), expected_mesh_count=143,
                                  expected_drawable_ids=substituted)
