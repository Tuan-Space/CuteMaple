"""Opt-in climbing articulation for the editable Maple IR (never a MOC writer).

Call ``apply_climb_refinement(builder)`` after V5Builder._load_layers.  The
new control defaults to zero: the existing wall-to-rope and cleanup bindings
are preserved.  See ``climb_metadata`` for the matching motion/host contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from build_rig import clamp, rotate

CONTROL = "ParamClimbRefine"
HANDOFF = "ParamClimbHandoff"
DRAPE_BLEND = "ParamClimbDrapeBlend"
DRAPE_UNFOLD_END = .40
DRAPE_EXCHANGE_END = .52
DRAPE_OPACITY_GUARD = .9999
CYCLE_DURATION = 1.0
LEG_SELECTION_EPSILON = .001
AIRBORNE_RETRACTION = .065
CLIMB_LEG_VOLUME = .18
# Whole lower-body translation follows the visible support-palm plane, rather
# than the historical wrist pivot at .687. Recheck against each native export.
WALL_X = .719
HIP_X = .557
MIRROR_X = 1.016
# Keep the already exported support travel; regression compares these values
# to build_v5.  This module deliberately does not import its caller at load.
LEGACY_RISE = .16
RISE = .12
PHASE_KEYS = [0, .10, .18, .25, .35, .40, .50, .55, .60, .70, .78, .85, .90, 1.]
TRAVEL_KNOTS = [(0, 0), (.18, .08), (.40, .40), (.55, .48), (.78, .82), (1, 1)]


def smoothstep(value):
    value = clamp(value)
    return value * value * (3 - 2 * value)


def climb_travel(phase):
    for (a, x), (b, y) in zip(TRAVEL_KNOTS, TRAVEL_KNOTS[1:]):
        if phase <= b:
            return x + (y-x) * clamp((phase-a)/(b-a))
    return TRAVEL_KNOTS[-1][1]


def hand_reach(phase, near):
    a, b = (.10, .35) if near else (.60, .85)
    return smoothstep((phase-a)/(b-a))


def climb_hand_y(phase, near):
    return RISE * (climb_travel(phase) - hand_reach(phase, near))


def leg_registration(point, source, target, transverse=1.):
    hip, knee, ankle = source
    a, b, c, d = ((hip, knee, target[0], target[1]) if point[1] < knee[1]
                  else (knee, ankle, target[1], target[2]))
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = math.hypot(dx, dy)
    along = ((point[0]-a[0])*dx + (point[1]-a[1])*dy)/(length*length)
    across = (-(point[0]-a[0])*dy + (point[1]-a[1])*dx)/length * transverse
    tx, ty = d[0]-c[0], d[1]-c[1]
    tl = max(1e-8, math.hypot(tx, ty))
    return c[0]+along*tx-across*ty/tl, c[1]+along*ty+across*tx/tl


def body_shift(phase, direction):
    """A small rigid weight transfer, without scaling the face or costume."""
    return direction * (.025 + .009 * math.sin(2 * math.pi * phase)), \
        .007 * math.sin(2 * math.pi * phase)


def _mirror(point, direction):
    return point if direction > 0 else (MIRROR_X - point[0], point[1])


def _distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _ik(first, last, upper, lower, bend_sign):
    """Two-bone solution; bounded reach extension only when actually required."""
    dx, dy = last[0] - first[0], last[1] - first[1]
    distance = max(1e-9, math.hypot(dx, dy))
    scale = max(1., distance / (upper + lower - 1e-6))
    upper, lower = upper * scale, lower * scale
    along = (upper * upper - lower * lower + distance * distance) / (2 * distance)
    height = math.sqrt(max(0., upper * upper - along * along))
    return (first[0] + along * dx / distance - bend_sign * height * dy / distance,
            first[1] + along * dy / distance + bend_sign * height * dx / distance)


def original_arm_joints(side, direction, phase):
    near = (side == "l") == (direction > 0)
    shoulder = _mirror((.429 if near else .487, .426 if near else .415), direction)
    elbow = _mirror((.539 if near else .552, .460 if near else .510), direction)
    wrist = _mirror((.687 if near else .675, .344 if near else .446), direction)
    amount = LEGACY_RISE * (climb_travel(phase) - hand_reach(phase, near))
    sx, wx = shoulder[0], wrist[0]
    result = []
    for x, y in (shoulder, elbow, wrist):
        u = (x - sx) / (wx - sx)
        result.append((x, y + .085 * (1 - u) + amount * u))
    return tuple(result)


def refined_arm_joints(side, direction, phase):
    old_s, old_e, old_w = original_arm_joints(side, direction, phase)
    near = (side == 'l') == (direction > 0)
    wrist = old_w[0], old_w[1] + (RISE-LEGACY_RISE) * (climb_travel(phase)-hand_reach(phase, near))
    dx, dy = body_shift(phase, direction)
    shoulder = old_s[0] + dx, old_s[1] + dy
    # Keep each bone's existing length. Select the same downward elbow branch,
    # so reaching changes elbow flexion instead of stretching the sleeve axis.
    cross = ((old_e[0] - old_s[0]) * (old_w[1] - old_s[1]) -
             (old_e[1] - old_s[1]) * (old_w[0] - old_s[0]))
    elbow = _ik(shoulder, wrist, _distance(old_s, old_e), _distance(old_e, old_w),
                -1 if cross > 0 else 1)
    return shoulder, elbow, wrist


def _triangle_mapping(point, source, target):
    """One shared affine field for both sleeve pieces; all seams stay shared."""
    a, b, c = source
    x, y = point[0] - a[0], point[1] - a[1]
    bx, by, cx, cy = b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1]
    determinant = bx * cy - by * cx
    if abs(determinant) < 1e-8:
        raise ValueError("Degenerate climb arm reference triangle")
    u, v = (x * cy - y * cx) / determinant, (bx * y - by * x) / determinant
    a, b, c = target
    return a[0] + u * (b[0] - a[0]) + v * (c[0] - a[0]), \
        a[1] + u * (b[1] - a[1]) + v * (c[1] - a[1])


def _sleeve_mapping(point, side, direction, phase):
    joints = refined_arm_joints(side, direction, phase)
    q = _triangle_mapping(point, original_arm_joints(side, direction, phase), joints)
    # Preserve the sleeve's generous transverse drape instead of reducing it
    # to a straight strap along an extended bone. Both segments use this same
    # continuous nearest-bone field, including their common elbow seam.
    nearest = []
    for a, b in zip(joints, joints[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        u = clamp(((q[0]-a[0])*dx+(q[1]-a[1])*dy)/(dx*dx+dy*dy))
        p = a[0]+u*dx, a[1]+u*dy
        nearest.append((_distance(p, q), p))
    _, p = min(nearest, key=lambda item: item[0])
    return q[0]+.32*(q[0]-p[0]), q[1]+.32*(q[1]-p[1])


def foot_reach(phase, near):
    # Opposite hand/foot travel together; the other pair remains planted.
    return hand_reach(phase, not near)


def refined_leg_joints(side, direction, phase, sole_depth):
    near = (side == "l") == (direction > 0)
    reach = foot_reach(phase, near)
    dx, dy = body_shift(phase, 1)
    hip = (HIP_X + (.012 if not near else 0) + dx, .674 + dy)
    ankle = (WALL_X - sole_depth - AIRBORNE_RETRACTION * math.sin(math.pi * reach),
             (.795 if near else .855) + RISE * (climb_travel(phase) - reach))
    # Knee toward the wall. The independently dorsiflexed foot no longer
    # points down merely because the shin is near vertical.
    knee = _ik(hip, ankle, .108, .112, -1)
    return tuple(_mirror(p, direction) for p in (hip, knee, ankle))


def climb_leg_width(point, source, amount=1.):
    """Give trousers transverse volume, tapering to the unchanged ankle.

    Bone lengths and shoe artwork remain fixed. The narrow taper ends before
    the ankle's rotation blend so the actual planted sole cannot move.
    """
    ankle=source[2]
    taper=1-smoothstep((point[1]-(ankle[1]-.050))/.040)
    return 1+CLIMB_LEG_VOLUME*clamp(amount)*taper


def _leg_mapping(point, source, target, direction, foot_angle=None):
    bent = leg_registration(point, source, target, climb_leg_width(point,source))
    # Cloth can bunch against the wall, but it must not push the planted shoe
    # backwards. Keep the toe trajectory independent of the trouser outline.
    wall = WALL_X if direction > 0 else MIRROR_X-WALL_X
    inner = wall-direction*.026
    intrusion = direction*(bent[0]-inner)
    if intrusion > 0:
        bent = (inner+direction*.024*(1-math.exp(-intrusion/.024)), bent[1])
    ankle = source[2]
    # A distinct ankle hinge, with a short continuous trouser-to-shoe blend.
    foot = rotate(point, ankle, -90 * direction if foot_angle is None else foot_angle)
    foot = target[2][0] + foot[0] - ankle[0], target[2][1] + foot[1] - ankle[1]
    weight = smoothstep((point[1] - (ankle[1] - .006)) / .020)
    return tuple(a + (b - a) * weight for a, b in zip(bent, foot))


def skirt_fold_point(point, phase, direction, panel=(.34, .575, .72, .91)):
    """Gather the painted forward sector in the legs' final pose coordinates.

    ``panel`` is the actual visible skirt bounds in this same coordinate
    frame. The rear train keeps its length; cloth over the thigh rises toward
    the knee. A forward gather includes the hanging front embroidery instead
    of placing a narrow maximum beyond the painted edge.
    """
    x, y = point
    left, waist, right, hem = panel
    if y <= waist:
        return x, y
    forward_x = x if direction > 0 else MIRROR_X-x
    rear, front = (left, right) if direction > 0 else (MIRROR_X-right, MIRROR_X-left)
    span = front-rear
    sector = smoothstep((forward_x-(rear+.40*span))/(.28*span))
    if sector == 0:
        return x, y
    legs = [refined_leg_joints(side, 1, phase, .038) for side in ('l', 'r')]
    weights = [math.exp((.720-knee[1])/.020) for _, knee, _ in legs]
    total = sum(weights)
    gather, lift = 0., 0.
    for (hip, knee, _), weight in zip(legs, weights):
        # The cloth starts near the hip, not at the knee outside the robe.
        center = hip[0]+.25*(knee[0]-hip[0])
        influence = sector*weight/total * math.exp(-((forward_x-center)/max(.085, .32*span))**2)
        gather += influence
        lift += influence * min(.18, .68*(hem-waist), max(.07, hem-knee[1]-.032))
    t = clamp((y-waist)/(hem-waist))
    # Zero slope at the waist; bounded derivative through the gathered hem.
    # This avoids crushing or reversing vertical cloth rows at a raised knee.
    lower = 1.2*t*t/(t+.2)
    return x+direction*.025*gather*lower, y-lift*lower


def _climb_material_pose(point, view, phase, direction, inverse=False):
    """Conjugate a material-local point with its normal profile/body parents."""
    from view_registration import register_point, VIEW_ANGLES
    dx, dy = body_shift(phase, direction)
    target_view = 'right' if direction > 0 else 'left'
    x, y = point
    if inverse:
        x, y = x-dx, y-dy
        # Inverse of Climb_Body_Lower at Active=1, including its fixed ends.
        y = y-.085 if y <= .625 else (y-.19975)/.7875 if y < .94 else y
        return register_point((x,y), target_view, VIEW_ANGLES[view], 'body')
    x, y = register_point((x,y), view, direction, 'body')
    return x+dx, y+.085*(1-clamp((y-.54)/.4))+dy


def skirt_material_fold_point(point, phase, direction, view, panel_bounds):
    """Map the common cloth/leg pose back into this PNG's own local space."""
    # Keep a complete unchanged lattice row beneath the painted waist. A
    # pointwise pin at the PNG cut alone leaks fold offsets upward when the
    # 17-row parent and the skirt mesh interpolate different sampling rows.
    a = _climb_material_pose((panel_bounds[0],max(.575,panel_bounds[1])), view, phase, direction)
    b = _climb_material_pose(panel_bounds[2:], view, phase, direction)
    panel = (min(a[0],b[0]), min(a[1],b[1]), max(a[0],b[0]), max(a[1],b[1]))
    posed = _climb_material_pose(point, view, phase, direction)
    folded = skirt_fold_point(posed, phase, direction, panel)
    return _climb_material_pose(folded, view, phase, direction, inverse=True)


