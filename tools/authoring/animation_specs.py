"""Scene timing used only when editing the full Cubism project."""
from dataclasses import dataclass

def _frames(state: str, count: int) -> list[str]:
    return [f"{state}_{index:02d}.png" for index in range(1, count + 1)]


@dataclass(frozen=True)
class AnimationSpec:
    frame_count: int
    delays_ms: tuple[int, ...]
    playback: str = "loop"
    cycles: int = 0
    contact_margin: float = 0.0
    contact_anchors: tuple[int, ...] = ()


ANIMATIONS = {
    "idle": AnimationSpec(6, (650, 650, 650, 160, 160, 650)),
    "walk_right": AnimationSpec(8, (125,) * 8),
    "walk_left": AnimationSpec(8, (125,) * 8),
    "drag_left": AnimationSpec(3, (180,) * 3),
    "drag_right": AnimationSpec(3, (180,) * 3),
    "happy": AnimationSpec(6, (180, 240, 350, 320, 250, 260), "one_shot"),
    "talk": AnimationSpec(4, (220,) * 4),
    "petting": AnimationSpec(4, (240,) * 4, "counted_loop", cycles=2),
    "fall_float": AnimationSpec(3, (600,) * 3),
    "land": AnimationSpec(4, (180, 240, 420, 420), "one_shot"),
    "climb_right": AnimationSpec(6, (162, 169, 169, 162, 169, 169), contact_margin=0.18,
                                  contact_anchors=(408, 424, 398, 421, 415, 418)),
    "climb_left": AnimationSpec(6, (162, 169, 169, 162, 169, 169), contact_margin=0.18,
                                 contact_anchors=(103, 87, 113, 90, 96, 93)),
    "swing_cycle": AnimationSpec(6, (170,) * 6, "counted_loop", cycles=3,
                                  contact_margin=40 / 512),
    "swing_idle": AnimationSpec(4, (1200, 1300, 170, 1200), contact_margin=40 / 512),
    "sleep_enter": AnimationSpec(4, (260, 300, 360, 420), "one_shot"),
    "sleep_loop": AnimationSpec(4, (700, 850, 700, 850)),
    "sleep_exit": AnimationSpec(4, (300, 260, 230, 260), "one_shot"),
    "clean_ground": AnimationSpec(6, (180,) * 6),
    "clean_climb_left": AnimationSpec(4, (200,) * 4),
    "clean_climb_right": AnimationSpec(4, (200,) * 4),
    "clean_top": AnimationSpec(4, (200,) * 4),
}

