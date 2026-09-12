"""Small native10 counterexamples for triangular, parent-prewarped lattices."""
from pathlib import Path
import json

import numpy as np
import pytest

from tools.authoring.native_warp_sampling import warp_points, triangle_lattice_weights, unwarp_points


FIXTURE = json.loads((Path(__file__).parent/'fixtures/native_head_warp10.json').read_text())


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: c['view']+'-'+c['feature']+'-'+c['frame'])
def test_frozen_native_crown_material_replays_through_its_actual_child_lattice(case):
    # These are actual Core material positions from native10, not positions
    # produced by the candidate solver. Preserve the native float tolerance.
    parent = np.zeros((65*65, 2))
    for index, value in case['parentGridPatch'].items():
        parent[int(index)] = value
    child = np.zeros((13*13, 2))
    indices = case['childIndices']
    addresses = [np.array([index % 13, index // 13])/12 for index in indices]
    child[indices] = warp_points(addresses, parent, 65)
    mesh = warp_points(case['meshVertices'], child, 13)
    actual = np.asarray(case['meshWeights']) @ mesh
    np.testing.assert_allclose(actual, case['expectedNativePoint'], rtol=0, atol=1e-7)


def test_non_affine_cell_uses_its_two_triangles_and_keeps_the_diagonal_continuous():
    # One raised corner separates triangle and bilinear interpolation.
    grid = [(0, 0), (1, 0), (0, 1), (1, 2)]
    np.testing.assert_allclose(warp_points([(.25, .25), (.75, .75)], grid, 2),
                               [(.25, .25), (.75, 1.25)], atol=1e-15)
    first, last = warp_points([(.4, .6-1e-8), (.4, .6+1e-8)], grid, 2)
    assert np.linalg.norm(first-last) < 4e-8
    for p in ((0, 0), (1, 1), (.31, .76)):
        indices, weights = triangle_lattice_weights(p, 2)
        assert len(indices) == len(weights) == 3
        assert abs(weights.sum()-1) < 1e-15


def test_invalid_material_addresses_are_rejected():
    with pytest.raises(ValueError):
        triangle_lattice_weights((float('nan'), .5), 3)
    with pytest.raises(ValueError):
        triangle_lattice_weights((.5, .5), 1)


def test_oriented_inverse_returns_original_material_across_non_affine_cells():
    grid = [(0, 0), (1, 0), (0, 1), (1, 2)]
    source = np.array([(0, 0), (1, 1), (.25, .25), (.75, .75), (.4, .6)])
    np.testing.assert_allclose(unwarp_points(warp_points(source, grid, 2), grid, 2), source, atol=1e-14)


def test_inverse_rejects_folded_canvas_or_unrelated_destination():
    with pytest.raises(ValueError, match='folded'):
        unwarp_points([(.5, .5)], [(0, 0), (1, 0), (0, 1), (-1, -1)], 2)
    with pytest.raises(ValueError, match='left'):
        unwarp_points([(2, .5)], [(0, 0), (1, 0), (0, 1), (1, 1)], 2)
