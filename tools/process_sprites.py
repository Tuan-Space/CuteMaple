from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


CANVAS_SIZE = 512
BASELINE_Y = 500
KEY_RGB = np.array([255.0, 0.0, 168.0], dtype=np.float32)


@dataclass(frozen=True)
class SheetSpec:
    source: str
    filename: str
    columns: int
    rows: int
    states: tuple[tuple[str, tuple[int, ...]], ...]


SHEETS = (
    SheetSpec("v4", "idle.png", 3, 2, (("idle", tuple(range(6))),)),
    SheetSpec("v2", "walk_right.png", 4, 2, (("walk_right", tuple(range(8))),)),
    SheetSpec("v2", "drag.png", 3, 2, (("drag_left", (0, 1, 2)), ("drag_right", (3, 4, 5)))),
    SheetSpec("v2", "happy.png", 3, 2, (("happy", tuple(range(6))),)),
    SheetSpec("v2", "climb_right.png", 3, 2, (("climb_right", tuple(range(6))),)),
    SheetSpec("v4", "petting.png", 2, 2, (("petting", tuple(range(4))),)),
    SheetSpec("v3", "talk.png", 2, 2, (("talk", tuple(range(4))),)),
    SheetSpec("v3", "fall_float.png", 3, 1, (("fall_float", tuple(range(3))),)),
    SheetSpec("v4", "swing_idle.png", 2, 2, (("swing_idle", tuple(range(4))),)),
    SheetSpec("v3", "sleep_enter.png", 2, 2, (("sleep_enter", tuple(range(4))),)),
    SheetSpec("v3", "sleep_loop.png", 2, 2, (("sleep_loop", tuple(range(4))),)),
    SheetSpec("v3", "sleep_exit.png", 2, 2, (("sleep_exit", tuple(range(4))),)),
)


def _remove_chroma(image: Image.Image) -> Image.Image:
    rgb_u8 = np.asarray(image.convert("RGB"), dtype=np.uint8)
    hsv = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2HSV)
    # Image generation may shade the requested key slightly. Select the broad
    # saturated magenta family, then remove only sizeable/background-connected
    # components so pink beads and blush inside the character remain intact.
    candidates = (
        (hsv[:, :, 0] >= 140)
        & (hsv[:, :, 0] <= 179)
        & (hsv[:, :, 1] >= 105)
        & (hsv[:, :, 2] >= 145)
    ).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidates, 8)
    background = np.zeros(candidates.shape, dtype=np.uint8)
    border_labels = set(np.unique(labels[0, :])) | set(np.unique(labels[-1, :]))
    border_labels |= set(np.unique(labels[:, 0])) | set(np.unique(labels[:, -1]))
    for label in range(1, count):
        if label in border_labels or stats[label, cv2.CC_STAT_AREA] >= 160:
            background[labels == label] = 1
    foreground = 1 - background
    distance = cv2.distanceTransform(foreground, cv2.DIST_L2, 3)
    alpha = np.clip(distance * 130.0, 0, 255).astype(np.uint8)
    rgba = np.dstack((rgb_u8, alpha))
    rgba[alpha == 0, :3] = 0
    return Image.fromarray(rgba, "RGBA")


def _slot(sheet: Image.Image, columns: int, rows: int, index: int) -> Image.Image:
    column, row = index % columns, index // columns
    left = round(column * sheet.width / columns)
    right = round((column + 1) * sheet.width / columns)
    top = round(row * sheet.height / rows)
    bottom = round((row + 1) * sheet.height / rows)
    return sheet.crop((left, top, right, bottom))


def _normalize(image: Image.Image) -> Image.Image:
    rgba = np.asarray(image).copy()
    alpha = rgba[:, :, 3]
    count, labels, stats, _ = cv2.connectedComponentsWithStats((alpha >= 12).astype(np.uint8), 8)
    if count <= 1:
        raise ValueError("sprite has no visible pixels")
    main_label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    alpha[labels != main_label] = 0
    rgba[alpha == 0, :3] = 0
    image = Image.fromarray(rgba, "RGBA")
    points = np.argwhere(alpha >= 12)
    if not len(points):
        raise ValueError("sprite has no visible pixels")
    top, left = points.min(axis=0)
    bottom, right = points.max(axis=0) + 1
    content = image.crop((int(left), int(top), int(right), int(bottom)))
    scale = min(460 / content.height, 486 / content.width)
    size = (max(1, round(content.width * scale)), max(1, round(content.height * scale)))
    content = content.resize(size, Image.Resampling.LANCZOS)
    content = content.filter(ImageFilter.UnsharpMask(radius=0.7, percent=110, threshold=2))
    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    x = (CANVAS_SIZE - size[0]) // 2
    y = BASELINE_Y - size[1]
    canvas.alpha_composite(content, (x, y))
    return canvas