def drape_phase_point(point, phase, direction, view, panel_bounds):
    """Only a small knee-following change for already painted bent drapery.

    The artist supplied the large fold. Applying skirt_fold_point here would
    gather it twice, recreating the inflated front panel. Waist and rear train
    stay at their registered positions and original length.
    """
    def knee_center(value):
        knees = [refined_leg_joints(side, 1, value, .038)[1] for side in ('l','r')]
        weights = [math.exp((.72-k[1])/.035) for k in knees]
        return tuple(sum(k[i]*w for k,w in zip(knees,weights))/sum(weights) for i in (0,1))
    before, after = knee_center(0), knee_center(phase)
    first_shift, shift = body_shift(0,1), body_shift(phase,1)
    delta = tuple((after[i]-shift[i])-(before[i]-first_shift[i]) for i in (0,1))
    delta = (max(-.012,min(.012,delta[0])), max(-.028,min(.028,delta[1])))
    x,y = point
    if y <= .575:
        return x,y
    left,_,right,hem = panel_bounds
    front = (right-x)/(right-left) if direction<0 else (x-left)/(right-left)
    sector = smoothstep((front-.36)/.40)
    # Registered source-space crease, localized well above the trailing hem.
    fold = smoothstep((y-.575)/.07)*(1-smoothstep((y-.73)/.14))
    posed = _climb_material_pose(point,view,phase,direction)
    moved = (posed[0]+direction*delta[0]*sector*fold,
             posed[1]+delta[1]*sector*fold)
    return _climb_material_pose(moved,view,phase,direction,inverse=True)


