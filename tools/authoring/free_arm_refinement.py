"""Unsupported arm poses, separate from the fixed wall/rope support chains."""
from __future__ import annotations

import math

from build_rig import clamp, rotate
from v51_pose import free_arm_joints, hanging_material_point, joint_delta_point


def crouch_point(p, value):
    # Lower the rigid face/chest; only the skirt and knees absorb the bend.
    return p[0], p[1] + .048 * value * (1 - clamp((p[1] - .56) / .374))


def free_joints(builder, side, value, brace=0.):
    return free_arm_joints(builder._joints(side), side, value, brace)


def original_sleeve_point(builder, side, p, value, hand=False):
    """Articulate the arm while its long hem hangs from a separate frame."""
    # Both original parts sample this complete field, including transparent
    # lattice corners around their wrist. A hand-only rigid lattice would
    # interpolate a different material contact after native parent sampling.
    return hanging_material_point(p, builder._joints(side),
        free_joints(builder, side, value), clamp((value-.05)/.95))


def brace_sleeve_point(builder, side, point, value, hand=False):
    """A second elbow pose after hanging, without gathering the cloth twice."""
    return joint_delta_point(point, free_joints(builder, side, 1),
                             free_joints(builder, side, 1, value))


def cloth_point(builder, side, segment, p, joints):
    shoulder, elbow, wrist = joints
    if segment == 'hand':
        source_wrist = builder._joints(side)[2]
        return p[0] + wrist[0] - source_wrist[0], p[1] + wrist[1] - source_wrist[1]
    src = builder._source_joints(side)
    a, b = src[:2] if segment == 'upper' else src[1:]
    c, d = joints[:2] if segment == 'upper' else joints[1:]
    u = (p[0] - a[0]) / (b[0] - a[0])
    drop = p[1] - (a[1] + u * (b[1] - a[1]))

    def drape(first, last):
        dx, dy = last[0] - first[0], last[1] - first[1]
        length = max(1e-8, math.hypot(dx, dy))
        nx, ny = -dy / length, dx / length
        if ny < 0:
            nx, ny = -nx, -ny
        return nx * .7, .3 + ny * .7

    upper, lower = drape(shoulder, elbow), drape(elbow, wrist)
    middle = tuple((x + y) / 2 for x, y in zip(upper, lower))
    start, end = (upper, middle) if segment == 'upper' else (middle, lower)
    axis = tuple(a + (b - a) * u for a, b in zip(start, end))
    return c[0] + u * (d[0] - c[0]) + axis[0] * drop, c[1] + u * (d[1] - c[1]) + axis[1] * drop


def _drape(a, b, handedness=None):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = max(1e-8, math.hypot(dx, dy))
    nx, ny = -dy / length, dx / length
    if (ny < 0 if handedness is None else handedness < 0):
        nx, ny = -nx, -ny
    return nx * .7, .3 + ny * .7


def relative_cloth_point(point, reference, target, segment):
    """Map already registered cloth, never feed it through donor registration.

    The lower sleeve and every hand use *the same affine field*. Thus their
    common cuff remains common even while support parameters are fading. The
    upper/lower fields also agree along the complete elbow cloth seam.
    """
    segment = 'lower' if segment == 'hand' else segment
    a, b = reference[:2] if segment == 'upper' else reference[1:]
    c, d = target[:2] if segment == 'upper' else target[1:]
    source_axes = (_drape(*reference[:2]), _drape(*reference[1:]))
    # Keep the original textile side through a turn. Choosing the normal by
    # its current screen-Y sign flips the complete sleeve at a vertical bone.
    handedness = -1 if reference[1][0] < reference[0][0] else 1
    target_axes = (_drape(*target[:2], handedness), _drape(*target[1:], handedness))
    axis = tuple((x + y) / 2 for x, y in zip(*source_axes))
    goal_axis = tuple((x + y) / 2 for x, y in zip(*target_axes))
    dx, dy = b[0] - a[0], b[1] - a[1]
    det = dx * axis[1] - dy * axis[0]
    if abs(det) < 1e-8:
        raise ValueError('Degenerate free-arm reference frame')
    px, py = point[0] - a[0], point[1] - a[1]
    along = (px * axis[1] - py * axis[0]) / det
    across = (dx * py - dy * px) / det
    return (c[0] + along * (d[0] - c[0]) + across * goal_axis[0],
            c[1] + along * (d[1] - c[1]) + across * goal_axis[1])


