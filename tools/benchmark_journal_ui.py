"""Offscreen UI timings and event-loop latency on an isolated mixed library."""
import os,sys,json,time,tempfile,argparse,statistics
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer,QEventLoop,QEvent,QObject
from journal_store import JournalStore
from journal_ui import JournalWindow
from journal_design import journal_font
from benchmark_journal import fixture

def memory_bytes():
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('peak','working','peakPaged','paged','peakNonPaged','nonPaged','pagefile','peakPagefile')]
    value=Counters();value.cb=ctypes.sizeof(value)
    call=ctypes.windll.psapi.GetProcessMemoryInfo;call.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    call(wintypes.HANDLE(-1),ctypes.byref(value),value.cb);return value.working

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--count',type=int,default=1000);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--stress',action='store_true');a=parser.parse_args()
    app=QApplication([]);app.setFont(journal_font());gaps=[];last=[time.perf_counter()];phase=['initial'];outliers=[]
    timer=QTimer();timer.setInterval(5)
    def heartbeat():
        now=time.perf_counter();gap=max(0,(now-last[0])*1000-5);gaps.append(gap);last[0]=now
        if gap>50:outliers.append(dict(phase=phase[0],delayMs=gap))
    timer.timeout.connect(heartbeat)
    with tempfile.TemporaryDirectory(prefix='journal-ui-') as folder:
        store=JournalStore(Path(folder)/'library',create=True,clock=lambda:1789704000.);fixture(store,a.count)
        start=time.perf_counter();window=JournalWindow(store,None);window.updates.check=lambda *_:None;window.show();construction=(time.perf_counter()-start)*1000
        last[0]=time.perf_counter();timer.start();results=[]
        def wait():
            assert window.wait_for_queries(30000),'Query timeout'
        sequence=(1,0,2,3,4,1,4,1,4,1);memory=[]
        if a.stress:sequence=tuple(range(5))*20
        for turn,index in enumerate(sequence):
            phase[0]=f'page {index}, turn {turn}'
            start=time.perf_counter();window.tabs.setCurrentIndex(index);app.processEvents();first=(time.perf_counter()-start)*1000;wait();app.processEvents();total=(time.perf_counter()-start)*1000
            results.append(dict(page=index,feedbackMs=first,contentMs=total))
            loop=QEventLoop();QTimer.singleShot(60,loop.quit);loop.exec()
            if turn in (len(sequence)//2-1,len(sequence)-1):memory.append(memory_bytes())
        batch_max=max(window.apply_batch_times,default=0);cache_size=len(window.queries._cache);cache_weight=sum(window.queries._cache_weights.values())
        window.close();window.queries.close();store.close();timer.stop()
        result=dict(count=a.count,constructionMs=construction,pages=results,eventLoopP95Ms=sorted(gaps)[int(len(gaps)*.95)] if gaps else 0,eventLoopMaxMs=max(gaps,default=0),applyBatchMaxMs=batch_max,cacheEntries=cache_size,estimatedCacheBytes=cache_weight,workingSetSamples=memory)
        result['outliers']=outliers
        result['passed']=max(r['feedbackMs'] for r in results)<=100 and result['eventLoopP95Ms']<=50 and result['eventLoopMaxMs']<=150 and results[0]['contentMs']<=(3000 if a.count>=10000 else 1000) and all(r['contentMs']<=200 for r in results[5:] if r['page']==1) and batch_max<=8
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