def _apply_climb_drape_materials(builder):
    """Retire the matching standing surface AND its opaque skirt underlay."""
    parts = {part.id:part for part in builder.parts}
    for layer in builder.layers:
        if layer.get('pose') != 'climb_drape':
            continue
        name, original = layer['id'],layer['climb_replaces']
        if original not in parts:
            raise ValueError(f'{name}: missing original skirt surface')
        view = layer.get('view','front')
        direction = -1 if view.startswith('left') else 1
        builder.opacity(CONTROL,name,lambda v:v)
        builder.opacity(CONTROL,original,lambda v:1-v)
        for underlay in builder.layers:
            if (underlay.get('family')=='costume_underlay_skirt'
                    and underlay.get('view','front')==view):
                builder.opacity(CONTROL,underlay['id'],lambda v:1-v)
        part = parts[name]
        node = builder.warp('ClimbDrape_'+name,part.parent_deformer,25)
        part.parent_deformer = node
        bounds = _painted_skirt_bounds(builder,layer)
        _gated_phase(builder,node,direction,
            lambda p,v,d=direction,view=view,bounds=bounds:
            drape_phase_point(p,v,d,view,bounds))


def _painted_skirt_bounds(builder, layer):
    """Ignore faint stray pixels when locating each painted skirt panel."""
    from PIL import Image
    with Image.open(builder.asset_root/layer['file']) as image:
        box = image.getchannel('A').point(lambda alpha: 255 if alpha >= 128 else 0).getbbox()
    if box is None:
        raise ValueError(f"{layer['id']}: skirt has no opaque painted panel")
    return tuple(value/(builder.width if i%2==0 else builder.height) for i,value in enumerate(box))


