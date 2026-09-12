"""Time-based, renderer-independent arbitration for ambient pet reactions.

The host owns explicit actions (dragging, physics, cleanup, sleep and pause).
Passing ``blocked=True`` always gives those actions priority. Input providers
retain only aggregate activity; this module never accepts keys or audio data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ReactionController:
    keyboard_seconds: float = 3.0
    keyboard_enabled: bool = True
    audio_enabled: bool = True
    _keyboard_until: float = field(default=-math.inf, init=False)
    _audio_active: bool = field(default=False, init=False)

    def note_keyboard(self, now: float) -> None:
        if self.keyboard_enabled and math.isfinite(now):
            self._keyboard_until = now + self.keyboard_seconds

    def set_audio(self, active: bool, now: float | None = None) -> None:
        """Accept the provider's debounced state; ``now`` is adapter-friendly."""
        self._audio_active = bool(active) and self.audio_enabled

    def set_enabled(self, keyboard: bool, audio: bool) -> None:
        self.keyboard_enabled = bool(keyboard)
        self.audio_enabled = bool(audio)
        if not self.keyboard_enabled:
            self._keyboard_until = -math.inf
        if not self.audio_enabled:
            self._audio_active = False

    def clear(self) -> None:
        self._keyboard_until = -math.inf
        self._audio_active = False

    def update(self, now: float, blocked: bool = False) -> str | None:
        if blocked or not math.isfinite(now):
            return None
        if self.keyboard_enabled and now < self._keyboard_until:
            return "keyboard"
        if self.audio_enabled and self._audio_active:
            return "audio"
        return None

    def active_effects(self, now: float, blocked: bool = False) -> tuple[str, ...]:
        """Independent visual channels; typing may own the pose while sound stays visible."""
        if blocked or not math.isfinite(now):
            return ()
        return tuple(name for name, active in (("keyboard", self.keyboard_enabled and now < self._keyboard_until),
                                               ("audio", self.audio_enabled and self._audio_active)) if active)


@dataclass
class AudioActivityGate:
    """Turn scalar output peaks into activity, with a short silence hold."""

    threshold: float = 0.015
    silence_seconds: float = 0.5
    _last_peak_time: float = field(default=-math.inf, init=False)

    def sample(self, peak: float, now: float) -> bool:
        if not math.isfinite(now) or not math.isfinite(peak):
            self.reset()
            return False
        if peak >= self.threshold:
            self._last_peak_time = now
        return now - self._last_peak_time < self.silence_seconds

    def reset(self) -> None:
        self._last_peak_time = -math.inf
