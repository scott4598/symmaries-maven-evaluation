#!/usr/bin/env python3

import csv
import sys
from pathlib import Path

sample_path = Path(
    "benchmark/screening-sample.csv"
)

results_path = Path(
    "benchmark/reproduction-latest.csv"
)

output_path = Path(
    "benchmark/exact-candidate-pool.csv"
)

with sample_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    sample = {
        row["candidate_id"]: row
        for row in csv.DictReader(stream)
    }

with results_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    results = list(csv.DictReader(stream))

eligible_exact = [
    row
    for row in results
    if row["status"] == "PASS_EXACT"
    and row["exit_code"] == "0"
    and row["buildcompare_ko"] == "0"
]

exact = []
evidence_failures = []

for row in eligible_exact:
    candidate = row["candidate_id"]
    attempt = row["attempt"]

    evidence = (
        Path("results/reproduction/metadata")
        / (
            f"{candidate}-attempt-"
            f"{attempt}.buildcompare"
        )
    )

    if not evidence.is_file():
        evidence_failures.append(
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
        evidence_failures.append(
            (
                candidate,
                (
                    "comparison evidence has ko="
                    f"{values.get('ko', 'missing')}"
                ),
                str(evidence),
            )
        )
        continue

    exact.append(row)

if evidence_failures:
    print(
        "Exact candidates with invalid or "
        "missing evidence:",
        file=sys.stderr,
    )

    for failure in evidence_failures:
        print(
            *failure,
            sep="\t",
            file=sys.stderr,
        )

    raise SystemExit(1)

output_fields = [
    "candidate_id",
    "artifact_id",
    "version",
    "build_tool",
    "jdk_version",
    "jdk_family",
    "maven_family",
    "lifecycle_endpoint",
    "stratum",
    "status",
    "exit_code",
    "buildcompare_ko",
    "failure_category",
    "buildcompare_path",
    "evidence_copy",
    "attempt",
    "duration_seconds",
]

with output_path.open(
    "w",
    newline="",
    encoding="utf-8",
) as stream:
    writer = csv.DictWriter(
        stream,
        fieldnames=output_fields,
    )
    writer.writeheader()

    for result in exact:
        candidate = result["candidate_id"]
        project = sample[candidate]
        attempt = result["attempt"]

        writer.writerow({
            "candidate_id": candidate,
            "artifact_id": project.get(
                "artifact_id",
                "",
            ),
            "version": project.get(
                "version",
                "",
            ),
            "build_tool": project.get(
                "build_tool",
                "",
            ),
            "jdk_version": project.get(
                "jdk_version",
                "",
            ),
            "jdk_family": project.get(
                "jdk_family",
                "",
            ),
            "maven_family": project.get(
                "maven_family",
                "",
            ),
            "lifecycle_endpoint": project.get(
                "lifecycle_endpoint",
                "",
            ),
            "stratum": (
                project.get("stratum", "")
                or project.get(
                    "selection_stratum",
                    "",
                )
            ),
            "status": result["status"],
            "exit_code": result["exit_code"],
            "buildcompare_ko": result[
                "buildcompare_ko"
            ],
            "failure_category": result[
                "failure_category"
            ],
            "buildcompare_path": result[
                "buildcompare_path"
            ],
            "evidence_copy": (
                "results/reproduction/metadata/"
                f"{candidate}-attempt-{attempt}"
                ".buildcompare"
            ),
            "attempt": attempt,
            "duration_seconds": result[
                "duration_seconds"
            ],
        })

print(
    f"Wrote {len(exact)} exact candidates "
    f"to {output_path}"
)