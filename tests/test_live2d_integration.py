import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MEINIFENG_DISABLE_CLEAN_TASK", "1")

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QWidget

import pet_app
from live2d_host import PetBridge, resolve_asset
from pet_core import ANIMATIONS, PetSettings
from pet_reactions import ReactionController


class FakeHost:
    def __init__(self, pet):
        self.ready = True
        self.generation = 4
        self.view = QWidget(pet)
        self.messages = []

    def presented(self, token):
        pass

    def send(self, kind, **values):
        self.messages.append({"type": kind, **values})

    def stop(self):
        self.ready = False


@pytest.fixture
def pet(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(pet_app, "save_settings", lambda *_: None)
    window = pet_app.PetWindow(app, PetSettings(autostart=False))
    window.show()
    app.processEvents()
    activate(window)
    yield window
    for timer in window.findChildren(pet_app.QTimer):
        timer.stop()
    for widget in (window.bubble, window.monitor_button, window.monitor_capsule, window.details_panel):
        widget.close()
    window.tray.hide()
    window._install_executor.shutdown(wait=False, cancel_futures=True)
    window.close()


def activate(pet, states=None):
    pet.live2d_host = FakeHost(pet)
    pet._frame_has_pixels=lambda:True
    from test_live2d_only import META, frame
    pet._renderer_generation = pet.live2d_host.generation
    pet._on_renderer_event({"generation": pet._renderer_generation, "type": "ready", "backend": "live2d", "states": states or [*ANIMATIONS,*pet_app.TOP_TRANSITIONS],
                           "locomotion":META['locomotion'],"capabilities":[]})
    frame(pet)
    return pet.live2d_host


def activate_motion_polish(pet):
    host=activate(pet)
    pet._climb_spec={"cycleDuration":1.,"risePerCycle":.12,"phaseTravel":[(0,0),(1,1)]}
    pet._motion_polish_supported=True
    pet._climb_endpoint_supported=True
    return host


def test_motion_polish_petting_uses_live_head_and_keeps_seated_motion(pet, monkeypatch):
    host = activate_motion_polish(pet)
    pet._attach_top()
    clock = [100.]
    monkeypatch.setattr(pet_app.time, "monotonic", lambda: clock[0])
    pet._renderer_geometry = {"headBounds": [.4, .15, .4, .25]}
    before = pet.state, pet._renderer_token, pet.animation_cycles
    plays = len([row for row in host.messages if row['type'] == 'play'])
    # The old top-of-window rectangle includes this unrelated empty region.
    for x in (10, 80, 10, 80):
        pet._track_petting(QPoint(x, 30), Qt.NoButton)
        clock[0] += .1
    assert pet.last_petting_at == 0
    for x in (95, 170, 95, 170):
        pet._track_petting(QPoint(x, 50), Qt.NoButton)
        clock[0] += .1
    assert pet._explicit_effect == 'swing_petting_right'
    assert pet._explicit_effect_until == pytest.approx(pet.last_petting_at + 3)
    assert (pet.state, pet._renderer_token, pet.animation_cycles) == before
    assert len([row for row in host.messages if row['type'] == 'play']) == plays
    assert any(row['type'] == 'expression' and row['name'] == 'swing_petting_right'
               and row['active'] and row['token'] == before[1] for row in host.messages)
    previous = pet.last_petting_at
    for x in (170, 95, 170, 95):
        pet._track_petting(QPoint(x, 50), Qt.NoButton)
        clock[0] += .1
    assert pet.last_petting_at == previous


@pytest.mark.parametrize('interrupt', ['pause', 'hide', 'play', 'error'])
def test_motion_polish_petting_strong_interrupt_clears_trace_and_expression(pet, interrupt):
    host = activate_motion_polish(pet); pet._attach_top()
    pet._start_temporary('petting')
    pet.pet_trace = [(100., 90)]
    token = pet._renderer_token
    if interrupt == 'pause':
        pet.settings.paused = True; pet._sync_runtime_pause()
    elif interrupt == 'hide':
        pet.hide_pet()
    elif interrupt == 'play':
        pet.start_state('swing_idle')
    else:
        pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'error', 'message': 'test interruption'})
    assert not pet.pet_trace and pet._explicit_effect is None
    assert 'swing_petting_right' not in pet._requested_expressions
    assert any(row['type'] == 'expression' and row['name'] == 'swing_petting_right'
               and not row['active'] and row['token'] == token for row in host.messages)


def test_motion_polish_climb_duration_and_world_travel_follow_metadata(pet):
    host = activate_motion_polish(pet)
    pet._attach_side('right')
    assert ANIMATIONS['climb_right'].duration_ms == ANIMATIONS['climb_left'].duration_ms == 1000
    assert [row for row in host.messages if row['type'] == 'play'][-1]['durationMs'] == 1000
    pet._motion_y = float(pet.y()); before = pet._motion_y
    pet._advance_native_climb({'climbPhase': .25, 'climbCycle': 0})
    assert pet._motion_y == pytest.approx(before - .25*.12*pet.height())
    # An old model keeps its own slower metadata; no host timer scales native phase a second time.
    pet._climb_spec['cycleDuration'] = 1.6
    pet.start_state('climb_right')
    assert [row for row in host.messages if row['type'] == 'play'][-1]['durationMs'] == 1600


class DragPointer:
    """Exercise the host's actual press/move path without dispatching OS input."""
    def __init__(self, x, y): self.point = QPointF(x, y)
    def button(self): return Qt.LeftButton
    def buttons(self): return Qt.LeftButton
    def globalPosition(self): return self.point
    def accept(self): pass


