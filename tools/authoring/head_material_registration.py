"""Measured facial material controls for the existing continuous head field.

The pupil and mouth images are isolated original semantic layers. Their
alpha-weighted pixel centres (alpha > 8) provide material addresses; whole
face/hair centroids do not, and are deliberately not used here. Coordinates
are in the already registered 1024-square source images, not rendered views.
This changes neither source pixels nor view opacity, body or collar controls.
"""
from __future__ import annotations

import numpy as np

try:
    from .native_warp_sampling import triangle_lattice_weights, warp_points, unwarp_points
except ImportError:
    from native_warp_sampling import triangle_lattice_weights, warp_points, unwarp_points


# The existing 13-node hair child undersamples the crown field; fitting that
# chain folds visible head cells. This bounded resolution keeps the shared
# material fit oriented without changing other hair, body or facial lattices.
HEAD_HAIR_SWAY_GRID = 33


MATERIAL_CENTRES = {
    'front': {'pupil_l': (474.5000285795942, 294.5000171477565),
              'pupil_r': (568.5013744899243, 294.5010830180484),
              'mouth': (521.4997565655698, 340.9999642008191)},
    'left_mid': {'pupil_l': (437.9100163382348, 294.2552465739722),
                 'pupil_r': (501.8801879086714, 296.6136646467411),
                 'mouth': (466.72987841808924, 340.8307442966419)},
    'left': {'pupil_r': (470.27363578795666, 294.6305436467788),
             'mouth': (436.9114785100764, 341.75196404322145)},
    'right_mid': {'pupil_l': (485.2511891249122, 297.78502062951554),
                  'pupil_r': (550.0699630990376, 300.1631561176007),
                  'mouth': (520.4354592342789, 343.6243652053164)},
    'right': {'pupil_l': (535.4401890008719, 299.7458464583939),
              'mouth': (566.8245527524152, 350.1668681534269)},
}

# Fixed semantic locations in the existing ten-row, nine-column topology.
MATERIAL_GRID_INDICES = {'pupil_l': 4 * 9 + 3,
                         'pupil_r': 4 * 9 + 5, 'mouth': 5 * 9 + 4}

# The top arc of each bun, measured separately in semantic x ROIs. Keeping
# their y coordinates independent avoids treating the tilted crown as one
# horizontal alpha bounding box. See tests for the original-pixel measurement.
CROWN_ARCS = {
    'front': ((452.25342465753425, 38.), (602.0373134328358, 39.)),
    'left_mid': ((521.0909090909091, 46.), (648.5769230769231, 45.)),
    'left': ((531.0918367346939, 53.), (622.974358974359, 43.)),
    'right_mid': ((360.95098039215685, 41.), (467.890243902439, 49.)),
    'right': ((384.3045977011494, 45.), (480.6190476190476, 56.)),
}

# The visible scalp parting is shared paint, unlike the cut-out opening below
# it. Manual original-pixel readings have about 3 source pixels uncertainty;
# aligning these addresses is not certification of every strand or outline.
SCALP_PARTINGS = {'front': (515., 148.), 'left_mid': (458., 160.),
                  'right_mid': (516., 156.), 'left': (444., 158.),
                  'right': (557., 164.)}

# Inner/outer pink beads on the visible near ornament. Far cropped views have
# no declared correspondence. These individual original-color ROI means are
# recorded with hashes in the material-control fixture, not alpha-box extrema.
ORNAMENT_BEADS = {
    'ornament_l': {'front': ((392.45, 336.98), (376.67, 337.54)),
        'right_mid': ((355.36, 333.78), (330.15, 333.95)),
        'right': ((396.68, 338.76), (367.77, 339.10))},
    'ornament_r': {'front': ((646.58, 335.36), (669.09, 331.94)),
        'left_mid': ((640.66, 324.83), (669.95, 323.47)),
        'left': ((603.72, 330.72), (635.35, 330.81))},
}

VIEW_ANGLES = {'left': -1., 'left_mid': -.5, 'front': 0., 'right_mid': .5, 'right': 1.}


