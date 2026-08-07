#!/usr/bin/env python3
"""Deterministic proportional stratified sample selector for CSV candidate inventories."""
import argparse, csv, math, random
from collections import defaultdict
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--input', required=True)
p.add_argument('--output', required=True)
p.add_argument('--size', type=int, default=80)
p.add_argument('--seed', type=int, default=4598)
p.add_argument('--id-column', default='candidate_id')
p.add_argument('--strata', nargs='*', default=[])
p.add_argument('--eligible-column')
p.add_argument('--eligible-value')
p.add_argument('--role-column', default='selection_role')
p.add_argument('--role-value', default='pilot-primary')
a=p.parse_args()
with Path(a.input).open(newline='',encoding='utf-8') as f:
    reader=csv.DictReader(f); rows=list(reader); fields=list(reader.fieldnames or [])
if a.eligible_column:
    rows=[r for r in rows if r.get(a.eligible_column,'') == (a.eligible_value or '')]
rows=[r for r in rows if r.get(a.id_column,'').strip()]
if not rows: raise SystemExit('No eligible rows')
if a.role_column not in fields: fields.append(a.role_column)
target=min(a.size,len(rows)); rng=random.Random(a.seed)
groups=defaultdict(list)
for r in rows:
    key=tuple(r.get(c,'') for c in a.strata) if a.strata else ('all',)
    groups[key].append(r)
for g in groups.values(): rng.shuffle(g)
raw={k:target*len(g)/len(rows) for k,g in groups.items()}
alloc={k:min(len(groups[k]),math.floor(v)) for k,v in raw.items()}
left=target-sum(alloc.values())
order=sorted(groups,key=lambda k:(raw[k]-math.floor(raw[k]),len(groups[k])),reverse=True)
while left:
    moved=False
    for k in order:
        if alloc[k] < len(groups[k]): alloc[k]+=1; left-=1; moved=True
        if left==0: break
    if not moved: break
selected=[]
for k in sorted(groups,key=str): selected.extend(groups[k][:alloc[k]])
rng.shuffle(selected)
for r in selected: r[a.role_column]=a.role_value
out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
with out.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(selected)
print(f'population={len(rows)} selected={len(selected)} seed={a.seed} output={out}')