@pytest.mark.parametrize('horizontal', [0, 1, -1])
@pytest.mark.parametrize('origin', ['petting', 'top_with_cleanup', 'ground_with_cleanup'])
def test_first_vertical_or_tiny_horizontal_drag_revokes_supported_token_and_late_callbacks(pet, origin, horizontal):
    host = activate_motion_polish(pet)
    if origin != 'ground_with_cleanup': pet._attach_top(run_intro=False)
    if origin == 'petting': pet._start_temporary('petting')
    else: pet._begin_cleanup_operation('a'*32)
    old_token, old_name = pet._renderer_token, pet._render_state_name()
    pet.mousePressEvent(DragPointer(400, 300))
    host.messages.clear()
    pet.mouseMoveEvent(DragPointer(400+horizontal, 320))
    expected = 'drag_left' if horizontal < 0 else 'drag_right'
    assert pet.dragging and pet.base_mode == 'ground' and pet.state == expected
    assert pet._renderer_token == old_token+1 and not hasattr(pet,'_cleanup_segment')
    assert pet._explicit_effect is None and not any(name.startswith('swing_petting') for name in pet._requested_expressions)
    if origin == 'petting':
        assert any(row['type']=='expression' and row['name']=='swing_petting_right' and not row['active']
                   and row['token']==old_token for row in host.messages)
    else: assert pet.cleanup_operation == 'a'*32
    token, messages = pet._renderer_token, len(host.messages)
    # The old callback is not stale merely by elapsed time; the new invocation
    # has to invalidate it before any horizontal movement occurs.
    for kind in ('finished', 'cycle', 'marker'):
        pet._on_renderer_event({"generation": pet._renderer_generation, 'type': kind, 'name': old_name, 'token': old_token, 'cycle': 3, 'marker': 'clean_sweep'})
    assert (pet.state, pet._renderer_token, len(host.messages)) == (expected, token, messages)
    for step in range(1, 5): pet.mouseMoveEvent(DragPointer(400+horizontal*(step+1), 320+step*8))
    assert pet._renderer_token == token, 'small per-frame horizontal changes must not restart the drag'
    before = len([row for row in host.messages if row['type']=='play'])
    pet.mouseMoveEvent(DragPointer(380, 370))
    assert pet.state == 'drag_left'
    after = len([row for row in host.messages if row['type']=='play'])
    assert after == before + (expected != 'drag_left')
    pet.mouseMoveEvent(DragPointer(374, 376))
    assert len([row for row in host.messages if row['type']=='play']) == after


def test_paused_vertical_detach_samples_drag_then_edge_pose_without_advancing_motion(pet):
    host = activate_motion_polish(pet); pet._attach_top(run_intro=False)
    pet._start_temporary('petting'); pet.toggle_pause()
    pet.move(pet._screen_area().center().x(), pet._screen_area().top()+180)
    old = pet._renderer_token
    pet.mousePressEvent(DragPointer(400, 300)); host.messages.clear()
    pet.mouseMoveEvent(DragPointer(400, 324))
    plays = [(i, row) for i, row in enumerate(host.messages) if row['type']=='play']
    assert len(plays)==1 and plays[0][1]['name']=='drag_right' and pet._renderer_token==old+1
    assert host.messages[plays[0][0]-1] == {'type':'pause', 'paused':True}
    assert pet.settings.paused and not pet.motion_timer.isActive()
    pet.move(pet._screen_area().left(), pet._screen_area().top()+180)
    position = pet.pos(); pet._preview_paused_drag()
    assert pet.state=='climb_left' and pet.pos()==position
    last = next(i for i in range(len(host.messages)-1, -1, -1) if host.messages[i]['type']=='play')
    assert host.messages[last-1] == {'type':'pause', 'paused':True}


def event(pet, kind, **values):
    pet._on_renderer_event({"generation": pet._renderer_generation, "type": kind, "token": pet._renderer_token, "name": pet.state, **values})


def test_new_defaults_settings_compatibility_and_menu(pet):
    settings = PetSettings.from_mapping({"scale": 1.2, "paused": True, "keyboard_enabled": "false"})
    assert settings.scale == 1.2 and settings.paused
    assert settings.gaze_enabled and settings.keyboard_enabled and settings.audio_enabled and settings.particles_enabled
    assert not settings.roaming_enabled
    assert not pet.behavior_timer.isActive()
    menu = pet._create_context_menu()
    action = menu.findChild(QAction, "roaming_enabled")
    action.setChecked(True)
    assert pet.settings.roaming_enabled and pet.behavior_timer.isActive()
    action.setChecked(False)
    assert not pet.settings.roaming_enabled and not pet.behavior_timer.isActive()
    menu.deleteLater()


def test_live_motion_completion_ignores_duplicate_and_interrupted_events(pet):
    activate(pet)
    pet._start_temporary("happy")
    old_token = pet._renderer_token
    assert pet._live2d_active and not hasattr(pet, 'animation_timer')
    pet._on_renderer_event({"generation": pet._renderer_generation, "type": "cycle", "name": "happy", "token": old_token - 1, "cycle": 2})
    assert pet.state == "happy" and pet.animation_cycles == 0
    event(pet, "cycle", cycle=1)
    event(pet, "cycle", cycle=1)
    assert pet.state == "happy" and pet.animation_cycles == 1
    event(pet, "finished")  # A single response settles on its native completion.
    assert pet.state == "idle" and pet._renderer_token != old_token
    pet._on_renderer_event({"generation": pet._renderer_generation, "type": "finished", "name": "happy", "token": old_token})
    pet._on_renderer_event({"generation": pet._renderer_generation, "type": "cycle", "name": "happy", "token": old_token, "cycle": 3})
    assert pet.state == "idle"


def test_sleep_follows_native_completion(pet):
    activate(pet)
    pet.sleep_phase = "enter"
    pet.start_state("sleep_enter")
    event(pet, "finished")
    assert pet.sleep_phase == "loop" and pet.state == "sleep_loop"


def test_errors_never_restore_a_legacy_renderer(pet):
    activate(pet, ["idle"])
    assert not pet._live2d_active and pet.loading_panel.isVisible()
    assert pet.retry_button.isVisible() and not hasattr(pet, 'sprite')
    activate(pet)
    event(pet, 'error', message='Missing Maple.moc3')
    assert not pet._live2d_active and 'Missing Maple.moc3' in pet.renderer_status
    sent=len(pet.live2d_host.messages)
    for _ in range(100):pet._update_runtime()
    assert len(pet.live2d_host.messages)==sent


def test_live_pause_hide_and_resume_keep_motion_token(pet):
    host = activate(pet)
    pet.start_state("swing_cycle")
    token = pet._renderer_token
    pet.toggle_pause()
    event(pet, "cycle", cycle=3)
    assert pet.state == "swing_cycle" and pet._renderer_token == token
    assert {"type": "pause", "paused": True} in host.messages
    pet.toggle_pause()
    assert pet._renderer_token == token
    pet.hide_pet()
    assert pet._animation_paused()
    event(pet, "cycle", cycle=3)
    assert pet.state == "swing_cycle"
    pet.show_pet()
    event(pet, "cycle", cycle=3)
    assert pet.state == "swing_idle"