def _apply_drape_transfer(builder):
    """Unfold the new painted garment before exchanging its material.

    The independent material blend holds the climbing garment opaque while
    its two leaf warps converge to the receiving skirt's actual parent pose.
    Old garments, body/leg/hand fields and their timing remain untouched.
    """
    import numpy as np
    from PIL import Image
    from v51_pose import DrapeSections
    from climb_skirt_transfer import NativeClothParents, motion_settings
    from native_warp_sampling import warp_points
    from maple_motions import build_motion, TRANSITIONS
    from build_v4 import view_weight

    layers = [layer for layer in builder.layers if layer.get('pose') == 'climb_drape']
    if not layers:
        return
    if DRAPE_BLEND in builder.params:
        raise ValueError('Drape material handoff already applied')
    builder.parameter(DRAPE_BLEND,0,1,keys=[0,DRAPE_OPACITY_GUARD,1])
    parts = {part.id: part for part in builder.parts}
    parents = NativeClothParents(builder)
    entries = []
    for layer in layers:
        new, old = layer['id'], layer['climb_replaces']
        old_layer = builder.layer_by_id[old]
        sections = []
        for material in (layer, old_layer):
            with Image.open(builder.asset_root/material['file']) as image:
                sections.append(DrapeSections(np.asarray(image.getchannel('A'))))
        new_section, old_section = sections
        new_parent, old_parent = parts[new].parent_deformer, parts[old].parent_deformer
        view = layer.get('view', 'front')
        underlays = [item['id'] for item in builder.layers
                     if item.get('family') == 'costume_underlay_skirt'
                     and item.get('view', 'front') == view]
        for key in builder.params[CONTROL].keyforms:
            for material in [new,old,*underlays]:
                key.opacity_overrides.pop(material,None)
        builder.opacity(DRAPE_BLEND,new,lambda value:value)
        for material in [old,*underlays]:
            builder.opacity(DRAPE_BLEND,material,lambda value:
                min(1.,(1-value)/(1-DRAPE_OPACITY_GUARD)))
        part = parts[new]
        parent = part.parent_deformer
        ancestor = builder.nodes[parent]
        # A nested grid retains the original sampling at Handoff=0.
        size = 2*(ancestor.grid_rows-1)+1
        name = 'DrapeTransfer_'+new
        builder.warp(name,parent,size)
        part.parent_deformer = name
        for key in builder.params[HANDOFF].keyforms:
            key.deformer_offset_weights[name] = key.value
        entries.append((name,parent,view,old_parent,new_section,old_section))
    defaults = {name: parameter.default for name, parameter in builder.params.items()}
    motions = {direction: build_motion('climb_to_top_'+side,
               TRANSITIONS['climb_to_top_'+side], defaults)
               for direction, side in [(-1, 'left'), (1, 'right')]}
    for key in builder.params['ParamPoseClimb'].keyforms:
        direction = 1 if key.value >= 0 else -1
        seconds = (1-abs(key.value))*1.8
        settings = motion_settings(motions[direction],seconds)
        settings['ParamPoseClimb'] = key.value
        cache = {}
        for name,parent,view,old_parent,new_section,old_section in entries:
            node = builder.nodes[name]
            source = np.asarray(node.grid_vertices)
            local = source.copy()
            if abs(key.value) <= 1e-12 or view_weight(view, settings['ParamBodyTurn']) <= 1e-12:
                key.deformer_offsets[name] = [tuple(row) for row in local-source]
                continue
            own,other = new_section,old_section
            own_node = builder.nodes[parent]
            grid = parents.grid(parent, settings, cache)
            # Fit a single waist-pinned shear and positive vertical unfold.
            # Applying inverse compensation only to painted controls creates
            # discontinuities when the native child lattice is resampled.
            # This field has no lateral/hem cutoffs and cannot fold locally:
            # below the waist its determinant is the positive height ratio.
            ys = np.linspace(own.pin+.005, own.hem-.005, 25)
            left,right = own.edges(ys)
            samples = np.asarray([(x,y) for y,l,r in zip(ys,left,right)
                                  for x in (l,(l+r)/2,r)])
            target_node = builder.nodes[old_parent]
            desired = warp_points(own.map(samples,other),
                parents.grid(old_parent,settings,cache),target_node.grid_rows,target_node.grid_cols)
            height = samples[:,1]-own.pin
            coefficients = np.zeros(2)
            for _ in range(4):
                fitted = samples+height[:,None]*coefficients
                posed = warp_points(fitted,grid,own_node.grid_rows,own_node.grid_cols)
                derivatives = []
                for axis in (0,1):
                    shifted = fitted.copy()
                    shifted[:,axis] += height*1e-5
                    derivatives.append((warp_points(shifted,grid,own_node.grid_rows,
                                                     own_node.grid_cols)-posed).ravel()/1e-5)
                step = np.linalg.lstsq(np.column_stack(derivatives),(desired-posed).ravel(),rcond=None)[0]
                coefficients += step
                # The long trailing garment is never compressed to match a
                # fold. Only a small positive unfold and sideways shear fit.
                coefficients[0] = np.clip(coefficients[0],-.35,.35)
                coefficients[1] = np.clip(coefficients[1],0.,.12)
            amount = smoothstep(seconds/DRAPE_UNFOLD_END)
            local = source+np.maximum(source[:,1]-own.pin,0)[:,None]*coefficients*amount
            if not np.isfinite(local).all():
                raise ValueError(f'Nonfinite drape transfer: {name}/{key.value}')
            key.deformer_offsets[name] = [tuple(row) for row in local-source]


def _gated_phase(builder, node, direction, fn):
    builder.deform("ParamClimbPhase", node, fn)
    for key in builder.params["ParamClimbDirection"].keyforms:
        key.deformer_offset_weights[node] = clamp(direction * key.value)
    for key in builder.params[CONTROL].keyforms:
        key.deformer_offset_weights[node] = key.value


def _mesh_point(mesh, positions, uv):
    import numpy as np
    source = np.asarray(mesh.uvs)
    for ids in mesh.triangles:
        a, b, c = source[list(ids)]
        try:
            weights = np.linalg.solve(np.array([b-a, c-a]).T, np.asarray(uv)-a)
        except np.linalg.LinAlgError:
            continue
        if min(weights) >= -1e-8 and sum(weights) <= 1+1e-8:
            return tuple(positions[ids[0]]*(1-sum(weights)) + positions[ids[1]]*weights[0] + positions[ids[2]]*weights[1])
    raise ValueError(f'{mesh.part_id}: handoff joint is outside source mesh')


def handoff_material_projection(seconds, near, direction):
    """Turn the left material over while the released foot is under the hem.

    The climb family uses a genuine reflected mesh on the left. Fading its
    reflection residual in world coordinates while rotating the ankle shears
    the shoe into a long point. Keep the reflection in the moving local frame;
    the brief front/back material exchange happens after the foot tucks in.
    Endpoint geometry remains supplied by the original receiving drawables.
    """
    if direction > 0:
        return 1.
    start, end = (.95, 1.22) if near else (.90, 1.15)
    return -math.cos(math.pi*smoothstep((seconds-start)/(end-start)))


