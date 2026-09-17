"""Rich-note commands must survive storage, and playback must release its UI."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
import subprocess,sys,textwrap
from pathlib import Path
from PySide6.QtCore import QEvent,Qt,QUrl,QMimeData,QEventLoop,QTimer
from PySide6.QtGui import QTextCursor,QTextFormat,QTextListFormat,QFontDatabase,QFont,QImage
from PySide6.QtMultimedia import QMediaPlayer,QMediaRecorder
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from journal_editors import NoteEditor
from journal_store import JournalStore


@pytest.fixture
def editor(tmp_path):
    app=QApplication.instance() or QApplication([])
    # Windows' offscreen platform does not enumerate system fonts. Qt's Markdown
    # writer queries resolved QFontInfo, so register real styles for this test.
    for name in ('arial.ttf','arialbd.ttf','arialbi.ttf','ariali.ttf'):
        font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
        if font.exists():QFontDatabase.addApplicationFont(str(font))
    store=JournalStore(tmp_path/'library',create=True)
    edit=NoteEditor(store);edit.preview.setFont(QFont('Arial',10));edit.resize(570,660);edit.show();app.processEvents()
    yield app,store,edit
    edit.timer.stop();edit.media.dismiss_playback();edit.close();app.removeEventFilter(edit);edit.deleteLater();app.processEvents();store.close()


def select(edit,text):
    cursor=edit.preview.document().find(text);assert not cursor.isNull();edit.preview.setTextCursor(cursor)


def reload_note(store,edit):
    assert edit.save();note=store.notes()[0];assert edit.load(note);return note['body']


def test_inline_commands_round_trip_and_original_backup(editor):
    _,store,edit=editor
    original='重点 斜体 删除';store.save_note('格式',original);edit.load(store.notes()[0])
    for text,command in [('重点',edit.toggle_bold),('斜体',edit.toggle_italic),('删除',edit.toggle_strike)]:select(edit,text);command()
    body=reload_note(store,edit)
    assert '**重点**' in body and '*斜体*' in body and '~~删除~~' in body
    assert store.rows('SELECT body FROM note_originals')[0]['body']==original
    for text,button in [('重点',edit.bold),('斜体',edit.italic),('删除',edit.strike)]:
        select(edit,text);edit.update_format_state();assert button.isChecked()
    edit.mode.setCurrentIndex(1);assert all(not group.isVisible() for group in edit.rich_tool_groups);assert edit.edit.toPlainText()==body
    edit.mode.setCurrentIndex(0);assert not edit.preview.literal


@pytest.mark.parametrize('source_first,modes,autosave',[(False,[1],False),(False,[1,0],False),(False,[1,0,1],True),(True,[0],False),(True,[0,1,0],True)])
def test_mode_switch_preserves_pending_original_backup(editor,source_first,modes,autosave):
    _,store,edit=editor;original='# 原稿\n\n尚未修改的正文';store.save_note('原稿',original);edit.load(store.notes()[0])
    if source_first:
        edit.mode.setCurrentIndex(1);edit.edit.setPlainText(original+'\n\n*新增的内容*')
    else:
        select(edit,'尚未修改的正文');edit.toggle_bold()
    for mode in modes:edit.mode.setCurrentIndex(mode)
    assert edit.body_dirty and edit.dirty
    if autosave:
        loop=QEventLoop();QTimer.singleShot(650,loop.quit);loop.exec();assert not edit.dirty
    else:assert edit.save()
    assert store.rows('SELECT body FROM note_originals')[0]['body']==original
    first_saved=store.notes()[0]['body'];assert first_saved!=original
    # A later load/save must preserve the first original instead of replacing it.
    edit.load(store.notes()[0]);edit.mode.setCurrentIndex(1);edit.edit.appendPlainText('再次修改');edit.mode.setCurrentIndex(0);assert edit.save()
    assert store.rows('SELECT body FROM note_originals')[0]['body']==original


def test_switching_views_without_edit_keeps_original_body_exact(editor):
    _,store,edit=editor;original='## 原稿\n\n*未修改*\n';store.save_note('原稿',original);edit.load(store.notes()[0])
    edit.mode.setCurrentIndex(1);edit.mode.setCurrentIndex(0);edit.title.setText('只改标题');assert edit.save()
    assert store.notes()[0]['body']==original and not store.rows('SELECT body FROM note_originals')


def test_heading_semantics_multi_block_and_one_step_undo(editor):
    _,store,edit=editor
    edit.preview.setPlainText('第一段\n第二段\n第三段');edit.preview.selectAll();edit.set_heading(2)
    assert edit.current_body().count('## ')==3
    edit.preview.undo();assert '## ' not in edit.current_body()
    edit.preview.redo();body=reload_note(store,edit);assert body.count('## ')==3
    block=edit.preview.document().begin()
    while block.isValid():assert block.blockFormat().headingLevel()==2;block=block.next()
    select(edit,'第二段');edit.set_heading(0);body=reload_note(store,edit)
    assert '## 第一段' in body and '## 第二段' not in body and '## 第三段' in body


def test_heading_selection_excludes_following_unselected_paragraph(editor):
    _,_,edit=editor;edit.preview.setPlainText('第一段\n第二段')
    cursor=edit.preview.textCursor();cursor.setPosition(0);cursor.setPosition(4,QTextCursor.KeepAnchor);edit.preview.setTextCursor(cursor);edit.set_heading(1)
    assert edit.preview.document().begin().blockFormat().headingLevel()==1
    assert edit.preview.document().begin().next().blockFormat().headingLevel()==0


def test_lists_nesting_numbering_and_quote_round_trip(editor):
    _,store,edit=editor;edit.preview.setPlainText('一级\n子项\n末项');edit.preview.selectAll();edit.toggle_list(QTextListFormat.ListDisc)
    select(edit,'一级');assert not edit.indent.isEnabled();edit.indent_list(1);assert edit.preview.textCursor().block().textList().format().indent()==1
    select(edit,'子项');edit.indent_list(1);assert edit.outdent.isEnabled()
    body=reload_note(store,edit);assert '- 一级' in body and '  - 子项' in body and '- 末项' in body
    select(edit,'子项');assert edit.preview.textCursor().block().textList().format().indent()==2
    edit.indent_list(-1);assert edit.preview.textCursor().block().textList().format().indent()==1
    edit.preview.selectAll();edit.toggle_list(QTextListFormat.ListDecimal);body=reload_note(store,edit)
    assert '1.  一级' in body and '2.  子项' in body and '3.  末项' in body
    edit.preview.selectAll();edit.toggle_list(QTextListFormat.ListDecimal);edit.toggle_quote();body=reload_note(store,edit)
    assert '> 一级' in body and '> 子项' in body and '> 末项' in body
    assert edit.preview.document().begin().blockFormat().intProperty(QTextFormat.BlockQuoteLevel)==1


def test_link_is_semantic_and_undoable(editor,monkeypatch):
    _,store,edit=editor;edit.preview.setPlainText('官网');select(edit,'官网')
    monkeypatch.setattr('journal_editors.QInputDialog.getText',lambda *a,**kw:('https://example.org/path',True))
    edit.insert_link();assert not edit.preview.currentCharFormat().isAnchor();edit.preview.insertPlainText('继续记录')
    body=reload_note(store,edit);assert '[官网](https://example.org/path)继续记录' in body
    select(edit,'继续记录');assert not edit.preview.currentCharFormat().isAnchor()


def test_pasted_image_and_list_undo_leave_plain_insertion_format(editor):
    _,store,edit=editor;edit.preview.setPlainText('开头');edit.preview.moveCursor(QTextCursor.End)
    mime=QMimeData();picture=QImage(8,8,QImage.Format_RGB32);picture.fill(0xff6633);mime.setImageData(picture);edit.preview.insertFromMimeData(mime)
    loop=QEventLoop();timer=QTimer();timer.timeout.connect(lambda:loop.quit() if edit.file_job is None else None);timer.start(10);QTimer.singleShot(3000,loop.quit);loop.exec();timer.stop()
    assert edit.file_job is None and len(store.attachments(edit.identity))==1
    assert not edit.preview.currentCharFormat().isImageFormat() and not edit.preview.currentCharFormat().isAnchor()
    edit.preview.insertPlainText('图片之后');body=reload_note(store,edit);assert '![' in body and 'attachments/' in body and '图片之后' in body
    edit.preview.setPlainText('第一项\n第二项');edit.preview.selectAll();edit.toggle_list(QTextListFormat.ListDisc);edit.preview.undo();edit.preview.moveCursor(QTextCursor.End);edit.preview.insertPlainText('\n普通段落')
    assert not edit.preview.textCursor().block().textList();assert '- 普通段落' not in edit.current_body()


@pytest.mark.parametrize('platform',['offscreen','windows'])
def test_production_font_chinese_emphasis_round_trip(platform):
    if platform=='windows' and os.name!='nt':pytest.skip('Windows platform validation')
    code=textwrap.dedent('''
        from tempfile import TemporaryDirectory
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFontInfo
        from journal_ui import JournalWindow
        from journal_store import JournalStore
        from journal_design import load_journal_fonts
        app=QApplication([])
        with TemporaryDirectory() as folder:
            store=JournalStore(folder,create=True);window=JournalWindow(store,None)
            window.show();app.processEvents();edit=window.note_editor
            edit.preview.setPlainText('中文加粗 中文斜体 中文删除')
            assert edit.preview.font().family()==load_journal_fonts()['body']
            for text,command in [('中文加粗',edit.toggle_bold),('中文斜体',edit.toggle_italic),('中文删除',edit.toggle_strike)]:
                edit.preview.setTextCursor(edit.preview.document().find(text));command()
            assert edit.save();body=store.notes()[0]['body']
            assert '**中文加粗**' in body and '*中文斜体*' in body and '~~中文删除~~' in body,repr(body)
            edit.load(store.notes()[0])
            for text,method in [('中文加粗','bold'),('中文斜体','italic'),('中文删除','strikeOut')]:
                fmt=edit.preview.document().find(text).charFormat().font()
                assert getattr(fmt,method)() and getattr(QFontInfo(fmt),method)()
            window.close();store.close()
    ''')
    env=dict(os.environ,QT_QPA_PLATFORM=platform,PYTHONIOENCODING='utf-8')
    result=subprocess.run([sys.executable,'-B','-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_complex_markdown_disables_lossy_commands(editor):
    _,store,edit=editor;body='# 原稿\n\n<div>保留 HTML</div>\n\n[^a]: 脚注';store.save_note('原稿',body);edit.load(store.notes()[0])
    assert edit.preview.literal and edit.format_hint.isVisible()
    assert not edit.bold.isEnabled() and not edit.heading.isEnabled()
    edit.preview.selectAll();edit.toggle_bold();edit.toggle_italic();edit.set_heading(2);edit.toggle_list(QTextListFormat.ListDisc)
    edit.title.setText('仅修改标题');assert edit.save();assert store.notes()[0]['body']==body


def test_reload_starts_at_top_and_reenable_restores_commands(editor):
    app,store,edit=editor;store.save_note('长笔记','# 开头\n\n'+('后面的内容\n\n'*80)+'> 最后引用')
    edit.load(store.notes()[0]);app.processEvents()
    assert edit.preview.textCursor().position()==0 and edit.preview.verticalScrollBar().value()==0
    assert edit.heading.currentIndex()==1 and not edit.quote.isChecked()
    edit.setEnabled(False);edit.setEnabled(True);assert edit.bold.isEnabled() and edit.heading.isEnabled()


def test_keyboard_shortcuts_and_toolbar_wrap(editor):
    app,_,edit=editor;edit.preview.setPlainText('快捷键');select(edit,'快捷键');edit.activateWindow();edit.preview.setFocus();app.processEvents()
    QTest.keyClick(edit.preview,Qt.Key_B,Qt.ControlModifier);QTest.keyClick(edit.preview,Qt.Key_I,Qt.ControlModifier)
    assert edit.preview.currentCharFormat().fontItalic() and edit.bold.isChecked()
    edit.resize(330,660);app.processEvents()
    assert edit.width()==330
    assert edit.format_actions.layout().heightForWidth(280)>32
    for button in edit.format_buttons:assert edit.format_actions.rect().contains(button.geometry())


class FakePlayer:
    def __init__(self):self.state=QMediaPlayer.StoppedState;self.url=QUrl.fromLocalFile('recording.wav');self.position=50
    def playbackState(self):return self.state
    def stop(self):self.state=QMediaPlayer.StoppedState
    def pause(self):self.state=QMediaPlayer.PausedState
    def play(self):self.state=QMediaPlayer.PlayingState
    def setSource(self,url):self.url=url
    def source(self):return self.url
    def setPosition(self,position):self.position=position


def playback(edit):
    media=edit.media;media.player=FakePlayer();media.show();media.play_panel.show();return media


def test_completed_playback_closes_on_outside_click_and_releases_file(editor):
    app,_,edit=editor;media=playback(edit);media.media_status_changed(QMediaPlayer.EndOfMedia);app.processEvents()
    assert media.playback_completed and media.play.text()=='重新播放'
    QTest.mouseClick(media.play_time,Qt.LeftButton);assert not media.play_panel.isHidden()
    QTest.mouseClick(edit.title,Qt.LeftButton)
    assert media.play_panel.isHidden() and media.isHidden() and media.player.source().isEmpty()


def test_active_playback_survives_outside_click_and_completed_window_deactivation(editor):
    app,_,edit=editor;media=playback(edit);media.player.state=QMediaPlayer.PlayingState
    QTest.mouseClick(edit.title,Qt.LeftButton);assert not media.play_panel.isHidden()
    media.media_status_changed(QMediaPlayer.EndOfMedia);app.sendEvent(edit,QEvent(QEvent.WindowDeactivate))
    assert media.play_panel.isHidden() and media.player.source().isEmpty()


def test_replay_resets_completion_and_load_clears_previous_player(editor):
    _,_,edit=editor;media=playback(edit);media.media_status_changed(QMediaPlayer.EndOfMedia);media.toggle_play()
    assert not media.playback_completed and media.player.position==0 and media.player.state==QMediaPlayer.PlayingState
    assert edit.load();assert media.play_panel.isHidden() and media.player.source().isEmpty()


def test_completed_playback_dismissal_preserves_recording_controls(editor):
    _,_,edit=editor;media=playback(edit)
    class Recording:
        def recorderState(self):return QMediaRecorder.RecordingState
    media.recorder=Recording();media.compact_stop.show();media.media_status_changed(QMediaPlayer.EndOfMedia)
    QTest.mouseClick(edit.title,Qt.LeftButton)
    assert media.play_panel.isHidden() and media.isVisible() and media.compact_stop.isVisible() and media.status.isVisible()