def _copy_free_field(builder, source, parameters, name, parent):
    """Use existing ground keyforms, gated off for the native support chain."""
    original = builder.nodes[source]
    current = builder.warp(name, parent, original.grid_cols)
    builder.nodes[current].grid_vertices = list(original.grid_vertices)
    for parameter in parameters:
        for key in builder.params[parameter].keyforms:
            if source in key.deformer_offsets:
                key.deformer_offsets[current] = list(key.deformer_offsets[source])
    for key in builder.params['ParamArmSupportBlend'].keyforms:
        key.deformer_offset_weights[current] = 1 - key.value
    return current


def apply_free_arm_refinement(builder):
    from build_v4 import swing_arm_joints
    builder.parameter('ParamFreeArmActive', 0, 1, keys=[0, .025, .05, .2, .4, .6, .8, 1])
    builder.parameter('ParamFreeArmPose', 0, 1, keys=[0, .05, .2, .4, .6, .8, 1])
    builder.parameter('ParamFreeArmBrace', 0, 1, keys=[0, .25, .5, .75, 1])
    builder.parameter('ParamArmSupportBlend', 0, 1, default=1)
    builder.deform('ParamCrouch', 'Body_Weight', crouch_point)
    for side in ('l', 'r'):
        # Keep the original painting throughout ground/free transitions, but
        # articulate two bones instead of extrapolating a wrist shear into the
        # long sleeve. Pose=0 remains the exact canonical illustration.
        for part in builder.parts:
            if part.id not in (f'arm_{side}', f'hand_{side}'):
                continue
            free_original = builder.warp(f'FreeOriginal_{part.id}', part.parent_deformer, 17)
            part.parent_deformer = free_original
            builder.deform('ParamFreeArmPose', free_original,
                           lambda p, v, s=side, h=part.id.startswith('hand_'):
                           original_sleeve_point(builder, s, p, v, h))
            for key in builder.params['ParamFreeArmActive'].keyforms:
                key.deformer_offset_weights[free_original] = key.value
            brace = builder.warp(f'FreeBrace_{part.id}', builder.nodes[free_original].parent, 17)
            builder.nodes[free_original].parent = brace
            builder.deform('ParamFreeArmBrace', brace,
                           lambda p, v, s=side, h=part.id.startswith('hand_'):
                           brace_sleeve_point(builder, s, p, v, h))
            for key in builder.params['ParamFreeArmPose'].keyforms:
                key.deformer_offset_weights[brace] = clamp((key.value-.05)/.95)
            for key in builder.params['ParamFreeArmActive'].keyforms:
                key.deformer_offset_weights[brace] = key.value
        # A direct wall -> pickup fade can retain ClimbActive after PoseClimb
        # has passed the wall-release section. The old support hand excludes
        # shoulder shear because its planted wrist lies on the shear's zero
        # axis. During pickup that invariant no longer holds: let the hand
        # gradually share the lower cuff's field. At FreeArmActive=0 this is
        # exactly the original identity hand field, including all old meshes.
        hand_shoulder = f'ClimbShoulder_{side}_hand'
        lower_shoulder = f'ClimbShoulder_{side}_lower'
        for key in builder.params['ParamPoseClimb'].keyforms:
            key.deformer_offsets[hand_shoulder] = list(key.deformer_offsets[lower_shoulder])
        for key in builder.params['ParamFreeArmActive'].keyforms:
            key.deformer_offset_weights[hand_shoulder] = clamp(key.value / .05)
        # The same invariant applies to alternating climb travel. Once a
        # pickup fade starts moving PoseClimb away from the wall, the moving
        # palm must share the cuff's affine travel field, not a translation
        # calibrated only for the original planted wall X. Current supported
        # locomotion has ClimbActive=1; its fade to pickup is 1-FreeArmActive.
        # Transfers/cleaning have no phase travel. This correlated gate keeps
        # each native node to three axes and leaves every old Active=0 form
        # unchanged; no production deadline or contact tolerance is involved.
        for direction in (-1, 1):
            hand_cycle = f'ClimbCycle_{direction}_{side}_hand'
            lower_cycle = f'ClimbCycle_{direction}_{side}_lower'
            fix = builder.warp(f'FreeCycle_{direction}_{side}',
                               builder.nodes[hand_cycle].parent, 17)
            builder.nodes[hand_cycle].parent = fix
            for key in builder.params['ParamClimbPhase'].keyforms:
                key.deformer_offsets[fix] = [
                    (a[0] - b[0], a[1] - b[1])
                    for a, b in zip(key.deformer_offsets[lower_cycle],
                                    key.deformer_offsets[hand_cycle])]
            for key in builder.params['ParamClimbDirection'].keyforms:
                key.deformer_offset_weights[fix] = clamp(direction * key.value)
            for key in builder.params['ParamFreeArmActive'].keyforms:
                key.deformer_offset_weights[fix] = (1 - key.value) * clamp(key.value / .05)
        for segment in ('upper', 'lower', 'hand'):
            name = f'ActiveArm_{side}_{segment}'
            node = builder.nodes[name]
            # Keep the complete native support chain intact. A free-arm field
            # consumes its final registered coordinates, outside every support
            # correction, rather than composing two source registrations.
            for key in builder.params['ParamArmPoseMode'].keyforms:
                key.deformer_offset_weights.pop(name, None)
            outer = f'{name}_RopeCorrection'
            parent = builder.nodes[outer].parent
            free = builder.warp(f'FreeArm_{side}_{segment}', parent, 17)
            builder.nodes[outer].parent = free
            reference = swing_arm_joints(builder, side)
            builder.deform('ParamFreeArmPose', free,
                           lambda p, v, s=side, seg=segment, ref=reference:
                           relative_cloth_point(p, ref, free_joints(builder, s, v), seg))
            for key in builder.params['ParamArmSupportBlend'].keyforms:
                key.deformer_offset_weights[free] = 1 - key.value

            brace = builder.warp(f'FreeBrace_{side}_{segment}', builder.nodes[free].parent, 17)
            builder.nodes[free].parent = brace
            builder.deform('ParamFreeArmBrace', brace,
                           lambda p, v, s=side:
                           brace_sleeve_point(builder, s, p, v))
            for key in builder.params['ParamFreeArmPose'].keyforms:
                key.deformer_offset_weights[brace] = clamp((key.value-.05)/.95)
            for key in builder.params['ParamArmSupportBlend'].keyforms:
                key.deformer_offset_weights[brace] = 1-key.value

            # Ground pickup may interrupt talking, keyboard activity or sleep.
            # While folded artwork is exchanged it must follow the *existing*
            # body's/arm's keyforms, not a second approximate resting pose.
            # Split only three-driver nodes so no native node exceeds 3 axes.
            parent = builder.nodes[brace].parent
            for suffix, source, params in (
                ('Transition', 'Body_Transition', ['ParamTransitionProgress']),
                ('Sleep', 'Body_SeatedSleep', ['ParamPoseSleep']),
                ('Sit', 'Body_SeatedSwing', ['ParamPoseSwing']),
                ('ClimbBody', 'Climb_Body_Lower', ['ParamClimbActive']),
                ('Weight', 'Body_Weight', ['ParamCrouch', 'ParamBounce']),
                ('TopWeight', 'Body_Weight', ['ParamPoseTop']),
                ('LeanXY', 'Body_TurnLean', ['ParamBodyAngleX', 'ParamBodyAngleY']),
                ('LeanZ', 'Body_TurnLean', ['ParamBodyAngleZ']),
                ('Breath', 'Body_Breath', ['ParamBreath']),
                ('Turn', f'Arm_{side.upper()}_Turn', ['ParamBodyTurn']),
                ('GroundPose', f'Arm_{side.upper()}_Pose', ['ParamPoseClimb', 'ParamPoseTop']),
                ('Reaction', f'Arm_{side.upper()}_Reaction', ['ParamTyping', 'ParamListening']),
                ('Pulse', f'Arm_{side.upper()}_Reaction', ['ParamTypingPulse']),
                ('Upper', f'Arm_{side.upper()}_Upper', [f'ParamArm{side.upper()}A']),
                ('Lower', f'Arm_{side.upper()}_Lower', [f'ParamArm{side.upper()}B'])):
                parent = _copy_free_field(builder, source, params,
                                          f'Free{suffix}_{side}_{segment}', parent)
            builder.nodes[brace].parent = parent
            for key in builder.params['ParamArmSupportBlend'].keyforms:
                key.deformer_offset_weights[f'{name}_Movement'] = key.value


