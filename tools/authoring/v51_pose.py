"""Small material-space pose functions shared by the editable body rig.

These functions move existing mesh material. They do not generate textures,
change support contacts, or imply that a source preview is native acceptance.
"""
from __future__ import annotations

import math


class DrapeSections:
    """Invertible correspondence between actual painted skirt cross sections.

    The identical upper material is pinned. Below it, each horizontal painted
    interval has a positive affine map; transparent margins translate with
    their nearest edge instead of extrapolating a narrow hem row's scale.
    No texture or UV is changed.
    """
    def __init__(self, alpha, *, pin=555/1024):
        import numpy as np
        alpha = np.asarray(alpha)
        if alpha.ndim != 2:
            raise ValueError('Expected a skirt alpha plane')
        height, width = alpha.shape
        rows = []
        for y in range(math.floor(pin*height), height):
            xs = np.flatnonzero(alpha[y] >= 128)
            if len(xs) >= 2:
                rows.append(((y+.5)/height, (xs[0]+.5)/width, (xs[-1]+.5)/width))
        if len(rows) < 16:
            raise ValueError('Skirt has insufficient painted rows below its waist')
        self.rows = np.asarray(rows, dtype=float)
        self.pin = pin
        self.hem = float(self.rows[-1, 0])

    def edges(self, ys):
        import numpy as np
        return tuple(np.interp(ys, self.rows[:, 0], self.rows[:, i]) for i in (1, 2))

    def map(self, points, destination):
        import numpy as np
        if self.pin != destination.pin:
            raise ValueError('Corresponding skirts must share their pinned waist')
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        target = points.copy()
        active = points[:, 1] > self.pin
        x, y = points[active].T
        yy = self.pin+(y-self.pin)*(destination.hem-self.pin)/(self.hem-self.pin)
        left, right = self.edges(y)
        new_left, new_right = destination.edges(yy)
        width = np.maximum(right-left, 1e-8)
        xx = new_left+(x-left)*(new_right-new_left)/width
        xx = np.where(x < left, new_left+x-left, xx)
        xx = np.where(x > right, new_right+x-right, xx)
        target[active] = np.column_stack((xx, yy))
        return target


def expand_climb_material_layers(manifest):
    """Expose opt-in, separately painted climbing drapes as editable parts.

    Keep the caller's layer list shared so build reports hash every actual
    texture, including these derived inventory rows. This never writes the
    manifest or pixels; a second builder cannot append duplicate parts.
    """
    known = {layer['id'] for layer in manifest['layers']}
    for layer in list(manifest['layers']):
        path = layer.get('climbMaterialFile')
        if not path:
            continue
        if manifest.get('version') != 5 or layer.get('family') != 'skirt':
            raise ValueError('Climb materials require a v5 skirt layer')
        name = layer['id']+'_climb_drape'
        if name in known:
            continue
        drape = dict(layer, id=name, file=path, pose='climb_drape',
                     has_seated_variant=False, opacity=1.,
                     draw_order=layer['draw_order']+1, climb_replaces=layer['id'])
        drape.pop('climbMaterialFile', None)
        manifest['layers'].append(drape)
        known.add(name)
    return manifest


def _smooth(value):
    value = min(1., max(0., value))
    return value * value * (3 - 2 * value)


def _mix(a, b, value):
    return tuple(x + (y-x)*value for x, y in zip(a, b))


def _bone_angle(a, b):
    return math.atan2(b[1]-a[1], b[0]-a[0])


def free_arm_joints(source, side, pose, brace=0.):
    """Rotate two fixed-length bones into hanging or gently braced poses.

    The upper arm stays near the body. Bracing bends the elbow; it does not
    increase shoulder width or stretch the forearm to a screen-space target.
    """
    pose = min(1., max(0., (pose-.05)/.95))
    brace = min(1., max(0., brace))
    sign = 1 if side == 'l' else -1
    target = (math.pi/2 + sign*math.radians(17-5*brace),
              math.pi/2 + sign*math.radians(-6+30*brace))
    result = [tuple(source[0])]
    for index, (a, b) in enumerate(zip(source, source[1:])):
        start = _bone_angle(a, b)
        delta = (target[index]-start+math.pi) % (2*math.pi)-math.pi
        angle = start + pose*delta
        length = math.dist(a, b)
        result.append((result[-1][0]+length*math.cos(angle),
                       result[-1][1]+length*math.sin(angle)))
    return tuple(result)


def _rigid_bone_point(point, source, target):
    a, b = source
    c, d = target
    angle = _bone_angle(c, d)-_bone_angle(a, b)
    cosine, sine = math.cos(angle), math.sin(angle)
    x, y = point[0]-a[0], point[1]-a[1]
    return c[0]+cosine*x-sine*y, c[1]+sine*x+cosine*y


