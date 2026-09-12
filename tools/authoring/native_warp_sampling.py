"""Triangle-lattice sampling observed in the current native Cubism export.

The material audit replays original native geometry through anti-diagonal
triangles, including child lattices prewarped through their parent. This is
an authoring calculation, not a replacement for runtime/Core validation.
"""
from __future__ import annotations

import numpy as np


def triangle_lattice_weights(point, rows, cols=None):
    """Return three flattened grid addresses and their interpolation weights."""
    cols = rows if cols is None else cols
    if min(rows, cols) < 2:
        raise ValueError('A warp lattice needs at least two rows and columns')
    point = np.asarray(point, dtype=float)
    if point.shape != (2,) or not np.isfinite(point).all():
        raise ValueError('Expected a finite two-dimensional material point')
    location = point * (cols - 1, rows - 1)
    cell = np.clip(np.floor(location).astype(int), 0, (cols - 2, rows - 2))
    u, v = location - cell
    if u + v <= 1:
        offsets, weights = ((0, 0), (1, 0), (0, 1)), (1-u-v, u, v)
    else:
        offsets, weights = ((1, 0), (1, 1), (0, 1)), (1-v, u+v-1, 1-u)
    indices = [(cell[1]+dy)*cols+cell[0]+dx for dx, dy in offsets]
    return np.asarray(indices, dtype=int), np.asarray(weights, dtype=float)


def warp_points(points, grid, rows, cols=None):
    """Evaluate a supplied native-style grid; parent composition is explicit."""
    cols = rows if cols is None else cols
    grid = np.asarray(grid, dtype=float).reshape(rows * cols, 2)
    result = []
    for point in points:
        indices, weights = triangle_lattice_weights(point, rows, cols)
        result.append(weights @ grid[indices])
    return np.asarray(result, dtype=float).reshape(-1, 2)


def unwarp_points(points, grid, rows, cols=None):
    """Invert oriented native triangles inside their painted canvas.

    A local mesh can compensate a sampled parent without inserting another
    large warp. Reject folds or points outside the mapped square instead of
    extrapolating across an unrelated cell. Boundary ties have the same map.
    """
    cols = rows if cols is None else cols
    grid = np.asarray(grid, dtype=float).reshape(rows * cols, 2)
    triangles = np.asarray([triangle for y in range(rows-1) for x in range(cols-1)
        for a in [y*cols+x] for triangle in ((a, a+1, a+cols), (a+1, a+cols+1, a+cols))])
    paint = grid[triangles]
    matrices = np.stack((paint[:, 1]-paint[:, 0], paint[:, 2]-paint[:, 0]), axis=2)
    determinants = np.linalg.det(matrices)
    if not np.isfinite(grid).all() or np.min(determinants) <= 1e-10:
        raise ValueError('Cannot invert a folded or degenerate native lattice')
    inverse = np.linalg.inv(matrices)
    source = np.column_stack((np.arange(rows*cols) % cols / (cols-1),
                              np.arange(rows*cols) // cols / (rows-1)))
    result = []
    for point in points:
        point = np.asarray(point, dtype=float)
        if point.shape != (2,) or not np.isfinite(point).all():
            raise ValueError('Expected finite material destinations')
        uv = np.einsum('nij,nj->ni', inverse, point-paint[:, 0])
        weights = np.column_stack((1-uv.sum(axis=1), uv))
        candidates = np.flatnonzero(np.min(weights, axis=1) >= -1e-8)
        if not len(candidates):
            raise ValueError('Material destination left its oriented native canvas')
        index = candidates[0]
        result.append(weights[index] @ source[triangles[index]])
    return np.asarray(result).reshape(-1, 2)