def test_native_close_suspends_providers_and_generic_show_resumes(pet):
    class Provider:
        def __init__(self):
            self.suspended = False

        def set_suspended(self, value):
            self.suspended = value

        def stop(self):
            self.suspended = True

    provider = pet.desktop_activity = Provider()
    pet.monitor_button.show()
    pet.close()  # Alt+F4 closes the frameless QWidget through this path.
    assert provider.suspended and not pet.monitor_button.isVisible()
    assert not hasattr(pet, 'animation_timer')
    pet.show()
    assert not provider.suspended and not hasattr(pet,'animation_timer')


def test_sustained_gaze_and_typing_use_last_click(pet, monkeypatch):
    host = activate(pet)
    pet.reactions = ReactionController()
    clock = [100.0]
    monkeypatch.setattr(pet_app.time, "monotonic", lambda: clock[0])
    cursor = pet.mapToGlobal(QPoint(pet.width() * 3, 20))
    monkeypatch.setattr(pet_app.QCursor, "pos", lambda: cursor)
    pet._update_runtime()
    assert host.messages[-1]["type"] == "gaze" and host.messages[-1]["x"] > 0
    clock[0] += 10
    pet._update_runtime()
    assert host.messages[-1]["enabled"]  # sustained gaze does not copy NNPet's 5s idle reset
    clicked = pet.mapToGlobal(QPoint(-pet.width() * 3, 20))
    pet._desktop_click(clicked.x(), clicked.y())
    pet._keyboard_activity()
    pet._update_runtime()
    assert next(message for message in reversed(host.messages) if message["type"] == "gaze")["x"] < 0
    pet.toggle_interaction("gaze_enabled", False)
    assert host.messages[-1] == {"type": "gaze", "x": 0.0, "y": 0.0, "enabled": False}


def test_live_geometry_positions_overlays_and_contact(pet):
    activate(pet)
    event(pet, "geometry", bounds=[.1, .1, .8, .8], anchors={"head": [.5, .3], "ground": [.5, .9]})
    assert pet._content_rect_global() == QRect(22, 22, 176, 176).translated(pet.pos())
    assert pet._floor_y(pet.current_screen.availableGeometry()) == pet.current_screen.availableGeometry().bottom() + 1 - 198
    pet._attach_side("left")
    event(pet, "geometry", bounds=[.1, .1, .8, .8], anchors={"left": [.2, .4]})
    assert pet.x() + 44 == pet.current_screen.availableGeometry().left()


def test_falling_distance_is_time_based(pet, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(pet_app.time, "monotonic", lambda: clock[0])
    pet.move(pet.x(), pet.current_screen.availableGeometry().top() + 50)
    pet.motion_mode = "fall"
    pet._motion_y = float(pet.y())
    pet._motion_updated_at = clock[0]
    origin = pet.y()
    for _ in range(10):
        clock[0] += .016
        pet._motion_step()
    assert 7 <= pet.y() - origin <= 10  # Starts gently rather than jumping to terminal speed.
    assert pet._fall_velocity > 90


def test_double_click_response_does_not_chase_animated_ground_bounds(pet):
    activate(pet)
    event(pet, 'geometry', bounds=[.1, .1, .8, .8], anchors={'ground': [.5, .9]})
    origin = pet.pos()
    pet._start_temporary('happy')
    for ground_y in (.902, .896, .904, .898, .9):
        event(pet, 'geometry', bounds=[.1, .1, .8, ground_y-.1], anchors={'ground': [.5, ground_y]})
        assert pet.pos() == origin
    event(pet, 'finished')
    assert pet.state == 'idle'
    event(pet, 'geometry', bounds=[.1, .1, .8, .8], anchors={'ground': [.5, .9]})
    assert pet.pos() == origin






@pytest.mark.parametrize('name', ['dust', 'clean_dust'])
def test_removed_dust_preview_never_emits_even_with_archived_brush_anchor(pet, monkeypatch, name):
    host = activate_motion_polish(pet)
    warnings = []
    monkeypatch.setattr(pet.bubble, 'show_message', lambda *args: warnings.append(args[0]))
    pet._renderer_geometry = {'anchors': {'head': [.5, .2]}}
    pet._preview_effect(name)
    assert warnings == []
    assert not any(row['type'] == 'effect' for row in host.messages)
    pet._renderer_geometry['anchors']['brushTip'] = [.8, .65]
    pet._preview_effect(name)
    assert warnings == []
    assert not any(row['type'] == 'effect' for row in host.messages)


@pytest.mark.parametrize("motion", ["climb", "fall"])
def test_resizing_during_vertical_motion_keeps_new_contact_position(pet, monkeypatch, motion):
    clock = [100.0]
    monkeypatch.setattr(pet_app.time, "monotonic", lambda: clock[0])
    area = pet.current_screen.availableGeometry()
    pet.move(area.center().x(), area.top() + 250)
    if motion == "climb":
        pet._attach_side("left"); pet._climb_cadence.tick(30)
    else:
        pet.motion_mode = "fall"
        pet._motion_y = float(pet.y())
    pet.set_scale(1.5)
    origin = pet.y()
    clock[0] += .016
    pet._motion_step()
    expected = 0  # A host timer alone cannot advance the native climbing phase.
    assert abs(pet.y() - origin - expected) <= 1


def test_live_side_position_uses_foot_contact_when_near_floor(pet):
    activate(pet)
    area = pet.current_screen.availableGeometry()
    event(pet, "geometry", bounds=[.1, .1, .8, .8], anchors={"ground": [.5, .9], "left": [.2, .4]})
    pet.move(pet.x(), pet._floor_y(area))
    pet._attach_side("left")
    event(pet, "geometry", bounds=[.1, .1, .8, .8], anchors={"ground": [.5, .9], "left": [.2, .4]})
    assert pet.y() == pet._floor_y(area)




def test_custom_scheme_cannot_read_application_or_parent_files(tmp_path):
    allowed = tmp_path / "web" / "dist" / "index.html"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("ok")
    private = tmp_path / "settings.json"
    private.write_text("private")
    assert resolve_asset(tmp_path, "/web/dist/index.html") == allowed
    assert resolve_asset(tmp_path, "/web/dist/%2e%2e/%2e%2e/settings.json") is None
    assert resolve_asset(tmp_path, "/settings.json") is None
    assert resolve_asset(tmp_path, "/assets/live2d/missing.moc3") is None


def test_bridge_accepts_only_structured_known_reports():
    bridge = PetBridge()
    seen = []
    bridge.event.connect(seen.append)
    for raw in ("garbage", "[]", '{"type":"execute"}', "x" * 65537):
        bridge.report(raw)
    assert seen == []
    bridge.report(json.dumps({"type": "error", "message": "missing model"}))
    assert seen == [{"type": "error", "message": "missing model"}]


def test_top_transition_uses_native_markers_and_rejects_interrupted_completion(pet):
    host = activate(pet, list(ANIMATIONS) + ['climb_to_top_left', 'climb_to_top_right'])
    pet._attach_side('left')
    pet._renderer_geometry = {'anchors': {'left': [.2, .4], 'gripLeft': [.2, .4], 'hang': [.5, 0]}}
    pet._begin_top_transition()
    token = pet._renderer_token
    assert pet.state == 'climb_left' and pet._render_state_name() == 'climb_to_top_left'
    assert any(m.get('name') == 'climb_to_top_left' and m.get('playback') == 'one_shot' for m in host.messages)
    start = pet.pos()
    payload = {'token': token, 'name': 'climb_to_top_left', 'bounds': [0, 0, 1, 1],
               'anchors': {'gripLeft': [.2, .4], 'hang': [.5, 0]}, 'transitionProgress': 0}
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'geometry', **payload})
    assert pet.pos() == start
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'marker', 'token': token, 'name': 'climb_to_top_left', 'marker': 'top_grab'})
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'geometry', **payload, 'transitionProgress': 1})
    assert pet.y() == pet._screen_area().top()
    pet.start_state('drag_left')
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'finished', 'token': token, 'name': 'climb_to_top_left', 'cycle': 1})
    assert pet.state == 'drag_left' and pet._top_transition is None


def test_native_top_transition_finishes_into_original_swing_behavior(pet):
    activate(pet, list(ANIMATIONS) + ['climb_to_top_right'])
    pet._attach_side('right')
    pet._renderer_geometry = {'anchors': {'right': [.8, .4], 'hang': [.5, 0]}}
    pet._begin_top_transition()
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'finished', 'token': pet._renderer_token,
                            'name': 'climb_to_top_right', 'cycle': 1})
    assert pet.state == 'swing_cycle' and pet.base_mode == 'top_swing'
    assert pet._top_transition is None


def test_removed_screen_is_never_dereferenced_during_reposition(pet):
    class DeletedScreen:
        def availableGeometry(self):
            raise AssertionError('deleted native screen accessed')
    removed = DeletedScreen()
    pet.current_screen = removed
    pet._screen_removed(removed)
    assert pet.current_screen is not removed
    assert pet._screen_area().width() > 0




def test_autostart_failure_never_persists_success(pet, monkeypatch):
    pet.settings.autostart = False
    monkeypatch.setattr(pet_app, 'set_autostart', lambda *_: False)
    messages = []
    monkeypatch.setattr(pet, 'say', messages.append)
    pet.toggle_autostart(True)
    assert not pet.settings.autostart
    assert messages and '失败' in messages[0]


class DoubleClick:
    def button(self):
        return Qt.LeftButton

    def accept(self):
        pass


@pytest.mark.parametrize('mode', ['climb_left', 'climb_right', 'top_swing', 'transition_left', 'transition_right'])
@pytest.mark.parametrize('interaction', ['double_click', 'petting', 'talk'])
def test_attached_interactions_keep_native_pose_token_and_motion(pet, monkeypatch, mode, interaction):
    host = activate(pet, list(ANIMATIONS) + list(pet_app.TOP_TRANSITIONS))
    if mode == 'top_swing':
        pet._attach_top()
    else:
        side = mode.rsplit('_', 1)[1]
        pet._attach_side(side)
        if mode.startswith('transition'):
            pet._renderer_geometry = {'anchors': {side: [.2, .4], 'gripLeft': [.2, .4], 'gripRight': [.8, .4], 'hang': [.5, 0]}}
            pet._begin_top_transition()
    before = (pet.state, pet._renderer_token, pet.motion_mode, pet.motion_timer.isActive(), pet.animation_cycles)
    transition = pet._top_transition
    plays = sum(message['type'] == 'play' for message in host.messages)
    clock = [100.0]
    monkeypatch.setattr(pet_app.time, 'monotonic', lambda: clock[0])
    if interaction == 'double_click':
        def safe_choice(options):
            assert 'glasses' not in options and 'fan' not in options
            return options[0]
        monkeypatch.setattr(pet_app.random, 'choice', safe_choice)
        pet.mouseDoubleClickEvent(DoubleClick())
    elif interaction == 'petting':
        for x in (20, 100, 20, 100):
            pet._track_petting(QPoint(x, 20), Qt.NoButton)
            clock[0] += .1
        assert pet.last_petting_at == pytest.approx(100.3)
    else:
        pet.say('保持当前支撑姿态回应')
    assert before == (pet.state, pet._renderer_token, pet.motion_mode, pet.motion_timer.isActive(), pet.animation_cycles)
    assert pet._top_transition is transition
    assert sum(message['type'] == 'play' for message in host.messages) == plays
    assert pet._applied_expressions <= {'smile', 'blush', 'maple'} and pet._applied_expressions
    if transition:
        for marker in ('top_grab', 'wall_release', 'settled'):
            pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'marker', 'name': transition['name'], 'token': before[1], 'marker': marker})
        pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'finished', 'name': transition['name'], 'token': before[1], 'cycle': 1})
        assert pet.state == 'swing_cycle' and pet.base_mode == 'top_swing'


