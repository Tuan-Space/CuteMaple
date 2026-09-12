"""Early, bounded local diagnostics; never records keyboard text or audio."""
from __future__ import annotations

import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import traceback
from datetime import datetime, timezone

_logger = logging.getLogger("cutemaple.diagnostics")
_logger.propagate = False
_logger.setLevel(logging.INFO)
_fault_stream = None
_initialized = False
_qt_handler = None
_qt_previous = None


def event(name: str, **fields) -> None:
    if not _initialized:
        return
    try:
        _logger.info(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                 "event": name, "pid": os.getpid(), **fields},
                                ensure_ascii=False, default=str))
    except Exception:
        pass


def exception(name: str, exc: BaseException) -> None:
    event(name, error=type(exc).__name__, message=str(exc),
          traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


def initialize(component: str = "app") -> Path | None:
    global _fault_stream, _initialized
    if _initialized:
        return None
    try:
        profile = os.environ.get("MEINIFENG_PROFILE_DIRECTORY")
        folder = (Path(profile).resolve() / "logs" if profile else
                  Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "美腻枫/logs")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{component}-{os.getpid()}.jsonl"
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)
        _initialized = True
        _fault_stream = (folder / f"{component}-{os.getpid()}.fault.log").open("a", encoding="utf-8")
        faulthandler.enable(file=_fault_stream, all_threads=True)
    except (OSError, RuntimeError, AttributeError):
        path = None
    previous_sys = sys.excepthook
    previous_thread = threading.excepthook

    def unhandled(kind, value, tb):
        exception("unhandled_python_exception", value.with_traceback(tb))
        if sys.stderr is not None:
            previous_sys(kind, value, tb)

    def thread_unhandled(args):
        exception("unhandled_thread_exception", args.exc_value)
        if sys.stderr is not None:
            previous_thread(args)

    sys.excepthook = unhandled
    threading.excepthook = thread_unhandled
    event("entry", component=component, launcher=sys.argv[0] if sys.argv else "",
          executable=sys.executable, python=sys.version.split()[0], cwd=os.getcwd())
    return path


def install_qt_logging() -> None:
    global _qt_handler, _qt_previous
    if _qt_handler is not None:
        return
    from PySide6.QtCore import qInstallMessageHandler

    def message(kind, context, text):
        event("qt_message", severity=str(kind), category=getattr(context, "category", None), message=text)
        if _qt_previous is not None:
            _qt_previous(kind, context, text)

    _qt_handler = message
    _qt_previous = qInstallMessageHandler(message)