def _normalize_group(
    images: list[Image.Image], top_anchor: bool = False, preserve_components: bool = False
) -> list[Image.Image]:
    """Normalize an action with one shared scale so short/blinking frames never zoom."""
    cleaned: list[Image.Image] = []
    boxes: list[tuple[int, int, int, int]] = []
    for image in images:
        rgba = np.asarray(image).copy()
        alpha = rgba[:, :, 3]
        count, labels, stats, _ = cv2.connectedComponentsWithStats((alpha >= 12).astype(np.uint8), 8)
        if count <= 1:
            raise ValueError("sprite has no visible pixels")
        main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        if preserve_components:
            keep = [label for label in range(1, count) if stats[label, cv2.CC_STAT_AREA] >= 8]
            alpha[~np.isin(labels, keep)] = 0
        else:
            alpha[labels != main] = 0
        rgba[alpha == 0, :3] = 0
        points = np.argwhere(alpha >= 12)
        top, left = points.min(axis=0)
        bottom, right = points.max(axis=0) + 1
        cleaned.append(Image.fromarray(rgba, "RGBA"))
        boxes.append((int(left), int(top), int(right), int(bottom)))
    max_width = max(right - left for left, _, right, _ in boxes)
    max_height = max(bottom - top for _, top, _, bottom in boxes)
    scale = min(486 / max_width, 460 / max_height)
    result: list[Image.Image] = []
    for image, box in zip(cleaned, boxes):
        content = image.crop(box)
        size = (max(1, round(content.width * scale)), max(1, round(content.height * scale)))
        content = content.resize(size, Image.Resampling.LANCZOS)
        content = content.filter(ImageFilter.UnsharpMask(radius=0.7, percent=110, threshold=2))
        canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
        x = (CANVAS_SIZE - size[0]) // 2
        y = 40 if top_anchor else BASELINE_Y - size[1]
        canvas.alpha_composite(content, (x, y))
        result.append(canvas)
    return result


def _remove_checkerboard(image: Image.Image) -> Image.Image:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    neutral = ((hsv[:, :, 1] <= 35) & (hsv[:, :, 2] >= 180)).astype(np.uint8)

    # The generator sometimes bakes the transparency preview into the RGB
    # image.  White clothing is intentionally neutral too, so seal the dark
    # and coloured line art before finding checkerboard pixels connected to
    # the canvas edge.  This keeps enclosed whites without filling genuine
    # gaps between sleeves, ribbons and the body.
    barrier = cv2.dilate(1 - neutral, np.ones((3, 3), np.uint8), iterations=2)
    traversable_background = neutral & (1 - barrier)
    count, labels, _, _ = cv2.connectedComponentsWithStats(traversable_background, 8)
    border = set(np.unique(labels[0])) | set(np.unique(labels[-1]))
    border |= set(np.unique(labels[:, 0])) | set(np.unique(labels[:, -1]))
    background = np.isin(labels, [label for label in border if label]).astype(np.uint8)
    background = cv2.dilate(background, np.ones((3, 3), np.uint8), iterations=2) & neutral
    foreground = 1 - background

    # Restore only fully enclosed holes.  They are normally pale fabric areas
    # surrounded by line art; real negative space remains connected to an edge.
    inverse = 1 - foreground
    hole_count, hole_labels, _, _ = cv2.connectedComponentsWithStats(inverse, 8)
    outside = set(np.unique(hole_labels[0])) | set(np.unique(hole_labels[-1]))
    outside |= set(np.unique(hole_labels[:, 0])) | set(np.unique(hole_labels[:, -1]))
    enclosed = ~np.isin(hole_labels, list(outside))
    foreground[enclosed] = 1

    component_count, component_labels, stats, _ = cv2.connectedComponentsWithStats(foreground, 8)
    keep = [label for label in range(1, component_count) if stats[label, cv2.CC_STAT_AREA] >= 8]
    foreground = np.isin(component_labels, keep).astype(np.uint8)
    alpha = np.clip(cv2.distanceTransform(foreground, cv2.DIST_L2, 3) * 130, 0, 255).astype(np.uint8)
    rgba = np.dstack((rgb, alpha))
    return Image.fromarray(rgba, "RGBA")