def _handoff_map(point, source, joints, foot_angle, transverse=1., leg_volume=0.):
    bent = leg_registration(point, source, joints,
                            transverse*climb_leg_width(point,source,leg_volume))
    ankle = source[2]
    local = (ankle[0]+(point[0]-ankle[0])*transverse, point[1])
    rotated = rotate(local, ankle, foot_angle)
    foot = joints[2][0]+rotated[0]-ankle[0], joints[2][1]+rotated[1]-ankle[1]
    w = smoothstep((point[1]-(ankle[1]-.006))/.020)
    return tuple(a+(b-a)*w for a, b in zip(bent, foot))


def _add_refined_legs(builder):
    """Native-exact phase meshes; nonlinear ankle warps are not baked from IR.

    The material ids l/r denote the near/far source artwork on this new family.
    One affine mirror turns the entire pose toward the opposite wall. This
    avoids multiplying a nonlinear phase field by a second direction axis.
    Legacy meshes and their parent chains remain intact at Refine=0.
    """
    from climb_contact_refinement import painted_sole_depth
    mirror = builder.warp('RefinedClimbLegMirror', builder.scene, 2)
    builder.deform('ParamClimbDirection', mirror,
                   lambda p, v: (MIRROR_X/2+v*(p[0]-MIRROR_X/2), p[1]))
    for side in ('l', 'r'):
        original = f'leg_{side}_climb'
        name = original+'_refined'
        old_part = next(p for p in builder.parts if p.id == original)
        mesh = builder.meshes[original]
        layer = builder.layer_by_id[original]
        source = tuple(tuple(layer[k]) for k in ('hip', 'pivot', 'ankle'))
        sole_depth = painted_sole_depth(builder, original, source[2][1])
        builder.parts.append(old_part.model_copy(update={'id': name, 'parent_deformer': mirror, 'draw_order': 83 if side == 'l' else 82}))
        builder.meshes[name] = mesh.model_copy(deep=True, update={'part_id': name})
        registered = dict(layer, id=name, pose='climb_refined')
        builder.layers.append(registered)
        builder.layer_by_id[name] = registered
        builder.layer_bounds[name] = builder.layer_bounds[original]
        builder.opacity('ParamClimbActive', name, lambda v: v)
        builder.opacity(CONTROL, name, lambda v: 1. if v >= LEG_SELECTION_EPSILON else 0.)
        builder.opacity(CONTROL, original, lambda v: 0. if v >= LEG_SELECTION_EPSILON else 1.)
        near = side == 'l'
        for key in builder.params['ParamClimbPhase'].keyforms:
            phase = key.value
            target = refined_leg_joints(side, 1, phase, sole_depth)
            # A little ankle release occurs only while the foot is in the air.
            # Both planted endpoints retain the exact original sole plane.
            angle = -90+13*math.sin(math.pi*foot_reach(phase, near))
            mapped = [_leg_mapping(p, source, target, 1, angle) for p in mesh.vertices]
            key.mesh_offsets[name] = [(q[0]-p[0], q[1]-p[1]) for p, q in zip(mesh.vertices, mapped)]


