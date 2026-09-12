"""Live2D-only lifecycle tests; fake renderer never enters the production package."""
import json, os
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('MEINIFENG_DISABLE_RUNTIME','1')
os.environ.setdefault('MEINIFENG_DISABLE_CLEAN_TASK','1')
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget
import pet_app, live2d_host
from pet_core import ANIMATIONS, PetSettings
META=json.loads((Path(__file__).parents[1]/'assets/live2d/Maple/Maple.pet.json').read_text(encoding='utf-8-sig'))
class FakeHost(QObject):
    event=Signal(dict)
    def __init__(self,parent,root):
        super().__init__(parent);self.view=QWidget(parent);self.ready=False;self.generation=1;self.messages=[];self.ack=None
    def load(self):pass
    def send(self,kind,**kw):self.messages.append(dict(type=kind,**kw))
    def stop(self):self.ready=False;self.view.hide()
    def presented(self,token):self.ack=token
@pytest.fixture
def pet(monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(live2d_host,'Live2DHost',FakeHost)
    monkeypatch.setattr(pet_app,'save_settings',lambda *_:None)
    monkeypatch.setattr(pet_app.PetWindow,'_frame_has_pixels',lambda _:True)
    p=pet_app.PetWindow(app,PetSettings());p.show();app.processEvents();p.retry_live2d()
    yield p
    p._stop_runtime();p._install_executor.shutdown(wait=False,cancel_futures=True)
    for w in (p,p.bubble,p.monitor_button,p.monitor_capsule,p.details_panel):w.close()
    p.tray.hide();app.processEvents()
def ready(p,states=None):
    h=p.live2d_host;h.ready=True
    h.event.emit(dict(type='ready',generation=h.generation,states=states if states is not None else [*ANIMATIONS,*pet_app.TOP_TRANSITIONS],locomotion=META['locomotion'],capabilities=['motion-polish-v1','climb-endpoint-v1']))
    return h
def frame(p,**overrides):
    event=dict(type='frame-ready',generation=p._renderer_generation,token=p._renderer_token,name=p._render_state_name(),bounds=[.1,.1,.8,.85],anchors={'head':[.5,.3],'ground':[.5,.95],'left':[.15,.4],'right':[.85,.4],'top':[.5,0]},climbPhase=0,climbCycle=0)
    event.update(overrides);p._on_renderer_event(event)
def test_loading_never_creates_old_character(pet):
    assert pet.loading_panel.isVisible() and not hasattr(pet,'sprite') and not hasattr(pet,'frames')
    assert pet.movement_paused
    ready(pet);assert not pet._presentation_ready
    frame(pet,token=pet._renderer_token-1);assert not pet._presentation_ready
    frame(pet,generation=99);assert not pet._presentation_ready
    frame(pet);assert pet._presentation_ready and not pet.loading_panel.isVisible()
    assert pet.live2d_host.view.isVisible() and pet.live2d_host.ack==pet._renderer_token
@pytest.mark.parametrize('missing',['idle','climb_to_top_left','sleep_exit'])
def test_missing_motion_is_failure_not_fallback(pet,missing):
    ready(pet,[x for x in [*ANIMATIONS,*pet_app.TOP_TRANSITIONS] if x!=missing])
    assert pet.retry_button.isVisible() and not pet._live2d_active and not hasattr(pet,'sprite')
@pytest.mark.parametrize('reason',['模型缺失','载入超时','渲染进程停止'])
def test_failure_retry_and_late_old_host(pet,reason):
    old=ready(pet);frame(pet)
    old.event.emit(dict(type='error',message=reason,generation=old.generation))
    assert pet.retry_button.isVisible() and pet.movement_paused
    pet.retry_live2d();old.event.emit(dict(type='error',message='late',generation=1))
    assert pet.renderer_status=='正在加载桌宠…'
    ready(pet);frame(pet);assert pet._presentation_ready
@pytest.mark.parametrize('state',list(ANIMATIONS))
def test_states_are_native_and_have_no_frame_clock(pet,state):
    ready(pet);frame(pet);pet.start_state(state)
    assert pet.state==state and not hasattr(pet,'animation_timer')
    assert [m for m in pet.live2d_host.messages if m['type']=='play'][-1]['name']==state
@pytest.mark.parametrize('hidden,paused',[(True,False),(False,True),(True,True)])
def test_first_frame_respects_visibility_and_pause(pet,hidden,paused):
    if hidden:pet.hide_pet()
    if paused:pet.toggle_pause()
    ready(pet);frame(pet)
    if hidden:
        assert not pet._presentation_ready and pet._pending_presentation
        pet.show_pet();pet._present_current_frame()
    assert pet._presentation_ready and pet.live2d_host.view.isVisible()
    assert pet.settings.paused==paused
    assert [m for m in pet.live2d_host.messages if m['type']=='pause'][-1]['paused']==paused
def test_native_cycles_and_invalid_callbacks(pet):
    ready(pet);frame(pet);pet._attach_top()
    for n in [1,2]:pet._on_renderer_event(dict(type='cycle',name='swing_cycle',token=pet._renderer_token,generation=1,cycle=n))
    assert pet.state=='swing_cycle'
    pet._on_renderer_event(dict(type='cycle',name='swing_cycle',token=pet._renderer_token,generation=1,cycle=3))
    assert pet.state=='swing_idle'
def test_wait_clock_stops_before_presentation(pet):
    pet._attach_side('left');before=pet._climb_cadence.remaining;pet._climb_clock-=20;pet._tick_climb_cadence()
    assert pet._climb_cadence.remaining==before


def test_bridge_delivers_first_frame_envelope():
    bridge=live2d_host.PetBridge();received=[];bridge.event.connect(received.append)
    envelope=dict(type='frame-ready',generation=3,token=9,name='idle',bounds=[0,0,1,1])
    bridge.report(json.dumps(envelope));assert received==[envelope]


def test_native_draw_waits_for_composited_pixels(pet):
    pet._frame_has_pixels=lambda:False
    ready(pet);frame(pet)
    assert not pet._presentation_ready and pet.loading_panel.isVisible()
    pet._frame_has_pixels=lambda:True;pet._present_current_frame()
    assert pet._presentation_ready and not pet.loading_panel.isVisible()
