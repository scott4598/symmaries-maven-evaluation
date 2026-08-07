#!/usr/bin/env python3

import csv
from collections import Counter
from pathlib import Path


selection_path = Path(
    "benchmark/final-pilot-selection.csv"
)

with selection_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    rows = list(csv.DictReader(stream))

failures = []

roles = Counter(
    row["selection_role"]
    for row in rows
)

for row in rows:
    candidate = row["candidate_id"]

    if row["selection_role"] not in {
        "pilot-primary",
        "pilot-reserve",
    }:
        failures.append(
            (
                candidate,
                "invalid selection role",
                row["selection_role"],
            )
        )

    if row["status"] != "PASS_EXACT":
        failures.append(
            (
                candidate,
                "status",
                row["status"],
            )
        )

    if row["attempt"] == "":
        failures.append(
            (
                candidate,
                "attempt",
                "missing",
            )
        )

    if row["buildcompare_ko"] != "0":
        failures.append(
            (
                candidate,
                "ko",
                row["buildcompare_ko"],
            )
        )

    evidence = Path(row["evidence_copy"])

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
    print("Final pilot validation failed:")

    for failure in failures:
        print(*failure, sep="\t")

    raise SystemExit(1)

print(
    f"Validated {len(rows)} selected rows: "
    f"{roles['pilot-primary']} primaries and "
    f"{roles['pilot-reserve']} reserves."
)