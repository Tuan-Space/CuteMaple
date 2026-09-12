from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from pathlib import Path


APP_NAME = "美腻枫"
SETTINGS_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class AnimationSpec:
    duration_ms: int
    playback: str = "loop"
    cycles: int = 0


ANIMATIONS = {
    'idle': AnimationSpec(2920, 'loop', 0),
    'walk_right': AnimationSpec(1000, 'loop', 0),
    'walk_left': AnimationSpec(1000, 'loop', 0),
    'drag_left': AnimationSpec(540, 'loop', 0),
    'drag_right': AnimationSpec(540, 'loop', 0),
    'happy': AnimationSpec(1600, 'one_shot', 0),
    'talk': AnimationSpec(880, 'loop', 0),
    'petting': AnimationSpec(960, 'counted_loop', 2),
    'fall_float': AnimationSpec(1800, 'loop', 0),
    'land': AnimationSpec(1260, 'one_shot', 0),
    'climb_right': AnimationSpec(1000, 'loop', 0),
    'climb_left': AnimationSpec(1000, 'loop', 0),
    'swing_cycle': AnimationSpec(1020, 'counted_loop', 3),
    'swing_idle': AnimationSpec(3870, 'loop', 0),
    'sleep_enter': AnimationSpec(1340, 'one_shot', 0),
    'sleep_loop': AnimationSpec(3100, 'loop', 0),
    'sleep_exit': AnimationSpec(1050, 'one_shot', 0),
}


DIALOGUES = [
    "今天也要开开心心呀～",
    "我会一直陪着你的。",
    "累了就休息一下吧。",
    "记得喝一口水哦！",
    "慢慢来，你已经做得很好啦。",
    "今天有什么开心的事吗？",
    "别忘了活动一下肩膀～",
    "再忙也要照顾好自己呀。",
    "我在这里，放心吧！",
    "给你一个软乎乎的拥抱～",
    "你认真起来的样子真可爱。",
    "要不要看看窗外，放松一下？",
    "今天也辛苦啦！",
    "好心情正在向你靠近～",
    "小小休息，是为了走得更远呀。",
    "你比自己想象中更厉害！",
    "不着急，我们一步一步来。",
    "让我陪你安静一会儿吧。",
    "坐久啦，起来走两步吧～",
    "深呼吸，烦恼先放到一边。",
    "今天也要对自己温柔一点。",
    "悄悄送你一颗小爱心！",
    "事情会一点点变好的。",
    "如果困了，就眯一会儿吧。",
    "我刚刚有乖乖待着哦！",
    "见到你真开心～",
    "别皱眉啦，好运正在路上。",
    "完成一点点也值得庆祝！",
    "你负责努力，我负责陪伴～",
    "愿你今天遇到好多小惊喜！",
]

HAPPY_DIALOGUES = [
    "太棒啦！今天也一定会有好事情发生！",
    "你一出现，我的好心情就满格啦！",
    "给你加满勇气，出发吧！",
    "你真的超厉害，值得大大庆祝！",
    "好耶！这份开心要和你一起分享～",
    "相信自己，你比想象中更闪亮！",
    "今天的你也是能量满满的小太阳！",
    "困难退散，你一定可以的！",
    "为你的每一点进步鼓掌！",
    "好运已经在向你飞奔过来啦！",
    "打起精神，我们一起把今天过得漂亮！",
    "你认真努力的样子，真的特别耀眼！",
    "送你双倍快乐和满满元气！",
    "新的惊喜正在前面等你哦！",
    "不管目标多远，你都在稳稳向前！",
]


@dataclass
class PetSettings:
    settings_schema_version: int = SETTINGS_SCHEMA_VERSION
    paused: bool = False
    scale: float = 1.0
    autostart: bool = False
    roaming_enabled: bool = False
    gaze_enabled: bool = True
    keyboard_enabled: bool = True
    audio_enabled: bool = True
    audio_endpoint: str = "default"
    particles_enabled: bool = True
    last_x: int | None = None
    last_y: int | None = None
    monitor_always_visible: bool = False
    auto_clean_interval_enabled: bool = False
    auto_clean_interval_minutes: int = 60
    auto_clean_memory_enabled: bool = False
    auto_clean_memory_percent: int = 80
    last_clean_timestamp: float = 0.0
    last_clean_attempt_timestamp: float = 0.0
    clean_task_prompted: bool = False
    clean_task_schema_version: int = 0
    clean_task_executable: str = ""

    @classmethod
    def from_mapping(cls, value: object) -> "PetSettings":
        if not isinstance(value, dict):
            return cls()
        paused = value.get("paused", False)
        scale = value.get("scale", 1.0)
        autostart = value.get("autostart", False)
        last_x = value.get("last_x")
        last_y = value.get("last_y")
        interval = value.get("auto_clean_interval_minutes", 60)
        threshold = value.get("auto_clean_memory_percent", 80)
        return cls(
            paused=paused if isinstance(paused, bool) else False,
            scale=float(scale) if isinstance(scale, (int, float)) and 0.6 <= float(scale) <= 1.8 else 1.0,
            autostart=autostart if isinstance(autostart, bool) else False,
            **{name: value.get(name, default) if isinstance(value.get(name, default), bool) else default
               for name, default in (("roaming_enabled", False), ("gaze_enabled", True),
                                     ("keyboard_enabled", True), ("audio_enabled", True),
                                     ("particles_enabled", True))},
            audio_endpoint=value.get("audio_endpoint", "default")
            if isinstance(value.get("audio_endpoint"), str) and 0 < len(value["audio_endpoint"]) <= 512 else "default",
            last_x=last_x if isinstance(last_x, int) else None,
            last_y=last_y if isinstance(last_y, int) else None,
            monitor_always_visible=value.get("monitor_always_visible", False)
            if isinstance(value.get("monitor_always_visible", False), bool) else False,
            auto_clean_interval_enabled=value.get("auto_clean_interval_enabled", False)
            if isinstance(value.get("auto_clean_interval_enabled", False), bool) else False,
            auto_clean_interval_minutes=interval if interval in (15, 30, 60, 120, 240) else 60,
            auto_clean_memory_enabled=value.get("auto_clean_memory_enabled", False)
            if isinstance(value.get("auto_clean_memory_enabled", False), bool) else False,
            auto_clean_memory_percent=threshold if isinstance(threshold, int) and 60 <= threshold <= 95
            and threshold % 5 == 0 else 80,
            last_clean_timestamp=float(value.get("last_clean_timestamp", 0.0))
            if isinstance(value.get("last_clean_timestamp", 0.0), (int, float)) else 0.0,
            last_clean_attempt_timestamp=float(value.get("last_clean_attempt_timestamp", 0.0))
            if isinstance(value.get("last_clean_attempt_timestamp", 0.0), (int, float)) else 0.0,
            clean_task_prompted=value.get("clean_task_prompted", False)
            if isinstance(value.get("clean_task_prompted", False), bool) else False,
            clean_task_schema_version=value.get("clean_task_schema_version", 0)
            if isinstance(value.get("clean_task_schema_version", 0), int) else 0,
            clean_task_executable=value.get("clean_task_executable", "")
            if isinstance(value.get("clean_task_executable", ""), str) else "",
        )


