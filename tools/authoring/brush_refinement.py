"""Independent v5.1 cleaning arm choreography and rigid brush grip.

No artwork is edited here. Both sleeve pieces pass through one affine
joint-triangle field in their already-posed coordinates. This field commutes
with native child-grid sampling; a registered cuff attaches fist and tool.
"""
from __future__ import annotations

import math

import numpy as np

from build_rig import Deformer, DeformerType, clamp, rotate
from free_arm_refinement import _contact_vertex
from native_warp_sampling import warp_points
from v51_pose import joint_delta_point

CONTEXTS = {-1: 'left', 0: 'ground', 1: 'right', 2: 'top'}
POSE_KEYS = [0, .15, .30, .45, .60, .75, .90, 1]
STROKE_KEYS = [-1, -.75, -.5, -.25, 0, .25, .5, .75, 1]
PARAMETERS = ['ParamCleanContext', 'ParamCleanPoseL', 'ParamCleanPoseR',
              'ParamCleanBrushL', 'ParamCleanBrushR', 'ParamCleanStroke']


def enabled(manifest):
    return manifest.get('brushRefinementVersion') == 1


def add_parameters(builder):
    builder.parameter('ParamCleanContext', -1, 2, keys=list(CONTEXTS))
    builder.parameter('ParamCleanStroke', keys=STROKE_KEYS)
    for side in ('L', 'R'):
        builder.parameter('ParamCleanPose'+side, 0, 1, keys=POSE_KEYS)
        builder.parameter('ParamCleanBrush'+side, 0, 1, keys=[0, .25, .5, .75, 1])


def working_side(context):
    return 'l' if context == -1 else 'r'


def _unit(point):
    length = math.hypot(*point)
    if length < 1e-10:
        raise ValueError('Degenerate cleaning material axis')
    return tuple(value/length for value in point)


def _sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _mix(a, b, value):
    return tuple(x+(y-x)*value for x, y in zip(a, b))


def _cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def solve_elbow(shoulder, wrist, upper, lower, reference):
    """Keep both authored lengths, choosing the reference elbow's branch."""
    delta = _sub(wrist, shoulder)
    distance = math.hypot(*delta)
    # Retain a visible elbow bend even when the requested pickup station is
    # outside reach. A near-straight two-bone frame pinches its shared cloth.
    maximum = math.sqrt(upper*upper+lower*lower+2*upper*lower*math.cos(math.radians(35)))
    limit = max(abs(upper-lower)+1e-6, min(maximum, distance))
    direction = _unit(delta)
    wrist = tuple(shoulder[i]+direction[i]*limit for i in (0, 1))
    along = (upper*upper-lower*lower+limit*limit)/(2*limit)
    height = math.sqrt(max(0., upper*upper-along*along))
    branch = 1 if _cross(_sub(reference[1], reference[0]),
                        _sub(reference[2], reference[0])) < 0 else -1
    elbow = (shoulder[0]+direction[0]*along-branch*direction[1]*height,
             shoulder[1]+direction[1]*along+branch*direction[0]*height)
    return tuple(shoulder), elbow, wrist


