"""Small structural/numerical tests; never construct the complete V5 rig."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools/authoring'))
import climb_contact_refinement as contact
from climb_refinement import climb_travel, hand_reach, transfer_refinement
from build_rig import MapleBuilder


@pytest.mark.parametrize('name', ['left_l', 'left_r', 'right_l', 'right_r'])
def test_actual_native_keys_pin_shoulder_and_material_palm_with_bounded_affine(name):
    calibration = contact.load_calibration()
    track = calibration['tracks'][name]
    direction = 1 if name.startswith('right') else -1
    for key in track['keys']:
        transform = contact.contact_transform(track, key['phase'], direction, calibration)
        matrix, _ = transform
        np.testing.assert_allclose(contact.affine_point(key['shoulder'], transform), key['shoulder'], atol=1e-12)
        target = [.719 if direction > 0 else .297,
            track['keys'][0]['palm'][1]+.12*(climb_travel(key['phase'])-hand_reach(key['phase'], track['role'] == 'near'))]
        np.testing.assert_allclose(contact.affine_point(key['palm'], transform), target, atol=1e-12)
        singular = np.linalg.svd(matrix)[1]
        assert .9 < min(singular) <= max(singular) < 1.12


def test_affine_commutes_with_nested_native_lattice_samples_and_keeps_cuffs_shared():
    calibration = contact.load_calibration()
    track = calibration['tracks']['right_r']
    transform = contact.contact_transform(track, .4, 1, calibration)
    grid = np.random.default_rng(921).random((9, 2))
    outer = np.random.default_rng(922).dirichlet(np.ones(9), size=4)
    inner = np.array([.1, .2, .4, .3])
    transformed = np.asarray([contact.affine_point(point, transform) for point in grid])
    flattened = inner@outer@transformed
    pointwise = contact.affine_point(inner@outer@grid, transform)
    np.testing.assert_allclose(flattened, pointwise, atol=1e-12)


def test_minimum_change_basis_does_not_use_ill_conditioned_cuff_palm_triangle():
    shoulder, palm = (.4, .4), (.7, .4)
    matrix, _ = contact.minimum_affine(shoulder, palm, (.72, .4))
    assert np.linalg.cond(matrix) < 1.1
    with pytest.raises(ValueError, match='span'):
        contact.minimum_affine(shoulder, (.401, .401), (.72, .4))
    with pytest.raises(ValueError, match='distort'):
        contact.minimum_affine(shoulder, palm, (.9, .6))


def test_painted_sole_ignores_transparent_padding_and_alpha_eight(tmp_path):
    image = Image.new('RGBA', (100, 100))
    image.putpixel((50, 85), (255, 255, 255, 9))
    image.putpixel((50, 95), (255, 255, 255, 8))
    image.save(tmp_path/'leg.png')
    builder = SimpleNamespace(asset_root=tmp_path, layer_by_id={'leg': {'file': 'leg.png'}}, width=100, height=100)
    assert contact.painted_sole_depth(builder, 'leg', .8) == pytest.approx(.055)


class SmallBuilder:
    warp = MapleBuilder.warp
    deform = MapleBuilder.deform
    parameter = MapleBuilder.parameter

    def __init__(self):
        self.scene = 'scene'
        self.nodes, self.params, self.parts = {}, {}, []
        self.parameter('ParamClimbPhase', 0, 1, keys=contact.load_calibration()['phaseKeys'])
        self.parameter('ParamClimbDirection', -1, 1)
        self.parameter('ParamPoseClimb', -1, 1,
            keys=sorted({0., *[sign*(1-t/1.8) for sign in (-1, 1) for t in (0, .16, .32, .55, .85, 1.8)]}))
        self.parameter('ParamClimbRefine', 0, 1, keys=[0, .001, 1])
        self.layer_by_id = {}
        for side in ('l', 'r'):
            for segment in ('upper', 'lower'):
                part = f'sleeve_{side}_{segment}'
                root = self.warp(part+'_outer', self.scene, 3)
                leaf = self.warp(part+'_inner', root, 3)
                self.parts.append(SimpleNamespace(id=part, parent_deformer=leaf))


def test_one_common_arm_parent_preserves_native_meshes_and_exact_three_driver_axes(monkeypatch):
    calibration = deepcopy(contact.load_calibration())
    calibration['sourceLayers'] = {}
    monkeypatch.setattr(contact, 'load_calibration', lambda: calibration)
    builder = SmallBuilder()
    contact.apply_climb_contact_refinement(builder)
    for side in ('l', 'r'):
        assert builder.nodes[f'sleeve_{side}_upper_outer'].parent == builder.nodes[f'sleeve_{side}_lower_outer'].parent
        for direction in ('left', 'right'):
            node = f'ClimbMaterialContact_{direction}_{side}'
            axes = {name for name, parameter in builder.params.items() if any(
                node in key.deformer_offsets or node in key.deformer_offset_weights for key in parameter.keyforms)}
            assert axes == {'ParamClimbPhase', 'ParamPoseClimb', 'ParamClimbRefine'}
            assert len(builder.nodes[node].grid_vertices) == 4
            assert builder.params['ParamClimbRefine'].keyforms[0].deformer_offset_weights[node] == 0


@pytest.mark.parametrize('direction', [-1, 1])
def test_first_rope_grip_and_later_hold_points_receive_no_far_arm_correction(direction):
    from build_v4 import palm_on_rope
    calibration = contact.load_calibration()
    label = 'right' if direction > 0 else 'left'
    far = 'r' if direction > 0 else 'l'
    track = calibration['tracks'][label+'_'+far]
    transform = contact.contact_transform(track, 0, direction, calibration)
    rope = np.array(palm_on_rope(far, 0, y=.43))
    corrected = np.array(contact.affine_point(rope, transform))
    assert np.linalg.norm(corrected-rope) > .001  # This is the actual prior regression.
    for seconds in (.32, .55, .85):
        pose = direction*(1-seconds/1.8)
        weight = contact.contact_pose_weight(pose, direction, 'far')*transfer_refinement(seconds)
        np.testing.assert_allclose(rope+(corrected-rope)*weight, rope, atol=1e-12)
    assert contact.contact_pose_weight(direction, direction, 'far') == 1
    assert contact.contact_pose_weight(direction, direction, 'near') == 1
    assert contact.contact_pose_weight(-direction, direction, 'near') == 0
    assert contact.contact_pose_weight(direction*(1-.55/1.8), direction, 'near') == 1


def test_distinct_material_metadata_is_identical_across_transfer_and_cleanup_states():
    builder = SimpleNamespace(climb_contact_calibration=contact.load_calibration())
    metadata = {'locomotion': {'climb': {}}, 'states': {}}
    contact.add_climb_contact_metadata(metadata, builder)
    for direction in ('left', 'right'):
        anchors = metadata['states']['climb_'+direction]['anchors']
        assert anchors['nearGrip']['drawable'] != anchors['farGrip']['drawable']
        assert anchors[direction] == anchors['nearGrip']
        for prefix in ('climb_to_top_', 'clean_climb_'):
            assert metadata['states'][prefix+direction]['anchors']['nearGrip'] == anchors['nearGrip']
        for suffix in ('_enter', '_exit'):
            assert metadata['states']['clean_climb_'+direction+suffix]['anchors'][direction] == anchors['nearGrip']
        assert direction not in metadata['states']['climb_to_top_'+direction]['anchors']