def _apply_leg_handoff(builder):
    """One visible pair of legs travels from the wall to identical swing pixels.

    These two drawables reuse the exact existing texture/UV/topology, without
    new artwork. T=0 preserves all historical opacity bindings. T=1 hides the
    old alternative legs, preventing their separated shoes crossfading.
    """
    import numpy as np
    from build_rig import Rig
    from diagnose_rig import DiagnosticRenderer
    builder.parameter(HANDOFF, 0, 1, keys=[0, LEG_SELECTION_EPSILON, 1])
    rig = Rig(meta={'name': 'Maple handoff source'}, textures=builder.textures,
              parts=builder.parts, meshes=list(builder.meshes.values()),
              deformers=list(builder.nodes.values()), parameters=list(builder.params.values()))
    r = DiagnosticRenderer.__new__(DiagnosticRenderer)
    r.rig = rig
    r.meshes = {m.part_id: m for m in rig.meshes}
    r.nodes = {d.id: d for d in rig.deformers}
    initial = {}
    for d in (-1, 1):
        initial[d] = r.evaluate({CONTROL: 1, 'ParamClimbActive': 1,
            'ParamClimbDirection': d, 'ParamPoseClimb': d, 'ParamClimbPhase': 0,
            'ParamArmPoseMode': 1, 'ParamBodyTurn': d, 'ParamHeadTurn': d})
    goal_pose = r.evaluate({'ParamPoseSwing': 1, 'ParamLegLB': 6, 'ParamLegRB': 6})

    old_legs = [p.id for p in builder.parts if builder.layer_by_id[p.id]['role'].startswith('leg_')]
    for name in old_legs:
        if name.endswith('_climb_refined'):
            # During handoff -> drag, do not reintroduce the source climb pair
            # between the outgoing handoff legs and incoming standing legs.
            builder.opacity(HANDOFF, name, lambda v: 0. if v >= LEG_SELECTION_EPSILON else 1.)
        else:
            builder.opacity(HANDOFF, name, lambda v: 1-v)
    for side in ('l', 'r'):
        original = f'leg_{side}_climb_refined'
        swing = f'leg_{side}_swing'
        name = f'leg_{side}_climb_handoff'
        source_part = next(p for p in builder.parts if p.id == original)
        source_mesh = builder.meshes[original]
        goal_mesh = builder.meshes[swing]
        if source_mesh.uvs != goal_mesh.uvs or source_mesh.triangles != goal_mesh.triangles:
            raise ValueError('Handoff requires identical existing leg UVs and topology')
        layer = builder.layer_by_id[original]
        swing_layer = builder.layer_by_id[swing]
        if (builder.asset_root/layer['file']).read_bytes() != (builder.asset_root/swing_layer['file']).read_bytes():
            raise ValueError('Handoff may only reuse pixel-identical climbing/swing leg sources')
        new_part = source_part.model_copy(update={'id': name, 'opacity': 1., 'parent_deformer': builder.scene})
        new_mesh = source_mesh.model_copy(deep=True, update={'part_id': name})
        builder.parts.append(new_part)
        builder.meshes[name] = new_mesh
        new_layer = dict(layer, id=name, pose='climb_handoff')
        builder.layers.append(new_layer)
        builder.layer_by_id[name] = new_layer
        builder.layer_bounds[name] = builder.layer_bounds[original]
        builder.opacity(HANDOFF, name, lambda v: v)
        source = tuple(tuple(layer[k]) for k in ('hip', 'pivot', 'ankle'))
        goal_vertices = goal_pose[swing][0]
        goal_joints = tuple(_mesh_point(source_mesh, goal_vertices, uv) for uv in source)
        goal_mapping = np.array([_handoff_map(p, source, goal_joints, 0) for p in source_mesh.vertices])
        final_residual = goal_vertices-goal_mapping
        for key in builder.params['ParamPoseClimb'].keyforms:
            direction = 1 if key.value >= 0 else -1
            seconds = (1-abs(key.value))*1.8
            near = side == 'l'
            release = .50 if near else .24
            arrive = 1.55 if near else 1.30
            progress = smoothstep((seconds-release)/(arrive-release))
            hip_progress = .75*progress+.25*smoothstep(seconds/1.55)
            first_vertices = initial[direction][original][0]
            first_joints = tuple(_mesh_point(source_mesh, first_vertices, uv) for uv in source)
            first_mapping = np.array([_handoff_map(p, source, first_joints, -90*direction, direction, 1.)
                                      for p in source_mesh.vertices])
            initial_residual = first_vertices-first_mapping
            hip = tuple(a+(b-a)*hip_progress for a, b in zip(first_joints[0], goal_joints[0]))
            ankle = tuple(a+(b-a)*progress for a, b in zip(first_joints[2], goal_joints[2]))
            upper = _distance(*first_joints[:2])*(1-progress)+_distance(*goal_joints[:2])*progress
            lower = _distance(*first_joints[1:])*(1-progress)+_distance(*goal_joints[1:])*progress
            s, e, w = first_joints
            cross = (e[0]-s[0])*(w[1]-s[1])-(e[1]-s[1])*(w[0]-s[0])
            knee = _ik(hip, ankle, upper, lower, -1 if cross > 0 else 1)
            if seconds <= 1e-9:
                target = first_vertices
            elif seconds >= arrive:
                target = goal_vertices
            else:
                joints = (hip, knee, ankle)
                projection = handoff_material_projection(seconds, near, direction)
                target = np.array([_handoff_map(p, source, joints, -90*direction*(1-progress), projection,
                                               1-progress)
                                   for p in source_mesh.vertices])
                target += initial_residual*(1-progress)+final_residual*progress
                # Bring the last small IK difference to the exact receiving
                # drawable before the layer exchange. This preserves pixels,
                # not just an ankle point, at the handoff boundary.
                settle = smoothstep((progress-.78)/.22)
                target = target*(1-settle)+goal_vertices*settle
            key.mesh_offsets[name] = [tuple(q-np.array(p)) for p, q in zip(source_mesh.vertices, target)]


def _apply_climb_body_and_skirt(builder):
    # Outer body translation moves the entire head/torso, not their proportions.
    current = "Climb_Body_Lower"
    parent = builder.nodes[current].parent
    for direction in (-1, 1):
        node = builder.warp(f"RefinedClimbBody_{direction}", parent, 17)
        builder.nodes[current].parent = node
        _gated_phase(builder, node, direction, lambda p, v, d=direction:
                     tuple(a + b for a, b in zip(p, body_shift(v, d))))
        current = node

    # Fold each visible skirt and its matching opacity underlay through the
    # same field. Leaving the underlay behind recreates the old rigid bell.
    panel_layers = {layer.get('view', 'front'): layer for layer in builder.layers
                    if layer.get('family') == 'skirt' and layer.get('pose', 'rest') == 'rest'}
    panel_bounds_by_view = {view: _painted_skirt_bounds(builder, layer)
                            for view, layer in panel_layers.items()}
    for part in builder.parts:
        layer = builder.layer_by_id[part.id]
        if layer.get('role') != 'clothing' or layer.get('pose', 'rest') != 'rest':
            continue
        current = f'ClimbFold_{part.id}'
        if current not in builder.nodes:
            continue
        # The legacy 19% lower-skirt compression must not stack with the
        # knee-local cloth field. It remains exactly unchanged at Refine=0.
        for key in builder.params[CONTROL].keyforms:
            key.deformer_offset_weights[current] = 1-key.value
        parent = builder.nodes[current].parent
        view = layer.get('view', 'front')
        panel_bounds = panel_bounds_by_view.get(view) or _painted_skirt_bounds(builder, layer)
        for direction in (-1, 1):
            node = builder.warp(f'RefinedClimbSkirt_{direction}_{part.id}', parent, 17)
            builder.nodes[current].parent = node
            _gated_phase(builder, node, direction,
                         lambda p, v, d=direction, view=view, bounds=panel_bounds:
                         skirt_material_fold_point(p, v, d, view, bounds))
            current = node


