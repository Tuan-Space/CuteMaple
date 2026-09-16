import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MEINIFENG_DISABLE_CLEAN_TASK", "1")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

import pet_app
from pet_core import PetSettings, ANIMATIONS
import pytest
from test_live2d_integration import activate, event

@pytest.fixture(autouse=True)
def native_fixture(monkeypatch):
    original=pet_app.PetWindow.__init__
    def initialize(self,*args,**kw):
        original(self,*args,**kw);self.show();activate(self)
    monkeypatch.setattr(pet_app.PetWindow,"__init__",initialize)



def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_size_motion_and_interaction_states(monkeypatch):
    app = _app()
    monkeypatch.setattr(pet_app, "save_settings", lambda *_args, **_kwargs: None)
    pet = pet_app.PetWindow(app, PetSettings(autostart=False))
    pet.show()
    app.processEvents()

    pet.set_scale(0.75)
    assert pet.width() == 165
    pet.restore_default_size()
    assert pet.width() == 220

    clock = [100.0]
    with monkeypatch.context() as walk_patch:
        walk_patch.setattr(pet_app.time, "monotonic", lambda: clock[0])
        pet._begin_walk()
        assert pet.state in {"walk_left", "walk_right"}
        assert pet.motion_mode == "walk_out"
        origin, target = pet.walk_origin_x, pet.walk_target_x
        clock[0] += .5
        pet._motion_step()
        assert pet.x() == round((origin + target) / 2)
        clock[0] += .5
        pet._motion_step()
        assert pet.motion_mode == "walk_back"
        clock[0] += 1.0
        pet._motion_step()
    assert pet.motion_mode is None
    assert pet.x() == origin
    assert pet.base_mode == "ground"

    pet._attach_side("left")
    assert pet.state == "climb_left"
    assert pet.motion_mode == "climb"
    pet._attach_top()
    assert pet.state == "swing_cycle"
    assert pet.attachment == "top"

    event(pet, "cycle", cycle=3)
    assert pet.state == "swing_idle"

    pet._detach_for_drag()
    pet.start_state("idle")
    for x in (10, 65, 20, 75):
        pet._track_petting(QPoint(x, 10), Qt.NoButton)
    assert pet.state == "petting"

    pet.close()
    pet.tray.hide()


def test_temporary_actions_restore_attachment_and_side_contacts_screen(monkeypatch):
    app = _app()
    monkeypatch.setattr(pet_app, "save_settings", lambda *_args, **_kwargs: None)
    pet = pet_app.PetWindow(app, PetSettings(autostart=False))
    area = pet.current_screen.availableGeometry()

    pet._attach_side("left")
    left_anchor = pet._model_anchor('left')[0]*512
    assert abs((pet.x() + round(left_anchor * pet.width() / 512)) - area.left()) <= 1
    pet.say("测试")
    pet._finish_temporary_state()
    assert pet.base_mode == "climb_left"
    assert pet.state == "climb_left"

    pet._attach_side("right")
    right_anchor = pet._model_anchor('right')[0]*512
    assert abs((pet.x() + round(right_anchor * pet.width() / 512)) - area.right()) <= 1

    pet._attach_top()
    pet._start_temporary("happy")
    pet._complete_animation()
    assert pet.base_mode == "top_swing"
    assert pet.state == "swing_idle"

    pet.close()
    pet.tray.hide()


