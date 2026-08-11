#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil

from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def load_targets(
    path: Path,
    candidate: str,
) -> list[dict[str, str]]:
    with path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        rows = [
            row
            for row in csv.DictReader(stream)
            if row.get(
                "candidate_id",
                "",
            ) == candidate
            and row.get(
                "enabled",
                "",
            ).lower() == "true"
        ]

    return sorted(
        rows,
        key=lambda row: row["target_id"],
    )


def required_count(
    available: int,
    fixed_count: int | None,
    percentage: float | None,
) -> int:
    if fixed_count is not None:
        return min(
            fixed_count,
            available,
        )

    if percentage is not None:
        value = round(
            available
            * percentage
            / 100.0
        )

        return min(
            max(1, value),
            available,
        )

    raise ValueError(
        "Either fixed count or percentage "
        "must be supplied"
    )


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--candidate",
        required=True,
    )

    parser.add_argument(
        "--scenario",
        required=True,
    )

    parser.add_argument(
        "--baseline-workspace",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output-workspace",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--targets",
        type=Path,
        default=Path(
            "config/"
            "scenario-mutation-targets.csv"
        ),
    )

    count_group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    count_group.add_argument(
        "--count",
        type=int,
    )

    count_group.add_argument(
        "--percentage",
        type=float,
    )

    parser.add_argument(
        "--report",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    if args.count is not None:
        if args.count < 1:
            parser.error(
                "--count must be positive"
            )

    if args.percentage is not None:
        if not (
            0 < args.percentage <= 100
        ):
            parser.error(
                "--percentage must be "
                "greater than 0 and at most 100"
            )

    if not args.baseline_workspace.is_dir():
        raise SystemExit(
            "ERROR: baseline workspace missing: "
            + str(args.baseline_workspace)
        )

    targets = load_targets(
        args.targets,
        args.candidate,
    )

    if not targets:
        raise SystemExit(
            "ERROR: no enabled mutation targets "
            f"for {args.candidate}"
        )

    count = required_count(
        len(targets),
        args.count,
        args.percentage,
    )

    selected = targets[:count]

    if args.output_workspace.exists():
        shutil.rmtree(
            args.output_workspace
        )

    shutil.copytree(
        args.baseline_workspace,
        args.output_workspace,
        symlinks=True,
    )

    report_rows = []

    for target in selected:
        relative_path = Path(
            target[
                "relative_source_path"
            ]
        )

        source_path = (
            args.output_workspace
            / relative_path
        )

        if not source_path.is_file():
            raise SystemExit(
                "ERROR: mutation source missing: "
                + str(source_path)
            )

        before_hash = sha256(
            source_path
        )

        text = source_path.read_text(
            encoding="utf-8",
        )

        old_literal = target[
            "old_literal"
        ]

        new_literal = target[
            "new_literal"
        ]

        occurrences = text.count(
            old_literal
        )

        if occurrences != 1:
            raise SystemExit(
                "ERROR: expected exactly one "
                f"occurrence of {old_literal!r} "
                f"in {source_path}, found "
                f"{occurrences}"
            )

        mutated = text.replace(
            old_literal,
            new_literal,
            1,
        )

        source_path.write_text(
            mutated,
            encoding="utf-8",
        )

        after_hash = sha256(
            source_path
        )

        if before_hash == after_hash:
            raise SystemExit(
                "ERROR: mutation did not alter "
                + str(source_path)
            )

        report_rows.append({
            "candidate_id": args.candidate,
            "scenario_id": args.scenario,
            "target_id": target[
                "target_id"
            ],
            "relative_source_path": (
                str(relative_path)
            ),
            "method_label": target[
                "method_label"
            ],
            "mutation_kind": target[
                "mutation_kind"
            ],
            "old_literal": old_literal,
            "new_literal": new_literal,
            "source_sha256_before": (
                before_hash
            ),
            "source_sha256_after": (
                after_hash
            ),
        })

    args.report.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fields = [
        "candidate_id",
        "scenario_id",
        "target_id",
        "relative_source_path",
        "method_label",
        "mutation_kind",
        "old_literal",
        "new_literal",
        "source_sha256_before",
        "source_sha256_after",
    ]

    with args.report.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(
            report_rows
        )

    metadata = {
        "candidate_id": args.candidate,
        "scenario_id": args.scenario,
        "eligible_targets": len(targets),
        "selected_targets": count,
        "requested_count": args.count,
        "requested_percentage": (
            args.percentage
        ),
        "actual_percentage": (
            100.0
            * count
            / len(targets)
        ),
    }

    metadata_path = (
        args.report.with_suffix(
            ".json"
        )
    )

    metadata_path.write_text(
        json.dumps(
            metadata,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        "Candidate:",
        args.candidate,
    )

    print(
        "Scenario:",
        args.scenario,
    )

    print(
        "Eligible targets:",
        len(targets),
    )

    print(
        "Selected targets:",
        count,
    )

    print(
        "Actual percentage:",
        f"{metadata['actual_percentage']:.2f}",
    )

    print(
        "Workspace:",
        args.output_workspace,
    )

    print(
        "Report:",
        args.report,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
