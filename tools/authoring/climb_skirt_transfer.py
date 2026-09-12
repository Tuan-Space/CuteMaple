"""Cloth-only knee gather during the existing wall-to-seat handoff.

The arm/body refinement retires before the seated artwork appears. These
ordinary warp keys bridge that interval without changing its motion lanes or
the already authored hand/leg geometry. Native validation remains required.
"""
from __future__ import annotations

import math
import numpy as np

from native_warp_sampling import warp_points, unwarp_points


def _smooth(value):
    value = min(1., max(0., value))
    return value*value*(3-2*value)


def motion_settings(motion, seconds):
    """Sample the builder's restricted cubic curves, without a runtime."""
    result = {}
    for curve in motion['Curves']:
        if curve['Target'] != 'Parameter':
            continue
        data = curve['Segments']
        ta, va = data[:2]
        for i in range(2, len(data), 7):
            kind, t1, v1, t2, v2, tb, vb = data[i:i+7]
            if kind != 1 or abs(t1-ta-(tb-ta)/3) > 2e-6 or abs(t2-tb+(tb-ta)/3) > 2e-6:
                raise ValueError('Expected the existing restricted cubic motion')
            if seconds <= tb:
                u = min(1., max(0., (seconds-ta)/(tb-ta)))
                va = (1-u)**3*va+3*(1-u)**2*u*v1+3*(1-u)*u*u*v2+u**3*vb
                break
            ta, va = tb, vb
        result[curve['Id']] = va
    return result


def _selection(parameter, value):
    keys = sorted(parameter.keyforms, key=lambda key: key.value)
    if value <= keys[0].value:
        return [(keys[0], 1.)]
    for first, last in zip(keys, keys[1:]):
        if value <= last.value:
            weight = (value-first.value)/(last.value-first.value)
            return [(first, 1-weight), (last, weight)]
    return [(keys[-1], 1.)]


class NativeClothParents:
    """Replay only the requested warp ancestors, prewarping each child grid.

    No Core or whole-rig evaluation is performed. Rotation parents are rejected
    because cloth has no such ancestor in this source contract.
    """
    def __init__(self, builder):
        self.builder = builder
        self.drivers = {}
        for parameter in builder.params.values():
            names = set()
            for key in parameter.keyforms:
                names.update(key.deformer_offsets)
                names.update(key.deformer_offset_weights)
            for name in names:
                self.drivers.setdefault(name, []).append(parameter)

    def grid(self, name, settings, cache):
        if name in cache:
            return cache[name]
        node = self.builder.nodes[name]
        if node.type != 'warp':
            raise ValueError(f'Unexpected non-warp cloth ancestor: {name}')
        base = np.asarray(node.grid_vertices, dtype=float)
        delta = np.zeros_like(base)
        weight = 1.
        for parameter in self.drivers.get(name, []):
            factor = 0.
            for key, fraction in _selection(parameter, settings.get(parameter.id, parameter.default)):
                if name in key.deformer_offsets:
                    delta += fraction*np.asarray(key.deformer_offsets[name])
                factor += fraction*key.deformer_offset_weights.get(name, 1.)
            weight *= factor
        grid = base+weight*delta
        if node.parent is not None:
            parent = self.builder.nodes[node.parent]
            grid = warp_points(grid, self.grid(node.parent, settings, cache), parent.grid_rows, parent.grid_cols)
        cache[name] = grid
        return grid


