import pytest
from locomotion import ClimbCadence


@pytest.mark.parametrize('hz',[30,60,144])
def test_wait_clock_and_native_two_cycle_completion(hz):
    c=ClimbCadence(); c.start(); initial=c.run
    for _ in range(hz*30-1): assert not c.tick(1/hz)
    assert c.waiting and c.tick(1/hz)
    assert not c.waiting and c.run==initial+1
    c.tick(1000)
    assert not c.waiting  # Elapsed time never fabricates native cycles.
    assert not c.finished(initial)
    assert c.finished(c.run) and c.remaining==30
    assert not c.finished(c.run)


def test_pause_keeps_remainder_and_new_attachment_resets():
    c=ClimbCadence(); c.start(); c.tick(12)
    c.tick(100,blocked=True); assert c.remaining==18
    c.tick(17.9); assert c.waiting
    c.tick(.1); assert not c.waiting
    c.stop(); c.tick(50); assert not c.active
    c.start(); assert c.waiting and c.remaining==30