def clean_joints(reference, side, context, pose=1., stroke=0.):
    """A low pickup followed by a short forearm arc; never a piston stroke.

    Pose=0 reproduces the exact supported reference. The work hand is below
    the chest; the ground left arm settles naturally without sweeping.
    """
    if context not in CONTEXTS or side not in ('l', 'r'):
        raise ValueError('Unknown cleaning posture')
    if pose <= 0:
        return tuple(tuple(p) for p in reference)
    shoulder, elbow, wrist = reference
    upper, lower = math.dist(shoulder, elbow), math.dist(elbow, wrist)
    interior = 1 if context == -1 else -1 if context == 1 else 1
    resting = context == 0 and side == 'l'
    if resting:
        # Settle both bones down, retaining the existing elbow branch. The
        # cloth below them is gathered separately rather than held sideways
        # by the old wall/swing sleeve cross-section.
        result = [tuple(shoulder)]
        for index, end_angle in enumerate((86., 110.)):
            a, b = reference[index:index+2]
            initial = math.atan2(b[1]-a[1], b[0]-a[0])
            delta = (math.radians(end_angle)-initial+math.pi)%(2*math.pi)-math.pi
            angle = initial+clamp(pose)*delta
            length = math.dist(a, b)
            result.append((result[-1][0]+length*math.cos(angle),
                           result[-1][1]+length*math.sin(angle)))
        return tuple(result)
    elif context in (-1, 1):
        # Reach the open side beside the waist, beyond the rear ribbon band.
        # The previous low station left the brush dangling inside the skirt.
        destination = (shoulder[0]+interior*.170, shoulder[1]+.080)
    else:
        destination = (shoulder[0]+.095, shoulder[1]+.145)
    # Interpolate the two bone angles, not the wrist's straight chord. The
    # wall hand crosses from one side of the shoulder to the other: that
    # chord passes within |upper-lower| of the shoulder and folds the elbow
    # nearly 180 degrees, flattening real sleeve material into a needle.
    # The shared angular arc carries the wrist below the shoulder instead.
    t = clamp(pose)
    final = solve_elbow(shoulder, destination, upper, lower, reference)
    start_angles = [math.atan2(b[1]-a[1], b[0]-a[0]) for a, b in zip(reference, reference[1:])]
    final_angles = [math.atan2(b[1]-a[1], b[0]-a[0]) for a, b in zip(final, final[1:])]
    turn = (final_angles[0]-start_angles[0]+math.pi)%(2*math.pi)-math.pi
    start_bend = (start_angles[1]-start_angles[0]+math.pi)%(2*math.pi)-math.pi
    final_bend = (final_angles[1]-final_angles[0]+math.pi)%(2*math.pi)-math.pi
    upper_angle = start_angles[0]+t*turn
    lower_angle = upper_angle+start_bend+(final_bend-start_bend)*t
    elbow = (shoulder[0]+upper*math.cos(upper_angle), shoulder[1]+upper*math.sin(upper_angle))
    end = (elbow[0]+lower*math.cos(lower_angle), elbow[1]+lower*math.sin(lower_angle))
    ready = tuple(shoulder), elbow, end
    if not stroke or resting:
        return ready
    # Rotate the forearm around its elbow, then solve the small elbow follow.
    # The wrist moves on an arc and both bone lengths remain unchanged.
    angle = interior*14*clamp(abs(stroke))*(1 if stroke > 0 else -1)
    wanted = rotate(ready[2], ready[1], angle)
    return solve_elbow(shoulder, wanted, upper, lower, ready)


def ground_painted_joints(reference, side, pose=1., stroke=0.):
    """Keep the original standing sleeve throughout ground pickup/stow.

    Its left elbow has the opposite fold to the supported donor sleeve.
    Preserve that signed fold while letting the wrist settle beside the hip.
    """
    if side == 'r':
        return clean_joints(reference, side, 0, pose, stroke)
    result = [tuple(reference[0])]
    for index, degrees in enumerate((120., 85.)):
        a, b = reference[index:index+2]
        start = math.atan2(b[1]-a[1], b[0]-a[0])
        turn = (math.radians(degrees)-start+math.pi)%(2*math.pi)-math.pi
        angle = start+clamp(pose)*turn
        length = math.dist(a, b)
        result.append((result[-1][0]+length*math.cos(angle),
                       result[-1][1]+length*math.sin(angle)))
    return tuple(result)


def ground_material_point(point, reference, target, amount):
    """A monotone vertical cloth field, with a modest gather below the cuff.

    The forearm moves the shared wrist, while fabric below it translates
    downward instead of rotating sideways. Each horizontal material section
    keeps at least 75 percent of its width; the cuff itself never gathers.
    """
    posed = joint_delta_point(point, reference, target)
    fraction = clamp((point[1]-reference[2][1]-.025)/.080)
    hang = fraction*fraction*(3-2*fraction)*clamp(amount)
    return posed[0]+.25*hang*(reference[2][0]-point[0]), posed[1]


def seam_axis(reference, target, original_axis):
    """Transport the seam through the joint triangle, retaining textile width.

    Averaging bone rotations can rotate a seam past one forearm and invert
    that sleeve. The common joint-triangle map preserves its side relative
    to BOTH bones; normalize only the transverse length, not its direction.
    """
    old = np.asarray([_sub(p, reference[0]) for p in reference[1:]]).T
    new = np.asarray([_sub(p, target[0]) for p in target[1:]]).T
    if abs(np.linalg.det(old)) < 1e-10:
        raise ValueError('Cleaning joint triangle is straight')
    direction = _unit(new @ np.linalg.solve(old, original_axis))
    length = math.hypot(*original_axis)
    return tuple(value*length for value in direction)