def gathered_transfer_points(source, posed, panel, joints, direction, settings, painted_hem=None):
    """A local knee bulge/crease and hem gather, with the waist row pinned.

    The knee/ankle coordinates come from the existing handoff mesh at this key.
    The shoe reflection happens under the hem: its ankle sets a lower bound on
    hem coverage, while the crease above it can still move with the knee.
    """
    source, posed = np.asarray(source), np.asarray(posed)
    left, waist, right, hem = panel
    result = posed.copy()
    strength = (1-settings.get('ParamClimbRefine', 0.))
    # Original skirt is hidden at .92. Keep the gather until that exchange;
    # identity at the canonical swing endpoint avoids outgoing hidden offsets.
    strength *= 1-_smooth((settings.get('ParamPoseSwing', 0.)-.92)/.08)
    if strength <= 1e-12:
        return result
    pin = max(.575, waist)
    span = max(1e-6, hem-pin)
    t = np.clip((source[:, 1]-pin)/span, 0., 1.)
    lower = 1.2*t*t/(t+.2)
    arch = 4*t*(1-t)
    # Compare in the posed plane; every original view uses the same leg path.
    knees = np.asarray([joint[1] for joint in joints])
    hips = np.asarray([joint[0] for joint in joints])
    ankles = np.asarray([joint[2] for joint in joints])
    centers = hips[:, 0]+.35*(knees[:, 0]-hips[:, 0])
    distances = posed[:, 0, None]-centers[None, :]
    influences = np.exp(-(distances/.105)**2)
    influence = np.max(influences, axis=1)
    # The full-canvas deformer has no painted cloth outside this panel. Taper
    # across its adjacent cells and leave the rest of the canvas untouched.
    lo = max(0., math.floor((left-1/16)*16)/16)
    hi = min(1., math.ceil((right+1/16)*16)/16)
    margin = np.clip((source[:,0]-lo)/(left-lo),0,1)*np.clip((hi-source[:,0])/(hi-right),0,1)
    influence *= margin
    dominant = np.argmax(influences, axis=1)
    knee_y = knees[dominant, 1]
    ankle_y = ankles[dominant, 1]
    # Keep cloth over the material-changing ankle. The hem lift can be small
    # during the tuck; the interior crease and knee silhouette must not vanish.
    if painted_hem is None:
        # Pure-function fixtures use an identity parent. Production supplies
        # original-alpha hem points already carried through the actual parent.
        painted_hem = np.array([[left,hem],[right,hem]])
    painted_hem = np.asarray(painted_hem)
    order = np.argsort(painted_hem[:,0])
    world_hem = np.interp(posed[:,0],painted_hem[order,0],painted_hem[order,1])
    lift = np.clip(world_hem-np.maximum(knee_y+.05, ankle_y+.022), 0., .10)
    crease = np.clip(ankle_y-knee_y, .035, .13)*.32
    # Bulge toward the actual raised knee, bounded locally rather than scaling
    # the costume. Both hem and waist have zero horizontal crease displacement.
    bow = np.clip(direction*(knees[dominant, 0]-hips[dominant, 0]), 0., .065)
    result[:, 0] += direction*strength*influence*bow*arch
    result[:, 1] -= strength*influence*(lift*lower+crease*arch)
    result[t == 0] = posed[t == 0]
    return result


def painted_hem_points(builder, layer):
    """Sample bottom opaque paint in narrow columns of the original skirt PNG."""
    from PIL import Image
    alpha = np.asarray(Image.open(builder.asset_root/layer['file']).getchannel('A'))
    ys, xs = np.nonzero(alpha >= 128)
    edges = np.linspace(xs.min(),xs.max()+1,34)
    points = []
    for left,right in zip(edges,edges[1:]):
        selected = (xs>=left)&(xs<right)
        if not selected.any():
            continue
        bottom = ys[selected].max()
        bottom_x = xs[selected & (ys==bottom)]
        points.append(((float(bottom_x.mean())+.5)/builder.width,(float(bottom)+.5)/builder.height))
    return np.asarray(points)