def apply_climb_refinement(builder):
    """Apply once after layers, before Rig construction; mutate this builder only."""
    if CONTROL in builder.params:
        raise ValueError("Climb refinement has already been applied")
    required = ["Climb_Body_Lower", "ClimbLeg_l", "ClimbLeg_r"]
    if any(name not in builder.nodes for name in required):
        raise ValueError("Call after V5Builder._load_layers has created the climb rig")
    builder.parameter(CONTROL, 0, 1, keys=[0, LEG_SELECTION_EPSILON, 1])
    _apply_climb_body_and_skirt(builder)
    _apply_climb_drape_materials(builder)

    # Identical affine fields on both pieces preserve the entire shared elbow
    # seam, not just a single sampled wrist. The wrist itself does not move.
    for side in ("l", "r"):
        for segment in ("upper", "lower", "hand"):
            current = f"ClimbShoulder_{side}_{segment}"
            parent = builder.nodes[current].parent
            phase_zero_grids = {}
            for direction in (-1, 1):
                node = builder.warp(f"RefinedClimbArm_{direction}_{side}_{segment}", parent, 25)
                builder.nodes[current].parent = node
                if segment == 'hand':
                    def hand_shift(p, v, s=side, d=direction):
                        old = original_arm_joints(s, d, v)[2]
                        new = refined_arm_joints(s, d, v)[2]
                        return p[0]+new[0]-old[0], p[1]+new[1]-old[1]
                    _gated_phase(builder, node, direction, hand_shift)
                else:
                    _gated_phase(builder, node, direction, lambda p, v, s=side, d=direction:
                                 _sleeve_mapping(p, s, d, v))
                    zero = next(k for k in builder.params['ParamClimbPhase'].keyforms if k.value == 0)
                    phase_zero_grids[direction] = [tuple(a+b for a, b in zip(p, delta))
                        for p, delta in zip(builder.nodes[node].grid_vertices, zero.deformer_offsets[node])]
                current = node
            if segment != 'hand':
                from build_v5 import supported_arm_axes
                from diagnose_rig import warp_points
                transfer = builder.warp(f'RefinedClimbTransfer_{side}_{segment}', parent, 17)
                builder.nodes[current].parent = transfer
                def follow_transfer(p, value, s=side, grids=phase_zero_grids):
                    direction = 1 if value >= 0 else -1
                    _, wrist = supported_arm_axes(s, value, builder._joints(s)[0])
                    mapped = warp_points([wrist], grids[direction], 25, 25)[0]
                    return p[0]+wrist[0]-mapped[0], p[1]+wrist[1]-mapped[1]
                builder.deform('ParamPoseClimb', transfer, follow_transfer)
                for key in builder.params[CONTROL].keyforms:
                    key.deformer_offset_weights[transfer] = key.value

        part = next(p for p in builder.parts if p.id == f"leg_{side}_climb")
        layer = builder.layer_by_id[part.id]
        source = tuple(tuple(layer[k]) for k in ("hip", "pivot", "ankle"))
        sole_depth = max(p[1] for p in builder.meshes[part.id].uvs) - source[2][1]
        if not .015 < sole_depth < .08:
            raise ValueError("Climb shoe source ankle/sole bounds are inconsistent")
        # Disable the old rigid-leg registration only while opted in. Both old
        # nodes remain byte-for-byte effective at Refine=0, including transfer.
        for name in (f"ClimbLeg_{side}", f"ClimbLegCycle_{side}"):
            for key in builder.params[CONTROL].keyforms:
                key.deformer_offset_weights[name] = 1 - key.value
        current = f"ClimbLegCycle_{side}"
        parent = builder.nodes[current].parent
        for direction in (-1, 1):
            node = builder.warp(f"RefinedClimbLeg_{direction}_{side}", parent, 31)
            builder.nodes[current].parent = node
            _gated_phase(builder, node, direction, lambda p, v, s=side, d=direction,
                         source=source, depth=sole_depth:
                         _leg_mapping(p, source, refined_leg_joints(s, d, v, depth), d))
            current = node
    _add_refined_legs(builder)
    _apply_leg_handoff(builder)
    from climb_skirt_transfer import apply_climb_skirt_transfer
    apply_climb_skirt_transfer(builder)
    _apply_drape_transfer(builder)
    return climb_metadata()


def climb_metadata():
    return {"phaseParameter": "ParamClimbPhase", "cycleDuration": CYCLE_DURATION,
            "risePerCycle": RISE, "phaseTravel": [[p, climb_travel(p)] for p in PHASE_KEYS],
            "supportWindows": {"near": [[0, .10], [.35, 1]], "far": [[0, .60], [.85, 1]]},
            "nearHand": {"left": "r", "right": "l"},
            "phaseWrapHasContinuousWorldSupport": True,
            "refinementParameter": CONTROL,
            "handoffParameter": HANDOFF,
            "handoffDrawables": ['leg_l_climb_handoff', 'leg_r_climb_handoff'],
            "refinedLegDrawables": ['leg_l_climb_refined', 'leg_r_climb_refined'],
            "refinedLegSelectionEpsilon": LEG_SELECTION_EPSILON,
            "refinedLegRoles": {'leg_l_climb_refined': 'near', 'leg_r_climb_refined': 'far'},
            "refinedLegSourceForHandoff": {'leg_l_climb_handoff': 'leg_l_climb_refined',
                                           'leg_r_climb_handoff': 'leg_r_climb_refined'},
            "requiredParameters": [CONTROL, HANDOFF],
            "handoffLegTiming": {'farRelease': .24, 'farArrive': 1.30,
                                  'nearRelease': .50, 'nearArrive': 1.55,
                                  'switchToSwing': 1.8},
            "footSupportWindows": {"near": [[0, .60], [.85, 1]], "far": [[0, .10], [.35, 1]]},
            "skirt": "opt-in separately painted climb drape with small knee-following phase field; standing surface and matching underlay retire together; original material UVs unchanged",
            "transferRefinementFadeSeconds": .85,
            "transferRefinementHoldSeconds": .10}