def material_map(point, reference, target, source_axis, target_axis, segment, gather=0.):
    """Affine two-bone mapping sharing the complete elbow material line."""
    index = 0 if segment == 'upper' else 1
    first, last = reference[index:index+2]
    start, end = target[index:index+2]
    delta = _sub(last, first)
    determinant = _cross(delta, source_axis)
    if abs(determinant) < 1e-9:
        raise ValueError('Cleaning reference has a collapsed sleeve section')
    offset = _sub(point, first)
    along = _cross(offset, source_axis)/determinant
    across = _cross(delta, offset)/determinant
    if gather:
        # A monotone cloth gather: nearby arm/cuff material keeps its tangent
        # while distant drape folds inward. A positive residual derivative
        # prevents collapsing the far hem into degenerate triangles.
        gathered = .12*across+.88*.040*math.tanh(across/.040)
        across += (gathered-across)*clamp(gather)
    return tuple(start[i]+along*(end[i]-start[i])+across*target_axis[i] for i in (0, 1))


def stroke_map(point, reference, target):
    """One affine stroke field for both pieces, including interrupted pickup.

    The three joint targets keep both bone lengths. Applying the SAME affine
    map to both sleeves preserves their seam even while Pose is interpolated;
    two maps fitted only to the final ready seam would split it mid-fade.
    """
    old = np.asarray([_sub(p, reference[0]) for p in reference[1:]]).T
    new = np.asarray([_sub(p, target[0]) for p in target[1:]]).T
    if abs(np.linalg.det(old)) < 1e-10:
        raise ValueError('Cleaning stroke has a straight reference arm')
    q = np.asarray(target[0])+new @ np.linalg.solve(old, _sub(point, reference[0]))
    return tuple(q)


def full_material_map(point, reference, target, source_axis, target_axis, gather=0.):
    """One continuous two-bone field for the entire arm's shared parent.

    Real support warps bend the painted elbow cut. Fitting separate affine
    nodes to only one tangent splits that curved cut. Both pieces must pass
    through this same complete field, including each grid's parent sampling.
    The two branches agree on their dividing line and keep their orientation.
    """
    at_shoulder = _cross(_sub(reference[0], reference[1]), source_axis)
    at_wrist = _cross(_sub(reference[2], reference[1]), source_axis)
    if at_shoulder*at_wrist >= 0:
        raise ValueError('Cleaning elbow section does not separate the two bones')
    section = _cross(_sub(point, reference[1]), source_axis)
    segment = 'upper' if section*at_shoulder >= 0 else 'lower'
    return material_map(point, reference, target, source_axis, target_axis, segment, gather)


def _selection(parameter, value):
    keys = parameter.keyforms
    for a, b in zip(keys, keys[1:]):
        if value <= b.value:
            weight = clamp((value-a.value)/(b.value-a.value))
            return ((a, 1-weight), (b, weight))
    return ((keys[-1], 1.),)


def _posed_sampler(builder, settings, stop):
    """Sample existing authoring lattices before the common wall correction.

    Child lattices are prewarped through their parents, matching the observed
    native interpolation rule. This is a construction reference; the next
    native export still has to check actual sleeves and grip contacts.
    """
    selected = [(p, _selection(p, settings.get(p.id, p.default))) for p in builder.params.values()]
    cache = {}

    def grid(name):
        if name in cache:
            return cache[name]
        node = builder.nodes[name]
        if node.type != DeformerType.warp:
            raise ValueError('Cleaning sleeve reference unexpectedly includes a rigid child')
        base = np.asarray(node.grid_vertices, dtype=float)
        delta = np.zeros_like(base)
        factor = 1.
        for _, keys in selected:
            weights = 0.
            bound = False
            for key, weight in keys:
                if name in key.deformer_offsets:
                    delta += np.asarray(key.deformer_offsets[name])*weight
                if name in key.deformer_offset_weights:
                    weights += key.deformer_offset_weights[name]*weight
                    bound = True
            if bound:
                factor *= weights
        result = base+delta*factor
        if node.parent != stop and node.parent is not None:
            parent = builder.nodes[node.parent]
            result = warp_points(result, grid(node.parent), parent.grid_rows, parent.grid_cols)
        cache[name] = result
        return result

    def sample(name, points):
        node = builder.nodes[name]
        return warp_points(points, grid(name), node.grid_rows, node.grid_cols)
    return sample


