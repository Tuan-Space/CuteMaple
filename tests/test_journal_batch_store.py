"""Batch selection, transactional trash operations, and attachment retention."""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from journal_recurrence import MilestoneRule, Rule, civil_timestamp
from journal_store import JournalStore


@pytest.fixture
def library(tmp_path):
    now=[civil_timestamp(datetime(2026,9,17,9),'Asia/Shanghai')]
    store=JournalStore(tmp_path/'library',create=True,clock=lambda:now[0])
    yield store,now
    store.close()


def rule_at(stamp,period='once'):
    return Rule(datetime.fromtimestamp(stamp,ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')[:19],period=period)


def deleted_plan(store,now,title,offset=3600,kind='todo'):
    identity=store.save_event(title,kind,'',rule_at(now+offset))
    store.archive_event(identity)
    return identity


def deleted_occurrence(store,identity,due,*,state='pending',title='单次标题'):
    with store.db:
        store.db.execute('INSERT INTO occurrences VALUES(?,?,?,?,?)',(identity,due,state,due+30 if state=='completed' else None,title))
    assert store.trash_occurrence(identity,due)
    return ('occurrence',identity,due)


def test_note_selection_covers_unloaded_search_and_trash_uses_deletion_order(library):
    store,now=library
    identities=[]
    for index in range(145):
        now[0]+=1
        identities.append(store.save_note('标题'+str(index),'匹配正文'))
    others=[store.save_note('不匹配','另一些内容') for _ in range(8)]
    assert len(store.notes('匹配正文'))==100
    assert store.note_ids('匹配正文')==list(reversed(identities))
    # Delete older notes last: trash order must not follow their creation time.
    for identity in reversed(identities[:130]):
        now[0]+=1
        store.trash_note(identity)
    for identity in others[:3]:store.trash_note(identity)
    matches=store.note_ids('匹配正文',trash=True)
    assert matches==identities[:130]
    pages=[row['id'] for offset in range(0,130,37) for row in store.notes('匹配正文',True,offset,37)]
    assert pages==matches
    result=store.restore_notes(matches+[matches[0],'absent'])
    assert result['restored']==matches and result['missing']==['absent']
    assert len(store.note_ids('匹配正文'))==145
    assert set(store.note_ids(trash=True))==set(others[:3])


@pytest.mark.parametrize('operation',['restore','purge'])
def test_note_batch_failure_rolls_back_every_note_and_skips_cleanup(library,monkeypatch,operation):
    store,_=library
    identities=[store.save_note(title,'original body') for title in ('A','B')]
    for identity in identities:store.trash_note(identity)
    called=[]
    monkeypatch.setattr(store,'collect_attachments',lambda:called.append(True))
    trigger=('BEFORE UPDATE OF deleted ON notes' if operation=='restore' else 'BEFORE DELETE ON notes')
    store.db.execute(f"CREATE TRIGGER fail_second {trigger} WHEN OLD.id='{identities[1]}' BEGIN SELECT RAISE(ABORT,'second note failed'); END")
    with pytest.raises(sqlite3.IntegrityError,match='second note failed'):
        (store.restore_notes if operation=='restore' else store.purge_notes)(identities)
    assert set(store.note_ids(trash=True))==set(identities)
    assert all(row['body']=='original body' for row in store.notes(trash=True))
    assert not called and not store.db.in_transaction


def test_active_note_purge_is_explicit_committed_and_cascades(library,tmp_path,monkeypatch):
    store,_=library
    identity=store.save_note('当前编辑笔记','正文')
    source=tmp_path/'attachment.txt';source.write_text('attachment',encoding='utf-8')
    attached=store.attach(identity,source)
    with store.db:store.db.execute('INSERT INTO note_originals VALUES(?,?)',(identity,'old body'))
    calls=[];collect=store.collect_attachments
    def inspect_committed_cleanup():
        assert not store.db.in_transaction
        observer=sqlite3.connect(store.path)
        try:assert observer.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==0
        finally:observer.close()
        calls.append(True);collect()
    monkeypatch.setattr(store,'collect_attachments',inspect_committed_cleanup)
    assert store.purge_notes([identity])['missing']==[identity]
    assert store.note_ids()==[identity] and not calls
    result=store.purge_notes([identity,identity,'absent'],allow_active=True)
    assert result['deleted']==[identity] and result['missing']==['absent']
    assert calls==[True] and result['cleanup_warning'] is None
    assert not store.rows('SELECT * FROM note_originals') and not store.rows('SELECT * FROM note_files')
    assert not store.attachment_path(attached['relative']).exists()


def test_bulk_cleanup_keeps_cross_note_original_and_backup_references(library,tmp_path,monkeypatch):
    store,_=library
    source=tmp_path/'file.bin';source.write_bytes(b'keep references')
    owners=[store.save_note(str(index),'') for index in range(4)]
    files=[store.attach(identity,source) for identity in owners]
    backup=sqlite3.connect(store.root/'backups'/'retained.sqlite3')
    try:store.db.backup(backup)
    finally:backup.close()
    # Keep only one owner's link in the retained snapshot so each protection
    # route is independently necessary for its corresponding attachment.
    backup=sqlite3.connect(store.root/'backups'/'retained.sqlite3')
    with backup:backup.execute('DELETE FROM note_files WHERE note_id!=?',(owners[2],))
    backup.close()
    reader=store.save_note('正文引用','![引用]('+files[0]['relative']+')')
    original_reader=store.save_note('保留原文','新正文')
    with store.db:store.db.execute('INSERT INTO note_originals VALUES(?,?)',(original_reader,'![原文]('+files[1]['relative']+')'))
    for identity in owners:store.trash_note(identity)
    calls=[];collect=store.collect_attachments
    def once():calls.append(True);collect()
    monkeypatch.setattr(store,'collect_attachments',once)
    result=store.purge_notes(owners)
    assert result['deleted']==owners and result['cleanup_warning'] is None and calls==[True]
    assert set(store.note_ids())=={reader,original_reader}
    assert all(store.attachment_path(row['relative']).exists() for row in files[:3])
    assert not store.attachment_path(files[3]['relative']).exists()
    assert {row['id'] for row in store.rows('SELECT * FROM files')}=={row['id'] for row in files[:3]}


def test_cleanup_failure_reports_committed_delete_and_preserves_files(library,tmp_path):
    store,_=library
    source=tmp_path/'file.bin';source.write_bytes(b'backup inspection must fail closed')
    identity=store.save_note('待删除','');attached=store.attach(identity,source);store.trash_note(identity)
    (store.root/'backups'/'unreadable.sqlite3').write_bytes(b'not a sqlite database')
    result=store.purge_notes([identity])
    assert result['deleted']==[identity] and result['cleanup_warning']
    assert '附件清理未完成' in result['cleanup_warning']
    assert not store.note_ids(trash=True) and not store.db.in_transaction
    observer=sqlite3.connect(store.path)
    try:assert observer.execute('SELECT COUNT(*) FROM notes WHERE id=?',(identity,)).fetchone()[0]==0
    finally:observer.close()
    assert store.attachment_path(attached['relative']).read_bytes()==source.read_bytes()


def test_unified_reminder_trash_filters_orders_and_pages_over_100_keys(library):
    store,now=library
    start=now[0]
    plan=store.save_event('改名后的计划','schedule','',rule_at(start+86400,'daily'))
    keys=[]
    for index in range(120):
        # Deliberately tie some deleted times; due and entity ID break the ties.
        now[0]=start+index//3
        keys.append(deleted_occurrence(store,plan,start+(index+1)*86400,title='匹配快照 '+str(index)))
    deleted_occurrence(store,plan,start+150*86400,title='其他快照')
    event_ids=[]
    for index in range(20):
        now[0]=start+index*2
        event_ids.append(deleted_plan(store,now[0],'匹配快照 '+str(index),kind='schedule'))
    deleted_plan(store,now[0],'匹配快照 错误类型',kind='todo')
    all_rows=store.reminder_trash('匹配快照','schedule',limit=None)
    assert len(all_rows)==140 and len(store.reminder_trash('匹配快照','schedule'))==100
    all_keys=store.reminder_trash_keys('匹配快照','schedule')
    assert len(set(all_keys))==140
    assert set(all_keys)==set(keys)|{('event',identity) for identity in event_ids}
    assert [row['deleted'] for row in all_rows]==sorted((row['deleted'] for row in all_rows),reverse=True)
    paged=[row['_key'] for offset in range(0,140,17) for row in store.reminder_trash('匹配快照','schedule',offset,17)]
    assert paged==all_keys
    assert all(row['title'].startswith('匹配快照') for row in all_rows)
    assert len(store.reminder_trash_keys('匹配快照','todo'))==1
    store.archive_event(plan)
    assert len(store.reminder_trash_keys('匹配快照','schedule'))==20
    assert ('event',plan) in store.reminder_trash_keys('改名后的计划')


def test_bulk_restore_preserves_completed_and_leaves_overdue_unfinished_in_trash(library):
    store,now=library
    future=deleted_plan(store,now[0],'未来计划')
    expired=deleted_plan(store,now[0],'过期未完成',offset=-3600)
    completed=store.save_event('已完成计划','todo','',rule_at(now[0]-7200))
    with store.db:store.db.execute('UPDATE events SET archived=1 WHERE id=?',(completed,))
    store.archive_event(completed)
    plan=store.save_event('同计划的不同次数','schedule','',rule_at(now[0],'daily'))
    first=deleted_occurrence(store,plan,now[0]+86400)
    second=deleted_occurrence(store,plan,now[0]+2*86400)
    finished=deleted_occurrence(store,plan,now[0]-86400,state='completed',title='原来已完成的标题')
    overdue=deleted_occurrence(store,plan,now[0]-2*86400,state='missed')
    missing=('occurrence',plan,now[0]+99*86400)
    result=store.restore_reminders([first,('event',future),second,finished,overdue,('event',completed),('event',expired),missing,first])
    assert set(result['restored'])=={first,second,finished,('event',future),('event',completed)}
    assert set(result['pending'])=={overdue,('event',expired)} and result['missing']==[missing]
    assert store.rows('SELECT archived FROM events WHERE id=?',(completed,))[0]['archived']==1
    assert store.rows('SELECT deleted FROM events WHERE id=?',(expired,))[0]['deleted'] is not None
    restored=store.rows('SELECT * FROM occurrences WHERE event_id=? AND due=?',(plan,finished[2]))[0]
    assert restored['state']=='completed' and restored['title']=='原来已完成的标题' and restored['answered']==finished[2]+30
    assert store.excluded(plan,overdue[2])
    alerts=store.rows("SELECT due FROM alerts WHERE event_id=? AND state='pending'",(plan,))
    assert {row['due'] for row in alerts}=={first[2],second[2]}


def test_bulk_restore_parent_order_and_missing_result(library):
    store,now=library
    plan=store.save_event('先恢复计划','todo','',rule_at(now[0]+86400,'daily'))
    child=deleted_occurrence(store,plan,now[0]+86400)
    store.archive_event(plan)
    assert store.restore_reminders([child])['pending']==[child]
    result=store.restore_reminders([child,('event',plan),('event','absent')])
    assert set(result['restored'])=={child,('event',plan)}
    assert result['missing']==[('event','absent')] and not result['pending']


@pytest.mark.parametrize('limit_kind',['count','end','milestone'])
def test_ended_unfinished_rules_stay_pending_while_completed_rules_restore(library,limit_kind):
    store,now=library
    start=rule_at(now[0]-3*86400).start
    if limit_kind=='milestone':
        expired=MilestoneRule(start,yearly=False,days=(1,2))
    else:
        limits={'count':2} if limit_kind=='count' else {'end':rule_at(now[0]-86400).start}
        expired=Rule(start,period='daily',**limits)
    unfinished=store.save_event('已到期但未完成','anniversary' if limit_kind=='milestone' else 'todo','',expired)
    completed=store.save_event('已到期且完成','anniversary' if limit_kind=='milestone' else 'todo','',expired)
    with store.db:store.db.execute('UPDATE events SET archived=1 WHERE id=?',(completed,))
    store.archive_event(unfinished);store.archive_event(completed)
    before=store.rows('SELECT * FROM events WHERE id=?',(unfinished,))[0]
    result=store.restore_reminders([('event',unfinished),('event',completed)])
    assert result['pending']==[('event',unfinished)]
    assert result['restored']==[('event',completed)]
    assert store.rows('SELECT * FROM events WHERE id=?',(unfinished,))[0]==before
    assert store.rows('SELECT archived,deleted FROM events WHERE id=?',(completed,))[0]=={'archived':1,'deleted':None}
    assert not store.pending()


def test_bulk_reminder_restore_rolls_back_rows_and_audit(library,monkeypatch):
    store,now=library
    first=deleted_plan(store,now[0],'A');second=deleted_plan(store,now[0],'B')
    original_audit=store.audit
    def failing_audit(entity,action,detail):
        original_audit(entity,action,detail)
        if entity==second and action=='event_restored':raise RuntimeError('second restore failed')
    monkeypatch.setattr(store,'audit',failing_audit)
    with pytest.raises(RuntimeError,match='second restore failed'):
        store.restore_reminders([('event',first),('event',second)])
    assert set(store.reminder_trash_keys())=={('event',first),('event',second)}
    assert not store.rows("SELECT * FROM audit WHERE action='event_restored'")
    assert not store.db.in_transaction


def test_bulk_reminder_purge_failure_rolls_back_parent_children_and_metadata(library):
    store,now=library
    first=store.save_event('A','todo','',rule_at(now[0]+86400,'daily'))
    child=deleted_occurrence(store,first,now[0]+86400)
    store.archive_event(first);store.set_setting('event_input_lunar:'+first,True)
    second=deleted_plan(store,now[0],'B')
    store.db.execute(f"CREATE TRIGGER fail_second BEFORE DELETE ON events WHEN OLD.id='{second}' BEGIN SELECT RAISE(ABORT,'second plan failed'); END")
    with pytest.raises(sqlite3.IntegrityError,match='second plan failed'):
        store.purge_reminders([child,('event',first),('event',second)])
    assert set(store.reminder_trash_keys())=={('event',first),('event',second)}
    exclusion=store.rows('SELECT * FROM occurrence_exclusions WHERE event_id=?',(first,))[0]
    assert exclusion['deleted'] is not None and json.loads(exclusion['snapshot'])['title']=='单次标题'
    assert store.setting('event_input_lunar:'+first) is True
    assert store.rows('SELECT * FROM audit WHERE entity=?',(first,))
    assert not store.db.in_transaction


def test_bulk_occurrence_purge_keeps_exclusion_across_restart(library):
    store,now=library
    initial=now[0]
    identity=store.save_event('每日安排','todo','',rule_at(initial,'daily'))
    first=deleted_occurrence(store,identity,initial)
    second=deleted_occurrence(store,identity,initial+86400)
    result=store.purge_reminders([first,first,('occurrence',identity,initial+99*86400)])
    assert result['deleted']==[first] and len(result['missing'])==1
    tomb=store.rows('SELECT * FROM occurrence_exclusions WHERE event_id=? AND due=?',(identity,initial))[0]
    assert tomb['deleted'] is None and tomb['snapshot'] is None and store.excluded(identity,initial)
    assert store.reminder_trash_keys()==[second]
    folder=store.root;store.close()
    reopened=JournalStore(folder,clock=lambda:now[0])
    try:
        with reopened.db:reopened.db.execute('UPDATE events SET cursor=?,next_check=0 WHERE id=?',(initial-.001,identity))
        reopened.tick()
        assert not reopened.rows('SELECT * FROM occurrences WHERE event_id=? AND due=?',(identity,initial))
        assert not reopened.pending()
        assert reopened.reminder_trash_keys()==[second]
    finally:reopened.close()


def test_bulk_plan_purge_removes_owned_rows_without_touching_other_plan(library):
    store,now=library
    removed=store.save_event('删除整项','todo','',rule_at(now[0],'daily'))
    kept=store.save_event('保留整项','schedule','',rule_at(now[0],'daily'))
    store.tick()
    removed_child=deleted_occurrence(store,removed,now[0]+86400)
    kept_child=deleted_occurrence(store,kept,now[0]+86400)
    untouched_child=deleted_occurrence(store,kept,now[0]+2*86400)
    store.archive_event(removed);store.set_setting('event_input_lunar:'+removed,True)
    result=store.purge_reminders([('event',removed),removed_child,kept_child,('event',kept),('event','absent')])
    assert set(result['deleted'])=={('event',removed),removed_child,kept_child}
    assert set(result['missing'])=={('event',kept),('event','absent')}
    assert not store.rows('SELECT * FROM events WHERE id=?',(removed,))
    for table in ('alerts','occurrences','occurrence_exclusions'):
        assert not store.rows(f'SELECT * FROM {table} WHERE event_id=?',(removed,))
    assert not store.rows('SELECT * FROM audit WHERE entity=? OR entity LIKE ?',(removed,removed+':%'))
    assert store.setting('event_input_lunar:'+removed) is None
    assert store.rows('SELECT * FROM events WHERE id=?',(kept,))
    assert store.excluded(kept,kept_child[2])
    assert store.reminder_trash_keys()==[untouched_child]
