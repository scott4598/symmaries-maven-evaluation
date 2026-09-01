#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,os,subprocess,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

def load(p):
    with Path(p).open(newline='',encoding='utf-8') as f:return list(csv.DictReader(f))
def write(p,rows,fields):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def main():
    x=argparse.ArgumentParser();x.add_argument('--attempt',type=int,default=1);x.add_argument('--timeout-minutes',type=int,default=20)
    x.add_argument('--selection',default='benchmark/d2-candidate-selection.csv');x.add_argument('--sample',default='benchmark/d2-candidate-sample.csv')
    x.add_argument('--modules',default='config/d2-analysis-modules.csv');x.add_argument('--results',default='benchmark/d2-validation-results.csv')
    x.add_argument('--results-root',default='results/d2-validation-runs');x.add_argument('--summary',default='benchmark/d2-validation-summary.csv')
    a=x.parse_args(); root=Path.cwd(); rows=load(a.selection); driver=[]
    logp=Path('results/d2-screening-driver')/f'attempt-{a.attempt}.log';logp.parent.mkdir(parents=True,exist_ok=True)
    with logp.open('a',encoding='utf-8') as log:
      for i,r in enumerate(rows,1):
        cid=r['candidate_id']; print(f'===== {now()} [{i}/{len(rows)}] {cid} D2 =====',flush=True)
        cmd=[sys.executable,'pipeline/run-symmaries-pilot.py','--selection',a.selection,'--sample',a.sample,'--modules',a.modules,
             '--scenarios','config/pilot-scenarios.csv','--candidate',cid,'--scenario','D2','--attempt',str(a.attempt),'--results',a.results,
             '--results-root',a.results_root,'--fallback-policy','config/policies/empty-sources-and-sinks.xml','--settings','config/settings-pilot-host.xml',
             '--build-environments','config/candidate-build-environments.json','--timeout-minutes',str(a.timeout_minutes)]
        try:
          q=subprocess.run(cmd,cwd=root,env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,check=False,
                           timeout=a.timeout_minutes*60+300); out=q.stdout or ''; code=str(q.returncode); state='INVOKED'
        except subprocess.TimeoutExpired as e:
          out=e.stdout or ''; out=out.decode(errors='replace') if isinstance(out,bytes) else out; out+='\nDRIVER_TIMEOUT\n';code='124';state='RUNNER_TIMEOUT'
        except Exception as e: out=f'\nDRIVER_EXCEPTION: {e!r}\n';code='-1';state='RUNNER_EXCEPTION'
        print(out,end='',flush=True);log.write(out);log.flush();driver.append({'candidate_id':cid,'driver_exit_code':code,'driver_status':state})
        write(Path('results/d2-screening-driver')/f'attempt-{a.attempt}-driver.csv',driver,['candidate_id','driver_exit_code','driver_status'])
    result=load(a.results) if Path(a.results).is_file() else []; selected={r['candidate_id']:r for r in result if r.get('scenario_id')=='D2' and r.get('attempt')==str(a.attempt)}
    summary=[{'candidate_id':r['candidate_id'],'status':selected.get(r['candidate_id'],{}).get('status','NO_RESULT'),
              'exit_code':selected.get(r['candidate_id'],{}).get('exit_code',''),'duration_seconds':selected.get(r['candidate_id'],{}).get('duration_seconds',''),
              'scan_id':selected.get(r['candidate_id'],{}).get('scan_id',''),'scan_status':selected.get(r['candidate_id'],{}).get('scan_status',''),
              'failure_category':selected.get(r['candidate_id'],{}).get('failure_category',''),'log_path':selected.get(r['candidate_id'],{}).get('log_path','')} for r in rows]
    write(a.summary,summary,list(summary[0])); counts=Counter(r['status'] for r in summary)
    Path(a.summary).with_suffix('.stats.txt').write_text('\n'.join([f'attempt={a.attempt}',f'total={len(rows)}']+[f'status_{k}={v}' for k,v in sorted(counts.items())])+"\n")
if __name__=='__main__':main()
