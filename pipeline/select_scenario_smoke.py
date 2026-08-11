#!/usr/bin/env python3

import csv
from pathlib import Path


SELECTION = Path(
    "benchmark/combined-d2-passes-selection.csv"
)

SAMPLE = Path(
    "benchmark/combined-d2-passes-sample.csv"
)

MODULES = Path(
    "config/combined-d2-passes-modules.csv"
)

PROFILE = Path(
    "benchmark/combined-d2-passes-profile.csv"
)

OUTPUT_SELECTION = Path(
    "benchmark/scenario-smoke-3-selection.csv"
)

OUTPUT_SAMPLE = Path(
    "benchmark/scenario-smoke-3-sample.csv"
)

OUTPUT_MODULES = Path(
    "config/scenario-smoke-3-modules.csv"
)


def load(path):
    with path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        reader = csv.DictReader(stream)

        return (
            list(reader),
            list(reader.fieldnames or []),
        )


def write(path, rows, fields):
    with path.open(
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


selection, selection_fields = load(
    SELECTION
)

sample, sample_fields = load(
    SAMPLE
)

modules, module_fields = load(
    MODULES
)

profile, _ = load(
    PROFILE
)

sample_by_id = {
    row["candidate_id"]: row
    for row in sample
}

module_by_id = {
    row["candidate_id"]: row
    for row in modules
}

profile_by_id = {
    row["candidate_id"]: row
    for row in profile
}

selected = []
seen_jdks = set()

for row in sorted(
    selection,
    key=lambda value: (
        value.get("jdk_version", ""),
        value["candidate_id"],
    ),
):
    candidate = row["candidate_id"]
    major = row.get(
        "jdk_version",
        "",
    ).split(".", 1)[0]

    if major in seen_jdks:
        continue

    if candidate not in sample_by_id:
        continue

    if candidate not in module_by_id:
        continue

    profile_row = profile_by_id.get(
        candidate,
        {},
    )

    if profile_row.get(
        "profile_status",
        "READY",
    ) != "READY":
        continue

    selected.append(row)
    seen_jdks.add(major)

    if len(selected) == 3:
        break

if len(selected) != 3:
    raise SystemExit(
        "ERROR: could not select three "
        "scenario candidates"
    )

selected_ids = {
    row["candidate_id"]
    for row in selected
}

write(
    OUTPUT_SELECTION,
    selected,
    selection_fields,
)

write(
    OUTPUT_SAMPLE,
    [
        sample_by_id[
            row["candidate_id"]
        ]
        for row in selected
    ],
    sample_fields,
)

write(
    OUTPUT_MODULES,
    [
        module_by_id[
            row["candidate_id"]
        ]
        for row in selected
    ],
    module_fields,
)

print("Selected scenario candidates:")

for row in selected:
    candidate = row["candidate_id"]

    print(
        candidate,
        row.get("jdk_version", ""),
        module_by_id[candidate].get(
            "module_path",
            "",
        ),
        sep="\t",
    )
