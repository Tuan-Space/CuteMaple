from pathlib import Path

from PIL import Image
import numpy as np
import cv2

from pet_core import SPRITE_GROUPS


ROOT = Path(__file__).resolve().parents[1]


def test_all_declared_sprites_are_valid_rgba_images():
    names = {name for values in SPRITE_GROUPS.values() for name in values}
    for name in names:
        image = Image.open(ROOT / "assets" / "sprites_v2" / name)
        assert image.size == (512, 512)
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema() == (0, 255)
        assert image.getbbox() is not None
        alpha = np.asarray(image.getchannel("A"))
        ys, xs = np.where(alpha >= 12)
        min_height = 380 if name.startswith("land_") else 420
        assert min_height <= int(ys.max() - ys.min() + 1) <= 470
        count, _, stats, _ = cv2.connectedComponentsWithStats((alpha >= 12).astype(np.uint8), 8)
        component_areas = stats[1:, cv2.CC_STAT_AREA]
        assert int(component_areas.max()) > 40_000
        # 清理动作的拂尘穗和长丝带可能是合法的独立前景组件。
        limit = 5 if name.startswith("clean_") else 3
        assert sum(int(area) >= 20 for area in component_areas) <= limit

        rgb = np.asarray(image.convert("RGB"))
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        residual_key = (
            (alpha >= 240)
            & (hsv[:, :, 0] >= 140)
            & (hsv[:, :, 0] <= 179)
            & (hsv[:, :, 1] >= 150)
        )
        key_count, _, key_stats, _ = cv2.connectedComponentsWithStats(residual_key.astype(np.uint8), 8)
        largest_key_patch = int(key_stats[1:, cv2.CC_STAT_AREA].max()) if key_count > 1 else 0
        assert largest_key_patch < 300


def test_sprite_manifest_matches_declared_states():
    import json

    manifest = json.loads((ROOT / "assets" / "sprites_v2" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == SPRITE_GROUPS


def test_icon_files_exist():
    icon_path = ROOT / "assets" / "icon.png"
    assert icon_path.is_file()
    assert (ROOT / "assets" / "美腻枫.ico").is_file()
    icon = Image.open(icon_path).convert("RGBA")
    left, top, right, bottom = icon.getchannel("A").getbbox()
    assert right - left >= 225
    assert bottom - top >= 225


def test_multiframe_swing_and_landing_geometry():
    from tools.process_sprites import _head_width

    def bounds(name):
        alpha = np.asarray(Image.open(ROOT / "assets" / "sprites_v2" / name).getchannel("A"))
        ys, xs = np.where(alpha >= 12)
        return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

    swing = [bounds(f"swing_cycle_{index:02}.png") for index in range(1, 7)]
    swing_centres = [(left + right) / 2 for left, _, right, _ in swing]
    assert max(swing_centres) - min(swing_centres) <= 1
    assert len({top for _, top, _, _ in swing}) == 1
    swing_heads = [
        _head_width(Image.open(ROOT / "assets" / "sprites_v2" / f"swing_cycle_{index:02}.png"))
        for index in range(1, 7)
    ]
    assert (max(swing_heads) - min(swing_heads)) / min(swing_heads) <= .03

    landing = [bounds(f"land_{index:02}.png") for index in range(1, 5)]
    assert len({bottom for _, _, _, bottom in landing}) == 1
    landing_heads = [
        _head_width(Image.open(ROOT / "assets" / "sprites_v2" / f"land_{index:02}.png"))
        for index in range(1, 5)
    ]
    assert (max(landing_heads) - min(landing_heads)) / min(landing_heads) <= .03
    land_last = np.asarray(Image.open(ROOT / "assets" / "sprites_v2" / "land_04.png"))
    idle_master = np.asarray(Image.open(ROOT / "assets" / "sprites_v2" / "idle_01.png"))
    assert np.array_equal(land_last, idle_master)
