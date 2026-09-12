"""Offline authored-IR motion preflight; NOT a native Cubism playback recording.

Uses the existing bilinear diagnostic renderer to inspect intermediate geometry
before an Editor export. The native Cubism interpolator can produce differences.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw
from diagnose_rig import DiagnosticRenderer, Rig, motion_parameters


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rig", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--motion", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New empty output directory")
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--size", type=int, default=384)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.fps <= 60 or not 128 <= args.size <= 1024:
        parser.error("Use a new output directory, 1–60 fps and 128–1024 pixels")
    rig = Rig.model_validate_json(args.rig.read_text(encoding="utf-8"))
    inputs = {str(path.resolve()): digest(path) for path in
              [args.rig, args.motion, *(args.assets / texture.path for texture in rig.textures)]}
    duration = json.loads(args.motion.read_text(encoding="utf-8-sig"))["Meta"]["Duration"]
    count = max(2, round(duration * args.fps) + 1)
    renderer = DiagnosticRenderer(rig, args.assets, args.size)
    args.output.mkdir(parents=True)
    frames, samples = [], []
    for index in range(count):
        phase = index / (count - 1)
        parameters = motion_parameters(args.motion, phase)
        frame, stats = renderer.render(parameters)
        frame.save(args.output / f"{index:04d}.png")
        background = Image.new("RGBA", frame.size, (247, 246, 243, 255))
        background.alpha_composite(frame)
        frames.append(background.convert("RGB"))
        samples.append({"timeSeconds": duration * phase, "parameters": parameters, **stats})
        if index % 10 == 0:
            print(f"IR preflight {index + 1}/{count}", flush=True)
    frames[0].save(args.output / "offline-preview.gif", save_all=True, append_images=frames[1:],
                   duration=round(1000 / args.fps), loop=0, disposal=2)
    columns, tile, label_height = 6, 224, 22
    rows = (count + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile, rows * (tile + label_height)), (247, 246, 243))
    pen = ImageDraw.Draw(sheet)
    for index, frame in enumerate(frames):
        x, y = (index % columns) * tile, (index // columns) * (tile + label_height)
        sheet.paste(frame.resize((tile, tile), Image.Resampling.LANCZOS), (x, y))
        pen.text((x + 4, y + tile + 2), f"IR {index:04d} / {duration * index / (count - 1):.2f}s", fill=(40, 40, 40))
    sheet.save(args.output / "all-frames.png")
    changed = [path for path, sha in inputs.items() if digest(Path(path)) != sha]
    report = {"method": __doc__, "nativeRuntimeValidation": False,
              "inputHashes": inputs, "inputsChangedDuringRender": changed,
              "authoredDurationSeconds": duration, "frames": count, "samples": samples,
              "visualReview": "pending", "gifSha256": digest(args.output / "offline-preview.gif")}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "frames": count, "inputsChanged": changed}), flush=True)
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