def transfer_refinement(time_seconds):
    return 1 - smoothstep((time_seconds-.10)/.75)


def diagnostic(manifest, output):
    """Small actual IR before/after contact sheet, explicitly not native QA."""
    from PIL import Image, ImageDraw
    from build_v5 import V5Builder
    from diagnose_rig import DiagnosticRenderer, motion_parameters
    manifest, output = Path(manifest).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a fresh diagnostic output directory")
    document = json.loads(manifest.read_text(encoding="utf8"))
    folder = manifest.parent
    inputs = [manifest, folder / "Maple.rig.json", *[folder / x["file"] for x in document["layers"]],
              *[folder/'runtime/motions'/f'{name}_{side}.motion3.json'
                for name in ('climb', 'climb_to_top') for side in ('left', 'right')]]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    class RefinedBuilder(V5Builder):
        def _load_layers(self, *args):
            super()._load_layers(*args)
            if CONTROL not in self.params:
                apply_climb_refinement(self)
    rig = RefinedBuilder(document, folder).build()
    renderer = DiagnosticRenderer(rig, folder, 384)
    output.mkdir(parents=True)
    (output / "Maple.refined.rig.json").write_text(rig.model_dump_json(indent=2), encoding="utf8")
    phases = [0, .18, .35, .50, .70, .85]
    rows = []
    for direction in ("left", "right"):
        sheet = Image.new("RGB", (len(phases) * 256, 2 * 282), "#393939")
        for row, enabled in enumerate((0, 1)):
            for column, phase in enumerate(phases):
                params = motion_parameters(folder / "runtime/motions" / f"climb_{direction}.motion3.json", phase)
                params[CONTROL] = enabled
                image, stats = renderer.render(params)
                name = f"{direction}-{'refined' if enabled else 'original'}-{phase:.2f}.png"
                image.save(output / name)
                thumb = image.resize((256, 256), Image.Resampling.LANCZOS)
                xy = column * 256, row * 282
                sheet.paste(thumb, xy, thumb)
                pen = ImageDraw.Draw(sheet)
                pen.text((xy[0] + 4, xy[1] + 258), name, fill="white")
                wall = WALL_X if direction == "right" else MIRROR_X-WALL_X
                pen.line((xy[0] + int(wall * 256), xy[1], xy[0] + int(wall * 256), xy[1] + 256), fill="#e0a852")
                rows.append({"file": name, "phase": phase, "refine": enabled, **stats})
        sheet.save(output / f"{direction}-before-after.png")
        transfer_sheet = Image.new('RGB', (4*256, 5*282), '#393939')
        for index, seconds in enumerate([i*.1 for i in range(19)]):
            params = motion_parameters(folder/'runtime/motions'/f'climb_to_top_{direction}.motion3.json', seconds/1.8)
            params[CONTROL] = transfer_refinement(seconds)
            params[HANDOFF] = 1
            frame, stats = renderer.render(params)
            name = f'{direction}-transfer-{seconds:.2f}.png'
            frame.save(output/name)
            thumb = frame.resize((256, 256), Image.Resampling.LANCZOS)
            xy = index%4*256, index//4*282
            transfer_sheet.paste(thumb, xy, thumb)
            ImageDraw.Draw(transfer_sheet).text((xy[0]+4, xy[1]+258), name, fill='white')
            rows.append({'file': name, 'seconds': seconds, 'refine': params[CONTROL], **stats})
        transfer_sheet.save(output/f'{direction}-transfer.png')
        # Complete cycle, including the wrap endpoint, for cloth/weight review.
        # These are explicitly IR frames; they are never native QA evidence.
        cycle_sheet = Image.new('RGB', (5*256, 5*282), '#393939')
        cycle_frames = []
        for index in range(21):
            phase = index/20
            params = motion_parameters(folder/'runtime/motions'/f'climb_{direction}.motion3.json', phase)
            params[CONTROL], params[HANDOFF] = 1, 0
            frame, stats = renderer.render(params)
            name = f'{direction}-full-cycle-{index:02d}.png'
            frame.save(output/name)
            thumb = frame.resize((256, 256), Image.Resampling.LANCZOS)
            xy = index%5*256, index//5*282
            cycle_sheet.paste(thumb, xy, thumb)
            ImageDraw.Draw(cycle_sheet).text((xy[0]+4, xy[1]+258), f'IR phase {phase:.2f}', fill='white')
            canvas = Image.new('RGBA',frame.size,'#393939')
            canvas.alpha_composite(frame)
            cycle_frames.append(canvas.convert('RGB'))
            rows.append({'file':name,'phase':phase,'refine':1,'scope':'IR full-cycle review',**stats})
        cycle_sheet.save(output/f'{direction}-full-cycle.png')
        cycle_frames[0].save(output/f'{direction}-ir-cycle.gif',save_all=True,
                            append_images=cycle_frames[1:],duration=80,loop=0,disposal=2)
    report = {"nativeRuntimeValidation": False, "metadata": climb_metadata(), "samples": rows,
              "inputHashes": hashes, "inputsChanged": [p for p, h in hashes.items()
                  if hashlib.sha256(Path(p).read_bytes()).hexdigest() != h], "visualReview": "pending"}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnostic(args.manifest, args.output)
    print(json.dumps({"samples": len(result["samples"]), "inputsChanged": result["inputsChanged"]}))
