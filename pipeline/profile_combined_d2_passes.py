#!/usr/bin/env python3

import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--selection",
        default=(
            "benchmark/"
            "combined-d2-passes-selection.csv"
        ),
    )

    parser.add_argument(
        "--modules",
        default=(
            "config/"
            "combined-d2-passes-modules.csv"
        ),
    )

    parser.add_argument(
        "--output",
        default=(
            "benchmark/"
            "combined-d2-passes-profile.csv"
        ),
    )

    args = parser.parse_args()

    selection_path = Path(
        args.selection
    )

    modules_path = Path(
        args.modules
    )

    output_path = Path(
        args.output
    )

    with selection_path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        selection = list(
            csv.DictReader(stream)
        )

    with modules_path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        modules = {
            row["candidate_id"]: row
            for row in csv.DictReader(stream)
        }

    fields = [
        "candidate_id",
        "group_id",
        "artifact_id",
        "version",
        "jdk_version",
        "maven_version",
        "module_path",
        "artifact_selector",
        "git_revision",
        "profile_status",
        "notes",
    ]

    rows = []

    for row in selection:
        candidate = row[
            "candidate_id"
        ]

        module = modules.get(
            candidate,
            {},
        )

        profile_status = (
            "READY"
            if module
            else "MODULE_MAPPING_MISSING"
        )

        rows.append({
            "candidate_id": candidate,
            "group_id": row.get(
                "group_id",
                "",
            ),
            "artifact_id": row.get(
                "artifact_id",
                "",
            ),
            "version": row.get(
                "version",
                "",
            ),
            "jdk_version": row.get(
                "jdk_version",
                "",
            ),
            "maven_version": row.get(
                "maven_version",
                "",
            ),
            "module_path": module.get(
                "module_path",
                "",
            ),
            "artifact_selector": (
                module.get(
                    "artifact_selector",
                    "",
                )
            ),
            "git_revision": row.get(
                "git_revision",
                "",
            ),
            "profile_status": (
                profile_status
            ),
            "notes": "",
        })

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

    print("Profiles:", len(rows))
    print("Output:", output_path)


if __name__ == "__main__":
    main()
