from __future__ import annotations

import math

from pet_reactions import AudioActivityGate, ReactionController


def test_explicit_actions_win_then_typing_then_audio():
    controller = ReactionController()
    controller.set_audio(True, 10)
    controller.note_keyboard(10)
    assert controller.update(10.2, blocked=True) is None
    assert controller.update(12.99) == "keyboard"
    assert controller.update(13) == "audio"
    controller.set_audio(False, 13)
    assert controller.update(13) is None


def test_typing_extends_without_queuing_or_retaining_key_data():
    controller = ReactionController()
    for now in (1, 1.2, 2, 3):
        controller.note_keyboard(now)
    assert controller.update(5.9) == "keyboard"
    assert controller.update(6) is None
    assert not hasattr(controller, "keys")


def test_disabled_and_cleared_activity_does_not_resume_later():
    controller = ReactionController()
    controller.note_keyboard(1)
    controller.set_audio(True)
    controller.set_enabled(False, False)
    controller.note_keyboard(2)
    controller.set_audio(True)
    controller.set_enabled(True, True)
    assert controller.update(2) is None
    controller.note_keyboard(3)
    controller.set_audio(True)
    controller.clear()
    assert controller.update(3) is None


def test_audio_threshold_and_silence_window():
    gate = AudioActivityGate()
    assert not gate.sample(0.014, 1)
    assert gate.sample(0.015, 1.1)
    assert gate.sample(0, 1.59)
    assert not gate.sample(0, 1.6)
    assert gate.sample(0.8, 2)
    gate.reset()
    assert not gate.sample(0, 2.1)


def test_invalid_provider_values_never_stick_active():
    gate = AudioActivityGate()
    gate.sample(1, 1)
    assert not gate.sample(math.nan, 1.1)
    assert not gate.sample(0, 1.2)
    controller = ReactionController()
    controller.note_keyboard(math.nan)
    assert controller.update(1) is None


def test_keyboard_and_audio_have_independent_visible_effects():
    controller = ReactionController()
    controller.note_keyboard(10); controller.set_audio(True)
    assert controller.update(11) == 'keyboard'
    assert controller.active_effects(11) == ('keyboard', 'audio')
    assert controller.active_effects(11, blocked=True) == ()
    assert controller.active_effects(14) == ('audio',)
    controller.clear()
    assert controller.active_effects(14) == ()
