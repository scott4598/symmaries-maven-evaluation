#!/usr/bin/env python3

import csv
from pathlib import Path

source = Path(
    "benchmark/reproduction-results.csv"
)

destination = Path(
    "benchmark/reproduction-latest.csv"
)

with source.open(
    newline="",
    encoding="utf-8",
) as stream:
    reader = csv.DictReader(stream)
    fields = reader.fieldnames
    rows = list(reader)

latest = {}

for row in rows:
    candidate = row["candidate_id"]
    attempt = int(row["attempt"])

    if (
        candidate not in latest
        or attempt > int(latest[candidate]["attempt"])
    ):
        latest[candidate] = row

with destination.open(
    "w",
    newline="",
    encoding="utf-8",
) as stream:
    writer = csv.DictWriter(
        stream,
        fieldnames=fields,
    )
    writer.writeheader()

    for candidate in sorted(latest):
        writer.writerow(latest[candidate])

print(
    f"Wrote {len(latest)} latest results "
    f"to {destination}"
)