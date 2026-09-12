import math
import ctypes
import io
import json
import pytest
import audio_probe
from audio_probe import _AudioSampler


@pytest.mark.parametrize('child_encoding', ['cp936', 'cp1252', 'utf-8'])
def test_device_status_pipe_roundtrips_unicode_into_utf8_parent(child_encoding, monkeypatch):
    # Reproduce the actual child locale/parent UTF-8 mismatch, including a
    # character that cannot be encoded in either Windows legacy code page.
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=child_encoding)
    monkeypatch.setattr(audio_probe.sys, 'stdout', stream)
    message = {'state': 'ready', 'endpointName': '扬声器（声卡）🎧',
               'endpoints': [{'id': 'output-1', 'name': '音频输出 🎵'}], 'peak': 0.0}
    audio_probe._write_status(message)
    received = raw.getvalue().decode('utf-8')
    assert json.loads(received) == message
    assert received.endswith('\n') and len(received.splitlines()) == 1


class Meter:
    def __init__(self, endpoint):
        self.endpoint = self.device_id = endpoint
        self.device_name = 'Test output'
        self.endpoints = [{'id': endpoint, 'name': self.device_name, 'isDefault': True}]
        self.muted = False
        self.level = .1
        self.closes = 0
    def peak(self): return self.level
    def refresh_default(self): return False
    def close(self): self.closes += 1


def test_unavailable_endpoint_has_three_bounded_attempts_and_explicit_retry_resets():
    calls = []
    def factory(endpoint):
        calls.append(endpoint)
        if endpoint == 'missing': raise OSError('Selected output endpoint is unavailable')
        return Meter(endpoint)
    sampler = _AudioSampler(factory)
    config = {'generation': 1, 'enabled': True, 'endpoint': 'missing'}
    for now in (0, .5, 1, 2, 3, 4, 100):
        report = sampler.sample(config, now)
    assert len(calls) == 3
    assert report['state'] == 'error' and not report['initialized'] and report['peak'] == 0
    assert 'unavailable' in report['error']
    report = sampler.sample({**config, 'generation': 2, 'endpoint': 'speaker'}, 101)
    assert report['state'] == 'active' and report['endpointId'] == 'speaker'
    sampler.close()


def test_mute_pause_and_endpoint_change_drop_old_activity_and_release_exactly_once():
    meters = []
    def factory(endpoint):
        result = Meter(endpoint); meters.append(result); return result
    sampler = _AudioSampler(factory)
    config = {'generation': 1, 'enabled': True, 'endpoint': 'speaker'}
    assert sampler.sample(config, 0)['active']
    meters[0].muted = True
    assert not sampler.sample(config, .1)['active']
    assert sampler.sample(config, .2)['state'] == 'muted'
    paused = sampler.sample({**config, 'generation': 2, 'enabled': False}, .3)
    assert paused['state'] == 'disabled' and paused['peak'] == 0 and meters[0].closes == 1
    sampler.sample({**config, 'generation': 3, 'endpoint': 'headphones'}, .4)
    assert meters[1].device_id == 'headphones'
    meters[1].level = math.nan
    report = sampler.sample({**config, 'generation': 3, 'endpoint': 'headphones'}, 1)
    assert report['peak'] == 0 and not report['active']
    sampler.close(); sampler.close()
    assert meters[1].closes == 1


def test_selected_device_must_be_an_enumerated_active_output_before_getdevice(monkeypatch):
    meter = audio_probe._EndpointMeter.__new__(audio_probe._EndpointMeter)
    meter.endpoint = 'capture-device-id'
    meter.enumerator = ctypes.c_void_p(1)
    meter.list_outputs = lambda: [{'id': 'speaker', 'name': 'Output', 'isDefault': True}]
    monkeypatch.setattr(audio_probe, '_com_call', lambda *a, **k: (_ for _ in ()).throw(AssertionError('GetDevice must not receive a capture ID')))
    with pytest.raises(OSError, match='unavailable'):
        meter.refresh_default()