def _contact_vertex(builder, name, uv):
    """Insert a material contact vertex, preserving all existing source UVs.

    A contact inside two independently triangulated, nonlinearly deformed
    meshes is not guaranteed to coincide in Core. An explicit shared input
    point removes that approximation. Only triangles containing it subdivide.
    """
    mesh = builder.meshes[name]
    for index, point in enumerate(mesh.uvs):
        if math.dist(point, uv) < 1e-12:
            return index
    found = []
    for index, triangle in enumerate(mesh.triangles):
        a, b, c = [mesh.uvs[i] for i in triangle]
        dx, dy, ex, ey = b[0]-a[0], b[1]-a[1], c[0]-a[0], c[1]-a[1]
        det = dx*ey-dy*ex
        if abs(det) < 1e-14:
            continue
        px, py = uv[0]-a[0], uv[1]-a[1]
        u, v = (px*ey-py*ex)/det, (dx*py-dy*px)/det
        weights = (1-u-v, u, v)
        if min(weights) >= -1e-10:
            found.append((index, triangle, weights))
    if not found:
        raise ValueError(f'{name}: painted contact lies outside its source triangles')
    _, triangle, weights = found[0]
    vertex = len(mesh.vertices)
    mesh.uvs.append(tuple(uv))
    mesh.vertices.append(tuple(sum(mesh.vertices[i][axis]*w for i, w in zip(triangle, weights))
                               for axis in (0, 1)))
    for parameter in builder.params.values():
        for key in parameter.keyforms:
            offsets = key.mesh_offsets.get(name)
            if offsets is not None:
                offsets.append(tuple(sum(offsets[i][axis]*w for i, w in zip(triangle, weights))
                                     for axis in (0, 1)))
    old = {index for index, _, _ in found}
    triangles = [t for index, t in enumerate(mesh.triangles) if index not in old]
    for _, triangle, _ in found:
        for a, b in zip(triangle, (*triangle[1:], triangle[0])):
            pa, pb = mesh.uvs[a], mesh.uvs[b]
            area = (pb[0]-pa[0])*(uv[1]-pa[1])-(pb[1]-pa[1])*(uv[0]-pa[0])
            if abs(area) > 1e-14:
                triangles.append((a, b, vertex))
    mesh.triangles = triangles
    return vertex


