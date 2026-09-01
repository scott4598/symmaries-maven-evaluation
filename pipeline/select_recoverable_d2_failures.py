#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


REVIEW_STATES = {
    "YES",
    "POSSIBLY",
    "INVESTIGATE",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract potentially recoverable D2 "
            "failures from the failure audit."
        )
    )

    parser.add_argument(
        "--audit",
        default="benchmark/d2-failure-audit.csv",
    )

    parser.add_argument(
        "--output",
        default=(
            "benchmark/"
            "d2-recoverable-failures.csv"
        ),
    )

    parser.add_argument(
        "--stats",
        default=(
            "benchmark/"
            "d2-recoverable-failures.stats.txt"
        ),
    )

    args = parser.parse_args()

    audit_path = Path(args.audit)
    output_path = Path(args.output)
    stats_path = Path(args.stats)

    if not audit_path.is_file():
        raise SystemExit(
            f"ERROR: audit file missing: {audit_path}"
        )

    with audit_path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])

        rows = [
            row
            for row in reader
            if row.get("category") != "PASS"
            and row.get("adjustable")
            in REVIEW_STATES
        ]

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(rows)

    categories = Counter(
        row["category"]
        for row in rows
    )

    lines = [
        f"total={len(rows)}",
    ]

    for category, count in sorted(
        categories.items()
    ):
        lines.append(
            f"category_{category}={count}"
        )

    stats_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote {len(rows)} rows to "
        f"{output_path}"
    )

    for category, count in (
        categories.most_common()
    ):
        print(
            count,
            category,
            sep="\t",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
