#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,zipfile
from pathlib import Path

def load(path):
    with Path(path).open(newline='',encoding='utf-8') as f: return list(csv.DictReader(f))
def write(path,rows,fields):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--summary',default='benchmark/d1-validation-summary.csv')
    p.add_argument('--selection',default='benchmark/d1-runnable-selection.csv')
    p.add_argument('--sample',default='benchmark/d1-runnable-sample.csv')
    p.add_argument('--d1-results-root',default='results/d1-validation-runs')
    p.add_argument(
        "--d1-workspaces-root",
        default="work/pilot",
    )    
    p.add_argument('--d1-attempt',type=int,default=1)
    p.add_argument('--output-selection',default='benchmark/d2-candidate-selection.csv')
    p.add_argument('--output-sample',default='benchmark/d2-candidate-sample.csv')
    p.add_argument('--output-modules',default='config/d2-analysis-modules.csv')
    p.add_argument('--report',default='benchmark/d2-artifact-verification.csv')
    p.add_argument('--exclusions',default='benchmark/d2-precheck-exclusions.csv')
    a=p.parse_args()
    passed={r['candidate_id'] for r in load(a.summary) if r.get('status')=='PASS'}
    sels={r['candidate_id']:r for r in load(a.selection)}; samples={r['candidate_id']:r for r in load(a.sample)}
    reports=[]; exclusions=[]; modules=[]; accepted=[]
    classifier_words=('-sources','-source','-javadoc','-tests','-test','original-')
    reflection_markers=(b'java/lang/reflect/AccessibleObject',b'slowCheckMemberAccess',b'setAccessible',b'trySetAccessible')
    for cid in sorted(passed):
        workspace = (
            Path(a.d1_workspaces_root)
            / cid
            / "D1"
            / f"attempt-{a.d1_attempt}"
            / "workspace"
        )
        jars=[]
        if workspace.is_dir():
            for jar in workspace.rglob('target/*.jar'):
                name=jar.name.lower()
                if not any(x in name for x in classifier_words): jars.append(jar)
        if not jars:
            exclusions.append({'candidate_id':cid,'reason':'NO_PRIMARY_JAR'}); continue
        artifact=sels[cid].get('artifact_id','').lower(); version=sels[cid].get('version','').lower()
        def score(j):
            n=j.name.lower(); exact=int(n==f'{artifact}-{version}.jar'); artifact_match=int(n.startswith(artifact+'-'))
            return (exact,artifact_match,-len(j.parts),j.stat().st_size)
        jar=max(jars,key=score)
        direct_reflection=False; matched=[]
        try:
            with zipfile.ZipFile(jar) as z:
                for name in z.namelist():
                    if not name.endswith('.class'): continue
                    data=z.read(name)
                    hits=[m.decode() for m in reflection_markers if m in data]
                    if hits: direct_reflection=True; matched.extend(f'{name}:{h}' for h in hits)
        except (OSError,zipfile.BadZipFile) as e:
            exclusions.append({'candidate_id':cid,'reason':f'INVALID_JAR:{e}'}); continue
        module_dir=jar.parent.parent
        try: module_path=str(module_dir.relative_to(workspace)) or '.'
        except ValueError: module_path='.'
        report={'candidate_id':cid,'jar_path':str(jar),'jar_size':str(jar.stat().st_size),'module_path':module_path,
                'direct_reflection':str(direct_reflection).lower(),'reflection_matches':' | '.join(matched[:20])}
        reports.append(report)
        if direct_reflection:
            exclusions.append({'candidate_id':cid,'reason':'DIRECT_ACCESSIBLEOBJECT_OR_SETACCESSIBLE_REFERENCE'}); continue
        accepted.append(sels[cid]); modules.append({'candidate_id':cid,'module_path':module_path,'artifact_selector':'main',
                                                     'mapping_status':'D1_VERIFIED_JAR','notes':jar.name})
    fields=list(next(iter(sels.values())).keys())
    write(a.output_selection,accepted,fields)
    write(a.output_sample,[samples[r['candidate_id']] for r in accepted],list(next(iter(samples.values())).keys()))
    write(a.output_modules,modules,['candidate_id','module_path','artifact_selector','mapping_status','notes'])
    write(a.report,reports,['candidate_id','jar_path','jar_size','module_path','direct_reflection','reflection_matches'])
    write(a.exclusions,exclusions,['candidate_id','reason'])
    print(f'd1_pass={len(passed)} d2_accepted={len(accepted)} excluded={len(exclusions)}')
if __name__=='__main__': main()