def _cuff_registration_bases(builder, parent, uv):
    """Normalize local hand size using the authored supported pose's Jacobian.

    Every matrix is centered on the *same* material contact. Pose-dependent
    scale/orientation compensation cannot change that contact. This is not
    a correction fitted to native error and adds only the existing pose axis.
    """
    from diagnose_rig import interpolate_keys
    from build_v4 import smoothstep
    drivers = {}
    for parameter in builder.params.values():
        names = set()
        for key in parameter.keyforms:
            names.update(key.deformer_offsets)
            names.update(key.deformer_offset_weights)
        for name in names:
            drivers.setdefault(name, []).append(parameter)
    chain = []
    node = parent
    while node is not None:
        chain.append(builder.nodes[node]); node = builder.nodes[node].parent

    def inverse_at(pose):
        time = (1-abs(pose))*1.8
        settings = {'ParamPoseClimb': pose, 'ParamArmPoseMode': 1,
                    'ParamClimbDirection': -1 if pose < 0 else 1,
                    'ParamClimbActive': 1-smoothstep((time-.32)/.53),
                    'ParamClimbRefine': 1-smoothstep((time-.10)/.75)}
        point, jac = uv, ((1., 0.), (0., 1.))
        for node in chain:
            base = node.grid_vertices
            delta = [[0., 0.] for _ in base]
            offset_weight = 1.
            for parameter in drivers.get(node.id, []):
                selection = interpolate_keys(parameter, settings.get(parameter.id, parameter.default))
                weight = 0.
                for key, fraction in selection:
                    weight += fraction*key.deformer_offset_weights.get(node.id, 1.)
                    for current, addition in zip(delta, key.deformer_offsets.get(node.id, [])):
                        current[0] += fraction*addition[0]
                        current[1] += fraction*addition[1]
                offset_weight *= weight
            grid = [(p[0]+d[0]*offset_weight, p[1]+d[1]*offset_weight) for p,d in zip(base,delta)]
            cols, rows = node.grid_cols, node.grid_rows
            gx, gy = point[0]*(cols-1), point[1]*(rows-1)
            ix, iy = min(cols-2, max(0, math.floor(gx))), min(rows-2, max(0, math.floor(gy)))
            tx, ty = gx-ix, gy-iy
            a,b,c,d = (grid[iy*cols+ix], grid[iy*cols+ix+1],
                       grid[(iy+1)*cols+ix], grid[(iy+1)*cols+ix+1])
            point = tuple((a[k]*(1-tx)+b[k]*tx)*(1-ty)+(c[k]*(1-tx)+d[k]*tx)*ty for k in (0,1))
            dx = tuple(((b[k]-a[k])*(1-ty)+(d[k]-c[k])*ty)*(cols-1) for k in (0,1))
            dy = tuple(((c[k]-a[k])*(1-tx)+(d[k]-b[k])*tx)*(rows-1) for k in (0,1))
            jac = tuple(tuple(dx[i]*jac[0][j]+dy[i]*jac[1][j] for j in (0,1)) for i in (0,1))
        det = jac[0][0]*jac[1][1]-jac[0][1]*jac[1][0]
        if abs(det) < 1e-8:
            raise ValueError(f'Degenerate cuff registration at PoseClimb={pose}')
        return ((jac[1][1]/det, -jac[0][1]/det), (-jac[1][0]/det, jac[0][0]/det))
    return {key.value: inverse_at(key.value) for key in builder.params['ParamPoseClimb'].keyforms}


