"""Read-only previews, mutation guards, compact tools and deletion handoff."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtCore import QEventLoop,QMimeData,QPoint,QRect,QSize,QTimer,QUrl,Qt
from PySide6.QtGui import QIcon,QImage,QTextCursor
from PySide6.QtWidgets import QApplication
from journal_editors import NoteEditor
from journal_store import JournalStore
from monitor_ui import ThemeBinding


@pytest.fixture
def editor(tmp_path):
    app=QApplication.instance() or QApplication([])
    store=JournalStore(tmp_path/'library',create=True)
    edit=NoteEditor(store);edit.theme=ThemeBinding(edit,'journal');edit.resize(540,680);edit.show();app.processEvents()
    yield app,store,edit
    edit.timer.stop();edit.media.pending_finalize=False;edit.media.dismiss_playback();edit.close();app.removeEventFilter(edit);edit.deleteLater();app.processEvents();store.close()


def load_note(store,edit,text='一段正文',title='当前笔记'):
    identity=store.save_note(title,text);edit.load(next(n for n in store.notes() if n['id']==identity));return identity


def settle_job(edit):
    loop=QEventLoop();timer=QTimer();timer.timeout.connect(lambda:loop.quit() if edit.file_job is None else None);timer.start(10);QTimer.singleShot(3000,loop.quit);loop.exec();timer.stop()
    assert edit.file_job is None


def test_readonly_preview_retains_copy_and_existing_attachment_play(editor,tmp_path,monkeypatch):
    app,store,edit=editor;identity=load_note(store,edit)
    audio=tmp_path/'录音.wav';audio.write_bytes(b'isolated playback routing fixture');store.attach(identity,audio);edit.refresh_files()
    assert edit.set_read_only(True)
    assert edit.isEnabled() and edit.title.isReadOnly() and edit.preview.isReadOnly() and edit.edit.isReadOnly()
    assert not edit.format_toolbar.isVisible() and not edit.status.isVisible() and edit.files.isVisible()
    edit.title.selectAll();edit.title.copy();assert app.clipboard().text()=='当前笔记'
    edit.preview.selectAll();edit.preview.copy();assert app.clipboard().text()=='一段正文'
    played=[];monkeypatch.setattr(edit.media,'load',lambda path:played.append(path))
    edit.open_file(edit.files.item(0));assert len(played)==1 and played[0].is_file()
    assert edit.set_read_only(False) and not edit.title.isReadOnly() and edit.format_toolbar.isVisible()


def test_readonly_blocks_native_input_and_programmatic_mutation_handlers(editor,tmp_path):
    from PySide6.QtTest import QTest
    app,store,edit=editor;identity=load_note(store,edit);assert edit.set_read_only(True)
    source=tmp_path/'不应复制.txt';source.write_text('保留来源',encoding='utf-8')
    image=QImage(8,8,QImage.Format_RGB32);image.fill(Qt.red)
    text=QMimeData();text.setText('不应插入');files=QMimeData();files.setUrls([QUrl.fromLocalFile(str(source))]);picture=QMimeData();picture.setImageData(image)
    dropped=[];edit.preview.filesDropped.connect(lambda paths:dropped.extend(paths))
    for mime in (text,files,picture):edit.preview.insertFromMimeData(mime);edit.edit.insertFromMimeData(mime)
    edit.preview.selectAll();edit.toggle_bold();edit.toggle_italic();edit.set_heading(2);edit.insert_markdown('不应插入')
    edit.add_files([str(source)],remove_sources=True);edit.paste_image(image);assert edit.show_recording() is False
    with pytest.raises(PermissionError):edit.prepare_recording()
    QTest.keyClick(edit.preview,Qt.Key_B,Qt.ControlModifier);QTest.keyClick(edit.preview,Qt.Key_X,Qt.ControlModifier)
    edit.mode.setCurrentIndex(1);edit.edit.selectAll();QTest.keyClick(edit.edit,Qt.Key_Delete);edit.edit.insertFromMimeData(text)
    assert edit.save() and store.notes()[0]['body']=='一段正文' and not edit.dirty
    assert not dropped and not store.attachments(identity) and not list((store.root/'recordings').iterdir()) and source.exists()


def test_entering_readonly_saves_pending_edit_and_original(editor):
    _,store,edit=editor;identity=load_note(store,edit);edit.preview.selectAll();edit.toggle_bold()
    assert edit.set_read_only(True)
    assert '**一段正文**' in store.notes()[0]['body']
    assert store.rows('SELECT body FROM note_originals WHERE note_id=?',(identity,))[0]['body']=='一段正文'
    assert not edit.timer.isActive() and not edit.dirty


def test_save_failure_blocks_readonly_and_finish_then_success_clears_status(editor,monkeypatch):
    _,store,edit=editor;load_note(store,edit);edit.preview.insertPlainText('修改')
    original_save=store.save_note
    def fail(*args):raise OSError('隔离写入失败')
    monkeypatch.setattr(store,'save_note',fail)
    assert not edit.finish() and not edit.set_read_only(True)
    assert not edit.read_only and edit.dirty and edit.status.isVisible() and '隔离写入失败' in edit.status.text()
    monkeypatch.setattr(store,'save_note',original_save);assert edit.save()
    assert not edit.status.isVisible() and edit.status.text()=='' and not edit.dirty


def test_media_finalizing_blocks_readonly_and_identity_clear(editor,monkeypatch):
    _,store,edit=editor;identity=load_note(store,edit);edit.media.pending_finalize=True
    monkeypatch.setattr(edit.media,'finish',lambda:None)
    assert not edit.set_read_only(True) and not edit.clear_note_identity() and edit.identity==identity and not edit.read_only
    edit.media.pending_finalize=False


def test_recording_attachment_handoff_blocks_finish_and_clears_status_after_retry(editor,monkeypatch):
    _,store,edit=editor;load_note(store,edit)
    monkeypatch.setattr(edit.media,'finish',lambda:setattr(edit,'file_job',object()))
    assert not edit.finish() and not edit.set_read_only(True) and not edit.clear_note_identity()
    assert '附件正在保存' in edit.status.text() and edit.status.isVisible()
    edit.file_job=None;monkeypatch.setattr(edit.media,'finish',lambda:None)
    assert edit.finish() and not edit.status.isVisible() and not edit.status.text()


def test_successful_delete_detaches_autosave_and_undo_without_resurrection(editor):
    _,store,edit=editor;identity=load_note(store,edit);edit.mode.setCurrentIndex(1);edit.edit.insertPlainText('先保存');assert edit.finish()
    store.trash_note(identity);store.purge_note(identity)
    # Even a stale queued edit/save cannot recreate the deleted identity.
    edit.edit.insertPlainText('待取消的保存');assert edit.timer.isActive()
    assert edit.clear_note_identity();edit.timer.timeout.emit();assert edit.save()
    assert edit.identity is None and not edit.dirty and not edit.timer.isActive() and not store.notes()
    assert edit.title.text()=='' and edit.current_body()=='' and not edit.edit.document().isUndoAvailable() and not edit.preview.document().isUndoAvailable()


@pytest.mark.parametrize('readonly',[False,True])
def test_native_context_menu_requests_only_current_note_deletion(editor,readonly):
    _,store,edit=editor;identity=load_note(store,edit);edit.set_read_only(readonly);requests=[];edit.permanentDeleteRequested.connect(requests.append)
    for widget in (edit.title,edit.preview,edit.edit):
        widget.selectAll();menu=edit.build_text_context_menu(widget)
        actions=[a for a in menu.actions() if a.objectName()=='permanentDeleteNote']
        assert len(actions)==1 and len(menu.actions())>2 and actions[0].isEnabled()
        actions[0].trigger();menu.deleteLater()
    assert requests==[identity]*3 and len(store.notes())==1
    edit.clear_note_identity();menu=edit.build_text_context_menu(edit.preview);assert not any(a.objectName()=='permanentDeleteNote' for a in menu.actions());menu.deleteLater()


def test_source_keeps_common_tools_and_routes_undo_redo_to_source(editor):
    app,store,edit=editor;load_note(store,edit,'原文');edit.mode.setCurrentIndex(1);edit.edit.moveCursor(QTextCursor.End);edit.edit.insertPlainText('新增');app.processEvents()
    assert all(not group.isVisible() for group in edit.rich_tool_groups)
    assert edit.plus.isVisible() and edit.undo.isVisible() and edit.redo.isVisible() and edit.mode.isVisible()
    assert edit.undo.isEnabled();edit.undo.click();assert edit.edit.toPlainText()=='原文'
    assert edit.redo.isEnabled();edit.redo.click();assert edit.edit.toPlainText()=='原文新增'
    assert edit.preview.toPlainText()=='原文';assert edit.save();assert store.notes()[0]['body']=='原文新增'


def test_source_attachment_job_keeps_progress_and_blocks_identity_clear(editor,tmp_path):
    _,store,edit=editor;identity=load_note(store,edit);edit.mode.setCurrentIndex(1)
    source=tmp_path/'清单.txt';source.write_text('附件',encoding='utf-8');edit.add_files([str(source)])
    assert edit.file_job is not None and not edit.plus.isEnabled() and edit.status.isVisible()
    assert not edit.clear_note_identity() and not edit.set_read_only(True)
    settle_job(edit)
    assert 'attachments/' in edit.edit.toPlainText() and len(store.attachments(identity))==1
    assert edit.plus.isEnabled() and not edit.status.isVisible() and not edit.read_only


def test_vector_controls_and_group_wrapping_are_font_independent(editor):
    app,_,edit=editor;edit.resize(330,680);app.processEvents()
    assert edit.width()==330
    for name in ('plus','undo','redo'):
        button=getattr(edit,name);assert button.text()=='' and not button.icon().isNull() and button.accessibleName()
        for mode in (QIcon.Normal,QIcon.Disabled):
            image=button.icon().pixmap(QSize(24,24),mode).toImage()
            assert any(image.pixelColor(x,y).alpha()>0 for x in range(image.width()) for y in range(image.height()))
    for button in edit.format_buttons:
        rect=QRect(button.mapTo(edit.format_actions,QPoint(0,0)),button.size());assert edit.format_actions.rect().contains(rect)
    for group in edit.rich_tool_groups:
        assert edit.format_actions.rect().contains(group.geometry())
    assert edit.format_actions.height()>30
