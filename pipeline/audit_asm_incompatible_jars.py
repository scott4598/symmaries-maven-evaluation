#!/usr/bin/env python3

from __future__ import annotations

import csv
import struct
import zipfile
from collections import Counter
from pathlib import Path


def class_major(
    data: bytes,
) -> int | None:
    if len(data) < 8:
        return None

    if data[:4] != b"\xca\xfe\xba\xbe":
        return None

    return struct.unpack(
        ">H",
        data[6:8],
    )[0]


artifact_path = Path(
    "benchmark/"
    "d2-artifact-verification.csv"
)

audit_path = Path(
    "benchmark/d2-failure-audit.csv"
)

with artifact_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    artifacts = {
        row["candidate_id"]: row["jar_path"]
        for row in csv.DictReader(stream)
    }

with audit_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    failed = [
        row["candidate_id"]
        for row in csv.DictReader(stream)
        if row["category"]
        == "FRONTEND_ASM_BYTECODE_LIMIT"
    ]

rows = []

for candidate in failed:
    jar_value = artifacts.get(
        candidate,
        "",
    )

    jar_path = Path(jar_value)

    if not jar_path.is_file():
        rows.append({
            "candidate_id": candidate,
            "jar_path": jar_value,
            "jar_present": "false",
            "module_info_count": "0",
            "nest_attribute_class_count": "0",
            "class_major_versions": "",
            "module_info_examples": "",
            "nest_class_examples": "",
        })
        continue

    module_info = []
    nest_classes = []
    class_versions = Counter()

    with zipfile.ZipFile(jar_path) as archive:
        for name in archive.namelist():
            if not name.endswith(".class"):
                continue

            data = archive.read(name)
            major = class_major(data)

            if major is not None:
                class_versions[major] += 1

            if (
                name == "module-info.class"
                or name.endswith(
                    "/module-info.class"
                )
            ):
                module_info.append(name)

            if (
                b"NestHost" in data
                or b"NestMembers" in data
            ):
                nest_classes.append(name)

    rows.append({
        "candidate_id": candidate,
        "jar_path": str(jar_path),
        "jar_present": "true",
        "module_info_count": str(
            len(module_info)
        ),
        "nest_attribute_class_count": str(
            len(nest_classes)
        ),
        "class_major_versions": ";".join(
            f"{major}:{count}"
            for major, count
            in sorted(class_versions.items())
        ),
        "module_info_examples": " | ".join(
            module_info[:5]
        ),
        "nest_class_examples": " | ".join(
            nest_classes[:10]
        ),
    })

fields = [
    "candidate_id",
    "jar_path",
    "jar_present",
    "module_info_count",
    "nest_attribute_class_count",
    "class_major_versions",
    "module_info_examples",
    "nest_class_examples",
]

output = Path(
    "benchmark/d2-asm-jar-audit.csv"
)

with output.open(
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

print(
    f"Wrote {len(rows)} rows to {output}"
)

print(
    "JARs present:",
    sum(
        row["jar_present"] == "true"
        for row in rows
    ),
)

print(
    "With module-info:",
    sum(
        int(row["module_info_count"]) > 0
        for row in rows
    ),
)

print(
    "With nest attributes:",
    sum(
        int(
            row[
                "nest_attribute_class_count"
            ]
        ) > 0
        for row in rows
    ),
)