def finish_free_arm_refinement(builder):
    """Register every active palm/fan into its sleeve's one native hierarchy.

    Copying equal warp keyforms is insufficient: separate source triangles
    evaluate interior contacts differently in the native nonlinear lattice.
    The wrist is also an actual registration-lattice control point: Core can
    deform a child lattice through its parent before sampling the child, so
    an interior point of even an affine child field can otherwise drift.
    The original ground arm/hand meshes, IDs, UVs and artwork remain untouched.
    """
    if 'ParamFreeArmActive' not in builder.params or 'ParamClimbRefine' not in builder.params:
        return
    parts = {part.id: part for part in builder.parts}
    for side in ('l', 'r'):
        cuff_name = f'sleeve_{side}_lower'
        cuff_uv = tuple(builder._source_joints(side)[2])
        hand_uv = tuple(builder._joints(side)[2])
        _contact_vertex(builder, cuff_name, cuff_uv)
        parent = parts[cuff_name].parent_deformer
        bases = _cuff_registration_bases(builder, parent, cuff_uv)
        center = (.5, .5)
        def register(point, inverse):
            x, y = point[0]-center[0], point[1]-center[1]
            return (cuff_uv[0]+inverse[0][0]*x+inverse[0][1]*y,
                    cuff_uv[1]+inverse[1][0]*x+inverse[1][1]*y)
        def registration(name, size):
            node = builder.warp(name, parent, size)
            source_grid = list(builder.nodes[node].grid_vertices)
            base = [register(p, bases[0]) for p in source_grid]
            builder.nodes[node].grid_vertices = base
            for key in builder.params['ParamPoseClimb'].keyforms:
                key.deformer_offsets[node] = [tuple(a-b for a,b in zip(register(p,bases[key.value]),q))
                                               for p,q in zip(source_grid,base)]
            return node
        node = registration(f'ActiveCuffMaterial_{side}', 3)
        # Native Core first deforms this child lattice through its sleeve
        # ancestors. The fan extends far beyond the small palm; a 3x3 lattice
        # interpolates across unrelated sleeve regions and flattens its face.
        # Give only the fan a denser lattice, retaining the same exact wrist
        # centre and sleeve parent. Existing painted palm contacts stay fixed.
        fan_node = registration(f'ActiveFanMaterial_{side}', 17)
        names = [f'hand_{side}_{suffix}'
                 for suffix in ('relaxed', 'support', 'grip_palm', 'grip_fingers')]
        names.append(f'clean_fan_{side}')
        for name in names:
            parts[name].parent_deformer = fan_node if name.startswith('clean_fan_') else node
            if not name.endswith('_grip_fingers'):
                _contact_vertex(builder, name, hand_uv)
            # Keep source positions unchanged because Editor derives atlas UVs
            # from them. Translate posed mesh forms into a child coordinate
            # system whose center control point is the wrist. This constant
            # translation commutes with the existing fan-opening mesh offsets;
            # the complete pose-dependent Jacobian still acts on that opening.
            shift = tuple(a-b for a, b in zip(center, hand_uv))
            # Reuse the mesh's existing visibility/opening axis: adding the
            # many-key climb axis would needlessly multiply its native forms.
            parameter = 'ParamCleanFan'+side.upper() if name.startswith('clean_fan_') else 'ParamArmPoseMode'
            for key in builder.params[parameter].keyforms:
                offsets = key.mesh_offsets.get(name, [(0., 0.)] * len(builder.meshes[name].vertices))
                key.mesh_offsets[name] = [(p[0]+shift[0], p[1]+shift[1]) for p in offsets]
    # Retire the now-unreferenced independent hand hierarchy and its inverse
    # pickup fields, rather than retain cancelling stacks in the native model.
    used = set()
    for part in builder.parts:
        node = part.parent_deformer
        while node is not None and node not in used:
            used.add(node)
            node = builder.nodes[node].parent
    retired = set(builder.nodes)-used
    for name in retired:
        del builder.nodes[name]
    for parameter in builder.params.values():
        for key in parameter.keyforms:
            for field in (key.deformer_offsets, key.deformer_offset_weights, key.deformer_opacity_overrides):
                for name in retired:
                    field.pop(name, None)


