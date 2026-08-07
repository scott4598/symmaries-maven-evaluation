#!/usr/bin/env python3

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path


def load_csv(path):
    with path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        return list(csv.DictReader(stream))


def first_value(row, names, default=""):
    for name in names:
        value = row.get(name, "").strip()

        if value:
            return value

    return default


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--sample",
        default="benchmark/screening-sample.csv",
    )

    parser.add_argument(
        "--pool",
        default="benchmark/exact-candidate-pool.csv",
    )

    parser.add_argument(
        "--output",
        default="benchmark/final-pilot-selection.csv",
    )

    parser.add_argument(
        "--target-size",
        type=int,
        default=12,
    )

    parser.add_argument(
        "--reserves-per-primary",
        type=int,
        default=1,
    )

    args = parser.parse_args()

    sample_path = Path(args.sample)
    pool_path = Path(args.pool)
    output_path = Path(args.output)

    sample_rows = load_csv(sample_path)
    pool_rows = load_csv(pool_path)

    sample_by_id = {
        row["candidate_id"]: row
        for row in sample_rows
    }

    sample_order = {
        row["candidate_id"]: index
        for index, row in enumerate(sample_rows)
    }

    candidates = []

    pool_failures = []

    for result in pool_rows:
        candidate = result.get(
            "candidate_id",
            "",
        )

        if result.get("status") != "PASS_EXACT":
            pool_failures.append(
                (
                    candidate,
                    "status",
                    result.get("status", ""),
                )
            )

        if result.get("exit_code") != "0":
            pool_failures.append(
                (
                    candidate,
                    "exit_code",
                    result.get("exit_code", ""),
                )
            )

        if result.get("buildcompare_ko") != "0":
            pool_failures.append(
                (
                    candidate,
                    "buildcompare_ko",
                    result.get(
                        "buildcompare_ko",
                        "",
                    ),
                )
            )

        evidence = Path(
            result.get("evidence_copy", "")
        )

        if not evidence.is_file():
            pool_failures.append(
                (
                    candidate,
                    "missing evidence",
                    str(evidence),
                )
            )

    if pool_failures:
        print(
            "Exact candidate pool validation "
            "failed:"
        )

        for failure in pool_failures:
            print(*failure, sep="\t")

        raise SystemExit(1)

    for result in pool_rows:
        candidate = result["candidate_id"]
        project = sample_by_id[candidate]

        jdk = first_value(
            project,
            [
                "jdk_family",
                "jdk_version",
                "jdk",
            ],
            "unknown",
        )

        maven = first_value(
            project,
            [
                "maven_family",
                "build_tool",
                "tool",
            ],
            "unknown",
        )

        endpoint = first_value(
            project,
            [
                "lifecycle_endpoint",
                "endpoint",
            ],
            "unknown",
        )

        composite = first_value(
            project,
            [
                "stratum",
                "selection_stratum",
                "sampling_stratum",
            ],
            (
                f"jdk={jdk}|maven={maven}|"
                f"endpoint={endpoint}"
            ),
        )

        candidates.append({
            **result,
            "artifact_id": project.get(
                "artifact_id",
                "",
            ),
            "version": project.get(
                "version",
                "",
            ),
            "jdk_family": jdk,
            "maven_family": maven,
            "lifecycle_endpoint": endpoint,
            "stratum": composite,
            "sample_order": sample_order[candidate],
        })

    target = min(
        args.target_size,
        len(candidates),
    )

    selected = []
    remaining = candidates.copy()

    jdk_counts = Counter()
    maven_counts = Counter()
    endpoint_counts = Counter()
    stratum_counts = Counter()

    while remaining and len(selected) < target:
        def score(candidate):
            novelty = (
                int(jdk_counts[
                    candidate["jdk_family"]
                ] == 0)
                + int(maven_counts[
                    candidate["maven_family"]
                ] == 0)
                + int(endpoint_counts[
                    candidate[
                        "lifecycle_endpoint"
                    ]
                ] == 0)
            )

            scarcity = (
                jdk_counts[
                    candidate["jdk_family"]
                ]
                + maven_counts[
                    candidate["maven_family"]
                ]
                + endpoint_counts[
                    candidate[
                        "lifecycle_endpoint"
                    ]
                ]
            )

            duplicate_stratum = stratum_counts[
                candidate["stratum"]
            ]

            return (
                -novelty,
                duplicate_stratum,
                scarcity,
                candidate["sample_order"],
                candidate["candidate_id"],
            )

        chosen = min(remaining, key=score)
        remaining.remove(chosen)
        selected.append(chosen)

        jdk_counts[chosen["jdk_family"]] += 1
        maven_counts[chosen["maven_family"]] += 1
        endpoint_counts[
            chosen["lifecycle_endpoint"]
        ] += 1
        stratum_counts[chosen["stratum"]] += 1

    by_stratum = defaultdict(list)

    for candidate in candidates:
        by_stratum[candidate["stratum"]].append(
            candidate
        )

    for stratum in by_stratum:
        by_stratum[stratum].sort(
            key=lambda row: (
                row["sample_order"],
                row["candidate_id"],
            )
        )

    selected_ids = {
        row["candidate_id"]
        for row in selected
    }

    output_rows = []

    for primary in selected:
        output_rows.append({
            **primary,
            "selection_role": "pilot-primary",
            "primary_for": "",
            "selection_reason": (
                "Greedy balanced coverage of JDK, "
                "Maven family, and lifecycle endpoint; "
                "tie broken by original seeded order"
            ),
        })

        reserves = [
            row
            for row in by_stratum[
                primary["stratum"]
            ]
            if row["candidate_id"] not in selected_ids
        ]

        for reserve in reserves[
            :args.reserves_per_primary
        ]:
            selected_ids.add(
                reserve["candidate_id"]
            )

            output_rows.append({
                **reserve,
                "selection_role": "pilot-reserve",
                "primary_for": primary[
                    "candidate_id"
                ],
                "selection_reason": (
                    "Exact same-stratum reserve for "
                    f"{primary['candidate_id']}"
                ),
            })

    fields = [
        "candidate_id",
        "selection_role",
        "primary_for",
        "stratum",
        "artifact_id",
        "version",
        "jdk_family",
        "maven_family",
        "lifecycle_endpoint",
        "status",
        "attempt",
        "buildcompare_ko",
        "buildcompare_path",
        "evidence_copy",
        "duration_seconds",
        "selection_reason",
        "sample_order",
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
            extrasaction="ignore",
        )

        writer.writeheader()
        writer.writerows(output_rows)

    print(
        f"Selected {len(selected)} pilot primaries."
    )

    reserve_count = sum(
        row["selection_role"] == "pilot-reserve"
        for row in output_rows
    )

    print(
        f"Selected {reserve_count} same-stratum "
        "reserves."
    )

    print("\nPrimary JDK coverage")

    for value, count in sorted(
        jdk_counts.items()
    ):
        print(f"  {value}: {count}")

    print("\nPrimary Maven coverage")

    for value, count in sorted(
        maven_counts.items()
    ):
        print(f"  {value}: {count}")

    print("\nPrimary endpoint coverage")

    for value, count in sorted(
        endpoint_counts.items()
    ):
        print(f"  {value}: {count}")

    missing_reserves = [
        row["candidate_id"]
        for row in selected
        if not any(
            output["selection_role"]
            == "pilot-reserve"
            and output["primary_for"]
            == row["candidate_id"]
            for output in output_rows
        )
    ]

    if missing_reserves:
        print(
            "\nPrimaries without an exact "
            "same-stratum reserve:"
        )

        for candidate in missing_reserves:
            print(f"  {candidate}")


if __name__ == "__main__":
    main()