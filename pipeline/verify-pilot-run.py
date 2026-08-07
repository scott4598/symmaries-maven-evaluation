#!/usr/bin/env python3

import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--results",
        default="benchmark/pilot-run-results.csv",
    )

    parser.add_argument(
        "--candidate",
        required=True,
    )

    parser.add_argument(
        "--scenario",
        required=True,
    )

    parser.add_argument(
        "--attempt",
        default="1",
    )

    args = parser.parse_args()

    with Path(args.results).open(
        newline="",
        encoding="utf-8",
    ) as stream:
        matches = [
            row
            for row in csv.DictReader(stream)
            if row["candidate_id"]
            == args.candidate
            and row["scenario_id"]
            == args.scenario
            and row["attempt"]
            == args.attempt
        ]

    if len(matches) != 1:
        raise SystemExit(
            "Expected one matching result, found "
            f"{len(matches)}"
        )

    row = matches[0]
    failures = []

    if row["status"] != "PASS":
        failures.append(
            f"status={row['status']}"
        )

    if row["exit_code"] != "0":
        failures.append(
            f"exit_code={row['exit_code']}"
        )

    plugin_enabled = (
        row["plugin_mode"] != "none"
    )

    if plugin_enabled:
        metadata_path = Path(
            row["metadata_file"]
        )

        if not metadata_path.is_file():
            failures.append(
                "scan metadata missing"
            )
        else:
            metadata = json.loads(
                metadata_path.read_text(
                    encoding="utf-8",
                )
            )

            if not metadata.get("scan_id"):
                failures.append(
                    "scan_id missing"
                )

            if metadata.get("status") not in {
                "done",
                "queued",
                "running",
            }:
                failures.append(
                    "unexpected scan status "
                    + str(metadata.get("status"))
                )

        if (
            row["submit_only"].lower()
            != "true"
            and not Path(
                row["result_zip"]
            ).is_file()
        ):
            failures.append(
                "summary ZIP missing"
            )

    if failures:
        for failure in failures:
            print(failure)

        raise SystemExit(1)

    print(
        f"Validated {args.candidate} "
        f"{args.scenario} attempt "
        f"{args.attempt}"
    )


if __name__ == "__main__":
    main()