def test_attached_ambient_and_explicit_expression_sources_expire_independently(pet, monkeypatch):
    host = activate(pet)
    pet._attach_top(run_intro=False)
    pet.reactions = ReactionController()
    clock = [100.0]
    monkeypatch.setattr(pet_app.time, 'monotonic', lambda: clock[0])
    pet._keyboard_activity()
    pet._update_runtime()
    pet._show_effect('smile', duration_ms=1000)
    assert pet._active_reaction == 'keyboard' and pet._applied_expressions == {'smile', 'keyboard'}
    clock[0] = 102
    pet._update_runtime()
    assert pet._explicit_effect is None and pet._applied_expressions == {'keyboard'}
    assert any(item['type'] == 'expression' and item['name'] == 'keyboard' and item['active'] for item in host.messages)
    clock[0] = 104
    pet._update_runtime()
    assert not pet._applied_expressions
    pet._audio_activity(True)
    pet._update_runtime()
    assert pet._active_reaction == 'audio' and pet._applied_expressions == {'audio'}
    pet._set_ground_idle()
    assert pet._applied_expressions == {'audio'}  # Ground listening keeps its complete gesture.


@pytest.mark.parametrize('accessory', ['glasses', 'fan'])
def test_ground_accessories_and_actions_remain_available_but_clear_on_attachment(pet, monkeypatch, accessory):
    activate(pet)
    monkeypatch.setattr(pet_app.random, 'choice', lambda choices: accessory if accessory in choices else choices[0])
    pet.mouseDoubleClickEvent(DoubleClick())
    assert pet.state == 'happy' and accessory in pet._applied_expressions
    pet._attach_side('left')
    assert pet._explicit_effect is None and accessory not in pet._applied_expressions
    pet._detach_for_drag()
    pet.start_state('drag_left')
    assert pet._applied_expressions == {'dizzy'}
    pet._set_ground_idle()
    pet._start_temporary('petting')
    assert pet.state == 'petting'
    pet.say('地面完整说话动作')
    assert pet.state == 'talk'


@pytest.mark.parametrize('side', ['left', 'right', 'top'])
def test_paused_drag_samples_contact_pose_before_release_without_moving_it(pet, side):
    host = activate(pet)
    pet.toggle_pause()
    pet.dragging = True
    pet._detach_for_drag()
    area = pet._screen_area()
    x = area.left() if side == 'left' else area.right() - pet.width() if side == 'right' else area.center().x()
    y = area.top() if side == 'top' else area.top() + 180
    pet.move(x, y)
    before = pet.pos()
    pet._preview_paused_drag()
    assert pet.pos() == before
    expected = 'swing_idle' if side == 'top' else f'climb_{side}'
    assert pet.state == expected and pet.settings.paused
    commands = [m['type'] for m in host.messages]
    play_index = len(commands) - 1 - commands[::-1].index('play')
    assert commands[play_index-2:play_index] == ['resize', 'pause']
    assert host.messages[play_index-1]['paused'] is True
    pet.dragging = False
    pet._settle_after_drag()
    assert pet.state == expected and pet.settings.paused
    assert not pet.motion_timer.isActive()


def test_details_panel_keeps_typing_and_sound_detection_enabled(pet):
    host = activate(pet)
    pet.reactions = ReactionController()
    pet.open_details_panel()
    pet._keyboard_activity()
    pet._audio_activity(True)
    pet._update_runtime()
    assert pet.movement_paused and not pet.activity_paused
    assert pet._active_reaction == 'keyboard'
    assert pet._reaction_expressions == pet._applied_expressions == {'keyboard', 'audio'}
    assert {m['name'] for m in host.messages if m['type'] == 'effect'} >= {'keyboard', 'audio'}
    assert not pet._animation_paused()


def _start_both_real_reaction_signals(pet, monkeypatch):
    host = activate(pet)
    pet.reactions = ReactionController()
    clock = [100.0]
    monkeypatch.setattr(pet_app.time, 'monotonic', lambda: clock[0])
    pet._audio_activity(True)
    pet._keyboard_activity()
    pet._update_runtime()
    return host, clock


def _expression_commands(host):
    return [(row['name'], row['active']) for row in host.messages if row['type'] == 'expression']


def test_simultaneous_reactions_stay_active_without_repeated_commands_and_stop_independently(pet, monkeypatch):
    host, clock = _start_both_real_reaction_signals(pet, monkeypatch)
    assert pet._active_reaction == 'keyboard'
    assert pet._reaction_expressions == pet._applied_expressions == {'keyboard', 'audio'}
    assert _expression_commands(host) == [('audio', True), ('keyboard', True)]
    for now in (100.5, 101, 102):
        clock[0] = now
        pet._update_runtime()
    assert _expression_commands(host) == [('audio', True), ('keyboard', True)]
    pet._audio_activity(False)
    pet._update_runtime()
    assert pet._active_reaction == 'keyboard' and pet._applied_expressions == {'keyboard'}
    assert _expression_commands(host)[-1] == ('audio', False)
    pet._audio_activity(True)
    pet._update_runtime()
    clock[0] = 103.1
    pet._update_runtime()
    assert pet._active_reaction == 'audio' and pet._applied_expressions == {'audio'}
    assert _expression_commands(host)[-1] == ('keyboard', False)
    pet._audio_activity(False)
    pet._update_runtime()
    assert pet._active_reaction is None and not pet._reaction_expressions and not pet._applied_expressions


@pytest.mark.parametrize('blocked', ['drag', 'sleep', 'temporary-motion'])
def test_both_ambient_expressions_withdraw_on_blocked_pose_then_restore_current_activity(pet, monkeypatch, blocked):
    host, clock = _start_both_real_reaction_signals(pet, monkeypatch)
    if blocked == 'drag': pet.dragging = True
    elif blocked == 'sleep': pet.sleep_phase = 'loop'
    elif blocked == 'cleanup': pet.cleanup_operation = 'test-operation'
    else: pet.start_state('happy')
    pet._update_runtime()
    assert pet._active_reaction is None and not pet._reaction_expressions and not pet._applied_expressions
    assert ('audio', False) in _expression_commands(host) and ('keyboard', False) in _expression_commands(host)
    clock[0] = 101
    if blocked == 'drag': pet.dragging = False
    elif blocked == 'sleep': pet.sleep_phase = None
    elif blocked == 'cleanup': pet.cleanup_operation = None
    else: pet.start_state('idle')
    pet._update_runtime()
    assert pet._active_reaction == 'keyboard' and pet._applied_expressions == {'keyboard', 'audio'}