def refine_head_material_controls(views):
    """Return new head guides; every view maps paint to one common trajectory.

    Mouth guide neighbours follow its relocated centre monotonically. The
    left profile has a short near-cheek interval: equal subdivisions there
    keep the existing area bound rather than allowing a pinched triangle.
    Missing far profile eyes remain geometric guides and are never invented
    as visible art. Rows 6--9 and all canvas boundary points remain exact.
    """
    output = {}
    for view, source in views.items():
        grid = np.asarray(source, dtype=float).copy().reshape(10, 9, 2) * 1024
        centres = MATERIAL_CENTRES[view]
        grid[1, 1:8, 1] = np.mean([point[1] for point in CROWN_ARCS[view]])
        for column, point in zip((3, 5), CROWN_ARCS[view]):
            grid[1, column] = point
        old = grid[2, 4].copy()
        parting = np.asarray(SCALP_PARTINGS[view])
        for column in (3, 4, 5):
            anchor = grid[2, 2 if column <= 4 else 6, 0]
            fraction = (grid[2, column, 0]-anchor)/(old[0]-anchor)
            grid[2, column, 0] = anchor+fraction*(parting[0]-anchor)
            grid[2, column, 1] += (parting[1]-old[1])*(1 if column == 4 else .5)
        for name, column in (('pupil_l', 3), ('pupil_r', 5)):
            if name in centres:
                grid[4, column] = centres[name]
        grid[4, 4, 1] = np.mean([p[1] for name, p in centres.items()
                                if name.startswith('pupil_')])
        old = grid[5, 4, 0]
        new, mouth_y = centres['mouth']
        low, high = grid[5, 1, 0], grid[5, 7, 0]
        for column in range(1, 8):
            x = grid[5, column, 0]
            grid[5, column, 0] = (low + (x - low) * (new - low) / (old - low)
                                  if x <= old else
                                  new + (x - old) * (high - new) / (high - old))
            grid[5, column, 1] = mouth_y
        if view == 'left':
            grid[5, 2:4, 0] = np.linspace(low, new, 4)[1:3]
        output[view] = grid.reshape(-1, 2) / 1024
    return output


def material_sources(view):
    """Independent original-paint addresses, including the two crown arcs."""
    return [(name, name, np.asarray(point) / 1024)
            for name, point in MATERIAL_CENTRES[view].items()] + [
                (f'bun_{i}', 'hair_front', np.asarray(point) / 1024)
                for i, point in enumerate(CROWN_ARCS[view])] + [
                ('scalp_parting', 'hair_front', np.asarray(SCALP_PARTINGS[view])/1024)]