def _repair_land_contact(image: Image.Image) -> Image.Image:
    """Recover pale fabric in the one generated contact pose without touching ribbons."""
    rgba = np.asarray(image.convert("RGBA")).copy()
    height, width = rgba.shape[:2]
    polygon = np.array([[
        (int(width * .39), int(height * .38)),
        (int(width * .60), int(height * .38)),
        (int(width * .67), int(height * .49)),
        (int(width * .70), int(height * .70)),
        (int(width * .67), int(height * .89)),
        (int(width * .55), int(height * .93)),
        (int(width * .44), int(height * .93)),
        (int(width * .32), int(height * .88)),
        (int(width * .35), int(height * .68)),
        (int(width * .37), int(height * .49)),
    ]], dtype=np.int32)
    repair_area = np.zeros((height, width), np.uint8)
    cv2.fillPoly(repair_area, polygon, 1)

    existing = rgba[:, :, 3] >= 12
    grab_mask = np.full((height, width), cv2.GC_BGD, np.uint8)
    grab_mask[(repair_area == 1) & ~existing] = cv2.GC_PR_FGD
    grab_mask[existing] = cv2.GC_FGD
    background_model = np.zeros((1, 65), np.float64)
    foreground_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(
        cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR), grab_mask, None,
        background_model, foreground_model, 4, cv2.GC_INIT_WITH_MASK,
    )
    restored = (repair_area == 1) & np.isin(grab_mask, (cv2.GC_FGD, cv2.GC_PR_FGD))
    rgba[restored, 3] = 255
    rgba[rgba[:, :, 3] == 0, :3] = 0
    return Image.fromarray(rgba, "RGBA")


def _head_width(image: Image.Image) -> int:
    rgba = np.asarray(image.convert("RGBA"))
    alpha = rgba[:, :, 3]
    ys, _ = np.where(alpha >= 12)
    cutoff = int(ys.min() + (ys.max() - ys.min()) * 0.58)
    rgb = rgba[:, :, :3]
    dark_hair = (
        (alpha >= 12) & (np.indices(alpha.shape)[0] <= cutoff)
        & (rgb[:, :, 0] < 135) & (rgb[:, :, 1] < 105) & (rgb[:, :, 2] < 100)
    ).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(dark_hair, 8)
    if count <= 1:
        raise ValueError("cannot locate the character head")
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return int(stats[label, cv2.CC_STAT_WIDTH])


def _normalize_keyframe(image: Image.Image, target_head_width: int, anchor: str) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A"))
    points = np.argwhere(alpha >= 12)
    top, left = points.min(axis=0)
    bottom, right = points.max(axis=0) + 1
    content = rgba.crop((int(left), int(top), int(right), int(bottom)))
    scale = target_head_width / _head_width(rgba)
    size = (max(1, round(content.width * scale)), max(1, round(content.height * scale)))
    if size[0] > 500 or size[1] > 470:
        fit = min(500 / size[0], 470 / size[1])
        size = (round(size[0] * fit), round(size[1] * fit))
    content = content.resize(size, Image.Resampling.LANCZOS)
    content = content.filter(ImageFilter.UnsharpMask(radius=0.7, percent=110, threshold=2))
    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    x = (CANVAS_SIZE - size[0]) // 2
    y = 40 if anchor == "top" else BASELINE_Y - size[1]
    canvas.alpha_composite(content, (x, y))
    return canvas


def _mirror(image: Image.Image) -> Image.Image:
    return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)