@pytest.mark.parametrize('suspend', ['pause', 'hide'])
def test_pause_hide_clear_both_channels_and_ignore_late_activity_until_fresh_resume(pet, monkeypatch, suspend):
    host, _ = _start_both_real_reaction_signals(pet, monkeypatch)
    if suspend == 'pause': pet.toggle_pause()
    else: pet.hide_pet()
    assert pet._active_reaction is None and not pet._reaction_expressions and not pet._applied_expressions
    pet._keyboard_activity()
    pet._audio_activity(True)
    pet._update_runtime()
    if suspend == 'pause': pet.toggle_pause()
    else: pet.show_pet()
    pet._update_runtime()
    assert not pet._reaction_expressions and not pet._applied_expressions
    pet._keyboard_activity()
    pet._audio_activity(True)
    pet._update_runtime()
    assert pet._applied_expressions == {'keyboard', 'audio'}


def test_renderer_reload_replays_current_expression_set_once_without_replaying_expired_typing(pet, monkeypatch):
    host, clock = _start_both_real_reaction_signals(pet, monkeypatch)
    host.messages.clear()
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'ready', 'backend': 'live2d', 'states': [*ANIMATIONS,*pet_app.TOP_TRANSITIONS], 'locomotion':__import__('test_live2d_only').META['locomotion']})
    assert _expression_commands(host) == [('audio', True), ('keyboard', True)]
    host.messages.clear()
    clock[0] = 104
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'ready', 'backend': 'live2d', 'states': [*ANIMATIONS,*pet_app.TOP_TRANSITIONS], 'locomotion':__import__('test_live2d_only').META['locomotion']})
    assert _expression_commands(host) == [('audio', True)]
    assert pet._applied_expressions == {'audio'}
    pet._stop_runtime()
    host.messages.clear()
    pet._keyboard_activity()
    pet._audio_activity(True)
    pet._update_runtime()
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'ready', 'backend': 'live2d', 'states': [*ANIMATIONS,*pet_app.TOP_TRANSITIONS], 'locomotion':__import__('test_live2d_only').META['locomotion']})
    assert not host.messages and not pet._reaction_expressions and pet._active_reaction is None


def test_independent_reaction_channel_does_not_cancel_an_explicit_expression(pet, monkeypatch):
    host, clock = _start_both_real_reaction_signals(pet, monkeypatch)
    pet._show_effect('smile', 1000)
    clock[0] = 101.1
    pet._update_runtime()
    assert pet._applied_expressions == {'audio', 'keyboard'}
    assert ('smile', False) in _expression_commands(host)
    pet.toggle_interaction('keyboard_enabled', False)
    assert pet._applied_expressions == {'audio'}
    pet.toggle_interaction('audio_enabled', False)
    assert not pet._applied_expressions


def test_v5_typing_and_listening_artwork_bindings_target_independent_native_parameters():
    from pathlib import Path
    metadata = json.loads((Path(__file__).resolve().parents[1] /
        'assets/live2d/Maple/Maple.pet.json').read_text(encoding='utf-8-sig'))
    keyboard = {row['id'] for row in metadata['expressions']['keyboard']}
    audio = {row['id'] for row in metadata['expressions']['audio']}
    assert 'ParamTyping' in keyboard and 'ParamListening' in audio
    assert not keyboard & audio  # No authored overwrite may cancel the other channel.


def test_cleanup_never_finishes_on_status_without_confirmed_exit(pet, monkeypatch):
    activate(pet)
    pet._begin_cleanup_operation('a' * 32)
    result = {'status': 'succeeded', 'terminal': True, 'processExitVerified': False,
              'operation_id': 'a' * 32, 'steps': {}, 'available_increase': None}
    monkeypatch.setattr(pet_app, 'read_cleanup', lambda _: result)
    pet._poll_cleanup()

    assert pet.cleanup_operation and not pet.cleanup_finish_pending
    assert '清理成功' not in pet.details_panel.result.text()
    assert '核验后台退出' in pet.details_panel.result.text()
    pet.cleanup_result = result
    pet._finish_cleanup_operation()
    assert pet.cleanup_operation and pet.settings.last_clean_timestamp == 0






def test_late_startup_authorization_callback_does_nothing_after_stop(pet, monkeypatch):
    import concurrent.futures
    queued = concurrent.futures.Future()
    pet._authorization_probe_future = queued
    pet._stop_runtime()
    monkeypatch.delenv('MEINIFENG_DISABLE_CLEAN_TASK', raising=False)
    monkeypatch.setattr(pet._install_executor, 'submit', lambda *_: pytest.fail('late query after executor shutdown'))
    pet._offer_clean_task_install()
    pet._poll_authorization_probe()
    assert queued.cancelled() and pet._authorization_probe_future is None


@pytest.mark.parametrize('dragging', [True, False])
def test_cleanup_completion_preserves_interrupted_paused_placement(pet, monkeypatch, dragging):
    activate(pet)
    pet._begin_cleanup_operation('c' * 32)
    pet.toggle_pause()
    pet.dragging = True
    pet._detach_for_drag()
    area = pet._screen_area()
    pet.move(area.center().x(), area.top() + 100)
    if not dragging:
        pet.dragging = False
        pet._settle_after_drag()
        assert pet.state == 'fall_float'
    before = (pet.pos(), pet.state, pet.motion_mode, pet.dragging)
    result = {'status': 'succeeded', 'terminal': True, 'processExitVerified': True,
              'completed_at': 123, 'operation_id': 'c' * 32, 'steps': {}, 'available_increase': None}
    monkeypatch.setattr(pet_app, 'read_cleanup', lambda _: result)
    pet._poll_cleanup()
    assert pet.cleanup_operation is None and pet.settings.last_clean_timestamp == 123
    assert before == (pet.pos(), pet.state, pet.motion_mode, pet.dragging)
    assert pet.settings.paused and not pet.motion_timer.isActive()


def test_cleanup_panel_names_current_backend_step(pet, monkeypatch):
    pet._begin_cleanup_operation('d' * 32)
    monkeypatch.setattr(pet_app, 'read_cleanup', lambda _: {'status': 'running', 'step': 'registry_cache', 'operation_id': 'd'*32})
    pet._poll_cleanup()
    assert '注册表缓存' in pet.details_panel.result.text()


