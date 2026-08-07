#!/usr/bin/env python3

import csv
from collections import Counter
from pathlib import Path

sample_path = Path(
    "benchmark/screening-sample.csv"
)

results_path = Path(
    "benchmark/reproduction-results.csv"
)

excluded = {
    "RCM-08439",
    "RCM-02124",
    "RCM-04854",
}

with sample_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    sample = list(csv.DictReader(stream))

with results_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    results = list(csv.DictReader(stream))

attempt_1 = {
    row["candidate_id"]: row
    for row in results
    if row["attempt"] == "1"
}

latest = {}

for row in results:
    candidate = row["candidate_id"]
    attempt = int(row["attempt"])

    if (
        candidate not in latest
        or attempt > int(latest[candidate]["attempt"])
    ):
        latest[candidate] = row

eligible_sample = []

for row in sample:
    candidate = row["candidate_id"]
    command = row.get(
        "build_command",
        "",
    ).strip()

    if candidate in excluded:
        continue

    if not command.startswith("mvn "):
        continue

    eligible_sample.append(row)

remaining = [
    row
    for row in eligible_sample
    if row["candidate_id"] not in attempt_1
]

latest_counts = Counter(
    row["status"]
    for row in latest.values()
)

print(
    "Sample rows:",
    len(sample),
)

print(
    "Automatically eligible:",
    len(eligible_sample),
)

print(
    "Attempt-1 observations:",
    len(attempt_1),
)

print(
    "Remaining attempt-1 candidates:",
    len(remaining),
)

print("\nLatest status counts")

for status, count in sorted(
    latest_counts.items()
):
    print(f"  {status}: {count}")

print("\nLatest non-exact or failed results")

for candidate, row in sorted(
    latest.items()
):
    if row["status"] != "PASS_EXACT":
        print(
            candidate,
            row["attempt"],
            row["status"],
            row["buildcompare_ko"] or "NA",
            row["failure_category"] or "none",
            row["log_path"],
            sep="\t",
        )

print("\nRemaining candidates")

for row in remaining:
    print(
        row["candidate_id"],
        row.get("stratum", ""),
        row.get("artifact_id", ""),
        row.get("jdk_family", ""),
        row.get("maven_family", ""),
        row.get("lifecycle_endpoint", ""),
        sep="\t",
    )