def process_all(
    v2_dir: Path, v3_dir: Path, v4_dir: Path, v6_raw_dir: Path,
    v6_clean_dir: Path, output_dir: Path,
) -> dict[str, list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("*.png"):
        old.unlink()
    manifest: dict[str, list[str]] = {}
    rendered: dict[str, list[Image.Image]] = {}
    for spec in SHEETS:
        source_dir = {"v2": v2_dir, "v3": v3_dir, "v4": v4_dir}[spec.source]
        sheet = Image.open(source_dir / spec.filename).convert("RGB")
        for state, indexes in spec.states:
            extracted_frames = []
            for index in indexes:
                extracted = _remove_chroma(_slot(sheet, spec.columns, spec.rows, index))
                if spec.source in ("v3", "v4"):
                    alpha = np.asarray(extracted.getchannel("A"))
                    if np.count_nonzero(alpha[:, :4] >= 12) or np.count_nonzero(alpha[:, -4:] >= 12):
                        if state != "swing_cycle":
                            raise ValueError(f"{spec.filename} frame {index + 1} touches a horizontal cell boundary")
                        guarded = np.asarray(extracted).copy()
                        guarded[:, :12] = 0
                        guarded[:, -12:] = 0
                        extracted = Image.fromarray(guarded, "RGBA")
                extracted_frames.append(extracted)
            frames = (_normalize_group(
                extracted_frames,
                top_anchor=state.startswith("swing_"),
                preserve_components=state == "swing_idle",
            )
                      if spec.source == "v4" else [_normalize(frame) for frame in extracted_frames])
            rendered[state] = frames

    swing_target = _head_width(rendered["swing_idle"][0])
    swing_keys = [
        _normalize_keyframe(_remove_checkerboard(Image.open(v6_raw_dir / f"swing_key_{index:02}.png")),
                            swing_target, "top")
        for index in range(1, 5)
    ]
    rendered["swing_cycle"] = [swing_keys[index].copy() for index in (0, 1, 2, 3, 2, 1)]
    land_target = _head_width(rendered["idle"][0])
    land_keys = []
    for index in range(1, 4):
        extracted = _remove_checkerboard(Image.open(v6_raw_dir / f"land_key_{index:02}.png"))
        if index == 1:
            extracted = _repair_land_contact(extracted)
        land_keys.append(_normalize_keyframe(extracted, land_target, "bottom"))
    rendered["land"] = [*land_keys, rendered["idle"][0].copy()]
    v6_clean_dir.mkdir(parents=True, exist_ok=True)
    for old in v6_clean_dir.glob("*.png"):
        old.unlink()
    for index, frame in enumerate(swing_keys, 1):
        frame.save(v6_clean_dir / f"swing_key_{index:02}.png", optimize=True)
    for index, frame in enumerate(land_keys, 1):
        frame.save(v6_clean_dir / f"land_key_{index:02}.png", optimize=True)

    rendered["walk_left"] = [_mirror(frame) for frame in rendered["walk_right"]]
    rendered["climb_left"] = [_mirror(frame) for frame in rendered["climb_right"]]

    for state, frames in rendered.items():
        names: list[str] = []
        for index, frame in enumerate(frames, 1):
            name = f"{state}_{index:02d}.png"
            frame.save(output_dir / name, optimize=True)
            names.append(name)
        manifest[state] = names
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def make_contact_sheet(sprite_dir: Path, manifest: dict[str, list[str]], destination: Path) -> None:
    states = list(manifest)
    cell, label = 150, 24
    columns = max(len(manifest[state]) for state in states)
    sheet = Image.new("RGB", (columns * cell, len(states) * (cell + label)), "#eaf3f8")
    draw = ImageDraw.Draw(sheet)
    for row, state in enumerate(states):
        draw.text((6, row * (cell + label) + 4), state, fill="#23485d")
        for column, name in enumerate(manifest[state]):
            image = Image.open(sprite_dir / name).convert("RGBA")
            image.thumbnail((cell - 4, cell - 4), Image.Resampling.LANCZOS)
            bg = Image.new("RGBA", (cell, cell), (234, 243, 248, 255))
            bg.alpha_composite(image, ((cell - image.width) // 2, (cell - image.height) // 2))
            sheet.paste(bg.convert("RGB"), (column * cell, row * (cell + label) + label))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, quality=92)


def make_alpha_qa(sprite_dir: Path, manifest: dict[str, list[str]], destination: Path) -> None:
    names = [name for values in manifest.values() for name in values]
    cell, columns = 120, 8
    rows = (len(names) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell, rows * cell), (22, 22, 25))
    for index, name in enumerate(names):
        image = Image.open(sprite_dir / name).convert("RGBA")
        image.thumbnail((cell, cell), Image.Resampling.LANCZOS)
        bg_color = (255, 0, 170, 255) if index % 2 else (25, 25, 28, 255)
        bg = Image.new("RGBA", (cell, cell), bg_color)
        bg.alpha_composite(image, ((cell - image.width) // 2, (cell - image.height) // 2))
        sheet.paste(bg.convert("RGB"), ((index % columns) * cell, (index // columns) * cell))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination)


def make_gifs(sprite_dir: Path, manifest: dict[str, list[str]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("*.gif"):
        old.unlink()
    for state, names in manifest.items():
        frames = []
        for name in names:
            sprite = Image.open(sprite_dir / name).convert("RGBA")
            bg = Image.new("RGBA", sprite.size, (239, 247, 251, 255))
            bg.alpha_composite(sprite)
            frames.append(bg.convert("RGB").resize((256, 256), Image.Resampling.LANCZOS))
        frames[0].save(
            output_dir / f"{state}.gif", save_all=True, append_images=frames[1:],
            duration=150 if "walk" in state or "climb" in state else 240, loop=0,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    sprite_dir = root / "assets" / "sprites_v2"
    manifest = process_all(
        root / "assets" / "source_sheets_v2",
        root / "assets" / "source_sheets_v3",
        root / "assets" / "source_sheets_v4",
        root / "assets" / "source_frames_v6_raw",
        root / "assets" / "source_frames_v6",
        sprite_dir,
    )
    make_contact_sheet(sprite_dir, manifest, root / "assets" / "动作总览_v2.jpg")
    make_alpha_qa(sprite_dir, manifest, root / "assets" / "透明检查_v2.png")
    make_gifs(sprite_dir, manifest, root / "assets" / "previews_v2")
    print(f"processed {sum(map(len, manifest.values()))} sprites in {len(manifest)} states")


if __name__ == "__main__":
    main()
