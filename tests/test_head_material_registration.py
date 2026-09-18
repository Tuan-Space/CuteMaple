"""Original-paint and sampled-warp guards for continuous head exchanges."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
from functools import lru_cache

import numpy as np
from PIL import Image
import pytest

from tools.authoring import view_registration as reg
from tools.authoring.head_material_registration import (
    MATERIAL_CENTRES, CROWN_ARCS, head_sampling_correction,
    material_sources, refine_head_warp_sampling, correction_weight, HEAD_HAIR_SWAY_GRID)
from tools.authoring.native_warp_sampling import warp_points, triangle_lattice_weights


SOURCE = Path(__file__).resolve().parents[1] / 'assets/authoring/source/layers'
PREFIX = {'front': '', 'left': 'profile_l_', 'left_mid': 'mid_l_',
          'right': 'profile_r_', 'right_mid': 'mid_r_'}
TURNS = [-1., -.8, -.7999, -.75, -.7001, -.7, -.5, -.3, -.2999,
         -.25, -.2001, -.2, 0., .2, .2001, .25, .2999, .3, .5,
         .7, .7001, .75, .7999, .8, 1.]
GRID = np.array([(x/64, y/64) for y in range(65) for x in range(65)])
BODY_HASHES = {
    'front': 'cc995325f1ac5032e5efad82fe58b4abe9eb9a769f780e8034391ae5ead77cc9',
    'right_mid': 'a61ed5883e06026929615807719eee04e237e0783eca57fe3024f56a77aab3a9',
    'left_mid': '666b8b5d412c431685577c551e6602599754c3d283b613c0068f3a6ba303fe4e',
    'right': '158a5451896a33251a9099fcdeca46bd01fb5ccbcd590f22514e92b64ec4e667',
    'left': '60c2a956d9253b858b4f5361b0350b555f24b855fbc0145b185918fd5f25cbc8',
}


def bounds(view):
    output = {}
    for family in set(MATERIAL_CENTRES[view]) | {'hair_front'}:
        with Image.open(SOURCE / (PREFIX[view] + family + '.png')) as image:
            output[family] = np.asarray(image.getchannel('A').getbbox()) / 1024
    return output


@lru_cache(maxsize=5)
def keyed_warps(view):
    """Exercise the production hook with only its one existing node, not a rig."""
    boxes = bounds(view)
    keys = [SimpleNamespace(value=turn, deformer_offsets={'head': [
        tuple(np.asarray(reg.register_point(tuple(p), view, turn, 'head')) - p)
        for p in GRID]}) for turn in TURNS]
    builder = SimpleNamespace(
        layers=[{'id': name, 'family': name, 'view': view} for name in boxes],
        layer_bounds=boxes, nodes={'head': SimpleNamespace(grid_vertices=GRID)},
        params={'ParamHeadTurn': SimpleNamespace(keyforms=keys)},
        _registered_point=reg.register_point)
    refine_head_warp_sampling(builder, 'head', view)
    return np.asarray([GRID + np.asarray(key.deformer_offsets['head']) for key in keys])


def test_measured_centres_are_from_original_isolated_paint():
    for view, features in MATERIAL_CENTRES.items():
        for family, expected in features.items():
            with Image.open(SOURCE / (PREFIX[view] + family + '.png')) as image:
                alpha = np.asarray(image.getchannel('A'), dtype=float)
            y, x = np.nonzero(alpha > 8)
            weights = alpha[y, x]
            actual = [np.dot(x+.5, weights)/weights.sum(), np.dot(y+.5, weights)/weights.sum()]
            assert np.max(np.abs(np.asarray(actual)-expected)) < 1e-9
            assert alpha[int(expected[1]), int(expected[0])] > 8


def test_measured_bun_arcs_belong_to_separate_original_crown_regions():
    rois = {'front': [(375, 498), (551, 665)],
            'left': [(465, 571), (577, 689)],
            'left_mid': [(452, 575), (585, 727)],
            'right': [(310, 442), (446, 541)],
            'right_mid': [(291, 425), (426, 532)]}
    for view, intervals in rois.items():
        with Image.open(SOURCE / (PREFIX[view] + 'hair_front.png')) as image:
            alpha = np.asarray(image.getchannel('A'))
        for (low, high), expected in zip(intervals, CROWN_ARCS[view]):
            y, x = np.nonzero(alpha[:120, low:high] >= 128)
            top = y.min()
            actual = ((x[y < top+3] + low + .5).mean(), float(top))
            assert np.max(np.abs(np.asarray(actual)-expected)) < 1e-9


def test_body_rows_and_collar_are_unchanged():
    for view in reg.VIEW_ANGLES:
        assert hashlib.sha256(reg.LANDMARKS['body'][view].tobytes()).hexdigest() == BODY_HASHES[view]
        legacy = reg._grid(reg._HEAD_ROWS[view], reg._HEAD_ROWS['front']).reshape(10, 9, 2)
        current = reg.LANDMARKS['head'][view].reshape(10, 9, 2)
        np.testing.assert_array_equal(current[6:], legacy[6:])
        np.testing.assert_array_equal(current[:, (0, 8)], legacy[:, (0, 8)])
        np.testing.assert_array_equal(current[0], legacy[0])


@pytest.mark.parametrize('view', list(reg.VIEW_ANGLES))
def test_existing_warp_samples_match_paint_targets_and_preserve_source_key(view):
    warps = keyed_warps(view)
    samples, correction, centres, condition = head_sampling_correction(view, bounds(view), GRID)
    assert condition < 3  # Independent small system, no near-singular fit.
    own = TURNS.index(reg.VIEW_ANGLES[view])
    np.testing.assert_array_equal(warps[own], GRID)
    pinned = (GRID[:, 1] >= 400/1024) | (GRID[:, 0] == 0) | (GRID[:, 0] == 1) | (GRID[:, 1] == 0)
    assert np.count_nonzero(correction[pinned]) == 0
    for turn, warped in zip(TURNS, warps):
        legacy = np.asarray([reg.register_point(tuple(p), view, turn, 'head') for p in GRID])
        np.testing.assert_array_equal(warped[pinned], legacy[pinned])
        if abs(turn-reg.VIEW_ANGLES[view]) <= .3:
            expected = np.asarray([reg.register_point(tuple(p), view, turn, 'head') for p in centres])
            assert np.max(np.abs(samples @ warped - expected)) * 384 < 1e-8
    # Native parameters interpolate grid keyforms, not nonlinear control targets.
    for a, b, first, last in zip(TURNS, TURNS[1:], warps, warps[1:]):
        if max(abs(a-reg.VIEW_ANGLES[view]), abs(b-reg.VIEW_ANGLES[view])) > .3:
            continue
        for fraction in (.13, .43, .87):
            turn = a + (b-a)*fraction
            expected = np.asarray([reg.register_point(tuple(p), view, turn, 'head') for p in centres])
            assert np.max(np.abs(samples @ (first*(1-fraction)+last*fraction)-expected)) * 384 < 1e-8


@pytest.mark.parametrize('view', list(reg.VIEW_ANGLES))
def test_every_visible_bilinear_warp_cell_remains_oriented(view):
    warps = keyed_warps(view)
    for a, b, first, last in zip(TURNS, TURNS[1:], warps, warps[1:]):
        if max(abs(a-reg.VIEW_ANGLES[view]), abs(b-reg.VIEW_ANGLES[view])) > .3:
            continue
        # For each bilinear corner the determinant is quadratic in key weight;
        # check its exact interior minimum, not just endpoint pose snapshots.
        def corners(values):
            g = values.reshape(65, 65, 2)
            du, du1 = g[:-1, 1:]-g[:-1, :-1], g[1:, 1:]-g[1:, :-1]
            dv, dv1 = g[1:, :-1]-g[:-1, :-1], g[1:, 1:]-g[:-1, 1:]
            return [(du, dv), (du, dv1), (du1, dv), (du1, dv1)]
        for (u, v), (u1, v1) in zip(corners(first), corners(last)):
            du, dv = u1-u, v1-v
            qa = reg._cross(du, dv)
            qb = reg._cross(du, v) + reg._cross(u, dv)
            qc = reg._cross(u, v)
            root = np.divide(-qb, 2*qa, out=np.zeros_like(qa), where=np.abs(qa) > 1e-15)
            minimum = np.minimum(qc, qa+qb+qc)
            minimum = np.minimum(minimum, np.where((root > 0) & (root < 1), qa*root*root+qb*root+qc, np.inf))
            assert minimum.min() > 1e-6, (view, a, b, float(minimum.min()))


def test_sampling_correction_is_continuous_and_does_not_choose_visibility():
    for view, own in reg.VIEW_ANGLES.items():
        for sign in (-1, 1):
            for limit in (.3, .5):
                a, b = own+sign*(limit-1e-6), own+sign*(limit+1e-6)
                assert abs(correction_weight(view, a)-correction_weight(view, b)) < 1e-9


@pytest.mark.parametrize('view', list(reg.VIEW_ANGLES))
def test_material_fit_survives_actual_prewarped_hair_child_and_direct_face(view):
    """Independently evaluate native geometry rather than the fitted row matrix."""
    warps = keyed_warps(view)
    source_bounds = bounds(view)
    child_size = HEAD_HAIR_SWAY_GRID
    child_source = np.asarray([(x/(child_size-1), y/(child_size-1))
                               for y in range(child_size) for x in range(child_size)])
    for a, b, first, last in zip(TURNS, TURNS[1:], warps, warps[1:]):
        if max(abs(a-reg.VIEW_ANGLES[view]), abs(b-reg.VIEW_ANGLES[view])) > .3:
            continue
        turn = a+(b-a)*.43
        parent = first*.57+last*.43
        child = warp_points(child_source, parent, 65)
        for _, family, point in material_sources(view):
            low = np.maximum(0, source_bounds[family][:2]-2/1024)
            high = np.minimum(1, source_bounds[family][2:]+2/1024)
            size = 17 if family == 'hair_front' else 9
            indices, weights = triangle_lattice_weights((point-low)/(high-low), size)
            vertices = [low+np.array([i % size, i // size])/(size-1)*(high-low) for i in indices]
            deformed = warp_points(vertices, child, child_size) if family == 'hair_front' else warp_points(vertices, parent, 65)
            actual = weights @ deformed
            expected = reg.register_point(point, view, turn, 'head')
            np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-10)