def _branch(builder, part):
    name = part.parent_deformer
    while True:
        parent = builder.nodes[name].parent
        if parent is None:
            raise ValueError('Cleaning arm is detached from its support scene')
        if parent == builder.scene or parent.startswith('ClimbMaterialContact_'):
            return name, parent
        name = parent


def _reference(builder, parts, side, context):
    upper, lower = [parts[f'sleeve_{side}_{segment}'] for segment in ('upper', 'lower')]
    _, stop = _branch(builder, upper)
    settings = {'ParamArmPoseMode': 1, 'ParamArmSupportBlend': 1,
                'ParamPoseSwing': float(context == 2), 'ParamClimbPhase': 0}
    if context in (-1, 1):
        settings.update(ParamPoseClimb=context, ParamClimbActive=1,
                        ParamClimbRefine=1, ParamClimbDirection=context)
    sample = _posed_sampler(builder, settings, stop)
    source = builder._source_joints(side)
    shoulder, elbow, transverse = sample(upper.parent_deformer,
        [source[0], source[1], (source[1][0], source[1][1]+.01)])
    lower_elbow, wrist = sample(lower.parent_deformer, source[1:])
    if math.dist(elbow, lower_elbow) > .001:
        raise ValueError(f'{side}/{context}: preexisting authoring elbow seam differs')
    return tuple(map(tuple, (shoulder, elbow, wrist))), tuple((transverse-elbow)/.01)


