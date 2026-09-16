"""Speech bubbles track painted heads, not the transparent host window."""
import pytest
from PySide6.QtCore import QRect, QPoint, Qt
from PySide6.QtTest import QTest
from test_live2d_integration import pet, event


@pytest.mark.parametrize('mode,side', [('climb_left','right'), ('climb_right','left')])
@pytest.mark.parametrize('scale', [.6, 1., 1.8])
@pytest.mark.parametrize('area', [QRect(0,0,1920,1080), QRect(-1920,-1080,1920,1080)])
def test_wall_dialogue_moves_down_to_head_top(pet, monkeypatch, mode, side, scale, area):
    monkeypatch.setattr(pet, '_screen_area', lambda: area)
    pet.settings.scale = scale
    pet._apply_size(False)
    pet.base_mode = mode
    pet._renderer_geometry = {'headBounds':[.3,.12,.25,.26], 'bounds':[.1,.05,.8,.9]}
    x = area.left()-round(.3*pet.width()) if side == 'right' else area.right()-round(.55*pet.width())
    pet.move(x, area.top()+200)
    pet._single_click()
    head, bubble = pet._bubble_anchor_rect(), pet.bubble.geometry()
    assert area.contains(bubble)
    assert head.top()-bubble.bottom()-1 == 8
    QTest.mouseDClick(pet, Qt.LeftButton, pos=QPoint(pet.width()//2, pet.height()//2))
    assert head.top()-pet.bubble.geometry().bottom()-1 == 8


def test_swing_stays_below_visible_body_and_follows_internal_geometry(pet, monkeypatch):
    area = QRect(0,0,1200,800)
    monkeypatch.setattr(pet, '_screen_area', lambda: area)
    monkeypatch.setattr(pet, '_position_top', lambda: None)
    pet.base_mode = 'top_swing'
    pet.move(420,0)
    pet._renderer_geometry = {'headBounds':[.1,.12,.3,.26], 'bounds':[.1,0,.8,.8]}
    pet._show_dialogue('秋千上的气泡')
    initial = pet.bubble.geometry()
    assert pet.bubble.y()-pet._bubble_anchor_rect().bottom()-1 == 8
    remaining = pet.bubble.hide_timer.remainingTime()
    # The head crosses the screen's halfway point while the window stays put.
    event(pet, 'geometry', bounds=[.1,0,.8,.95], headBounds=[.8,.2,.3,.26])
    assert pet.pos() == QPoint(420,0)
    assert pet.bubble.y()-pet._bubble_anchor_rect().bottom()-1 == 8
    assert pet.bubble.x() > initial.x() and pet.bubble.y() > initial.y()
    assert pet.bubble.hide_timer.remainingTime() <= remaining
    assert pet.bubble.y()-pet._bubble_anchor_rect().bottom()-1 == 8
    # At the screen edge only horizontal clamping changes; placement stays below.
    pet.move(1000,0)
    assert pet.bubble.y()-pet._bubble_anchor_rect().bottom()-1 == 8
    assert area.contains(pet.bubble.geometry())


def test_climb_endpoint_early_return_updates_bubble_and_rejects_stale_geometry(pet, monkeypatch):
    monkeypatch.setattr(pet, '_advance_native_climb', lambda e: True)
    pet.base_mode = 'climb_left'
    pet._renderer_geometry = {'headBounds':[.1,.2,.3,.3]}
    pet._show_dialogue('继续向上')
    before = pet.bubble.pos()
    args = {'bounds':[0,0,1,1], 'headBounds':[.2,.35,.3,.3]}
    event(pet, 'geometry', token=pet._renderer_token-1, **args)
    assert pet.bubble.pos() == before
    event(pet, 'geometry', generation=pet._renderer_generation-1, **args)
    assert pet.bubble.pos() == before
    event(pet, 'geometry', **args)
    assert pet.bubble.pos() != before


@pytest.mark.parametrize('bad', [None, [], [.1,.1,-1,.2], [float('nan'),0,.2,.3]])
def test_invalid_head_uses_visible_model_bounds(pet, bad):
    pet._renderer_geometry = {'bounds':[.25,.3,.5,.6], 'headBounds':bad}
    assert pet._bubble_anchor_rect() == pet._content_rect_global()


@pytest.mark.parametrize('mode', ['ground','climb_left','climb_right','top_swing'])
@pytest.mark.parametrize('corner', ['tl','tr','bl','br'])
def test_long_dialogue_is_inside_screen_at_corners(pet, mode, corner):
    area = QRect(-1280,0,1280,720)
    head = QRect(area.left() if corner.endswith('l') else area.right()-70,
                 area.top() if corner.startswith('t') else area.bottom()-75, 70,75)
    pet.bubble.show_message('屏幕边缘的对话也需要完整显示，不应跑到屏幕外。'*4, head, area, placement=mode)
    assert area.contains(pet.bubble.geometry())


def test_cleanup_uses_same_anchor_without_lifetime_or_hidden_regression(pet, monkeypatch):
    pet.base_mode = 'top_swing'
    pet.move(400,0)
    pet._renderer_geometry = {'headBounds':[.3,.1,.4,.3], 'bounds':[.1,0,.8,.8]}
    pet._set_cleanup_progress({'status':'failed', 'message':'受控测试结果'}, active=False)
    expires, text = pet.bubble._cleanup_expires, pet.bubble.label.text()
    pet.move(pet.x()+20,pet.y()+30)
    pet.set_scale(1.5)
    pet._show_dialogue('不应覆盖清理提示')
    assert pet.bubble._cleanup_expires == expires and pet.bubble.label.text() == text
    assert pet.bubble.y()-pet._bubble_anchor_rect().bottom()-1 == 8
    pet.hide_pet()
    pet._show_dialogue('隐藏后迟到的单击')
    pet._follow_bubble()
    assert not pet.bubble.isVisible()
    pet.show_pet()
    assert pet.bubble.isVisible() and pet.bubble._cleanup_expires == expires
