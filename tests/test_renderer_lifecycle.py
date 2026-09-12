import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from live2d_host import Live2DHost, PetBridge


class Host(Live2DHost):
    def __init__(self):
        QObject.__init__(self)
        self._generation, self._state, self.ready = 7, 'loading', False
        self.timeout = QTimer(self)
        self.view = QWidget()
        self.bridge = PetBridge(self)
        self.messages = []
        self.event.connect(self.messages.append)


def test_old_page_and_late_ready_cannot_revive_failed_host():
    app = QApplication.instance() or QApplication([])
    host = Host()
    host._report({'type': 'ready', 'backend': 'live2d', 'generation': 6})
    assert not host.ready
    host._fail('timeout', 7)
    host._report({'type': 'ready', 'backend': 'live2d', 'generation': 7})
    assert not host.ready and host._state == 'failed'
    assert len(host.messages) == 1
    host._fail('late load failure', 6)
    assert len(host.messages) == 1
    host.view.close()


def test_ready_is_single_delivery_and_old_termination_does_not_fail_new_load():
    app = QApplication.instance() or QApplication([])
    host = Host()
    host._report({'type': 'ready', 'backend': 'live2d', 'generation': 7})
    host._report({'type': 'ready', 'backend': 'live2d', 'generation': 7})
    host._fail('previous page crashed', 6)
    assert host.ready and len(host.messages) == 1
    host._state = 'stopped'
    host._report({'type': 'error', 'generation': 7, 'message': 'late'})
    assert len(host.messages) == 1
    host.view.close()
