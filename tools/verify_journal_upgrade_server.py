"""Isolated production save/shutdown server used by installer acceptance."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
p=argparse.ArgumentParser();p.add_argument('--profile',required=True,type=Path);a=p.parse_args()
root=a.profile.resolve()
if root.exists():raise ValueError('Use a new isolated profile')
root.mkdir(parents=True)
os.environ['MEINIFENG_PROFILE_DIRECTORY']=str(root)
from PySide6.QtCore import QTimer
from journal_store import JournalStore
from journal_service import JournalService
from journal_install import install_server
from pet_app import run
store=JournalStore(root/'library',create=True)
note_id=store.save_note('升级保存测试','升级前')
def observer(app,pet):
    pet.journal=JournalService(store,pet)
    pet.journal.window.updates.check=lambda *_:None
    pet._upgrade_server=install_server(pet)
    editor=pet.journal.window.note_editor
    editor.load(store.rows('SELECT * FROM notes WHERE id=?',(note_id,))[0])
    editor.edit.setPlainText('安装器请求退出时保存的内容')
    editor.timer.stop()
    (root/'ready.json').write_text(json.dumps({'noteId':note_id,'listening':pet._upgrade_server is not None}),encoding='utf-8')
    QTimer.singleShot(300000,pet.quit_app)
code=run(observer=observer)
reopened=JournalStore(root/'library')
try:
    saved=reopened.notes()[0]['body']
    (root/'result.json').write_text(json.dumps({'exitCode':code,'saved':saved,'passed':saved=='安装器请求退出时保存的内容'},ensure_ascii=False),encoding='utf-8')
finally:reopened.close()
raise SystemExit(code)
