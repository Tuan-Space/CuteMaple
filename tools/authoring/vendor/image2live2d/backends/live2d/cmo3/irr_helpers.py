"""Pure IR keyform helpers extracted from pinned Apache-2.0 upstream.

Only the constant and four functions below are retained. No binary MOC3
serialization is included; export runtime binaries with Cubism Editor.
"""
from __future__ import annotations

from ....irr.schema import Rig


MAX_PARAMS_PER_MESH = 6


def _affecting_params(rig, part_id, forced=()):
    """Parameters whose keyforms move ``part_id`` (nonzero per-vertex delta), with a magnitude score
    so we can cap the least-important ones. Unlike the nijilive/preview path, moc3 is deformer-free, so
    we bake EVERY parameter's per-vertex offsets into keyforms (head/body turns included).

    ``forced`` ids are always included first (even with zero additive offset) — used for group-rotation
    params that drive a baked directional head/body shift rather than per-vertex offsets, so their key
    axes must exist in the mesh's keyform grid."""
    forced_ids = [fid for fid in forced if any(p.id == fid for p in rig.parameters)]
    forced_params = [p for p in rig.parameters if p.id in forced_ids]
    out = []
    for p in rig.parameters:
        if p.id in forced_ids:
            continue
        mag = 0.0
        for kf in p.keyforms:
            for dx, dy in kf.mesh_offsets.get(part_id, []):
                mag += abs(dx) + abs(dy)
        if mag > 1e-9:
            out.append((p, mag))
    out.sort(key=lambda pm: -pm[1])                       # strongest first
    return (forced_params + [p for p, _ in out])[:MAX_PARAMS_PER_MESH]


def _offset_at(param, value, part_id, nverts):
    """Per-vertex (dx, dy) deltas for ``param`` at keyform ``value`` (zeros if none)."""
    for kf in param.keyforms:
        if kf.value == value:
            offs = kf.mesh_offsets.get(part_id)
            if offs and len(offs) == nverts:
                return offs
            break
    return [(0.0, 0.0)] * nverts


def _opacity_at(param, value, part_id):
    """Absolute opacity override for ``part_id`` at ``param``'s keyform ``value``, or ``None`` if this
    param does not key the part's opacity there (then the part keeps its base opacity along this axis)."""
    for kf in param.keyforms:
        if kf.value == value:
            return kf.opacity_overrides.get(part_id)
    return None


def _opacity_params(rig, part_id):
    """Parameters that key ``part_id``'s opacity (via ``opacity_overrides``). These must join the mesh's
    keyform grid even with zero per-vertex offset, or the opacity fade has no axis to vary along."""
    out = []
    for p in rig.parameters:
        if any(part_id in kf.opacity_overrides for kf in p.keyforms):
            out.append(p)
    return out
