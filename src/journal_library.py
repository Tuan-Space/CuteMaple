"""First-run library selection; bootstrap contains paths, never journal content."""
from __future__ import annotations
import json
import os
import shutil
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QMessageBox
from monitor_ui import ThemeBinding
from journal_store import JournalStore


def bootstrap_path():
    return Path(os.environ.get('APPDATA',Path.home()/'AppData/Roaming'))/'美腻枫'/'library.json'


def select_library(parent=None):
    dialog=QDialog(parent); dialog.setWindowTitle('选择你的手账'); dialog.resize(500,360)
    dialog.theme=ThemeBinding(dialog,'journal')
    layout=QVBoxLayout(dialog);layout.setContentsMargins(24,24,24,24);layout.setSpacing(16)
    title=QLabel('让日子有个安放的地方'); title.setObjectName('title');title.setWordWrap(True); layout.addWidget(title)
    info=QLabel('提醒、笔记、录音和附件都保存在你选择的文件夹。\n以后换电脑时，可以带走整个文件夹。');info.setObjectName('pageDescription'); info.setWordWrap(True); layout.addWidget(info)
    card=QWidget();card.setObjectName('surface');card.setAttribute(Qt.WA_StyledBackground);actions=QVBoxLayout(card);actions.setContentsMargins(16,16,16,16);actions.setSpacing(12);layout.addWidget(card)
    selected=[]
    def choose(create):
        path=QFileDialog.getExistingDirectory(dialog,'选择资料库文件夹')
        if not path: return
        root=Path(path)
        from pet_core import resource_root
        if root.resolve().is_relative_to(resource_root().resolve()):
            QMessageBox.information(dialog,'请把资料放在程序之外','资料库应独立于程序安装目录，避免升级或卸载影响个人内容。');return
        if create and (root/'journal.sqlite3').exists():
            QMessageBox.information(dialog,'已有资料库','请选择“打开已有资料库”，不会覆盖原有内容。'); return
        if create and any(root.iterdir()):
            root=root/'美腻枫手账'
            if root.exists() and any(root.iterdir()):
                QMessageBox.information(dialog,'请选择独立文件夹','此位置已经有“美腻枫手账”文件夹，请打开已有资料库或换一个位置。'); return
        try:
            store=JournalStore(root,create=create)
            selected.append(store); dialog.accept()
        except Exception as error:
            QMessageBox.warning(dialog,'暂时不能打开',str(error))
    for text,create in [('新建资料库',True),('打开已有资料库',False)]:
        button=QPushButton(text);button.setObjectName('primary' if create else '');button.setCursor(Qt.PointingHandCursor); button.clicked.connect(lambda _,c=create:choose(c)); actions.addWidget(button)
    caption=QLabel('选择程序安装目录以外的位置，升级时也能安心保留记录。');caption.setObjectName('muted');caption.setWordWrap(True);layout.addWidget(caption)
    accepted=dialog.exec()==QDialog.Accepted;dialog.deleteLater()
    return selected[0] if accepted else None


def open_startup_library():
    explicit=os.environ.get('MEINIFENG_PROFILE_DIRECTORY')
    if explicit:
        # Existing QA entrypoints deliberately bypass the production setup wizard.
        return None
    pointer=bootstrap_path()
    store=None
    if pointer.exists():
        try:
            metadata=json.loads(pointer.read_text(encoding='utf-8'))
            store=JournalStore(metadata['path'])
        except Exception as error:
            QMessageBox.warning(None,'资料库需要重新选择',str(error)+'\n原来的资料不会被覆盖。')
    if store is None:
        store=select_library()
    if store is None:
        return None
    previous=pointer.parent/'settings.json'
    if previous.exists() and not (store.root/'settings.json').exists():
        shutil.copy2(previous,store.root/'settings.json')
    save_library_pointer(store.root)
    os.environ['MEINIFENG_PROFILE_DIRECTORY']=str(store.root)
    return store


def save_library_pointer(root):
    pointer=bootstrap_path(); pointer.parent.mkdir(parents=True,exist_ok=True)
    temporary=pointer.with_suffix('.tmp')
    temporary.write_text(json.dumps({'path':str(root)},ensure_ascii=False),encoding='utf-8')
    temporary.replace(pointer)
