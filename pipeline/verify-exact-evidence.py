#!/usr/bin/env python3

import csv
from pathlib import Path


pool_path = Path(
    "benchmark/exact-candidate-pool.csv"
)

with pool_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    rows = list(csv.DictReader(stream))

failures = []

for row in rows:
    candidate = row["candidate_id"]
    evidence = Path(row["evidence_copy"])

    if row["status"] != "PASS_EXACT":
        failures.append(
            (
                candidate,
                "unexpected status",
                row["status"],
            )
        )

    if row["exit_code"] != "0":
        failures.append(
            (
                candidate,
                "unexpected exit code",
                row["exit_code"],
            )
        )

    if row["buildcompare_ko"] != "0":
        failures.append(
            (
                candidate,
                "pool ko",
                row["buildcompare_ko"],
            )
        )

    if not evidence.is_file():
        failures.append(
            (
                candidate,
                "missing evidence",
                str(evidence),
            )
        )
        continue

    values = {}

    for line in evidence.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    if values.get("ko") != "0":
        failures.append(
            (
                candidate,
                "evidence ko",
                values.get("ko", "missing"),
            )
        )

if failures:
    print("Evidence verification failed:")

    for failure in failures:
        print(*failure, sep="\t")

    raise SystemExit(1)

print(
    f"Verified {len(rows)} exact candidates "
    "with complete ko=0 evidence."
)