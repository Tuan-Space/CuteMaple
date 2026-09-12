"""Bounded ordering and far-material retirement for painted head exchanges.

Facial detail stays in its common foreground band: grouping each eye/face
mesh by view would multiply view alpha separately and wash out pupils. Only
base face/ear/hair/ornament paint is grouped. Source geometry/UVs are untouched.
"""
from __future__ import annotations


BASE_ROLES = frozenset(('face_base', 'hair_front', 'hair_side'))
FAR_FAMILIES = frozenset(('ornament_l', 'ornament_r', 'ribbon_l', 'ribbon_r'))


def natural_far_family(family, turn):
    """The far side is a view/turn occlusion decision, never an alpha-box guess."""
    return family in FAR_FAMILIES and turn != 0 and family.endswith('_l' if turn < 0 else '_r')


def grouped_head_base_orders(layers, old_orders, paint_order):
    """Use the existing180..219 base band; retain within-view painted order."""
    selected = [(index, layer) for index, layer in enumerate(layers)
                if layer.get('zone') == 'head' and layer.get('role') in BASE_ROLES
                and layer.get('view') in paint_order]
    selected.sort(key=lambda item: (paint_order[item[1]['view']], old_orders[item[1]['id']], item[0]))
    if len(selected) > 40:
        raise ValueError('Head base inventory no longer fits below facial detail220')
    return {layer['id']: 180 + index for index, (_, layer) in enumerate(selected)}


def far_retirement_key_factors(layer, turns, original_coverage, inherited_weight, available):
    """Return one factor per native head key only for an actual far lower layer.

    The visible lower parent is1, so its mesh keys use the original complementary
    coverage. Upper mid-views need no extra binding. At a hidden lower guard
    endpoint the factor must be0: choosing1 would create a quarter-opacity
    flash between the two native linear axes even though both endpoint products
    are nearly zero. The tiny remaining guard product error is tested directly.
    """
    family, view = layer.get('family'), layer.get('view')
    if layer.get('zone') != 'head' or family not in FAR_FAMILIES or view not in available:
        return None
    weights = [(original_coverage(turn, available).get(view, 0.),
                inherited_weight(view, turn, available)) for turn in turns]
    if not any(natural_far_family(family, turn) and inherited-original > 1e-12
               for turn, (original, inherited) in zip(turns, weights)):
        return None
    factors = []
    for turn, (original, inherited) in zip(turns, weights):
        if not natural_far_family(family, turn):
            factor = 1.
        elif inherited <= 1e-12:
            factor = 0.
        else:
            factor = original/inherited
        if not 0 <= factor <= 1 + 1e-12:
            raise ValueError('Invalid complementary far-material coverage')
        factors.append(min(1., factor))
    return factors


def apply_head_view_exchange(builder, original_coverage, inherited_weight, paint_order):
    """Finalize only head base orders and the existing HeadTurn opacity keys."""
    parts = {part.id: part for part in builder.parts}
    orders = grouped_head_base_orders(builder.layers, {name: part.draw_order for name, part in parts.items()}, paint_order)
    for parameter in builder.params.values():
        for key in parameter.keyforms:
            if set(orders).intersection(key.draw_order_overrides):
                raise ValueError('Animated head base order needs explicit exchange review')
    for name, order in orders.items():
        parts[name].draw_order = order
    keys = builder.params['ParamHeadTurn'].keyforms
    turns = [key.value for key in keys]
    available = {layer.get('view') for layer in builder.layers if layer.get('view') in paint_order}
    for layer in builder.layers:
        factors = far_retirement_key_factors(layer, turns, original_coverage, inherited_weight, available)
        if factors is None:
            continue
        name = layer['id']
        for key, factor in zip(keys, factors):
            if name in key.opacity_overrides:
                raise ValueError(f'Head material already has a competing opacity binding: {name}')
            key.opacity_overrides[name] = factor