def apply_brush_refinement(builder):
    if not enabled(builder.manifest):
        return
    parts = {part.id: part for part in builder.parts}
    # Capture references before inserting any new fields.
    references = {(side, context): _reference(builder, parts, side, context)
                  for side in ('l', 'r') for context in CONTEXTS}
    for side in ('l', 'r'):
        pose_parameter = 'ParamCleanPose'+side.upper()
        branches = [_branch(builder, parts[f'sleeve_{side}_{segment}']) for segment in ('upper', 'lower')]
        if len({parent for _, parent in branches}) != 1:
            raise ValueError('Cleaning sleeves do not share their final support frame')
        parent = branches[0][1]
        for context, label in CONTEXTS.items():
            reference, axis = references[side, context]
            # Native composes complete child lattices through this parent
            # before sampling the painted mesh. A piecewise 33-grid field
            # preserved the seam but displaced real shoulders by >40 px.
            # One affine triangle map commutes with that sampling and thus
            # reaches all three registered joints, not only an analytic cut.
            pose = builder.warp(f'CleanArm_{label}_{side}', parent, 2)
            source_grid = builder.nodes[pose].grid_vertices
            for key in builder.params[pose_parameter].keyforms:
                if key.value == 0:
                    key.deformer_offsets[pose] = [(0., 0.) for _ in source_grid]
                    continue
                target = clean_joints(reference, side, context, key.value)
                key.deformer_offsets[pose] = [
                    _sub(stroke_map(p, reference, target), p)
                    for p in source_grid]
            ready = clean_joints(reference, side, context)
            sweep = builder.warp(f'BrushStroke_{label}_{side}', parent, 2)
            builder.nodes[pose].parent = sweep
            builder.deform('ParamCleanStroke', sweep,
                lambda p, v, ref=reference, ready=ready, s=side, c=context:
                p if v == 0 else stroke_map(p, ready, clean_joints(ref, s, c, 1, v)))
            for key in builder.params['ParamCleanContext'].keyforms:
                key.deformer_offset_weights[pose] = float(key.value == context)
                key.deformer_offset_weights[sweep] = float(key.value == context)
            for key in builder.params[pose_parameter].keyforms:
                key.deformer_offset_weights[sweep] = key.value
            parent = pose
        for branch, _ in branches:
            builder.nodes[branch].parent = parent

        # Rotate the closed fist and brush together at the material cuff.
        # A rigid child keeps their shape independent of the sleeve's shear.
        cuff = f'ActiveCuffMaterial_{side}'
        # The ready grip has the inherited cuff orientation. An additional
        # fixed tilt would exchange two differently angled fists at pickup.
        wrist = builder.warp(f'BrushWrist_{side}', cuff, 3)
        parent = cuff
        for context, label in CONTEXTS.items():
            node = builder.warp(f'BrushWristTurn_{label}_{side}', parent, 3)
            sign = -1 if context == 1 else 1
            builder.deform('ParamCleanStroke', node,
                           lambda p, v, direction=sign: rotate(p, (.5, .5), direction*16*v))
            for key in builder.params['ParamCleanContext'].keyforms:
                key.deformer_offset_weights[node] = float(key.value == context)
            parent = node
        builder.nodes[wrist].parent = parent
        rigid = f'BrushGripFrame_{side}'
        builder.nodes[rigid] = Deformer(id=rigid, type=DeformerType.rotation,
                                       parent=wrist, pivot=(.5, .5))
        visible = 'ParamCleanBrush'+side.upper()
        source_wrist = builder._joints(side)[2]
        shift = _sub((.5, .5), source_wrist)
        # The material fist stays in its registered cuff. Give the brush one
        # fixed seated direction inside that grip for each context, so its
        # bristles sweep the open space rather than dangling into the skirt.
        tool_angles = {}
        for context in CONTEXTS:
            settings = {'ParamArmPoseMode': 1, 'ParamArmSupportBlend': 1,
                        'ParamPoseSwing': float(context == 2), 'ParamClimbPhase': 0,
                        'ParamCleanContext': context, pose_parameter: 1}
            if context in (-1, 1):
                settings.update(ParamPoseClimb=context, ParamClimbActive=1,
                                ParamClimbRefine=1, ParamClimbDirection=context)
            origin, up = _posed_sampler(builder, settings, None)(wrist,
                [(.5, .5), (.5, .5-1e-5)])
            direction = up-origin
            inherited = math.degrees(math.atan2(direction[0], -direction[1]))
            desired = -84. if context == 1 else 84.
            tool_angles[context] = (desired-inherited+180.)%360.-180.
        for name in [f'clean_brush_{side}', f'clean_hand_{side}_palm', f'clean_hand_{side}_fingers']:
            layer = builder.layer_by_id[name]
            parts[name].parent_deformer = rigid
            builder.opacity('ParamArmPoseMode', name, lambda v: clamp(v/.05))
            for key in builder.params[visible].keyforms:
                key.mesh_offsets[name] = [shift for _ in builder.meshes[name].vertices]
                key.opacity_overrides[name] = key.value
            if name.startswith('clean_brush_'):
                grip = tuple(layer['gripSourceUv'])
                builder.deform('ParamCleanContext', name,
                    lambda p, v, pivot=grip: rotate(p, pivot, tool_angles[v]), mesh=True)
                for field in ('gripSourceUv', 'tipSourceUv'):
                    _contact_vertex(builder, name, tuple(layer[field]))
            elif name.endswith('_fingers'):
                _contact_vertex(builder, name, tuple(layer['gripSourceUv']))
            else:
                _contact_vertex(builder, name, tuple(source_wrist))
        for shape in ('relaxed', 'support', 'grip_palm', 'grip_fingers'):
            builder.opacity(visible, f'hand_{side}_{shape}', lambda v: 1-v)
        # The far arm normally belongs behind the torso and rear ribbons.
        # Once its working forearm emerges, keep that SAME sleeve material
        # in front of the ribbons, matching the already-visible fist. A
        # dedicated editable part avoids competing BodyTurn/Clean draw-order
        # axes. The upper arm and every inactive support arm keep their depth.
        original = f'sleeve_{side}_lower'
        foreground = f'clean_sleeve_{side}_lower'
        source_part = parts[original]
        builder.parts.append(source_part.model_copy(update={'id': foreground, 'draw_order': 158}))
        builder.meshes[foreground] = builder.meshes[original].model_copy(deep=True,
                                                        update={'part_id': foreground})
        for parameter in builder.params.values():
            for key in parameter.keyforms:
                if original in key.mesh_offsets:
                    key.mesh_offsets[foreground] = list(key.mesh_offsets[original])
                if original in key.opacity_overrides:
                    key.opacity_overrides[foreground] = key.opacity_overrides[original]
        layer = dict(builder.layer_by_id.get(original, {}), id=foreground,
                     clean_brush_forearm=True, sourceDrawable=original)
        builder.layer_by_id[foreground] = layer
        if hasattr(builder, 'layers'):
            builder.layers.append(layer)
        builder.opacity(visible, original, lambda v: 1-v)
        builder.opacity(visible, foreground, lambda v: v)
    builder.brush_references = references
    apply_ground_painted_brush(builder)


