#!/usr/bin/env python3
import argparse,csv,re
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--results',required=True); p.add_argument('--output',required=True); a=p.parse_args()
with Path(a.results).open(newline='',encoding='utf-8') as f: rows=list(csv.DictReader(f))
fields=['candidate_id','scenario_id','attempt','status','exit_code','scan_id','failure_category','maven_executable','java_home','reason']
out=[]
for r in rows:
    run=Path(r.get('log_path','')).parent
    env={}
    ep=run/'run.env'
    if ep.is_file():
        for line in ep.read_text(errors='replace').splitlines():
            if '=' in line:
                k,v=line.split('=',1); env[k]=v
    reason=''
    lp=Path(r.get('log_path',''))
    if lp.is_file():
        text=lp.read_text(errors='replace')
        hits=re.findall(r'(?m)^\[ERROR\].*$',text)
        reason=(hits[-1] if hits else '')[:500]
    out.append({**{k:r.get(k,'') for k in fields},'maven_executable':env.get('maven_executable',''),'java_home':env.get('java_home',''),'reason':reason})
op=Path(a.output); op.parent.mkdir(parents=True,exist_ok=True)
with op.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=fields,delimiter='\t'); w.writeheader(); w.writerows(out)
print(op)