def test_global_idle_sleep_and_wake_cycle(monkeypatch):
    app = _app()
    clock = [1000.0]
    monkeypatch.setattr(pet_app, "save_settings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pet_app.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pet_app, "get_system_idle_seconds", lambda: 61.0)
    pet = pet_app.PetWindow(app, PetSettings(autostart=False))
    pet.show()
    pet.awake_cycle_started = clock[0] - 61
    pet._set_ground_idle()
    pet._check_system_idle()
    assert pet.sleep_phase == "enter"
    assert pet.state == "sleep_enter"

    pet._complete_animation()
    assert pet.sleep_phase == "loop"
    assert pet.state == "sleep_loop"
    clock[0] += 241
    pet._check_system_idle()
    assert pet.sleep_phase == "exit"
    pet._complete_animation()
    assert pet.sleep_phase is None
    assert pet.awake_cycle_started == clock[0]

    pet.close()
    pet.tray.hide()


def test_happy_finishes_once_and_paused_motion_is_visually_frozen(monkeypatch):
    app = _app()
    monkeypatch.setattr(pet_app, "save_settings", lambda *_args, **_kwargs: None)
    pet = pet_app.PetWindow(app, PetSettings(paused=False, autostart=False))
    pet.show()
    pet._start_temporary("happy")
    assert pet.state == "happy"
    event(pet, "finished")
    assert pet.state == "idle"

    pet.settings.paused = True
    pet.motion_mode = "fall"
    pet.start_state("fall_float")
    pet.motion_timer.stop()
    assert not hasattr(pet,'animation_timer')
    pet.toggle_pause()
    assert pet.motion_timer.isActive()
    assert not hasattr(pet,'animation_timer')
    pet.close()
    pet.tray.hide()


def test_shared_menu_and_petting_twice(monkeypatch):
    app = _app()
    monkeypatch.setattr(pet_app, "save_settings", lambda *_args, **_kwargs: None)
    pet = pet_app.PetWindow(app, PetSettings(autostart=False))
    pet.show()
    app.processEvents()

    menu = pet._create_context_menu()
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == [
        "枫叶手账 · 提醒与笔记", "隐藏美腻枫", "暂停活动", "互动", "大小", "开机自动启动", "性能与内存", "退出",
    ]
    tray_menu = pet.tray.contextMenu()
    pet._sync_context_menu(tray_menu)
    assert [action.text() for action in tray_menu.actions() if not action.isSeparator()] == [
        "枫叶手账 · 提醒与笔记", "隐藏美腻枫", "暂停活动", "互动", "大小", "开机自动启动", "性能与内存", "退出",
    ]
    pet.hide_pet()
    pet._sync_context_menu(tray_menu)
    assert next(a for a in tray_menu.actions() if a.objectName() == "visibility_action").text() == "显示美腻枫"

    pet.show_pet()
    pet._start_temporary("petting")
    event(pet,"cycle",cycle=1)
    assert pet.state=="petting"
    event(pet,"cycle",cycle=2)
    assert pet.state == "idle"

    menu.deleteLater()
    pet.close()
    pet.tray.hide()


def test_details_panel_freezes_movement_and_follows_pet(monkeypatch):
    app = _app()
    monkeypatch.setattr(pet_app, "save_settings", lambda *_args, **_kwargs: None)
    pet = pet_app.PetWindow(app, PetSettings(paused=False, autostart=False))
    pet.show(); app.processEvents()

    pet.monitor_button.show(); pet.monitor_capsule.show()
    pet.open_details_panel(); app.processEvents()
    assert pet.details_panel.isVisible()
    assert not pet.monitor_capsule.isVisible()
    assert pet.movement_paused and not pet.activity_paused
    assert pet.details_panel.testAttribute(Qt.WA_TranslucentBackground)
    pet._flip_panel_for_drag(10)
    assert pet._panel_side == "left"
    pet._flip_panel_for_drag(-10)
    assert pet._panel_side == "left"  # 右侧尚放不下时不提前翻面
    area = pet.current_screen.availableGeometry(); pet.dragging = True
    pet.move(area.left() + 30, pet.y())
    pet._flip_panel_for_drag(-10)
    assert pet._panel_side == "right"
    pet.dragging = False
    old_panel = pet.details_panel.pos()
    pet.move(pet.x() - 30, pet.y()); app.processEvents()
    assert pet.details_panel.pos() != old_panel

    pet.open_details_panel(); app.processEvents()
    assert not pet.details_panel.isVisible()
    assert not pet.activity_paused
    pet.close(); pet.tray.hide()


def test_overlay_union_bounds_do_not_intersect_any_animation(monkeypatch):
    app = _app()
    monkeypatch.setattr(pet_app, "save_settings", lambda *_args, **_kwargs: None)
    pet = pet_app.PetWindow(app, PetSettings(autostart=False))
    pet.show(); pet.monitor_button.show(); pet.monitor_capsule.show(); app.processEvents()
    assert pet.monitor_button.testAttribute(Qt.WA_TranslucentBackground)
    assert pet.monitor_capsule.testAttribute(Qt.WA_TranslucentBackground)
    area = pet.current_screen.availableGeometry()
    pet.move(area.center().x() - pet.width() // 2, area.bottom() - pet.height() + 1)
    pet.start_state("idle"); pet._position_monitor_overlays()
    assert pet.monitor_button.x() > pet._content_rect_global().right()
    for scale in (0.6, 1.0, 1.4, 1.8):
        pet.set_scale(scale)
        for state in ANIMATIONS:
            pet.start_state(state);
            pet._position_monitor_overlays()
            content = pet._content_rect_global()
            area = pet.current_screen.availableGeometry()
            assert not pet.monitor_button.geometry().intersects(content), (scale, state, "button")
            assert not pet.monitor_capsule.geometry().intersects(content), (scale, state, "capsule")
            assert pet._rect_inside(pet.monitor_button.geometry(), area)
            assert pet._rect_inside(pet.monitor_capsule.geometry(), area)
    pet.open_details_panel(); app.processEvents()
    for scale in (0.6, 1.0, 1.4, 1.8):
        pet.set_scale(scale)
        for state in ANIMATIONS:
            pet.start_state(state); pet._panel_side = None
            pet._position_details_panel(); pet._position_monitor_overlays()
            content = pet._content_rect_global(); area = pet.current_screen.availableGeometry()
            assert not pet.details_panel.geometry().intersects(content), (scale, state, "details")
            assert not pet.monitor_button.geometry().intersects(content), (scale, state, "button-details")
            assert not pet.monitor_button.geometry().intersects(pet.details_panel.geometry())
            assert pet._rect_inside(pet.details_panel.geometry(), area)
            assert pet._rect_inside(pet.monitor_button.geometry(), area)
            assert not pet.monitor_capsule.isVisible()
    pet.monitor_button.close(); pet.monitor_capsule.close(); pet.details_panel.close()
    pet.close(); pet.tray.hide()




