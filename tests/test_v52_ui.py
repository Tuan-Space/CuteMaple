"""Production host with controlled backend/native reports; never requests UAC."""
import concurrent.futures
import pytest
import pet_app
from test_live2d_integration import pet, activate, event, _start_both_real_reaction_signals


def pose(pet,name):
    if name in ('left','right'): pet._attach_side(name)
    elif name=='top': pet._attach_top(False)
    else:
        if name.startswith('sleep_'): pet.sleep_phase=name.removeprefix('sleep_'); pet.sleep_started_at=123
        if name=='fall_float': pet.motion_mode='fall'
        pet.start_state(name)


@pytest.mark.parametrize('name',['idle','left','right','top','fall_float','land','sleep_enter','sleep_loop','petting'])
@pytest.mark.parametrize('status',['succeeded','failed','partial','cancelled','timed_out'])
def test_cleanup_lifecycle_never_changes_pose_or_clock(pet,monkeypatch,name,status):
    host=activate(pet);pose(pet,name)
    before=(pet.state,pet._renderer_token,pet.pos(),pet.motion_mode,pet.sleep_phase,
            pet.sleep_started_at,pet._climb_cadence.run,pet._climb_cadence.remaining)
    host.messages.clear()
    pet._begin_cleanup_operation('a'*32)
    result={'operation_id':'a'*32,'status':'running','step':'registry_cache'}
    monkeypatch.setattr(pet_app,'read_cleanup',lambda _:result)
    pet._poll_cleanup();assert '注册表缓存' in pet.details_panel.result.text()
    assert pet.cleanup_operation=='a'*32
    result.update(status=status,terminal=True,processExitVerified=True,completed_at=321,steps={})
    pet._poll_cleanup()
    after=(pet.state,pet._renderer_token,pet.pos(),pet.motion_mode,pet.sleep_phase,
           pet.sleep_started_at,pet._climb_cadence.run,pet._climb_cadence.remaining)
    assert before==after and pet.cleanup_operation is None
    assert pet.settings.last_clean_timestamp==(321 if status=='succeeded' else 0)
    assert not any(row['type'] in ('play','pause','climb-endpoint','effect') for row in host.messages)


def test_cleanup_keeps_real_activity_response(pet,monkeypatch):
    host,clock=_start_both_real_reaction_signals(pet,monkeypatch)
    pet._begin_cleanup_operation('a'*32);clock[0]+=0.1;pet._update_runtime()
    assert pet._active_reaction=='keyboard' and pet._applied_expressions=={'keyboard','audio'}
    assert not any(row.get('name','').startswith('clean_') for row in host.messages)


def test_startup_and_unapproved_automatic_cleanup_never_request_uac(pet,monkeypatch):
    monkeypatch.setattr(pet_app,'session_is_valid',lambda:False)
    monkeypatch.setattr(pet._install_executor,'submit',lambda *_:pytest.fail('No startup/automatic authorization'))
    pet._offer_clean_task_install();pet._poll_authorization_probe()
    pet.start_memory_cleanup(manual=False);pet.start_memory_cleanup(manual=False)
    assert pet._cleanup_future is None and pet._install_future is None
    assert '本次' in pet.details_panel.result.text()


@pytest.mark.parametrize('status',['helper_missing','security_blocked','cancelled','failed'])
def test_failed_launch_immediately_reports_to_visible_ui(pet,monkeypatch,status):
    pending=concurrent.futures.Future();submitted=[];feedback=[]
    monkeypatch.setattr(pet._install_executor,'submit',lambda *args:submitted.append(args) or pending)
    monkeypatch.setattr(pet,'_show_cleanup_feedback',lambda value:feedback.append(value.copy()))
    pet.start_memory_cleanup()
    assert pet.monitor_button._cleaning and pet.details_panel.clean_button.text()=='查看清理进度'
    pet.start_memory_cleanup();assert len(submitted)==1 and len(feedback)==2
    pending.set_result({'ok':False,'status':status,'message':'controlled visible failure'})
    pet._poll_cleanup()
    assert feedback[-1]['status']==status and not pet.monitor_button._cleaning
    assert pet.cleanup_operation is None and not pet.cleanup_poll_timer.isActive()


def test_session_authorization_result_is_not_persisted_as_task(pet,monkeypatch):
    result=concurrent.futures.Future();result.set_result({'ok':True,'valid':True,'status':'authorized'})
    pet._install_future=result
    old=(pet.settings.clean_task_schema_version,pet.settings.clean_task_executable)
    pet._poll_clean_task_install()
    assert pet._install_future is None
    assert (pet.settings.clean_task_schema_version,pet.settings.clean_task_executable)==old
    assert '已授权' in pet.details_panel.result.text()


@pytest.mark.parametrize('side',['left','right'])
def test_panel_hold_freezes_wait_and_partial_burst_without_reset(pet,monkeypatch,side):
    host=activate(pet);clock=[100.];monkeypatch.setattr(pet_app.time,'monotonic',lambda:clock[0])
    pet._attach_side(side);token=pet._renderer_token;run=pet._climb_cadence.run
    assert host.messages[-1]['type']=='climb-control' and host.messages[-1]['resting']
    for _ in range(40):clock[0]+=.25;pet._tick_climb_cadence()
    assert pet._climb_cadence.remaining==20
    before=pet.pos();pet.open_details_panel()
    for _ in range(200):clock[0]+=.25;pet._tick_climb_cadence()
    assert pet.pos()==before and pet._climb_cadence.remaining==20
    pet.close_details_panel()
    for _ in range(80):clock[0]+=.25;pet._tick_climb_cadence()
    assert not pet._climb_cadence.waiting and pet._renderer_token==token
    current_run=pet._climb_cadence.run;assert current_run==run+1
    pet.open_details_panel();assert host.messages[-1].get('type') or host.messages
    assert any(m.get('type')=='climb-control' and m.get('resting') for m in host.messages)
    pet.close_details_panel();assert pet._climb_cadence.run==current_run
    event(pet,'climb-rest',run=current_run,cycles=2)
    assert pet._climb_cadence.waiting and pet._climb_cadence.remaining==30
    event(pet,'climb-rest',run=current_run-1,cycles=2)
    assert pet._climb_cadence.remaining==30


def test_completed_burst_arriving_during_pause_is_not_lost(pet):
    activate(pet);pet._attach_side('left');pet._climb_cadence.tick(30)
    run=pet._climb_cadence.run;pet.toggle_pause()
    event(pet,'climb-rest',run=run,cycles=2)
    assert pet._climb_cadence.waiting and pet._climb_cadence.remaining==30


def test_cleaning_effects_and_play_states_are_removed(pet):
    host=activate(pet);token=pet._renderer_token;host.messages.clear()
    for name in ('clean_ground','clean_top','clean_climb_left','clean_climb_right'):pet.start_state(name)
    for name in ('clean_dust','clean_done'):pet._emit_effect(name,interval=0);pet._preview_effect(name)
    assert pet._renderer_token==token and pet.state=='idle' and not host.messages
    assert not any(name.startswith('clean_') for name in pet.frames)