def test_cleanup_partial_failure_does_not_claim_success_or_release_success_effect(pet, monkeypatch):
    host = activate(pet)
    pet._begin_cleanup_operation('b' * 32)
    result = {'status': 'partial', 'terminal': True, 'processExitVerified': True,
              'completed_at': 123, 'operation_id': 'b' * 32, 'available_increase': None,
              'steps': {'system_file_cache': {'ok': False, 'message': 'controlled failure'}}}
    monkeypatch.setattr(pet_app, 'read_cleanup', lambda _: result)
    pet._poll_cleanup()
    event(pet, 'cycle', cycle=2)
    assert pet.cleanup_operation is None and pet.settings.last_clean_timestamp == 0
    assert '失败' in pet.details_panel.result.text()
    assert not any(m['type'] == 'effect' and m['name'] == 'clean_done' for m in host.messages)


def test_cleanup_duplicate_clicks_submit_only_one_pending_request(pet, monkeypatch):
    import concurrent.futures
    pending = concurrent.futures.Future()
    calls = []
    monkeypatch.setattr(pet._install_executor, 'submit', lambda *args: (calls.append(args), pending)[1])
    pet.start_memory_cleanup()
    pet.start_memory_cleanup()
    assert len(calls) == 1 and pet._cleanup_future is pending
    pending.set_result({'ok': False, 'status': 'failed', 'message': 'controlled start failure'})
    pet._poll_cleanup()
    assert pet.cleanup_operation is None and pet.details_panel.clean_button.isEnabled()


def test_native_climb_uses_authored_phase_once_and_does_not_jump_after_pause(pet):
    from locomotion import climb_spec
    activate(pet)
    pet._climb_spec = climb_spec({'climb': {'risePerCycle': .16, 'cycleDuration': .9,
        'phaseTravel': [[0, 0], [.5, .4], [1, 1]]}})
    area = pet._screen_area()
    pet.move(area.left(), area.top() + 230)
    pet._attach_side('left')
    origin = pet.y()
    pet._advance_native_climb({'climbPhase': .5, 'climbCycle': 0})
    assert abs(pet.y() - origin + .16 * .4 * pet.height()) <= 1
    after = pet.y()
    pet._advance_native_climb({'climbPhase': .5, 'climbCycle': 0})
    pet._advance_native_climb({'climbPhase': .2, 'climbCycle': 0})
    pet._advance_native_climb({'climbPhase': .5, 'climbCycle': 0})
    assert pet.y() == after
    pet.panel_pause_active = True
    pet._advance_native_climb({'climbPhase': 1, 'climbCycle': 0})
    assert pet.y() == after
    pet.panel_pause_active = False
    pet._advance_native_climb({'climbPhase': 0, 'climbCycle': 1})
    assert pet.y() == after


def _refined_climb_fixture(pet, side='left'):
    states = [*ANIMATIONS, *pet_app.TOP_TRANSITIONS]
    host = activate(pet, states)
    pet._on_renderer_event({"generation": pet._renderer_generation, 'type': 'ready', 'states': states,
        'capabilities': ['climb-endpoint-v1'], 'locomotion': {'climb': {
            'risePerCycle': .12, 'cycleDuration': 1.6, 'refinementParameter': 'ParamClimbRefine',
            'phaseTravel': [[0, 0], [.4, .4], [1, 1]]}}})
    area = pet._screen_area()
    pet.move(area.left() + 20, area.top() + 80)
    pet._attach_side(side)
    # One rise plus a pixel of clearance: the next reaching hand arrives at
    # the roof while the planted interval must keep its world position.
    pet.move(pet.x(), round(area.top() + .12 * pet.height() + 1 - .34 * pet.height()))
    pet._motion_y = float(pet.y())
    token = pet._renderer_token

    def sample(phase, *, endpoint=None, generation=4, cycle=0, sample_token=None):
        travel = phase
        # The near hand has finished reaching by .4 and is planted thereafter.
        hand_y = .34 + .12 * (travel - min(phase / .4, 1))
        anchors = {side: [.2 if side == 'left' else .8, hand_y],
                   'grip' + side.title(): [.2 if side == 'left' else .8, hand_y], 'hang': [.5, 0]}
        payload = {'type': 'geometry', 'name': 'climb_' + side, 'token': token if sample_token is None else sample_token,
            'generation': generation, 'bounds': [.1, .1, .8, .8], 'anchors': anchors,
            'climbPhase': phase, 'climbCycle': cycle}
        if endpoint is not None:
            payload['climbEndpoint'] = endpoint
        pet._on_renderer_event(payload)
        return payload

    sample(0)
    assert pet._climb_endpoint_request is not None
    return host, sample, token


@pytest.mark.parametrize('side', ['left', 'right'])
def test_refined_climb_keeps_world_grip_and_enters_only_after_bound_endpoint(pet, side):
    host, sample, token = _refined_climb_fixture(pet, side)
    request = pet._climb_endpoint_request['request']
    contacts = []
    positions = []
    for phase in (.4, .7, .9):
        payload = sample(phase)
        contacts.append(pet.y() + payload['anchors'][side][1] * pet.height())
        positions.append(pet.y())
        assert pet._top_transition is None and pet._renderer_token == token
    assert max(contacts) - min(contacts) <= 1  # Native grip stays planted, including near the roof.
    assert positions[-1] < positions[0]  # The window was not clamped while native arms advanced.
    sample(1, endpoint={'request': request, 'cycle': 0})
    assert pet._render_state_name() == 'climb_to_top_' + side
    assert abs(pet._top_transition['wall'][1] - contacts[-1]) <= 1
    assert sum(row['type'] == 'climb-endpoint' and row['enabled'] for row in host.messages) == 1
    assert pet._climb_endpoint_request is None


@pytest.mark.parametrize('invalid', ['midphase', 'wrong_request', 'wrong_generation', 'wrong_cycle', 'missing_ack'])
def test_refined_climb_rejects_unbound_or_nonendpoint_geometry(pet, invalid):
    _, sample, token = _refined_climb_fixture(pet)
    request = pet._climb_endpoint_request['request']
    sample(.4); sample(.7)
    ack = {'request': request + (1 if invalid == 'wrong_request' else 0), 'cycle': 9 if invalid == 'wrong_cycle' else 0}
    sample(.9 if invalid == 'midphase' else 1,
           endpoint=None if invalid == 'missing_ack' else ack,
           generation=3 if invalid == 'wrong_generation' else 4)
    assert pet._top_transition is None and pet._renderer_token == token


@pytest.mark.parametrize('interruption', ['pause', 'panel', 'hide', 'drag', 'state'])
def test_refined_climb_cancel_discards_late_endpoint_without_false_transition(pet, interruption):
    host, sample, token = _refined_climb_fixture(pet)
    request = pet._climb_endpoint_request['request']
    sample(.4)
    if interruption == 'pause':
        pet.settings.paused = True
        pet._sync_runtime_pause()
    elif interruption == 'panel':
        pet.panel_pause_active = True
        pet._sync_runtime_pause()
    elif interruption == 'hide':
        pet.hide_pet()
    else:
        if interruption == 'drag':
            pet.dragging = True
        pet.start_state('drag_left' if interruption == 'drag' else 'idle')
    assert pet._climb_endpoint_request is None
    sample(1, endpoint={'request': request, 'cycle': 0})
    assert pet._top_transition is None
    if interruption in {'pause', 'panel', 'hide'}:
        assert any(row['type'] == 'climb-endpoint' and not row['enabled'] and row['request'] == request for row in host.messages)
    else:
        assert pet._renderer_token != token


@pytest.mark.parametrize('side', ['left', 'right'])
def test_top_transfer_keeps_painted_contact_and_never_switches_to_padded_edge(pet, side):
    activate(pet)
    pet._attach_side(side)
    material_x = .3 if side == 'left' else .7
    anchors = {side: [material_x, .4], 'nearGrip': [material_x, .4],
               'grip' + side.title(): [material_x + .02, .37], 'hang': [.5, 0]}
    pet._renderer_geometry = {'anchors': anchors}
    pet._begin_top_transition()
    assert pet._top_transition['anchor_key'] == 'nearGrip'
    start = pet.pos()
    payload = {'generation':pet._renderer_generation, 'type': 'geometry', 'token': pet._renderer_token, 'name': 'climb_to_top_' + side,
               'bounds': [0, 0, 1, 1], 'anchors': anchors, 'transitionProgress': 0}
    pet._on_renderer_event(payload)
    assert pet.pos() == start
    # A different extreme vertex/padding must not move the locked painted palm.
    changed_edge = dict(anchors, **{'grip' + side.title(): [material_x + .06, .45]})
    pet._on_renderer_event(dict(payload, anchors=changed_edge))
    assert pet.pos() == start
    # Missing native evidence keeps placement; it cannot silently select another point.
    missing_material = {key: value for key, value in changed_edge.items() if key != 'nearGrip'}
    pet._on_renderer_event(dict(payload, anchors=missing_material))
    assert pet.pos() == start
    shifted_material = dict(changed_edge, nearGrip=[material_x, .42])
    pet._on_renderer_event(dict(payload, anchors=shifted_material))
    assert abs(pet.y() - (start.y() - .02 * pet.height())) <= 1


def test_refined_climb_direct_ceiling_call_cannot_reset_midcycle_pose(pet):
    _, sample, token = _refined_climb_fixture(pet)
    sample(.4)
    pet._begin_top_transition()
    assert pet._top_transition is None and pet._renderer_token == token


@pytest.mark.parametrize('side', ['left', 'right'])
def test_roof_request_uses_painted_grip_instead_of_padded_extreme(pet, side):
    _, sample, _ = _refined_climb_fixture(pet, side)
    pet._cancel_climb_endpoint()
    area = pet._screen_area()
    pet.move(pet.x(), area.top() + 10)
    pet._motion_y = float(pet.y())
    anchors = {side: [.3, .02], 'grip' + side.title(): [.3, .01],
               'nearGrip': [.3, .4], 'hang': [.5, 0]}
    pet._renderer_geometry = {'anchors': anchors}
    pet._advance_native_climb({'climbPhase': 0, 'climbCycle': 0})
    assert pet._climb_endpoint_request is None
    anchors['nearGrip'] = [.3, .02]
    pet._advance_native_climb({'climbPhase': 0, 'climbCycle': 0})
    assert pet._climb_endpoint_request['purpose'] == 'top'


def test_refined_climb_dropped_geometry_does_not_strand_endpoint_or_replay_missed_travel(pet):
    _, sample, _ = _refined_climb_fixture(pet)
    request = pet._climb_endpoint_request['request']
    before = pet.y()
    sample(1, endpoint={'request': request, 'cycle': 0})
    assert pet._render_state_name() == 'climb_to_top_left'
    assert pet.y() == before


def test_legacy_climb_retains_existing_ceiling_transition_without_new_contract(pet):
    activate(pet, [*ANIMATIONS, *pet_app.TOP_TRANSITIONS])
    from locomotion import climb_spec
    pet._climb_spec = climb_spec({'climb': {'risePerCycle': .16, 'cycleDuration': .9,
        'phaseTravel': [[0, 0], [1, 1]]}})
    pet._attach_side('left')
    pet._renderer_geometry = {'anchors': {'left': [.2, .4], 'hang': [.5, 0]}}
    pet._motion_y = pet._screen_area().top() - .4 * pet.height()
    assert pet._advance_native_climb({'climbPhase': .2, 'climbCycle': 0})
    assert pet._render_state_name() == 'climb_to_top_left'
















def test_resize_and_reposition_do_not_create_false_falling_wind(pet):
    host = activate(pet)
    area = pet._screen_area()
    pet.motion_mode = 'fall'
    pet.start_state('fall_float')
    pet._fall_velocity = 120.0
    pet._motion_velocity = (0.0, 120.0)
    pet.move(area.center().x(), area.top() + 80)
    pet._clamp_to_current_screen()
    pet.set_scale(1.4)
    pet._send_motion_context()
    context = next(m for m in reversed(host.messages) if m['type'] == 'context')
    assert context['falling'] and context['vx'] == 0
    assert context['vy'] == pytest.approx(120.0 / pet.height())
    pet._attach_side('left')
    pet._send_motion_context()
    context = next(m for m in reversed(host.messages) if m['type'] == 'context')
    assert context['attached'] and not context['falling']
    assert context['vx'] == context['vy'] == 0






