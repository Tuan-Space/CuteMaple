"""Local-only QtWebEngine host; all model execution stays in the browser renderer.

The bridge exchanges JSON, never Python source. Importing this module does not
construct a browser or register a scheme, until an application explicitly creates its renderer.
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote

from PySide6.QtCore import QObject, Signal, Slot

SCHEME = b"cutemaple"
APP_URL = "cutemaple://app/web/dist/index.html"
MODEL_URL = "cutemaple://app/assets/live2d/Maple/Maple.model3.json"
_scheme_registered = False


def resolve_asset(root: Path, url_path: str) -> Path | None:
    """Restrict the browser to bundled frontend/model data, including symlinks."""
    relative = unquote(url_path).replace("\\", "/").lstrip("/")
    if "\x00" in relative:
        return None
    target = (root / relative).resolve()
    allowed = ((root / "web" / "dist").resolve(), (root / "assets" / "live2d").resolve())
    return target if any(target.is_relative_to(folder) for folder in allowed) and target.is_file() else None


def register_live2d_scheme() -> None:
    """Call before QApplication, as required by QtWebEngine custom schemes."""
    global _scheme_registered
    if _scheme_registered:
        return
    from PySide6.QtWebEngineCore import QWebEngineUrlScheme
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    flags = QWebEngineUrlScheme.Flag
    scheme.setFlags(flags.SecureScheme | flags.LocalScheme | flags.LocalAccessAllowed |
                    flags.CorsEnabled | flags.FetchApiAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)
    _scheme_registered = True


class PetBridge(QObject):
    command = Signal(str)
    event = Signal(dict)

    @Slot(str)
    def report(self, raw: str) -> None:
        if len(raw) > 65536:
            return
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return
        if isinstance(value, dict) and value.get("type") in {
            "connected", "ready", "error", "cycle", "finished", "geometry", "frame-ready", "marker", "climb-rest",
        }:
            self.event.emit(value)

    def send(self, command_type: str, **values) -> None:
        self.command.emit(json.dumps({"type": command_type, **values}, ensure_ascii=False, allow_nan=False))


class Live2DHost(QObject):
    event = Signal(dict)

    def __init__(self, parent, root: Path) -> None:
        super().__init__(parent)
        from PySide6.QtCore import QFile, QIODevice, QTimer, QUrl, Qt
        from PySide6.QtGui import QColor
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import (
            QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
            QWebEngineUrlRequestInterceptor, QWebEngineUrlSchemeHandler,
            QWebEngineUrlRequestJob,
        )
        from PySide6.QtWebEngineWidgets import QWebEngineView

        class Assets(QWebEngineUrlSchemeHandler):
            def requestStarted(handler, job):
                url = job.requestUrl()
                path = resolve_asset(root, url.path()) if url.host() == "app" else None
                if path is None:
                    job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                    return
                source = QFile(str(path), job)
                if not source.open(QIODevice.OpenModeFlag.ReadOnly):
                    job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                    return
                mime = {".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm",
                        ".json": "application/json", ".moc3": "application/octet-stream"}.get(path.suffix)
                job.reply((mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream").encode(), source)

        class LocalOnly(QWebEngineUrlRequestInterceptor):
            def interceptRequest(interceptor, request):
                url = request.requestUrl()
                if not ((url.scheme() == "cutemaple" and url.host() == "app") or
                        url.scheme() in {"qrc", "data", "blob", "about"}):
                    request.block(True)

        self.profile = QWebEngineProfile(self)
        self.assets = Assets(self.profile)
        self.interceptor = LocalOnly(self.profile)
        self.profile.installUrlSchemeHandler(SCHEME, self.assets)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.downloadRequested.connect(lambda item: item.cancel())
        self.view = QWebEngineView(parent)
        self.view.setObjectName("live2dRenderer")
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._generation = 0
        self._state = "new"
        self.ready = False
        self.page = self.channel = self.bridge = None
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self._fail("Live2D 模型载入超时", self._generation))
        self.view.hide()

    @property
    def generation(self):
        return self._generation

    def presented(self, token: int) -> None:
        if self.ready:
            self.timeout.stop()
            self._log("renderer_presented", token=token)

    def _log(self, name, **fields):
        try:
            from diagnostics import event
            event(name, generation=self._generation, phase=self._state, **fields)
        except ImportError:
            pass

    def load(self) -> None:
        if self._state == "stopped":
            return
        from PySide6.QtCore import QUrl, QUrlQuery
        from PySide6.QtGui import QColor
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
        self._generation += 1
        generation = self._generation
        self._state, self.ready = "loading", False
        previous = self.page
        self.page = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.page)
        if previous is not None:
            previous.deleteLater()
        page = self.page
        page.setBackgroundColor(QColor(0, 0, 0, 0))
        page.featurePermissionRequested.connect(lambda origin, feature: page.setFeaturePermission(
            origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser))
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        self.channel = QWebChannel(page)
        self.bridge = PetBridge(self.channel)
        self.channel.registerObject("petBridge", self.bridge)
        page.setWebChannel(self.channel)
        self.bridge.event.connect(self._report)
        page.renderProcessTerminated.connect(lambda status, code: self._terminated(generation, status, code))
        page.renderProcessPidChanged.connect(lambda pid: self._log("renderer_pid", pid=pid, load=generation))
        page.loadFinished.connect(lambda ok: None if ok else self._fail("无法载入本地 Live2D 页面", generation))
        url = QUrl(APP_URL)
        query = QUrlQuery(url)
        query.addQueryItem("generation", str(generation))
        url.setQuery(query)
        self.timeout.start(20000)
        self._log("renderer_load")
        self.view.load(url)

    def _terminated(self, generation, status, code):
        self._log("renderer_terminated", load=generation, status=str(status), code=code)
        self._fail(f"Live2D 渲染进程已停止（{status.name}: {code}）", generation)

    def _fail(self, message: str, generation=None) -> None:
        if (generation is not None and generation != self._generation) or self._state in {"failed", "stopped"}:
            return
        self._state, self.ready = "failed", False
        if self.bridge is not None:
            self.bridge.send("stop", generation=self._generation)
        self.timeout.stop()
        self.view.hide()
        self._log("renderer_error", message=message)
        self.event.emit({"type": "error", "message": message, "generation": self._generation})

    def _report(self, event: dict) -> None:
        if event.get("generation") != self._generation or self._state in {"failed", "stopped"}:
            return
        kind = event.get("type")
        if kind == "ready" and self._state != "loading":
            return
        if kind == "connected" and self._state == "loading":
            self.bridge.send("configure", modelUrl=MODEL_URL, generation=self._generation)
        elif kind == "ready" and self._state == "loading":
            if event.get("backend") != "live2d":
                self._fail("模型未通过 Live2D 载入验证")
                return
            self._state, self.ready = "ready", True
            self._log("renderer_ready")
        elif kind == "error":
            self._fail(str(event.get("message", "Live2D error")))
            return
        elif self._state != "ready":
            return
        self.event.emit(event)

    def send(self, command_type: str, **values) -> None:
        if self.bridge is not None and self._state == "ready":
            self.bridge.send(command_type, generation=self._generation, **values)

    def stop(self) -> None:
        if self._state == "stopped":
            return
        self._state, self.ready = "stopped", False
        self._generation += 1
        self.timeout.stop()
        self.view.stop()
        self.view.hide()
        if self.page is not None:
            self.page.deleteLater()
        self.view.deleteLater()
        self.profile.deleteLater()