def material_sample_weights(point, bounds, mesh_size, warp_size=65, child_warp_size=None):
    """Material evaluation through native triangle grids and an optional child.

    The native export samples anti-diagonal triangles for both art meshes
    and warps. A child lattice is transformed through its parent first; even
    an identity child can undersample the parent field. Include that exact
    neutral hierarchy rather than composing a point through two warps.
    """
    point, bounds = np.asarray(point), np.asarray(bounds)
    low, high = bounds[:2], bounds[2:]
    n = mesh_size - 1
    xy = (point - low) / (high - low) * n
    if np.any(xy < 0) or np.any(xy > n):
        raise ValueError('Paint address lies outside its source mesh')
    indices, weights = triangle_lattice_weights((point-low)/(high-low), mesh_size)
    locations = [(low + np.array([index % mesh_size, index // mesh_size]) / n * (high-low), weight)
                 for index, weight in zip(indices, weights)]
    if child_warp_size is not None:
        expanded = []
        for vertex, weight in locations:
            indices, coefficients = triangle_lattice_weights(vertex, child_warp_size)
            expanded.extend((np.array([index % child_warp_size, index // child_warp_size]) /
                             (child_warp_size-1), weight * coefficient)
                            for index, coefficient in zip(indices, coefficients))
        locations = expanded
    row = np.zeros(warp_size * warp_size)
    for vertex, weight in locations:
        indices, coefficients = triangle_lattice_weights(vertex, warp_size)
        for index, coefficient in zip(indices, coefficients):
            row[index] += weight * coefficient
    return row


def head_sampling_correction(view, bounds_by_family, grid):
    """Small smooth basis for exact painted-material sampling constraints.

    No additional deformation axes, parts or meshes are introduced. The
    compact basis vanishes on the canvas boundary and at/below source y=400,
    so the established collar/neck field stays untouched. All constraints
    are solved together rather than independently shifting a pupil or lip.
    """
    grid = np.asarray(grid, dtype=float)
    sources = material_sources(view)
    rows = []
    for _, family, point in sources:
        low, high = np.asarray(bounds_by_family[family])[:2], np.asarray(bounds_by_family[family])[2:]
        bounds = np.concatenate((np.maximum(0, low-2/1024), np.minimum(1, high+2/1024)))
        rows.append(material_sample_weights(point, bounds, 17 if family == 'hair_front' else 9,
                    child_warp_size=HEAD_HAIR_SWAY_GRID if family == 'hair_front' else None))
    samples = np.asarray(rows)
    centers = np.asarray([point for _, _, point in sources])
    radius = .13
    distance = np.linalg.norm(grid[:, None] - centers[None, :], axis=2) / radius
    basis = np.maximum(0, 1-distance)**4 * (1+4*distance)
    def smooth(value):
        value = np.clip(value, 0, 1)
        return value*value*(3-2*value)
    basis *= (smooth(grid[:, 1]/.025) *
              (1-smooth((grid[:, 1]-.35)/(400/1024-.35))))[:, None]
    basis[(grid[:, 0] <= 0) | (grid[:, 0] >= 1) | (grid[:, 1] >= 400/1024)] = 0
    matrix = samples @ basis
    condition = float(np.linalg.cond(matrix))
    if not np.isfinite(condition) or condition > 100:
        raise ValueError(f'Unstable joint head-material correction: {condition}')
    return samples, basis @ np.linalg.inv(matrix), centers, condition


def refine_head_warp_sampling(builder, node_name, view):
    """Correct the existing native warp keyforms after semantic registration."""
    bounds = {layer['family']: builder.layer_bounds[layer['id']]
              for layer in builder.layers if layer.get('view') == view
              and layer.get('family') in {family for _, family, _ in material_sources(view)}}
    grid = np.asarray(builder.nodes[node_name].grid_vertices, dtype=float)
    samples, correction, centers, _ = head_sampling_correction(view, bounds, grid)
    for key in builder.params['ParamHeadTurn'].keyforms:
        # The exact native source-view key remains byte-for-byte identity.
        if key.value == VIEW_ANGLES[view]:
            continue
        actual = grid + np.asarray(key.deformer_offsets[node_name])
        target = np.asarray([builder._registered_point(tuple(p), view, key.value, 'head')
                             for p in centers])
        corrected = actual + correction_weight(view, key.value) * correction @ (target - samples @ actual)
        key.deformer_offsets[node_name] = [tuple(point) for point in corrected - grid]


def correction_weight(view, turn):
    """Full correction wherever this painted view is visible; smooth outside.

    Retain the pre-existing far hidden forms instead of fitting an absent
    view's paint across an unrelated opposite profile. This never changes
    visibility; the same native opacity keyforms remain responsible for that.
    """
    x = np.clip((abs(turn - VIEW_ANGLES[view]) - .3) / .2, 0, 1)
    return float(1 - x*x*(3-2*x))


def ornament_targets(family, view, turn):
    """A common near-side bead trajectory; missing far paint stays unmodified."""
    views = ORNAMENT_BEADS.get(family, {})
    if view not in views or abs(turn-VIEW_ANGLES[view]) > .300001:
        return None
    ordered = sorted(VIEW_ANGLES, key=VIEW_ANGLES.get)
    for first, last in zip(ordered, ordered[1:]):
        a, b = VIEW_ANGLES[first], VIEW_ANGLES[last]
        if a <= turn <= b and first in views and last in views:
            f = (turn-a)/(b-a)
            return (np.asarray(views[first])*(1-f)+np.asarray(views[last])*f)/1024
    return None


def registered_ornament_vertices(source, parent_grid, parent_rows, source_beads,
                                  target_beads, painted_top):
    """Undo the verified child sampling with a rooted material similarity.

    Only two unambiguously corresponding beads determine a scale/rotation and
    translation. The flower/root remains on its existing head chain; the short
    hanging strings absorb the small correction. Sway physics stays authored
    on the original parent, and no extra warp/opacity/texture is introduced.
    """
    source = np.asarray(source, dtype=float)
    size = int(round(len(source)**.5))
    if size*size != len(source):
        raise ValueError('Expected the existing square ornament art mesh')
    low, high = source.min(axis=0), source.max(axis=0)
    actual = warp_points(source, parent_grid, parent_rows)
    rows = []
    for point in source_beads:
        indices, weights = triangle_lattice_weights((point-low)/(high-low), size)
        row = np.zeros(len(source)); row[indices] = weights; rows.append(row)
    samples = np.asarray(rows)
    anchors = samples @ actual
    vector, target = anchors[1]-anchors[0], target_beads[1]-target_beads[0]
    length2 = float(vector@vector)
    if length2 <= 1e-8:
        raise ValueError('Ornament bead addresses are not distinct')
    a = float(vector@target)/length2
    b = float(vector[0]*target[1]-vector[1]*target[0])/length2
    scale = (a*a+b*b)**.5
    if not .65 <= scale <= 1.6:
        raise ValueError(f'Unbounded ornament material scale: {scale}')
    matrix = np.array([[a,-b],[b,a]])
    desired = (actual-anchors[0])@matrix.T+target_beads[0]
    start, end = painted_top+20/1024, float(np.min(source_beads[:,1]))-20/1024
    if end <= start:
        raise ValueError('No visible hanging string interval below ornament root')
    u = np.clip((source[:,1]-start)/(end-start),0,1)
    weight = u*u*(3-2*u)
    desired = actual+(desired-actual)*weight[:,None]
    local = unwarp_points(desired, parent_grid, parent_rows)
    return local, {'scale':scale,
        'maximumDestinationShiftPixels384':float(np.linalg.norm(desired-actual,axis=1).max()*384),
        'beadResidualPixels384':float(np.linalg.norm(samples@desired-target_beads,axis=1).max()*384)}


def refine_ornament_material_meshes(builder):
    """Apply bounded head keys only after the final native child chain exists."""
    by_id = {part.id:part for part in builder.parts}
    for layer in builder.layers:
        family, view = layer.get('family'), layer.get('view')
        if family not in ORNAMENT_BEADS or view not in ORNAMENT_BEADS[family] or layer.get('zone') != 'head':
            continue
        name = layer['id']; view_node = f'View_head_{view}'
        chain = []; parent = by_id[name].parent_deformer
        while parent != view_node:
            node = builder.nodes[parent]
            if not node.grid_vertices or not node.parent:
                raise ValueError(f'Ornament left its expected view warp: {name}')
            chain.append(node); parent = node.parent
        source = np.asarray(builder.meshes[name].vertices)
        beads = np.asarray(ORNAMENT_BEADS[family][view])/1024
        for key in builder.params['ParamHeadTurn'].keyforms:
            target = ornament_targets(family,view,key.value)
            if target is None or key.value == VIEW_ANGLES[view]:
                continue
            if name in key.mesh_offsets:
                raise ValueError(f'Ornament already has another head mesh binding: {name}')
            node = builder.nodes[view_node]
            grid = np.asarray(node.grid_vertices)+np.asarray(key.deformer_offsets[view_node])
            rows = node.grid_rows
            for child in reversed(chain):
                points = np.asarray(child.grid_vertices)
                if child.id in key.deformer_offsets:
                    points = points+np.asarray(key.deformer_offsets[child.id])
                grid = warp_points(points,grid,rows); rows = child.grid_rows
            local, _ = registered_ornament_vertices(source,grid,rows,beads,target,builder.layer_bounds[name][1])
            key.mesh_offsets[name] = [tuple(point) for point in local-source]