def finish_fan_material_refinement(builder):
    """Carry the fan rigidly at the existing palm's exact wrist control point.

    Run after contact refinements. A warp child inherits its parent's shear,
    including the free-arm/support crossfade; increasing its grid density
    cannot make a rigid prop. Cubism's warp-parent/rotation-child combination
    inherits position and rotation while preserving the child's shape:
    https://docs.live2d.com/en/cubism-editor-manual/combintion-of-parent-child-relation/

    Opening remains the original fan mesh animation below this rigid frame.
    Its source vertices, UVs, shaft registration and opening keys are unchanged.
    No new parameter or fitted inverse scale is needed. Native export must
    still verify the inherited angle and shaft contact through actual motions.
    """
    from build_rig import Deformer, DeformerType

    parts = {part.id: part for part in builder.parts}
    for side in ('l', 'r'):
        fan = parts.get(f'clean_fan_{side}')
        name = f'ActiveFanMaterial_{side}'
        parent = f'ActiveCuffMaterial_{side}'
        if fan is None or name not in builder.nodes or parent not in builder.nodes:
            continue
        if fan.parent_deformer != name:
            raise ValueError(f'{fan.id}: expected registered fan parent {name}')
        # Palm and fan shaft mesh keyforms already put the wrist at (.5,.5),
        # which is an actual control point in the unchanged 3x3 cuff lattice.
        # A rotation origin at precisely this point shares its entire final
        # ancestor chain, including free poses and support/cleaning blends.
        builder.nodes[name] = Deformer(id=name, type=DeformerType.rotation,
                                      parent=parent, pivot=(.5, .5))
        for parameter in builder.params.values():
            for key in parameter.keyforms:
                for field in (key.deformer_offsets, key.deformer_offset_weights,
                              key.deformer_opacity_overrides):
                    field.pop(name, None)
