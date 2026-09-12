"""Tiny painted triangles exercise the offline rasterizer without a model build."""
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "authoring"))
from diagnose_rig import DiagnosticRenderer


QUAD = [(0., 0.), (1., 0.), (0., 1.), (1., 1.)]
QUAD_TRIANGLES = [(0, 1, 2), (1, 3, 2)]
OVERLAP = [(0., 0.), (1., 0.), (0., 1.)] * 2
OVERLAP_TRIANGLES = [(0, 1, 2), (3, 4, 5)]


def layer(name, colors, *, vertices=QUAD, triangles=QUAD_TRIANGLES,
          uvs=None, opacity=1., order=0, clip_to=(), invert=False):
    pixels = np.array([colors], dtype=np.float32)
    pixels[:, :, :3] *= pixels[:, :, 3:4]
    return (
        SimpleNamespace(id=name, texture_id=name, opacity=opacity, draw_order=order,
                        parent_deformer=None, clip_to=list(clip_to), invert_clipping=invert),
        SimpleNamespace(vertices=vertices, triangles=triangles,
                        uvs=uvs if uvs is not None else [(0.5, 0.5)] * len(vertices)),
        pixels,
    )


def renderer(*layers, resolution=8):
    value = DiagnosticRenderer.__new__(DiagnosticRenderer)
    value.res = resolution
    value.rig = SimpleNamespace(parts=[entry[0] for entry in layers], parameters=[])
    value.nodes = {}
    value.meshes = {part.id: mesh for part, mesh, _ in layers}
    value.textures = {part.id: pixels for part, _, pixels in layers}
    return value


def overlap(colors, *, opacity=1., triangles=OVERLAP_TRIANGLES):
    return layer("fold", colors, vertices=OVERLAP, triangles=triangles,
                 uvs=[(0.25, 0.5)] * 3 + [(0.75, 0.5)] * 3, opacity=opacity)


def test_later_transparent_triangle_preserves_earlier_painted_coverage():
    value = renderer(overlap([(1., 0., 0., 1.), (0., 0., 0., 0.)]))
    image, _ = value.render({})
    assert image.getpixel((1, 1)) == (255, 0, 0, 255)
    assert image.getpixel((7, 7)) == (0, 0, 0, 0)


def test_separated_overlays_keep_background_and_earlier_paint_through_batch_gaps():
    first = [(0., 0.), (0.375, 0.), (0., 0.375)]
    second = [(0.625, 0.625), (1., 0.625), (1., 1.)]
    vertices = QUAD + first + second + first
    triangles = QUAD_TRIANGLES + [(4, 5, 6), (7, 8, 9), (10, 11, 12)]
    colors = [(1., 0., 0., 1.), (0., 1., 0., 1.),
              (0., 0., 1., 1.), (0., 0., 0., 0.)]
    uvs = [(0.125, 0.5)] * 4 + [(0.375, 0.5)] * 3 + [(0.625, 0.5)] * 3 + [(0.875, 0.5)] * 3
    image, _ = renderer(layer("patches", colors, vertices=vertices,
                               triangles=triangles, uvs=uvs)).render({})
    assert image.getpixel((0, 0)) == (0, 255, 0, 255)
    assert image.getpixel((7, 6)) == (0, 0, 255, 255)
    assert image.getpixel((3, 3)) == (255, 0, 0, 255)


@pytest.mark.parametrize("opacity,expected", [
    (1., (85, 0, 170, 191)),
    (0.5, (109, 0, 145, 111)),
])
def test_overlapping_translucent_triangles_blend_in_mesh_order(opacity, expected):
    colors = [(1., 0., 0., 0.5), (0., 0., 1., 0.5)]
    forward, _ = renderer(overlap(colors, opacity=opacity)).render({})
    reverse, _ = renderer(overlap(colors, opacity=opacity,
                                  triangles=list(reversed(OVERLAP_TRIANGLES)))).render({})
    assert forward.getpixel((1, 1)) == expected
    assert reverse.getpixel((1, 1)) == (expected[2], 0, expected[0], expected[3])


@pytest.mark.parametrize("winding", ["positive", "negative", "mixed"])
def test_adjacent_triangles_cover_shared_edge_once_for_either_winding(winding):
    triangles = [tuple(reversed(triangle)) if winding == "negative" or
                 (winding == "mixed" and index == 1) else triangle
                 for index, triangle in enumerate(QUAD_TRIANGLES)]
    value = renderer(layer("quad", [(0., 1., 0., 0.5)], triangles=triangles))
    image, stats = value.render({})
    np.testing.assert_array_equal(np.asarray(image),
                                  np.broadcast_to([0, 255, 0, 127], (8, 8, 4)))
    assert stats["invertedTriangles"].get("quad", 0) == {
        "positive": 0, "negative": 2, "mixed": 1,
    }[winding]


@pytest.mark.parametrize("invert,expected", [
    (False, (127, 0, 127, 102)),
    (True, (218, 0, 36, 178)),
])
def test_clipping_opacity_and_draw_order_remain_effective(invert, expected):
    mask = layer("mask", [(0., 0., 1., 0.25)], order=2)
    paint = layer("paint", [(1., 0., 0., 1.)], order=3, opacity=0.8,
                  clip_to=["mask"], invert=invert)
    image, stats = renderer(paint, mask).render({})
    assert image.getpixel((1, 1)) == expected
    assert stats["visibleLayers"] == ["mask", "paint"]


def test_overlap_clipping_applies_to_each_fragment_without_erasing_prior_paint():
    mask = layer("mask", [(0., 0., 1., 0.5)])
    paint = layer("paint", [(1., 0., 0., 1.)], vertices=OVERLAP,
                  triangles=OVERLAP_TRIANGLES, opacity=0.5, order=1, clip_to=["mask"])
    image, _ = renderer(mask, paint).render({})
    # Two red fragments each contribute alpha .25; their combined alpha is
    # .4375, then the existing blue mask contributes .28125 behind them.
    assert image.getpixel((1, 1)) == (155, 0, 99, 183)


def test_missing_or_hidden_clipping_source_still_fails_closed():
    mask = layer("mask", [(1., 1., 1., 1.)])
    paint = layer("paint", [(1., 0., 0., 1.)], order=1, clip_to=["mask"])
    for value, settings in [(renderer(paint), {}),
                            (renderer(mask, paint), {"__hiddenLayers": ["mask"]})]:
        with pytest.raises(ValueError, match="clipping source must be drawn before target"):
            value.render(settings)