def apply_climb_skirt_transfer(builder):
    """Add only ten clothing leaf nodes, using existing PoseClimb/Handoff axes."""
    from climb_refinement import HANDOFF, _mesh_point, _painted_skirt_bounds
    from maple_motions import build_motion, TRANSITIONS
    from build_v4 import view_weight

    selected = [part for part in builder.parts
                if builder.layer_by_id[part.id].get('role') == 'clothing'
                and builder.layer_by_id[part.id].get('has_seated_variant')
                and builder.layer_by_id[part.id].get('pose', 'rest') == 'rest']
    if not selected:
        return
    defaults = {name: parameter.default for name, parameter in builder.params.items()}
    motions = {direction: build_motion('climb_to_top_'+side, TRANSITIONS['climb_to_top_'+side], defaults)
               for direction, side in [(-1, 'left'), (1, 'right')]}
    parents = NativeClothParents(builder)
    # Use each painted skirt's bounds for its matching opacity underlay too.
    panels = {layer.get('view', 'front'): _painted_skirt_bounds(builder, layer)
              for layer in builder.layers if layer.get('family') == 'skirt'
              and layer.get('pose', 'rest') == 'rest'}
    hems = {layer.get('view', 'front'): painted_hem_points(builder,layer)
            for layer in builder.layers if layer.get('family') == 'skirt'
            and layer.get('pose','rest') == 'rest'}
    entries = []
    for part in selected:
        name = 'ClimbSkirtTransfer_'+part.id
        if name in builder.nodes:
            raise ValueError('Cloth handoff already applied')
        parent = part.parent_deformer
        ancestor = builder.nodes[parent]
        if ancestor.type != 'warp' or (ancestor.grid_rows,ancestor.grid_cols)!=(17,17):
            raise ValueError('Identity cloth child must retain its existing 17 by 17 parent sampling')
        node = builder.warp(name, parent, 17)
        part.parent_deformer = node
        layer = builder.layer_by_id[part.id]
        view = layer.get('view','front')
        entries.append((node, parent, panels[view],hems[view],view))
        for key in builder.params[HANDOFF].keyforms:
            key.deformer_offset_weights[node] = key.value
    for key in builder.params['ParamPoseClimb'].keyforms:
        direction = 1 if key.value >= 0 else -1
        seconds = (1-abs(key.value))*1.8
        settings = motion_settings(motions[direction], seconds)
        settings['ParamPoseClimb'] = key.value
        if abs(settings.get('ParamSwing',0.))>1e-12:
            raise ValueError('Handoff leg/cloth planes require the existing neutral swing scene')
        cache = {}
        joints = []
        for side in ('l', 'r'):
            name = f'leg_{side}_climb_handoff'
            mesh = builder.meshes[name]
            positions = np.asarray(mesh.vertices)+np.asarray(key.mesh_offsets[name])
            layer = builder.layer_by_id[name]
            joints.append(tuple(_mesh_point(mesh, positions, layer[anchor]) for anchor in ('hip', 'pivot', 'ankle')))
        for name, parent, panel,hem,view in entries:
            node, ancestor = builder.nodes[name], builder.nodes[parent]
            source = np.asarray(node.grid_vertices)
            if (abs(key.value) >= 1-1e-12 or abs(key.value) <= 1e-12
                    or view_weight(view,settings.get('ParamBodyTurn',0.))<=1e-12):
                local = source
            else:
                grid = parents.grid(parent, settings, cache)
                posed = warp_points(source, grid, ancestor.grid_rows, ancestor.grid_cols)
                posed_hem = warp_points(hem,grid,ancestor.grid_rows,ancestor.grid_cols)
                target = gathered_transfer_points(source, posed, panel, joints, direction, settings,posed_hem)
                changed = np.max(np.abs(target-posed), axis=1) > 1e-12
                local = source.copy()
                if np.any(changed):
                    # Existing full-body view fields can fold empty canvas far
                    # outside the robe. Invert only the unchanged rectangular
                    # cloth domain, rejecting any fold inside that domain.
                    cols,rows=ancestor.grid_cols,ancestor.grid_rows
                    x0=max(0,math.floor((panel[0]-1/16)*(cols-1)))
                    x1=min(cols-1,math.ceil((panel[2]+1/16)*(cols-1)))
                    y0=math.floor(max(.575,panel[1])*(rows-1))
                    cropped=grid.reshape(rows,cols,2)[y0:,x0:x1+1]
                    uv=unwarp_points(target[changed],cropped,len(cropped),x1-x0+1)
                    local[changed]=uv*((x1-x0)/(cols-1),(rows-1-y0)/(rows-1))+(x0/(cols-1),y0/(rows-1))
            if not np.isfinite(local).all():
                raise ValueError(f'Nonfinite cloth transfer key: {name}/{key.value}')
            key.deformer_offsets[name] = [tuple(row) for row in local-source]
