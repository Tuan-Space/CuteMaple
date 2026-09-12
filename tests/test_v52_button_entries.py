import concurrent.futures
from test_live2d_integration import pet
import pytest
from PySide6.QtGui import QAction
@pytest.mark.parametrize('entry',['details','monitor','menu'])
def test_actual_qt_cleanup_signal_reaches_async_backend_once(pet,monkeypatch,entry):
    calls=[];pending=concurrent.futures.Future()
    monkeypatch.setattr(pet._install_executor,'submit',lambda *a:calls.append(a) or pending)
    monkeypatch.setattr(pet,'_show_cleanup_feedback',lambda *_:None)
    if entry=='details':trigger=pet.details_panel.clean_button.click
    elif entry=='monitor':trigger=pet.monitor_button.doubleClicked.emit
    else:
        menu=pet._create_context_menu()
        actions=menu.findChildren(QAction)
        trigger=next(action for action in actions if action.text()=='立即清理内存').trigger
    trigger();trigger()
    assert len(calls)==1 and pet._cleanup_future is pending
    assert pet.details_panel.clean_button.text()=='查看清理进度'
