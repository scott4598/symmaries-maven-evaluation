#!/usr/bin/env python3

import csv
import hashlib
import os
import sys

rc_root = os.environ.get("RC_ROOT")

if not rc_root:
    raise SystemExit(
        "ERROR: RC_ROOT is not set. Load .env before running."
    )

sample_files = [
    "benchmark/screening-sample.csv",
    "benchmark/screening-reserve.csv",
]

errors = []

for sample_file in sample_files:
    with open(
        sample_file,
        newline="",
        encoding="utf-8",
    ) as stream:
        rows = list(csv.DictReader(stream))

    for row in rows:
        full_path = os.path.join(
            rc_root,
            row["buildspec_path"],
        )

        if not os.path.isfile(full_path):
            errors.append(
                (
                    row["candidate_id"],
                    "MISSING",
                    full_path,
                )
            )
            continue

        digest = hashlib.sha256()

        with open(full_path, "rb") as buildspec:
            for block in iter(
                lambda: buildspec.read(1024 * 1024),
                b"",
            ):
                digest.update(block)

        actual_hash = digest.hexdigest()
        expected_hash = row["buildspec_sha256"]

        if actual_hash != expected_hash:
            errors.append(
                (
                    row["candidate_id"],
                    "HASH_MISMATCH",
                    full_path,
                )
            )

print("Validation errors:", len(errors))

for error in errors:
    print(*error, sep="\t")

if errors:
    sys.exit(1)

print("All selected build specifications verified.")