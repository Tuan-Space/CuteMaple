"""Measure event-loop latency while real scheduler and file jobs run in isolation."""
import os,sys,time,json,tempfile,argparse
from pathlib import Path
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from journal_store import JournalStore
from journal_queries import QueryRunner,schedule_data
from journal_jobs import FileJob
from journal_service import JournalService
from benchmark_journal import fixture

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);a=parser.parse_args()
    app=QApplication([]);now=1789704000.;errors=[];finished=[];gaps=[];last=[time.perf_counter()]
    timer=QTimer();timer.setInterval(5)
    def beat():
        current=time.perf_counter();gaps.append(max(0,(current-last[0])*1000-5));last[0]=current
    timer.timeout.connect(beat)
    with tempfile.TemporaryDirectory(prefix='journal-background-') as folder:
        root=Path(folder);store=JournalStore(root/'library',create=True,clock=lambda:now);fixture(store,10000)
        with store.db:store.db.execute('UPDATE events SET cursor=?,next_check=? WHERE CAST(id AS INTEGER)<100',(now-2*86400,now))
        for n in range(4):
            path=root/f'attachment-{n}.bin';path.write_bytes(bytes(range(256))*16384);store.attach(str(n),path)
        class Scheduler:
            _apply_tick=JournalService._apply_tick
            _after_tick=lambda self:finished.append('schedule')
        scheduler=Scheduler();scheduler.store=store;scheduler.bubble=SimpleNamespace(refresh=lambda:None);scheduler.window=SimpleNamespace(notice=errors.append)
        runner=QueryRunner(store.path);start=time.perf_counter();last[0]=start;timer.start()
        def schedule_done(result,error):
            if error:errors.append(error);finished.append('schedule')
            else:scheduler._apply_tick(now,result)
        runner.submit('schedule',now,lambda reader,cancel:schedule_data(reader,now,cancel),schedule_done)
        jobs=[];phases={}
        def launch(name,work):
            job=FileJob();jobs.append(job);began=time.perf_counter()
            def done(value,error):
                if error:errors.append(error)
                phases[name]=(time.perf_counter()-began)*1000;finished.append(name)
            job.finished.connect(done);job.start(work)
        launch('dailyBackupAndReferenceScan',store.backup_snapshot)
        launch('export',lambda:store.export_backup(root/'export.zip'))
        deadline=time.perf_counter()+30
        while len(finished)<3 and time.perf_counter()<deadline:app.processEvents();time.sleep(.001)
        app.processEvents();timer.stop();runner.close()
        result=dict(records=10000,healthRecords=30000,attachmentBytes=4*4*1024*1024,totalMs=(time.perf_counter()-start)*1000,phasesMs=phases,eventLoopP95Ms=sorted(gaps)[int(len(gaps)*.95)] if gaps else 0,eventLoopMaxMs=max(gaps,default=0),pendingNotifications=len(store.pending()),errors=errors,finished=finished)
        result['passed']=len(finished)==3 and not errors and result['eventLoopP95Ms']<=50 and result['eventLoopMaxMs']<=150
        store.close();a.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