def apply_ground_painted_brush(builder):
    """Use original long sleeves and a tool registered to their real cuff.

    Small hand/tool aliases reuse unchanged PNGs/UVs. They are needed
    because a brush cannot switch between unrelated original/support parent
    chains. A ground-to-drag blend therefore carries the tool through the
    exact same original sleeve field rather than a fitted endpoint offset.
    """
    parts = {part.id: part for part in builder.parts}
    if 'arm_l' not in parts or 'arm_r' not in parts:
        # Bounded support-only authoring components have no original paint.
        return
    for side in ('l', 'r'):
        reference = builder._joints(side)
        ready = ground_painted_joints(reference, side)
        for part_name in ('arm_'+side, 'hand_'+side):
            original = parts[part_name]
            sweep = builder.warp(f'GroundBrushStroke_{part_name}', original.parent_deformer, 33)
            pose = builder.warp(f'GroundBrushPose_{part_name}', sweep, 33)
            original.parent_deformer = pose
            # Work directly in the original paint's coordinates. Unlike the
            # supported sleeves this material has no large source-to-support
            # registration inside the new field. Its lower hem has a gravity
            # frame instead of following the forearm into a sideways wing.
            builder.deform('ParamCleanPose'+side.upper(), pose,
                lambda point, value, ref=reference, s=side:
                ground_material_point(point, ref, ground_painted_joints(ref, s, value), value))
            builder.deform('ParamCleanStroke', sweep,
                lambda point, value, ref=reference, target=ready, s=side:
                joint_delta_point(point, target, ground_painted_joints(ref, s, 1, value)))
            for node in (pose, sweep):
                for key in builder.params['ParamCleanContext'].keyforms:
                    key.deformer_offset_weights[node] = float(key.value == 0)
                for key in builder.params['ParamFreeArmActive'].keyforms:
                    key.deformer_offset_weights[node] = 1-key.value

    side = 'r'
    source_wrist = tuple(builder._joints(side)[2])
    arm = parts['arm_r']
    _contact_vertex(builder, arm.id, source_wrist)
    wrist = builder.warp('GroundBrushCuff_r', arm.parent_deformer, 3)
    builder.nodes[wrist].grid_vertices = [
        (source_wrist[0]+p[0]-.5, source_wrist[1]+p[1]-.5)
        for p in builder.nodes[wrist].grid_vertices]
    turn = builder.warp('GroundBrushWristTurn_r', wrist, 3)
    builder.deform('ParamCleanStroke', turn, lambda p, v: rotate(p, (.5, .5), 16*v))
    rigid = 'GroundBrushGripFrame_r'
    builder.nodes[rigid] = Deformer(id=rigid, type=DeformerType.rotation,
                                   parent=turn, pivot=(.5, .5))
    settings = {'ParamArmPoseMode': 0, 'ParamArmSupportBlend': 0,
                'ParamCleanContext': 0, 'ParamCleanPoseR': 1, 'ParamBreath': 0}
    origin, up = _posed_sampler(builder, settings, None)(wrist, [(.5, .5), (.5, .5-1e-5)])
    direction = up-origin
    inherited = math.degrees(math.atan2(direction[0], -direction[1]))
    tool_angle = (84.-inherited+180.)%360.-180.
    shift = _sub((.5, .5), source_wrist)
    for source in ('clean_brush_r', 'clean_hand_r_palm', 'clean_hand_r_fingers'):
        name = source.replace('clean_', 'clean_ground_', 1)
        builder.parts.append(parts[source].model_copy(update={'id': name, 'parent_deformer': rigid}))
        builder.meshes[name] = builder.meshes[source].model_copy(deep=True, update={'part_id': name})
        layer = dict(builder.layer_by_id[source], id=name, groundBrushAliasOf=source)
        builder.layer_by_id[name] = layer
        if hasattr(builder, 'layers'):
            builder.layers.append(layer)
        grip = tuple(layer['gripSourceUv'])
        angle = tool_angle if source == 'clean_brush_r' else 0.
        builder.deform('ParamCleanBrushR', name,
            lambda p, v, pivot=grip, angle=angle:
            tuple(a+b for a, b in zip(rotate(p, pivot, angle), shift)), mesh=True)
        builder.opacity('ParamCleanBrushR', name, lambda v: v)
        builder.opacity('ParamArmPoseMode', name, lambda v: 1-clamp(v/.05))
        fields = (('gripSourceUv', 'tipSourceUv') if source == 'clean_brush_r' else
                  ('gripSourceUv',) if source.endswith('_fingers') else ('wristSourceUv',))
        for field in fields:
            _contact_vertex(builder, name, tuple(layer[field]))
    source, name = 'hand_r_relaxed', 'clean_ground_hand_r_relaxed'
    builder.parts.append(parts[source].model_copy(update={'id': name, 'parent_deformer': rigid}))
    builder.meshes[name] = builder.meshes[source].model_copy(deep=True, update={'part_id': name})
    layer = dict(builder.layer_by_id[source], id=name, groundBrushAliasOf=source)
    builder.layer_by_id[name] = layer
    if hasattr(builder, 'layers'):
        builder.layers.append(layer)
    builder.deform('ParamCleanBrushR', name, lambda p, v:
                   tuple(a+b for a, b in zip(p, shift)), mesh=True)
    builder.opacity('ParamCleanBrushR', name, lambda v: 1-v)
    builder.opacity('ParamHandRShape', name, lambda v: abs(v))
    builder.opacity('ParamArmPoseMode', name, lambda v: 1-clamp(v/.05))
    _contact_vertex(builder, name, source_wrist)
    builder.opacity('ParamCleanBrushR', 'hand_r', lambda v: 1-v)
    builder.ground_brush_original_paint = True


