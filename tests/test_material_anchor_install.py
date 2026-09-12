from copy import deepcopy

import pytest

from tools.authoring.install_editor_export import resolve_material_anchors


def fixture():
    source = {'drawable': 'palm', 'sourceUv': [.45, .55]}
    metadata = {'anchors': {'legacy': {'drawable': 'palm', 'edge': 'right'}},
                'states': {'climb_right': {'anchors': {'right': source}}}}
    atlas = {'atlasSize': [2048, 1024], 'regions': [{'name': 'source-palm', 'drawables': ['palm'],
        'sourceSize': [1000, 1000], 'translationToCanvas': [400, 500],
        'crop': [420, 520, 480, 580], 'page': 0}]}
    native = {'drawables': ['palm'], 'textureIndices': [0]}
    return metadata, atlas, native, {'verified': True}


def test_source_material_is_resolved_to_native_core_v_and_does_not_mutate_source():
    metadata, atlas, native, evidence = fixture()
    original = deepcopy(metadata)
    result, conversions = resolve_material_anchors(metadata, atlas, native, evidence)
    assert metadata == original
    assert result['states']['climb_right']['anchors']['right'] == {'drawable': 'palm', 'atlasUv': [50/2048, 1-50/1024]}
    assert result['anchors'] == original['anchors']
    assert conversions[0]['source']['sourceUv'] == [.45, .55]


@pytest.mark.parametrize('evidence', [None, {}, {'verified': False}])
def test_source_material_requires_complete_verified_evidence(evidence):
    metadata, atlas, native, _ = fixture()
    with pytest.raises(ValueError, match='complete verified'):
        resolve_material_anchors(metadata, atlas, native, evidence)


@pytest.mark.parametrize('point', [[float('nan'), .55], [.9, .55], [.45], [True, .55]])
def test_invalid_or_outside_crop_material_never_becomes_a_faked_runtime_point(point):
    metadata, atlas, native, evidence = fixture()
    metadata['states']['climb_right']['anchors']['right']['sourceUv'] = point
    with pytest.raises(ValueError):
        resolve_material_anchors(metadata, atlas, native, evidence)


def test_material_page_and_descriptor_ambiguity_rejected():
    metadata, atlas, native, evidence = fixture()
    native['textureIndices'] = [1]
    with pytest.raises(ValueError, match='page differs'):
        resolve_material_anchors(metadata, atlas, native, evidence)
    native['textureIndices'] = [0]
    metadata['states']['climb_right']['anchors']['right']['edge'] = 'right'
    with pytest.raises(ValueError, match='ambiguous'):
        resolve_material_anchors(metadata, atlas, native, evidence)


def test_original_edge_metadata_requires_no_new_proof_or_rewrite():
    metadata = {'anchors': {'left': {'drawable': 'palm', 'edge': 'left'}}}
    result, conversions = resolve_material_anchors(metadata, None, {}, None)
    assert result == metadata
    assert conversions == []
