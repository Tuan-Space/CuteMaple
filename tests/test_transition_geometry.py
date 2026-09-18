"""Painted-geometry regressions for authored IR, NOT native Cubism QA.

Uses the current generated v4 rig, source PNGs and runtime motion files. No
archived diagnostic image or manually positioned screen ROI is a test input.
Install the development environment with ``pip install -r requirements-build.txt``
(OpenCV, its NumPy dependency, Pillow and pytest), then run with
``.venv/Scripts/python.exe -m pytest tests/test_transition_geometry.py``.
Missing authoring dependencies are collection errors, never visual-QA passes.
These packages are deliberately absent from the published runtime requirements.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/authoring"))
from diagnose_rig import DiagnosticRenderer, Rig, motion_parameters

ASSETS = ROOT / "assets/authoring/source"
RESOLUTION = 384


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def transition_ir():
    rig_path = ASSETS / "Maple.rig.json"
    manifest_path = ASSETS / "layers.json"
    initial_hash = _digest(rig_path)
    rig = Rig.model_validate_json(rig_path.read_text(encoding="utf-8"))
    layers = json.loads(manifest_path.read_text(encoding="utf-8"))["layers"]
    groups = {
        "face": {layer["id"] for layer in layers
                 if layer["role"] == "face_base" and layer.get("family") == "face_base"},
        "neck": {layer["id"] for layer in layers if layer["role"] == "neck"},
        # The front neck can be painted into this legitimate costume underlay.
        "torso": {layer["id"] for layer in layers if layer["role"] == "torso"},
        "standing": {layer["id"] for layer in layers if layer.get("has_seated_variant")},
        "seated": {layer["id"] for layer in layers if layer.get("pose") == "swing"
                   and (layer["role"] == "clothing" or layer["role"].startswith("leg_"))},
    }
    assert all(groups.values()), "Required semantic anatomy/pose groups disappeared"
    selected = set.union(*groups.values())
    parts = [part for part in rig.parts if part.id in selected]
    assert {part.id for part in parts} == selected
    textures = [texture for texture in rig.textures
                if texture.id in {part.texture_id for part in parts}]
    motions = {side: ROOT / f"assets/live2d/Maple/motions/climb_to_top_{side}.motion3.json"
               for side in ("left", "right")}
    paths = [rig_path, manifest_path, *motions.values(),
             *(ASSETS / texture.path for texture in textures)]
    hashes = {path: _digest(path) for path in paths}
    assert hashes[rig_path] == initial_hash, "Rig changed while the fixture was loading"

    # Keep every authored mesh and parent lattice; prune only unused paint and
    # its opacity entries. DiagnosticRenderer otherwise evaluates unchanged IR.
    parameters = [parameter.model_copy(update={"keyforms": [
        key.model_copy(update={"opacity_overrides": {
            name: value for name, value in key.opacity_overrides.items() if name in selected
        }}) for key in parameter.keyforms
    ]}) for parameter in rig.parameters]
    subset = rig.model_copy(update={"parts": parts, "textures": textures,
                                   "parameters": parameters})
    renderer = DiagnosticRenderer(subset, ASSETS, RESOLUTION)
    yield renderer, groups, motions, initial_hash
    changed = [str(path.relative_to(ROOT)) for path, sha in hashes.items()
               if not path.exists() or _digest(path) != sha]
    assert not changed, f"Authoring inputs changed during IR checks; rerun: {changed}"


def _alpha(renderer, groups, group, settings):
    hidden = set.union(*groups.values()) - groups[group]
    image, _ = renderer.render({**settings, "__hiddenLayers": sorted(hidden)})
    return np.asarray(image)[:, :, 3].astype(float) / 255


def _dominant_component(labels, mask):
    values, counts = np.unique(labels[mask], return_counts=True)
    assert len(values) and mask.sum() > 20, "Anatomical paint disappeared"
    return int(values[np.argmax(counts)])


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("seconds", [1.10, 1.20, 1.30])
def test_transition_face_neck_and_torso_have_a_painted_connection(transition_ir, side, seconds):
    renderer, groups, motions, sha = transition_ir
    duration = json.loads(motions[side].read_text(encoding="utf-8"))["Meta"]["Duration"]
    settings = motion_parameters(motions[side], seconds / duration)
    masks = {group: _alpha(renderer, groups, group, settings) >= .5
             for group in ("face", "neck", "torso")}
    # Each painted boundary gets one pixel of anti-alias tolerance at 384 px;
    # this permits a 2-pixel edge seam, but cannot bridge the known 10-pixel gap.
    anatomy = masks["face"] | masks["neck"] | masks["torso"]
    tolerant = cv2.dilate(anatomy.astype(np.uint8), np.ones((3, 3), np.uint8))
    _, labels = cv2.connectedComponents(tolerant, connectivity=8)
    face = _dominant_component(labels, masks["face"])
    torso = _dominant_component(labels, masks["torso"])
    # Requiring a shared component all the way to torso rejects an isolated
    # neck touching the head while remaining detached from the body.
    assert face == torso and face != 0, (
        f"IR {sha}: {side} at {seconds:.2f}s has no painted head/body path; "
        f"solid neck pixels={masks['neck'].sum()}, components={face}/{torso}"
    )


@pytest.mark.parametrize("side", ["left", "right"])
def test_lower_body_exchange_is_short_and_opaque_where_materials_overlap(transition_ir, side):
    renderer, groups, motions, sha = transition_ir
    duration = json.loads(motions[side].read_text(encoding="utf-8"))["Meta"]["Duration"]
    times = np.linspace(0, duration, round(duration * 20) + 1)
    candidates = []
    for seconds in times:
        settings = motion_parameters(motions[side], float(seconds / duration))
        evaluated = renderer.evaluate(settings)
        standing, seated = (max(evaluated[name][1] for name in groups[group])
                            for group in ("standing", "seated"))
        if min(standing, seated) > .05:
            candidates.append((min(standing, seated), float(seconds), settings))
    assert candidates, "No material exchange sampled; inspect choreography or increase sampling"

    # Only three representative actual rasters, selected from the motion's most
    # visible exchange rather than fixed old choreography times. Cheap 20 Hz
    # geometry evaluation above locates the interval without whole-model video.
    selected = sorted(candidates, reverse=True, key=lambda item: item[0])[:3]
    measurements = []
    for _, seconds, settings in selected:
        standing = _alpha(renderer, groups, "standing", settings)
        seated = _alpha(renderer, groups, "seated", settings)
        first, second = standing > .05, seated > .05
        union = first | second
        assert union.any(), "Lower-body painted geometry disappeared"
        mismatch = float((first ^ second).sum() / union.sum())
        overlap = cv2.erode((first & second).astype(np.uint8),
                            np.ones((5, 5), np.uint8)).astype(bool)
        assert overlap.sum() > 20, "Pose materials have no substantial painted overlap"
        combined = seated + standing * (1 - seated)
        opaque_fraction = float((combined[overlap] >= .90).mean())
        measurements.append((round(seconds, 3), round(mismatch, 3), round(opaque_fraction, 3)))

    # Five percent unmatched silhouette is beyond an antialiased edge. A short
    # exchange may reshape the skirt; a >250 ms double silhouette is a ghost.
    exchange_seconds = candidates[-1][1] - candidates[0][1] + duration / (len(times) - 1)
    errors = []
    if max(item[1] for item in measurements) > .05 and exchange_seconds > .25 + 1e-8:
        errors.append(f"distinct lower-body silhouettes coexist for {exchange_seconds:.3f}s (>0.25s)")
    # Allow 5% of the eroded overlap for holes/fine paint, but require solid
    # underlying cloth in the rest; .5/.5 normal-over cannot satisfy this.
    if min(item[2] for item in measurements) < .95:
        errors.append("less than 95% of shared material interior is at least 90% opaque")
    assert not errors, (f"IR {sha}: {side}: {'; '.join(errors)}; "
                        f"(seconds, silhouette mismatch, opaque overlap)={measurements}")
