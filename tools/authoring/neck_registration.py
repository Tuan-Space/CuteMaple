"""One blended neck registration connecting independent head/body turns.

All coordinates are normalized on the existing 1024-square registered source.
The view is the neck's painted/body material view. Its opacity belongs to the
body material selection; this function supplies geometry only.

For independent Cubism parameter contributions, use neck_point(p, view, 0, 0)
as the baseline, then subtract THAT baseline from each single-parameter form.
Adding two full registrations relative to p would apply the neutral map twice.
This field does not include the additional AngleX/Y/Z or expression warps.
"""
from __future__ import annotations

try:
    from .view_registration import register_point
except ImportError:  # Existing authoring tools also run directly as scripts.
    from view_registration import register_point


# Existing alpha bounds in registered pixels, measured 2026-09-08:
# left 355..409, left_mid 352..411, right 350..421, right_mid 347..424.
# Keep the first six pixels with the jaw and the last four fully with the
# collar; the intervening source band supplies a smooth convex blend.
# The newly separated front neck uses the requested .35..41 default band.
_Y_BANDS = {
    'front': (.35, .41),
    'left': (361/1024, 405/1024),
    'left_mid': (358/1024, 407/1024),
    'right': (356/1024, 417/1024),
    'right_mid': (353/1024, 420/1024),
}


def neck_point(point, view, head_turn, body_turn):
    """Jaw edge follows head; collar edge follows body, with weights summing 1."""
    # Delegate argument validation to the common fields, including both turns
    # even when this point lies entirely within one endpoint's region.
    head = register_point(point, view, head_turn, 'head')
    body = register_point(point, view, body_turn, 'body')
    first, last = _Y_BANDS[view]
    t = min(1., max(0., (float(point[1])-first)/(last-first)))
    weight = t*t*(3-2*t)
    return tuple(a+(b-a)*weight for a, b in zip(head, body))
