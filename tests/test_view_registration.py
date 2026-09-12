"""Independent numerical guards for a shared, continuous anatomical field."""
import numpy as np
import pytest
from pathlib import Path
from PIL import Image
from tools.authoring.view_registration import (LANDMARKS, TRIANGLES, VIEW_ANGLES,
                                               register_point, target_landmarks, validate_registration)


def test_every_source_endpoint_and_canvas_boundary_are_exact_identity():
    rng = np.random.default_rng(1729)
    for zone in ('head', 'body'):
        for view, turn in VIEW_ANGLES.items():
            for p in rng.random((30, 2)):
                assert register_point(p, view, turn, zone) == tuple(p)
            for target_turn in (-1, -.75, -.25, .25, .75, 1):
                for edge in ([(0, t) for t in np.linspace(0, 1, 9)] +
                             [(t, 1) for t in np.linspace(0, 1, 9)] +
                             [(1, t) for t in np.linspace(0, 1, 9)] +
                             [(t, 0) for t in np.linspace(0, 1, 9)]):
                    assert register_point(edge, view, target_turn, zone) == tuple(edge)


def test_corresponding_points_from_different_art_views_share_one_target():
    # Interior triangle points, not just the control vertices themselves.
    weights = np.array([.22, .31, .47])
    for zone, views in LANDMARKS.items():
        for turn in (-.751, -.749, -.251, -.249, .249, .251, .749, .751):
            target = target_landmarks(turn, zone)
            for tri in TRIANGLES[::7]:
                expected = weights @ target[tri]
                for view, source in views.items():
                    actual = register_point(weights @ source[tri], view, turn, zone)
                    assert np.linalg.norm(actual-expected) < 1e-10


def test_complete_angle_intervals_have_no_fold_and_handoffs_do_not_jump():
    audit = validate_registration()
    for result in audit['zones'].values():
        assert result['minimumDoubleAreaPixels'] > 300
        assert result['maximumLandmarkTravelAcross002TurnPixels'] < .6
        assert result['maximumScaleInVisibleAngularInterval'] < 5
    # Shared target remains inside the unchanged convex canvas for dense probes.
    for zone in ('head', 'body'):
        for view in VIEW_ANGLES:
            for turn in (-.75, -.25, .25, .75):
                for x, y in np.random.default_rng(31).random((80, 2)):
                    point = register_point((x, y), view, turn, zone)
                    assert min(point) >= 0 and max(point) <= 1


def test_mapping_is_two_dimensional_and_shares_neck_seam():
    # A vertical line bends through semantic facial rows; this is not separate
    # global x/y rescaling of each mesh or its alpha bounds.
    first = register_point((.47, .29), 'front', .5, 'head')
    second = register_point((.47, .35), 'front', .5, 'head')
    assert abs(first[0]-second[0]) > .003
    for view in VIEW_ANGLES:
        for x in np.linspace(.36, .66, 13):
            for turn in (-.75, -.25, .25, .75):
                head = register_point((x, 400/1024), view, turn, 'head')
                body = register_point((x, 400/1024), view, turn, 'body')
                assert np.linalg.norm(np.array(head)-body) < 1e-10


def test_bad_inputs_fail_and_outside_points_do_not_extrapolate():
    for point in [(float('nan'), .5), (.5, float('inf')), (.4, .5, .6)]:
        with pytest.raises(ValueError):
            register_point(point, 'front', .5, 'head')
    for view, turn, zone in [('other', 0, 'head'), ('front', 2, 'head'),
                             ('front', float('nan'), 'body'), ('front', 0, 'legs')]:
        with pytest.raises(ValueError):
            register_point((.5, .5), view, turn, zone)
    assert register_point((3, -.7), 'front', 1, 'head') == (3, -.7)


def test_actual_gold_panel_centres_match_after_existing_warp_and_mesh_sampling():
    """Use the painted source masks, not the manually declared control axes.

    Model the existing 65x65 bilinear view warp and 17x17 triangle mesh; a
    sharp knot can align mathematical landmarks while moving rendered pixels.
    """
    root = Path(__file__).resolve().parents[1]/'assets/authoring/revisions/v4/layers'
    definitions = {'front': ('', (475, 555)), 'right_mid': ('mid_r_', (480, 570)),
                   'right': ('profile_r_', (500, 590)), 'left_mid': ('mid_l_', (400, 520)),
                   'left': ('profile_l_', (400, 520))}
    measurements, bounds = {}, {}
    for view, (prefix, roi) in definitions.items():
        with Image.open(root/(prefix+'skirt.png')) as source:
            box = source.getchannel('A').getbbox()
            pixels = np.asarray(source.convert('RGBA'), dtype=float)
        bounds[view] = np.array([max(0, box[0]-2), max(0, box[1]-2),
                                 min(1024, box[2]+2), min(1024, box[3]+2)])/1024
        r, g, b, a = np.moveaxis(pixels, -1, 0)
        gold = (r>155)&(g>95)&(b<150)&(r-g>12)&(g-b>25)&(a>180)
        gold[:, :roi[0]] = False
        gold[:, roi[1]:] = False
        assert not gold[878:883].any()  # 880 is a fitted control, never a measurement.
        measurements[view] = {}
        for y in (700, 780, 800, 820, 840):
            _, xs = np.where(gold[y-2:y+3])
            assert len(xs) > 30
            measurements[view][y] = (xs.mean()/1024, y/1024)

    def sampled(point, view, turn):
        def warp(p):
            xy = np.asarray(p)*64
            cell = np.minimum(63, np.floor(xy).astype(int))
            u, v = xy-cell
            grid = [np.array(register_point((cell+offset)/64, view, turn, 'body'))
                    for offset in [(0, 0), (1, 0), (0, 1), (1, 1)]]
            return (grid[0]*(1-u)+grid[1]*u)*(1-v)+(grid[2]*(1-u)+grid[3]*u)*v
        low, high = bounds[view][:2], bounds[view][2:]
        xy = (np.asarray(point)-low)/(high-low)*16
        cell = np.minimum(15, np.floor(xy).astype(int))
        u, v = xy-cell
        offsets = [(0, 0), (1, 0), (0, 1)] if u+v <= 1 else [(1, 0), (1, 1), (0, 1)]
        weights = [1-u-v, u, v] if u+v <= 1 else [1-v, u+v-1, 1-u]
        vertices = [low+(cell+offset)/16*(high-low) for offset in offsets]
        return sum(weight*warp(vertex) for weight, vertex in zip(weights, vertices))

    for first, last, turn in [('front', 'right_mid', .25), ('right_mid', 'right', .75),
                              ('left_mid', 'front', -.25), ('left', 'left_mid', -.75)]:
        for y in measurements[first]:
            before = sampled(measurements[first][y], first, turn-.001)
            after = sampled(measurements[last][y], last, turn+.001)
            assert np.linalg.norm(before-after)*512 < 2, (first, last, y)
