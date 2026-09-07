from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "source_sheets_v7"
DEST = ROOT / "assets" / "sprites_v2"
PREVIEW = ROOT / "assets" / "previews_v2"
SPECS = {
    "clean_ground": ("clean_ground.png", 3, 2, 6, "bottom"),
    "clean_climb_left": ("clean_climb_left.png", 2, 2, 4, "center"),
    "clean_climb_right": ("clean_climb_right.png", 2, 2, 4, "center"),
    "clean_top": ("clean_top.png", 2, 2, 4, "top"),
}


def remove_key(image: Image.Image) -> Image.Image:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    candidate = ((hsv[:, :, 0] >= 140) & (hsv[:, :, 0] <= 179) &
                 (hsv[:, :, 1] >= 90) & (hsv[:, :, 2] >= 130)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
    borders = set(np.unique(labels[0])) | set(np.unique(labels[-1]))
    borders |= set(np.unique(labels[:, 0])) | set(np.unique(labels[:, -1]))
    removable = [n for n in range(1, count) if n in borders or stats[n, cv2.CC_STAT_AREA] >= 300]
    background = np.isin(labels, removable).astype(np.uint8)
    background = cv2.morphologyEx(background, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    foreground = 1 - background
    component_count, component_labels, component_stats, _ = cv2.connectedComponentsWithStats(foreground, 8)
    keep = [n for n in range(1, component_count) if component_stats[n, cv2.CC_STAT_AREA] >= 8]
    foreground = np.isin(component_labels, keep).astype(np.uint8)
    alpha = np.clip(cv2.distanceTransform(foreground, cv2.DIST_L2, 3) * 135, 0, 255).astype(np.uint8)
    rgba = np.dstack((rgb, alpha)); rgba[alpha == 0, :3] = 0
    return Image.fromarray(rgba, "RGBA")


def crop_slot(sheet: Image.Image, columns: int, rows: int, index: int) -> Image.Image:
    col, row = index % columns, index // columns
    return sheet.crop((round(col * sheet.width / columns), round(row * sheet.height / rows),
                       round((col + 1) * sheet.width / columns), round((row + 1) * sheet.height / rows)))


def normalize_group(images: list[Image.Image], anchor: str) -> list[Image.Image]:
    boxes = []
    for image in images:
        alpha = np.asarray(image.getchannel("A"))
        points = np.argwhere(alpha >= 12)
        top, left = points.min(axis=0); bottom, right = points.max(axis=0) + 1
        boxes.append((int(left), int(top), int(right), int(bottom)))
    max_width = max(r - l for l, _, r, _ in boxes)
    max_height = max(b - t for _, t, _, b in boxes)
    scale = min(486 / max_width, 460 / max_height)
    rendered = []
    for image, box in zip(images, boxes):
        content = image.crop(box)
        size = (round(content.width * scale), round(content.height * scale))
        content = content.resize(size, Image.Resampling.LANCZOS)
        content = content.filter(ImageFilter.UnsharpMask(radius=.65, percent=105, threshold=2))
        canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        x = (512 - size[0]) // 2
        y = 40 if anchor == "top" else (500 - size[1] if anchor == "bottom" else (512 - size[1]) // 2)
        canvas.alpha_composite(content, (x, y)); rendered.append(canvas)
    return rendered


def main() -> None:
    manifest_path = DEST / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_frames = []
    for state, (filename, columns, rows, count, anchor) in SPECS.items():
        sheet = Image.open(SOURCE / filename).convert("RGB")
        raw = [remove_key(crop_slot(sheet, columns, rows, i)) for i in range(count)]
        frames = normalize_group(raw, anchor)
        names = []
        for index, frame in enumerate(frames, 1):
            name = f"{state}_{index:02d}.png"; frame.save(DEST / name, optimize=True); names.append(name)
            all_frames.append((state, frame))
        manifest[state] = names
        gif = [Image.new("RGBA", (512, 512), "#eef7fb") for _ in frames]
        for bg, frame in zip(gif, frames): bg.alpha_composite(frame)
        gif[0].convert("RGB").resize((256, 256)).save(
            PREVIEW / f"{state}.gif", save_all=True,
            append_images=[f.convert("RGB").resize((256, 256)) for f in gif[1:]], duration=200, loop=0)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    qa = Image.new("RGB", (6 * 180, 4 * 205), "#202126"); draw = ImageDraw.Draw(qa)
    for index, (state, frame) in enumerate(all_frames):
        col, row = index % 6, index // 6
        bg = Image.new("RGBA", (180, 180), "#ff00a8" if index % 2 else "#202126")
        thumb = frame.copy(); thumb.thumbnail((176, 176), Image.Resampling.LANCZOS)
        bg.alpha_composite(thumb, ((180-thumb.width)//2, (180-thumb.height)//2))
        qa.paste(bg.convert("RGB"), (col * 180, row * 205 + 25)); draw.text((col * 180 + 4, row * 205 + 5), state, fill="white")
    qa.save(ROOT / "assets" / "清理动画透明检查_v7.png")
    print(f"processed {len(all_frames)} cleanup frames")


if __name__ == "__main__":
    main()
