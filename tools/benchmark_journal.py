"""Repeatable isolated query benchmarks; each subprocess has a hard timeout."""
import argparse,json,os,subprocess,sys,tempfile,time
from pathlib import Path

def fixture(store,count):
    rules=[dict(start='2000-01-01T10:00:00',period=p,calendar=c) for p,c in [('daily','solar'),('weekly','solar'),('monthly','solar'),('yearly','solar'),('monthly','lunar'),('yearly','lunar')]]
    with store.db:
        for n in range(count):
            rule=json.dumps(rules[n%len(rules)])
            store.db.execute('INSERT INTO events(id,title,kind,body,rule,created,updated,cursor,archived,next_check) VALUES(?,?,?,?,?,?,?,?,0,?)',(str(n),f'安排 {n:05}','schedule','测试',rule,1,1,1,9999999999))
            store.db.execute('INSERT INTO notes VALUES(?,?,?,?,?,NULL)',(str(n),f'笔记 {n:05}','# 正文\n\n记录日常。',n+1,n+1))
        for n in range(count*3):store.db.execute('INSERT INTO habit_log VALUES(?,?,?,?,?,?)',(str(n),'water',1789689600+n,'2026-09-18',1,1789689601+n))

def child(a):
    sys.path.insert(0,str(a.modules.resolve()));from journal_store import JournalStore
    from journal_recurrence import Rule
    calculations=[0];candidate=Rule._candidate
    def measured(self,index):
        calculations[0]+=1
        return candidate(self,index)
    Rule._candidate=measured
    with tempfile.TemporaryDirectory(prefix='journal-perf-') as directory:
        store=JournalStore(Path(directory)/'library',create=True,clock=lambda:1789704000.)
        fixture(store,a.count);queries=[];store.db.set_trace_callback(queries.append)
        started=time.perf_counter();rows=store.events(status='active',limit=51);cold=time.perf_counter()-started
        before=len(queries);cold_calculations=calculations[0];started=time.perf_counter();store.events(status='active',limit=51);warm=time.perf_counter()-started
        print(json.dumps(dict(count=a.count,coldSeconds=cold,warmSeconds=warm,coldQueries=before,warmQueries=len(queries)-before,coldCandidates=cold_calculations,warmCandidates=calculations[0]-cold_calculations,rows=len(rows))),flush=True);store.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--modules',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--output',type=Path);p.add_argument('--count',type=int);p.add_argument('--timeout',type=int,default=30);a=p.parse_args()
    if a.count is not None:return child(a)
    results=[]
    for n in (0,100,1000,10000):
        try:
            run=subprocess.run([sys.executable,__file__,'--modules',str(a.modules),'--count',str(n)],capture_output=True,text=True,timeout=a.timeout)
            results.append(json.loads(run.stdout) if run.returncode==0 else dict(count=n,error=run.stderr))
        except subprocess.TimeoutExpired:results.append(dict(count=n,timedOut=True,timeoutSeconds=a.timeout))
        print(json.dumps(results[-1]),flush=True)
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(results,indent=2),encoding='utf-8')

if __name__=='__main__':main()
