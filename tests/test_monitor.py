from __future__ import annotations

from types import SimpleNamespace

import resource_monitor
import memory_cleaner
from monitor_ui import clamp_rect
from pet_core import PetSettings, auto_cleanup_due
from PySide6.QtCore import QRect


def test_format_bytes():
    assert resource_monitor.format_bytes(0, True) == "0 B/s"
    assert resource_monitor.format_bytes(1536) == "1.5 KB"
    assert resource_monitor.format_bytes(-5) == "0 B"


def test_network_delta_and_counter_reset(monkeypatch):
    values = [
        {"A": SimpleNamespace(bytes_recv=1000, bytes_sent=500)},
        {"A": SimpleNamespace(bytes_recv=2024, bytes_sent=1012)},
        {"A": SimpleNamespace(bytes_recv=2, bytes_sent=1)},
    ]
    clock = iter((10.0, 11.0, 12.0))
    monkeypatch.setattr(resource_monitor.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(resource_monitor.psutil, "net_io_counters", lambda **_kwargs: values.pop(0))
    monkeypatch.setattr(resource_monitor.psutil, "net_if_stats", lambda: {"A": SimpleNamespace(isup=True)})
    sampler = resource_monitor.NetworkSampler(smoothing=1.0)
    first = sampler.sample()
    assert first.download_bps == 1024 and first.upload_bps == 512
    reset = sampler.sample()
    assert reset.download_bps == 0 and reset.upload_bps == 0


def test_settings_compatibility_and_clamp():
    settings = PetSettings.from_mapping({"monitor_always_visible": True,
                                         "auto_clean_interval_enabled": True,
                                         "auto_clean_interval_minutes": 30,
                                         "auto_clean_memory_enabled": True,
                                         "auto_clean_memory_percent": 85})
    assert settings.monitor_always_visible and settings.auto_clean_interval_minutes == 30
    assert settings.auto_clean_memory_percent == 85
    assert clamp_rect(QRect(900, -500, 380, 455), QRect(-600, -200, 1000, 800)) == QRect(20, -200, 380, 455)


def test_auto_cleanup_or_triggers_and_cooldown():
    settings = PetSettings(auto_clean_interval_enabled=True, auto_clean_interval_minutes=15,
                           auto_clean_memory_enabled=True, auto_clean_memory_percent=80,
                           last_clean_timestamp=1000)
    assert not auto_cleanup_due(settings, 95, 1599, 0)
    assert auto_cleanup_due(settings, 81, 1600, 0)
    assert auto_cleanup_due(settings, 20, 1900, 0)




def test_deep_cleanup_uses_modified_and_full_standby_lists(monkeypatch):
    import reference_cleanup_helper as memory_cleaner
    snapshots = iter((
        SimpleNamespace(physical_available=4_000, load_percent=74),
        SimpleNamespace(physical_available=7_000, load_percent=38),
    ))
    commands = []

    def fake_nt_set(info_class, payload):
        value = payload.value if hasattr(payload, "value") else None
        commands.append((info_class, value))
        return (False, "blocked") if (info_class, value) == (80, 2) else (True, "ok")

    monkeypatch.setattr(memory_cleaner, "memory_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(memory_cleaner, "_enable_privilege", lambda _name: True)
    monkeypatch.setattr(memory_cleaner, "_nt_set", fake_nt_set)
    monkeypatch.setattr(memory_cleaner, "_trim_process_working_sets", lambda: (True, "fallback"))
    monkeypatch.setattr(memory_cleaner, "_shrink_system_file_cache", lambda: (True, "ok"))
    monkeypatch.setattr(memory_cleaner.time, "sleep", lambda _seconds: None)

    result = memory_cleaner.perform_cleanup()
    assert (80, 3) in commands
    assert (80, 4) in commands
    assert "low_priority_standby" not in result["steps"]
    assert result["steps"]["process_working_sets_fallback"]["ok"]
    assert result["available_increase"] == 3_000