def add_metadata(metadata, builder):
    if not enabled(builder.manifest):
        return
    refine = metadata['refinement']
    refine['motionPolishVersion'] = 1
    refine['requiredParameters'] += PARAMETERS
    refine['cleaningTool'] = {'type': 'brush', 'parameters': PARAMETERS,
                             'contexts': {str(k): v for k, v in CONTEXTS.items()},
                             'rigidGrip': True, 'legacyFanUsed': False,
                             'forearms': {side: {'drawable': f'clean_sleeve_{side}_lower',
                                                'replaces': f'sleeve_{side}_lower',
                                                'visibilityParameter': 'ParamCleanBrush'+side.upper(),
                                                'drawOrder': 158} for side in ('l', 'r')}}
    for state in ('clean_ground', 'clean_top', 'clean_climb_left', 'clean_climb_right'):
        context = {'clean_ground': 0, 'clean_top': 2, 'clean_climb_left': -1, 'clean_climb_right': 1}[state]
        side = working_side(context)
        original_ground = context == 0 and getattr(builder, 'ground_brush_original_paint', False)
        brush = builder.layer_by_id['clean_ground_brush_r' if original_ground else 'clean_brush_'+side]
        anchors = metadata['states'][state]['anchors']
        anchors.pop('fanTip', None)
        anchors.update(brushTip={'drawable': brush['id'], 'sourceUv': brush['tipSourceUv']},
                       brushGrip={'drawable': brush['id'], 'sourceUv': brush['gripSourceUv']},
                       freeHand={'drawable': 'clean_ground_hand_r_fingers' if original_ground else 'clean_hand_'+side+'_fingers',
                                 'sourceUv': brush['gripSourceUv']})
        metadata['states'][state]['cleaningTool'] = {'workSide': side,
            'supportSide': None if context == 0 else ('r' if side == 'l' else 'l'), 'context': context,
            'forearmDrawable': f'clean_sleeve_{side}_lower'}
        if original_ground:
            metadata['states'][state]['cleaningTool'].update(
                forearmDrawable='arm_r', forearmKind='whole',
                cuffSourceUv=list(builder._joints('r')[2]), armPoseMode=0,
                relaxedHandDrawable='clean_ground_hand_r_relaxed')
        for suffix in ('enter', 'exit'):
            metadata['states'][state+'_'+suffix]['anchors'] = dict(anchors)
            metadata['states'][state+'_'+suffix]['cleaningTool'] = dict(metadata['states'][state]['cleaningTool'])
