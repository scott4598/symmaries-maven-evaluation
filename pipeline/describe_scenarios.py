#!/usr/bin/env python3
import argparse, csv
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--file', default='config/pilot-scenarios.csv')
a=p.parse_args()
rows=list(csv.DictReader(Path(a.file).open(newline='',encoding='utf-8')))
if not rows:
    raise SystemExit('No scenarios found')
fields=rows[0].keys()
print('\t'.join(fields))
for row in rows:
    print('\t'.join(row.get(f,'') for f in fields))