def hanging_material_point(point, source, target, amount, *, hand=False, gather=.60):
    """Two-bone skinning near the arm; a separate gravity frame below cuff.

    The sleeve hem never extrapolates the forearm's lateral displacement.
    The hand and complete cuff region use the exact same lower-bone field.
    Farther down, cloth folds inward while retaining its hanging length.
    """
    if amount <= 0:
        return tuple(point)
    upper = _rigid_bone_point(point, source[:2], target[:2])
    lower = _rigid_bone_point(point, source[1:], target[1:])
    elbow_weight = _smooth((point[1]-(source[1][1]-.016))/.032)
    posed = _mix(upper, lower, elbow_weight)
    if hand:
        return lower
    # The unpainted cuff border and the whole hand cut retain rigid geometry.
    # Only material well below that shared border is allowed to gather.
    dx, dy = source[2][0]-source[1][0], source[2][1]-source[1][1]
    across = abs((point[0]-source[1][0])*dy-(point[1]-source[1][1])*dx)/math.hypot(dx,dy)
    hang = (_smooth((point[1]-source[1][1]-.018)/.060)
            * _smooth((across-.025)/.070) * amount)
    gravity = (target[2][0]+gather*(point[0]-source[2][0]),
               target[2][1]+point[1]-source[2][1])
    return _mix(posed, gravity, hang)


def _drape(a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = max(1e-8, math.hypot(dx, dy))
    nx, ny = -dy/length, dx/length
    if ny < 0:
        nx, ny = -nx, -ny
    return nx*.7, .3+ny*.7


def joint_delta_point(point, source, target):
    """Bend an already hanging sleeve without rotating its hem sideways.

    The three authored joint displacements form a smooth vertical field.
    Below the wrist it is a rigid translation, so a fall preparation cannot
    extrapolate into the long fabric or gather material a second time.
    """
    deltas = [tuple(b-a for a,b in zip(s,t)) for s,t in zip(source,target)]
    if point[1] <= source[0][1]:
        delta = deltas[0]
    elif point[1] >= source[2][1]:
        delta = deltas[2]
    else:
        index = 0 if point[1] < source[1][1] else 1
        fraction = _smooth((point[1]-source[index][1]) /
                           (source[index+1][1]-source[index][1]))
        delta = _mix(deltas[index], deltas[index+1], fraction)
    return point[0]+delta[0], point[1]+delta[1]


def _hermite(a, b, da, db, u):
    return tuple((2*u**3-3*u*u+1)*a[i]+(u**3-2*u*u+u)*da[i]
                 +(-2*u**3+3*u*u)*b[i]+(u**3-u*u)*db[i] for i in (0, 1))


def sleeve_point(point, source_joints, target_joints, segment, *,
                 roundness=0., axis_joints=None):
    """Register split donor sleeves with shared elbow position and tangent.

    roundness=0 preserves the existing supported cloth registration exactly.
    A positive value rounds the centerline and cloth section through the
    elbow; shoulder and wrist positions remain exact. Source bones must be
    horizontal, as in the original independently painted sleeve materials.
    """
    if segment == 'hand':
        return (point[0]+target_joints[2][0]-source_joints[2][0],
                point[1]+target_joints[2][1]-source_joints[2][1])
    first = 0 if segment == 'upper' else 1
    a, b = source_joints[first:first+2]
    c, d = target_joints[first:first+2]
    u = (point[0]-a[0])/(b[0]-a[0])
    drop = point[1]-(a[1]+u*(b[1]-a[1]))
    axes = axis_joints or target_joints
    upper, lower = _drape(*axes[:2]), _drape(*axes[1:])
    middle = _mix(upper, lower, .5)
    start, end = (upper, middle) if first == 0 else (middle, lower)
    # Extrapolation outside a segment stays affine, avoiding a cubic tail
    # turning the transparent full-canvas lattice back through the material.
    rounding = roundness if 0 <= u <= 1 else 0.
    axis = _mix(start, end, u+rounding*(_smooth(u)-u))
    # The donor's shoulder end is painted as a flared horizontal opening.
    # Turning the upper bone almost vertical makes that opening an upright
    # white spike. Round and gather ONLY this shoulder material, with no
    # change at elbow/cuff or in the unrounded wall pose.
    if first == 0:
        cap = roundness*(1-_smooth((u-.05)/.45))
        axis = tuple(value*(1-.45*cap) for value in axis)
    line = _mix(c, d, u)
    common = tuple((target_joints[2][i]-target_joints[0][i])*.5 for i in (0, 1))
    bone = tuple(d[i]-c[i] for i in (0, 1))
    shoulder_tangent = (bone[0], bone[1]*.45)
    da, db = (shoulder_tangent, common) if first == 0 else (common, bone)
    curved = _hermite(c, d, da, db, u) if 0 <= u <= 1 else line
    if first == 0 and u < 0:
        # An affine continuation of the short shoulder tangent also gathers
        # the painted pixels just outside S. Never extrapolate a cubic there.
        curved = tuple(c[i]+u*da[i] for i in (0,1))
    line = _mix(line, curved, roundness if first == 0 and u < 0 else rounding)
    return line[0]+axis[0]*drop, line[1]+axis[1]*drop


def full_sleeve_point(point, source_joints, target_joints, *,
                      roundness=0., axis_joints=None):
    """One native field for both pieces, including their unpainted controls.

    Matching only the mathematical elbow cut is insufficient. Core prewarps
    whole child lattices through nonlinear parents before sampling material;
    separate extrapolated upper/lower grids then yield different cut points.
    Both pieces must therefore carry the same complete source-space field.
    The shoulder/wrist cells still use their original segment's mapping.
    """
    upper, elbow, wrist = source_joints
    lower_half = (point[0]-elbow[0])*(wrist[0]-upper[0]) >= 0
    return sleeve_point(point,source_joints,target_joints,
                        'lower' if lower_half else 'upper',
                        roundness=roundness,axis_joints=axis_joints)
