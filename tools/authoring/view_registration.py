"""Shared, bounded 2-D semantic registration for Maple's five painted views.

Coordinates below are measured on the registered 1024-square source layers:
the front artwork is already registered; left donor pixels use (.79*x+74,
.79*y+20), right donor pixels use (.79*x-54, .79*y+20). They are deliberately
coarse anatomical guides, not alpha-row contours. Buns/crown, eyes, mouth,
chin/neck and the shoulder/waist/hem/shoes share a fixed triangle topology.

Each source view maps directly to the SAME interpolated target landmarks.
The whole square boundary is fixed, so there is no uncontrolled extrapolation.
Within every triangle the mapping is affine. The audit checks the exact
quadratic signed-area minimum throughout each angular interval, not just its
endpoints. This constrains the registration itself; it does not certify the
Editor warp interpolation, painted feature agreement or ornament alignment.

Public input/output are normalized (one unit = 1024 source pixels). This file
does not read images, change layer registration, add geometry or set opacity.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
import numpy as np

try:
    from .head_material_registration import refine_head_material_controls
except ImportError:  # Authoring tools also run directly from this directory.
    from head_material_registration import refine_head_material_controls

VIEW_ANGLES = {'left': -1., 'left_mid': -.5, 'front': 0., 'right_mid': .5, 'right': 1.}

# Seven interior columns: outer skull, cheek/temple, left-eye axis,
# central facial axis, right-eye axis, cheek/temple, outer skull.
# Interior rows have their measured y; boundary y always remains at front y.
# At the chin the eye-axis columns continue down the cheek, not onto new eyes.
_HEAD_ROWS = {
    'front': [
        (80, [380, 420, 470, 520, 570, 620, 659]),
        (161, [348, 420, 474, 520, 570, 626, 698]),
        (255, [370, 420, 474, 520, 570, 626, 668]),
        (294, [398, 427, 474, 520, 566, 617, 647]),
        (341, [408, 439, 474, 520, 566, 610, 640]),
        (373, [405, 454, 496, 520, 550, 584, 650]),
        (400, [360, 433, 466, 520, 558, 609, 686]),
        (480, [360, 452, 485, 522, 562, 598, 686]),
    ],
    'right_mid': [
        (80, [296, 330, 375, 420, 455, 490, 520]),
        (176, [262, 390, 461, 501, 541, 567, 581]),
        (266, [300, 410, 482, 520, 547, 576, 596]),
        (296, [355, 429, 485, 520, 546, 572, 590]),
        (344, [375, 422, 475, 523, 543, 555, 578]),
        (374, [380, 419, 467, 497, 517, 547, 573]),
        (400, [287, 395, 420, 470, 493, 521, 620]),
        (480, [287, 399, 437, 479, 495, 512, 620]),
    ],
    'left_mid': [
        (80, [454, 485, 520, 565, 610, 657, 705]),
        (183, [387, 407, 437, 480, 548, 643, 733]),
        (266, [388, 407, 437, 466, 501, 584, 690]),
        (292, [397, 413, 437, 466, 501, 580, 639]),
        (340, [411, 427, 445, 466, 511, 580, 639]),
        (368, [423, 442, 459, 483, 515, 561, 640]),
        (400, [380, 461, 488, 523, 563, 596, 720]),
        (480, [380, 461, 482, 511, 546, 576, 720]),
    ],
    'right': [
        (80, [318, 350, 390, 430, 469, 505, 537]),
        (186, [283, 405, 482, 535, 571, 594, 610]),
        (259, [309, 435, 529, 560, 584, 602, 624]),
        (297, [347, 435, 529, 560, 584, 596, 614]),
        (351, [405, 445, 510, 559, 575, 587, 606]),
        (381, [419, 449, 489, 527, 556, 580, 606]),
        (400, [310, 430, 455, 491, 520, 553, 620]),
        (480, [310, 431, 464, 504, 528, 553, 620]),
    ],
    'left': [
        (80, [464, 495, 529, 567, 608, 648, 681]),
        (197, [384, 403, 427, 469, 529, 636, 716]),
        (256, [382, 404, 420, 449, 476, 572, 665]),
        (287, [385, 403, 420, 449, 476, 580, 606]),
        (343, [405, 425, 437, 453, 508, 574, 606]),
        (372, [419, 443, 459, 482, 523, 573, 620]),
        (400, [390, 468, 488, 525, 560, 587, 730]),
        (480, [390, 455, 487, 523, 561, 595, 730]),
    ],
}

# The neck and waist rows match HEAD exactly. Between them, all adjoining
# body layers use this same field regardless of their PNG/mesh bounding box.
# Shoulder edges, belt centre, skirt spread and feet are measured separately;
# ribbons and dangling ornaments are intentionally not silhouette constraints.
# Lower centre columns follow the actual gold hanging-panel axis. The y=700
# value is a measured gold-mask centroid. The y=880 CONTROL value comes from
# a line fitted through the measured 700, 780, 800, 820 and 840 bands, anchored
# at 700; no gold pixels exist at 880 in these sources. This evaluates that
# fitted line at the existing row without adding a warp row or claiming a
# measurement outside the painted panel. Maximum source fit residual: 1.44px.
# The immediately neighbouring columns are a common +/-40px panel corridor.
# This keeps the local field affine through the motif, so sampling it into the
# existing 65-row warp and 17-row mesh does not displace the axis at a sharp
# central kink. Shoulder/waist, outer skirt controls, y and head are unchanged.
_BODY_ROWS = {
    'front': [
        (350, [360, 433, 466, 520, 558, 609, 686]),
        _HEAD_ROWS['front'][6],
        (440, [360, 447, 482, 522, 562, 603, 686]),
        _HEAD_ROWS['front'][7],
        (540, [330, 447, 483, 522, 561, 605, 710]),
        (700, [290, 373, 474.66102, 514.66102, 554.66102, 675, 750]),
        (880, [285, 367, 474.62445, 514.62445, 554.62445, 671, 755]),
        (950, [330, 452, 485, 520, 556, 587, 710]),
    ],
    'right_mid': [
        (350, [287, 395, 420, 470, 493, 521, 620]),
        _HEAD_ROWS['right_mid'][6],
        (440, [287, 394, 425, 467, 491, 510, 620]),
        _HEAD_ROWS['right_mid'][7],
        (540, [260, 393, 437, 481, 512, 536, 650]),
        (700, [220, 334, 476.65972, 516.65972, 556.65972, 593, 690]),
        (880, [215, 333, 491.00917, 531.00917, 571.00917, 593, 700]),
        (950, [287, 392, 423, 453, 486, 521, 645]),
    ],
    'left_mid': [
        (350, [380, 461, 488, 523, 563, 596, 720]),
        _HEAD_ROWS['left_mid'][6],
        (440, [380, 461, 481, 511, 555, 590, 720]),
        _HEAD_ROWS['left_mid'][7],
        (540, [350, 447, 480, 511, 548, 580, 740]),
        (700, [300, 380, 437.73054, 477.73054, 517.73054, 663, 795]),
        (880, [300, 395, 428.70508, 468.70508, 508.70508, 680, 795]),
        (950, [370, 459, 492, 525, 560, 594, 730]),
    ],
    'right': [
        (350, [310, 430, 455, 491, 520, 553, 620]),
        _HEAD_ROWS['right'][6],
        (440, [310, 431, 462, 503, 528, 551, 620]),
        _HEAD_ROWS['right'][7],
        (540, [290, 426, 463, 505, 531, 561, 645]),
        (700, [235, 371, 498.09524, 538.09524, 578.09524, 631, 710]),
        (880, [230, 355, 507.41110, 547.41110, 587.41110, 622, 710]),
        (950, [310, 423, 452, 481, 511, 540, 620]),
    ],
    'left': [
        (350, [390, 468, 488, 525, 560, 587, 730]),
        _HEAD_ROWS['left'][6],
        (440, [390, 454, 483, 523, 560, 588, 730]),
        _HEAD_ROWS['left'][7],
        (540, [365, 439, 481, 523, 564, 601, 750]),
        (700, [300, 372, 430.79333, 470.79333, 510.79333, 654, 790]),
        (880, [300, 390, 419.79415, 459.79415, 499.79415, 667, 790]),
        (950, [390, 461, 491, 521, 556, 591, 730]),
    ],
}

def _grid(rows, boundary_rows):
    result = [[(x, 0.) for x in np.linspace(0, 1024, 9)]]
    for (y, xs), (boundary_y, _) in zip(rows, boundary_rows):
        result.append([(0., boundary_y), *((x, y) for x in xs), (1024., boundary_y)])
    result.append([(x, 1024.) for x in np.linspace(0, 1024, 9)])
    return np.asarray(result, dtype=float).reshape(-1, 2) / 1024

LANDMARKS = {zone: {view: _grid(rows, views['front']) for view, rows in views.items()}
             for zone, views in [('head', _HEAD_ROWS), ('body', _BODY_ROWS)]}
LANDMARKS['head'] = refine_head_material_controls(LANDMARKS['head'])
TRIANGLES = np.asarray([(a, a + 1, a + 10) if half == 0 else (a, a + 10, a + 9)
                        for row in range(9) for col in range(8)
                        for a in [row * 9 + col] for half in range(2)], dtype=int)
for views in LANDMARKS.values():
    for points in views.values():
        points.setflags(write=False)
TRIANGLES.setflags(write=False)

def _arguments(view, turn, zone):
    if view not in VIEW_ANGLES or zone not in LANDMARKS:
        raise ValueError(f'Unknown registration view/zone: {view!r}/{zone!r}')
    if not math.isfinite(turn) or not -1 <= turn <= 1:
        raise ValueError('Turn must be finite and within -1..1')

@lru_cache(maxsize=512)
def target_landmarks(turn: float, zone: str) -> np.ndarray:
    _arguments('front', turn, zone)
    views = sorted(VIEW_ANGLES, key=VIEW_ANGLES.get)
    for left, right in zip(views, views[1:]):
        a, b = VIEW_ANGLES[left], VIEW_ANGLES[right]
        if a <= turn <= b:
            weight = (turn-a)/(b-a)
            result = LANDMARKS[zone][left]*(1-weight) + LANDMARKS[zone][right]*weight
            result.setflags(write=False)
            return result
    raise AssertionError('Validated turn was not bracketed')

@lru_cache(maxsize=10)
def _source_triangles(view, zone):
    triangles = LANDMARKS[zone][view][TRIANGLES]
    matrices = np.stack((triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), axis=2)
    return triangles[:, 0], np.linalg.inv(matrices)

@lru_cache(maxsize=65536)
def _locate(point, view, zone):
    origin, inverses = _source_triangles(view, zone)
    uv = np.einsum('nij,nj->ni', inverses, np.asarray(point)-origin)
    weights = np.column_stack((1-uv.sum(axis=1), uv))
    inside = np.flatnonzero(weights.min(axis=1) >= -1e-10)
    if not len(inside):
        raise ValueError(f'Source point escaped the fixed registration square: {point}')
    index = int(inside[0])
    return index, weights[index]

def register_point(point, view, turn, zone):
    """Map a normalized source point to the common pose, preserving endpoints.

    Out-of-canvas inputs stay unchanged; the complete canvas boundary also
    stays fixed, so this extension is continuous and never extrapolates.
    """
    turn = float(turn)
    _arguments(view, turn, zone)
    point = tuple(float(value) for value in point)
    if len(point) != 2 or not all(math.isfinite(value) for value in point):
        raise ValueError('Expected two finite normalized source coordinates')
    if turn == VIEW_ANGLES[view] or any(value <= 0 or value >= 1 for value in point):
        return point
    triangle, weights = _locate(point, view, zone)
    target = target_landmarks(turn, zone)[TRIANGLES[triangle]]
    return tuple(weights @ target)

def _cross(a, b):
    return a[..., 0]*b[..., 1]-a[..., 1]*b[..., 0]

def validate_registration():
    """Exact signed-area bound plus sampled scale/landmark handoff evidence."""
    report = {'method': 'shared-semantic-piecewise-affine', 'triangles': len(TRIANGLES),
              'ornamentAlignment': 'not-certified', 'nativeInterpolation': 'not-certified', 'zones': {}}
    ordered = sorted(VIEW_ANGLES, key=VIEW_ANGLES.get)
    for zone, views in LANDMARKS.items():
        minimum = math.inf
        for left, right in zip(ordered, ordered[1:]):
            a, b = views[left][TRIANGLES], views[right][TRIANGLES]
            u, v = a[:, 1]-a[:, 0], a[:, 2]-a[:, 0]
            du, dv = b[:, 1]-b[:, 0]-u, b[:, 2]-b[:, 0]-v
            qa, qb, qc = _cross(du, dv), _cross(du, v)+_cross(u, dv), _cross(u, v)
            candidates = [qc, qa+qb+qc]
            t = np.divide(-qb, 2*qa, out=np.zeros_like(qa), where=abs(qa)>1e-15)
            candidates.append(np.where((t>0)&(t<1), qa*t*t+qb*t+qc, math.inf))
            minimum = min(minimum, *(float(values.min()) for values in candidates))
        if minimum <= 1e-8:
            raise ValueError(f'Folded/degenerate semantic registration triangle in {zone}: {minimum}')
        max_scale = 0.
        visible_scale = 0.
        max_jump = 0.
        for view, source in views.items():
            origins, inverse = _source_triangles(view, zone)
            for turn in np.linspace(-1, 1, 81):
                dest = target_landmarks(float(turn), zone)[TRIANGLES]
                matrices = np.stack((dest[:, 1]-dest[:, 0], dest[:, 2]-dest[:, 0]), axis=2) @ inverse
                max_scale = max(max_scale, float(np.linalg.svd(matrices, compute_uv=False).max()))
                if abs(float(turn)-VIEW_ANGLES[view]) <= .2500001:
                    visible_scale = max(visible_scale, float(np.linalg.svd(matrices, compute_uv=False).max()))
            for boundary in (-.75, -.25, .25, .75):
                a, b = target_landmarks(boundary-.001, zone), target_landmarks(boundary+.001, zone)
                max_jump = max(max_jump, float(np.linalg.norm(a-b, axis=1).max()*1024))
        report['zones'][zone] = {'minimumDoubleAreaPixels': minimum*1024**2,
                                'maximumScaleAcrossAllViewPairs': max_scale,
                                'maximumScaleInVisibleAngularInterval': visible_scale,
                                'maximumLandmarkTravelAcross002TurnPixels': max_jump,
                                'sourceEndpointIdentity': True, 'fixedCanvasBoundary': True}
    return report

if __name__ == '__main__':
    print(json.dumps(validate_registration(), indent=2))
