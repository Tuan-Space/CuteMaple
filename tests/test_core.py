from __future__ import annotations

import json
import random

from pet_core import (ANIMATIONS, DIALOGUES, HAPPY_DIALOGUES, PetSettings,
                      choose_dialogue, choose_happy_dialogue, load_settings, save_settings)


def test_default_settings_when_missing(tmp_path):
    settings = load_settings(tmp_path / "missing.json")
    assert settings == PetSettings()


def test_corrupt_settings_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("not-json", encoding="utf-8")
    assert load_settings(path) == PetSettings()


def test_invalid_values_are_sanitized(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"paused": "yes", "scale": 99, "autostart": 1, "last_x": "4"}), encoding="utf-8")
    assert load_settings(path) == PetSettings()


def test_settings_round_trip(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    expected = PetSettings(paused=True, scale=1.2, autostart=False, last_x=-300, last_y=45)
    save_settings(expected, path)
    assert load_settings(path) == expected


def test_dialogue_catalog_and_selection():
    assert len(DIALOGUES) == 30
    assert len(HAPPY_DIALOGUES) == 15
    assert choose_dialogue(random.Random(7)) in DIALOGUES
    assert choose_happy_dialogue(random.Random(7)) in HAPPY_DIALOGUES


def test_transition_animation_metadata():
    assert ANIMATIONS["fall_float"].playback == "loop"
    assert ANIMATIONS["land"].playback == "one_shot"
    assert ANIMATIONS["swing_cycle"].playback == "counted_loop"
    assert ANIMATIONS["swing_cycle"].cycles == 3
    assert ANIMATIONS["happy"].playback == "counted_loop"
    assert ANIMATIONS["happy"].cycles == 2
    assert ANIMATIONS["petting"].playback == "counted_loop"
    assert ANIMATIONS["petting"].cycles == 2
    assert "peek_left" not in ANIMATIONS
