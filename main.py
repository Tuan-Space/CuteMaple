import sys
import traceback

from memory_cleaner import helper_main, install_task

from pet_app import run
from pet_core import config_path


if __name__ == "__main__":
    try:
        if "--install-clean-task" in sys.argv:
            operation_id = ""
            if "--install-operation" in sys.argv:
                index = sys.argv.index("--install-operation")
                if index + 1 < len(sys.argv):
                    operation_id = sys.argv[index + 1]
            raise SystemExit(install_task(operation_id))
        if "--memory-clean-helper" in sys.argv:
            raise SystemExit(helper_main())
        raise SystemExit(run())
    except SystemExit:
        raise
    except Exception:
        try:
            crash_log = config_path().with_name("crash.log")
            crash_log.parent.mkdir(parents=True, exist_ok=True)
            crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise
