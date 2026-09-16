import json
import sqlite3
from datetime import datetime
from journal_store import JournalStore
from journal_recurrence import Rule
import pytest

def test_library_special_characters(tmp_path):
    s=JournalStore(tmp_path/'中文 # 资料',create=True);s.save_note('保存','数据');s.backup_daily();s.close()

def test_failed_transaction_does_not_consume_alert(tmp_path):
    s=JournalStore(tmp_path/'library',create=True);s.save_event('原子回应','todo','',Rule(datetime.now().isoformat(timespec='seconds')));s.tick();alert=s.pending()[0]
    s.db.execute('PRAGMA query_only=ON')
    with pytest.raises(sqlite3.OperationalError):s.respond(alert['id'],'done')
    assert s.pending()[0]['id']==alert['id'];s.db.execute('PRAGMA query_only=OFF');assert s.respond(alert['id'],'done');s.close()

def test_restart_keeps_unanswered_and_snoozed(tmp_path):
    folder=tmp_path/'library';s=JournalStore(folder,create=True);s.save_event('重启','todo','',Rule(datetime.now().isoformat(timespec='seconds')));s.tick();alert=s.pending()[0];s.respond(alert['id'],'snooze',600);s.close()
    s=JournalStore(folder);s.tick();assert not s.pending();assert s.rows("SELECT * FROM alerts WHERE state='pending'")[0]['id']==alert['id'];s.close()

def test_purging_one_note_preserves_other_attachment(tmp_path):
    s=JournalStore(tmp_path/'library',create=True);source=tmp_path/'attachment.bin';source.write_bytes(b'unique-content')
    a=s.save_note('A','');b=s.save_note('B','');fa=s.attach(a,source);fb=s.attach(b,source);s.trash_note(a);s.purge_note(a)
    assert s.attachment_path(fb['relative']).read_bytes()==b'unique-content';assert not s.attachment_path(fa['relative']).exists();s.close()

def test_completed_occurrence_survives_edit(tmp_path):
    s=JournalStore(tmp_path/'library',create=True);r=Rule(datetime.now().isoformat(timespec='seconds'),period='daily');identity=s.save_event('原题','todo','',r);s.tick();s.respond(s.pending()[0]['id'],'done');s.save_event('新题','todo','',r,identity);s.tick()
    completed=s.rows("SELECT * FROM occurrences WHERE state='completed'");assert len(completed)==1 and completed[0]['title']=='原题';s.close()
