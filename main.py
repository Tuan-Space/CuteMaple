"""Establish diagnostics before importing Qt or native providers."""
from __future__ import annotations
import sys
import os
from pathlib import Path


def main() -> int:
    if '--request-quit' in sys.argv:
        from journal_install import request_quit
        return request_quit()
    if "--verify-desktop" in sys.argv or "--verify-cleanup" in sys.argv or "--verify-journal" in sys.argv:
        # Parse only the explicit profile before logging/native imports. The QA
        # module owns the rest of its argument validation.
        try:
            index = sys.argv.index("--profile")
            profile = Path(sys.argv[index + 1])
            real_profile = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "美腻枫"
            if (not profile.is_absolute() or profile.resolve().is_relative_to(real_profile.resolve())
                    or real_profile.resolve().is_relative_to(profile.resolve())):
                return 2
            os.environ["MEINIFENG_PROFILE_DIRECTORY"] = str(profile.resolve())
        except (ValueError, IndexError, OSError):
            return 2
    from diagnostics import event, exception, initialize
    initialize("audio" if "--audio-probe-child" in sys.argv else "app")
    try:
        if "--audio-probe-child" in sys.argv:
            from audio_probe import audio_child_main
            return int(audio_child_main())
        if any(flag in sys.argv for flag in ("--install-clean-task", "--memory-clean-helper")):
            event("obsolete_helper_entry_rejected")
            return 2
        if "--verify-desktop" in sys.argv:
            from desktop_check import run
            return int(run(sys.argv[sys.argv.index("--verify-desktop") + 1:]))
        if "--verify-journal" in sys.argv:
            from journal_check import run
            return int(run(sys.argv[sys.argv.index("--verify-journal") + 1:]))
        if "--verify-cleanup" in sys.argv:
            from cleanup_check import run
            return int(run(sys.argv[sys.argv.index("--verify-cleanup") + 1:]))
        if "--verify-live2d" in sys.argv:
            from runtime_check import run
            result = run(sys.argv[sys.argv.index("--verify-live2d") + 1:])
        else:
            from pet_app import run
            result = run()
        event("exit", code=result)
        return int(result)
    except Exception as exc:
        exception("startup_or_runtime_failure", exc)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