def config_path() -> Path:
    profile = os.environ.get("MEINIFENG_PROFILE_DIRECTORY")
    if profile:
        return Path(profile).resolve() / "settings.json"
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / APP_NAME / "settings.json"


def load_settings(path: Path | None = None) -> PetSettings:
    target = path or config_path()
    try:
        original = target.read_bytes()
    except OSError:
        return PetSettings()
    try:
        value = json.loads(original.decode("utf-8-sig"))
    except (ValueError, TypeError):
        value = {}  # Keep the damaged bytes in the same migration backup.
    settings = PetSettings.from_mapping(value)
    version = value.get("settings_schema_version", 0) if isinstance(value, dict) else 0
    if not isinstance(version, int) or version < SETTINGS_SCHEMA_VERSION:
        # Preserve the original before changing permissions-related preferences.
        # This never edits Run keys, tasks, or security settings.
        settings.autostart = False
        settings.auto_clean_interval_enabled = False
        settings.auto_clean_memory_enabled = False
        settings.clean_task_prompted = False
        settings.clean_task_schema_version = 0
        settings.clean_task_executable = ""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = target.with_name(f"{target.stem}.pre-v3.{stamp}.json")
        try:
            with backup.open("xb") as stream:
                stream.write(original)
            save_settings(settings, target)
            from diagnostics import event
            event("settings_migrated", schema=SETTINGS_SCHEMA_VERSION, backup=backup,
                  automaticSystemIntegrationDisabled=True)
        except OSError as exc:
            # Even a read-only profile must not reactivate old implicit consent.
            from diagnostics import event
            event("settings_migration_not_saved", message=str(exc))
    return settings


def save_settings(settings: PetSettings, path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def resource_root() -> Path:
    return Path(__file__).resolve().parent


def is_compiled() -> bool:
    return "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def stable_application_path() -> Path:
    """Resolve the installed launcher, never a onefile extraction payload."""
    if not is_compiled():
        return (resource_root() / "main.py").resolve()
    argument = Path(sys.argv[0])
    containing = getattr(globals().get("__compiled__"), "containing_dir", None)
    candidate = argument.resolve()
    # Nuitka standalone may expose containing_dir as the parent of its .dist
    # directory. The observed absolute argv0 is the authoritative launcher.
    if not argument.is_absolute() and not candidate.is_file() and containing:
        candidate = (Path(containing) / argument.name).resolve()
    if not argument.name or any(part.lower().startswith("onefile_") for part in candidate.parts):
        raise ValueError("Cannot register a temporary extraction executable; use the installed directory application")
    if candidate.suffix.lower() != ".exe":
        raise ValueError("The installed application launcher must be an executable")
    return candidate


def startup_command() -> str:
    application = stable_application_path()
    if is_compiled():
        return f'"{application}"'
    return f'"{Path(sys.executable).resolve()}" "{application}"'


def set_autostart(enabled: bool) -> bool:
    if os.name != "nt" or os.environ.get("MEINIFENG_DISABLE_AUTOSTART") == "1":
        return False
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        command = startup_command() if enabled else None
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        from diagnostics import event
        event("autostart_updated", enabled=enabled, command=command)
        return True
    except (OSError, ValueError) as exc:
        from diagnostics import event
        event("autostart_update_failed", enabled=enabled, message=str(exc))
        return False


def choose_dialogue(rng: random.Random | None = None) -> str:
    return (rng or random).choice(DIALOGUES)


def choose_happy_dialogue(rng: random.Random | None = None) -> str:
    return (rng or random).choice(HAPPY_DIALOGUES)


def auto_cleanup_due(settings: PetSettings, memory_percent: float, now: float, cycle_started: float) -> bool:
    baseline = settings.last_clean_timestamp or cycle_started
    elapsed = max(0.0, now - baseline)
    if elapsed < 600 or now - settings.last_clean_attempt_timestamp < 600:
        return False
    interval_due = settings.auto_clean_interval_enabled and elapsed >= settings.auto_clean_interval_minutes * 60
    memory_due = settings.auto_clean_memory_enabled and memory_percent >= settings.auto_clean_memory_percent
    return interval_due or memory_due
