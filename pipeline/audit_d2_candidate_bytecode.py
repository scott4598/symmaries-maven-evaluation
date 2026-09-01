#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import struct
import zipfile
from collections import Counter
from pathlib import Path

REFLECTION_MARKERS = (
    b"java/lang/reflect/AccessibleObject",
    b"slowCheckMemberAccess",
    b"setAccessible",
    b"trySetAccessible",
)


def class_major_version(
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


def load_selection(
    path: Path,
) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"ERROR: selection file missing: {path}")

    with path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        rows = list(csv.DictReader(stream))

    candidate_ids = [
        row["candidate_id"] for row in rows if row.get("candidate_id")
    ]

    if not candidate_ids:
        raise SystemExit(f"ERROR: selection has no candidates: {path}")

    if len(candidate_ids) != len(set(candidate_ids)):
        raise SystemExit(
            "ERROR: selection contains duplicate candidate IDs"
        )

    return candidate_ids


def load_artifacts(
    path: Path,
) -> dict[str, dict[str, str]]:
    if not path.is_file():
        raise SystemExit(f"ERROR: artifact report missing: {path}")

    with path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        rows = list(csv.DictReader(stream))

    return {
        row["candidate_id"]: row
        for row in rows
        if row.get("candidate_id")
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit selected D2 JARs for class-file "
            "versions, module descriptors, nest "
            "attributes, records, sealed classes, "
            "and problematic reflection references."
        )
    )

    parser.add_argument(
        "--artifacts",
        required=True,
        help=(
            "Artifact verification CSV produced by prepare_d2_candidates.py"
        ),
    )

    parser.add_argument(
        "--selection",
        required=True,
        help="Accepted D2 candidate selection CSV",
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Output bytecode audit CSV",
    )

    args = parser.parse_args()

    artifact_path = Path(args.artifacts)
    selection_path = Path(args.selection)
    output_path = Path(args.output)

    candidate_ids = load_selection(selection_path)
    artifacts = load_artifacts(artifact_path)

    output_rows = []

    for candidate_id in candidate_ids:
        artifact = artifacts.get(candidate_id)

        if artifact is None:
            output_rows.append(
                {
                    "candidate_id": candidate_id,
                    "jar_path": "",
                    "jar_present": "false",
                    "audit_status": "ARTIFACT_RECORD_MISSING",
                    "class_count": "0",
                    "module_info_count": "0",
                    "nest_attribute_class_count": "0",
                    "record_attribute_class_count": "0",
                    "permitted_subclass_class_count": "0",
                    "reflection_marker_class_count": "0",
                    "class_major_versions": "",
                    "module_info_examples": "",
                    "nest_class_examples": "",
                    "record_class_examples": "",
                    "permitted_subclass_examples": "",
                    "reflection_examples": "",
                    "error": "",
                }
            )
            continue

        jar_path = Path(artifact.get("jar_path", ""))

        if not jar_path.is_file():
            output_rows.append(
                {
                    "candidate_id": candidate_id,
                    "jar_path": str(jar_path),
                    "jar_present": "false",
                    "audit_status": "JAR_MISSING",
                    "class_count": "0",
                    "module_info_count": "0",
                    "nest_attribute_class_count": "0",
                    "record_attribute_class_count": "0",
                    "permitted_subclass_class_count": "0",
                    "reflection_marker_class_count": "0",
                    "class_major_versions": "",
                    "module_info_examples": "",
                    "nest_class_examples": "",
                    "record_class_examples": "",
                    "permitted_subclass_examples": "",
                    "reflection_examples": "",
                    "error": "",
                }
            )
            continue

        class_count = 0
        class_versions: Counter[int] = Counter()

        module_info_classes = []
        nest_classes = []
        record_classes = []
        permitted_subclass_classes = []
        reflection_classes = []

        audit_status = "AUDITED"
        error_text = ""

        try:
            with zipfile.ZipFile(jar_path) as archive:
                for entry_name in archive.namelist():
                    if not entry_name.endswith(".class"):
                        continue

                    class_count += 1

                    try:
                        data = archive.read(entry_name)
                    except Exception as error:
                        audit_status = "PARTIAL_READ_FAILURE"
                        error_text += f"{entry_name}: {error!r}; "
                        continue

                    major = class_major_version(data)

                    if major is not None:
                        class_versions[major] += 1

                    if (
                        entry_name == "module-info.class"
                        or entry_name.endswith("/module-info.class")
                    ):
                        module_info_classes.append(entry_name)

                    if b"NestHost" in data or b"NestMembers" in data:
                        nest_classes.append(entry_name)

                    if b"Record" in data:
                        record_classes.append(entry_name)

                    if b"PermittedSubclasses" in data:
                        permitted_subclass_classes.append(entry_name)

                    if any(
                        marker in data for marker in REFLECTION_MARKERS
                    ):
                        reflection_classes.append(entry_name)

        except zipfile.BadZipFile as error:
            audit_status = "INVALID_JAR"
            error_text = repr(error)

        except OSError as error:
            audit_status = "JAR_READ_FAILURE"
            error_text = repr(error)

        output_rows.append(
            {
                "candidate_id": candidate_id,
                "jar_path": str(jar_path),
                "jar_present": "true",
                "audit_status": audit_status,
                "class_count": str(class_count),
                "module_info_count": str(len(module_info_classes)),
                "nest_attribute_class_count": str(len(nest_classes)),
                "record_attribute_class_count": str(
                    len(record_classes)
                ),
                "permitted_subclass_class_count": str(
                    len(permitted_subclass_classes)
                ),
                "reflection_marker_class_count": str(
                    len(reflection_classes)
                ),
                "class_major_versions": ";".join(
                    f"{major}:{count}"
                    for major, count in sorted(class_versions.items())
                ),
                "module_info_examples": " | ".join(
                    module_info_classes[:5]
                ),
                "nest_class_examples": " | ".join(nest_classes[:10]),
                "record_class_examples": " | ".join(
                    record_classes[:10]
                ),
                "permitted_subclass_examples": " | ".join(
                    permitted_subclass_classes[:10]
                ),
                "reflection_examples": " | ".join(
                    reflection_classes[:10]
                ),
                "error": error_text,
            }
        )

    fields = [
        "candidate_id",
        "jar_path",
        "jar_present",
        "audit_status",
        "class_count",
        "module_info_count",
        "nest_attribute_class_count",
        "record_attribute_class_count",
        "permitted_subclass_class_count",
        "reflection_marker_class_count",
        "class_major_versions",
        "module_info_examples",
        "nest_class_examples",
        "record_class_examples",
        "permitted_subclass_examples",
        "reflection_examples",
        "error",
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
        writer.writerows(output_rows)

    audited = [
        row
        for row in output_rows
        if row["audit_status"] == "AUDITED"
    ]

    missing = [
        row for row in output_rows if row["jar_present"] == "false"
    ]

    partial_or_invalid = [
        row
        for row in output_rows
        if row["audit_status"] not in {"AUDITED"}
        and row["jar_present"] == "true"
    ]

    nest_candidates = [
        row
        for row in output_rows
        if int(row["nest_attribute_class_count"]) > 0
    ]

    module_candidates = [
        row for row in output_rows if int(row["module_info_count"]) > 0
    ]

    record_candidates = [
        row
        for row in output_rows
        if int(row["record_attribute_class_count"]) > 0
    ]

    permitted_candidates = [
        row
        for row in output_rows
        if int(row["permitted_subclass_class_count"]) > 0
    ]

    reflection_candidates = [
        row
        for row in output_rows
        if int(row["reflection_marker_class_count"]) > 0
    ]

    print("Selected candidates:", len(candidate_ids))
    print("Successfully audited:", len(audited))
    print("Missing artifact records or JARs:", len(missing))
    print("Partial or invalid JAR audits:", len(partial_or_invalid))
    print("Candidates with module-info:", len(module_candidates))
    print("Candidates with nest attributes:", len(nest_candidates))
    print("Candidates with record markers:", len(record_candidates))
    print(
        "Candidates with permitted subclasses:",
        len(permitted_candidates),
    )
    print(
        "Candidates with reflection markers:",
        len(reflection_candidates),
    )
    print("Output:", output_path)

    if missing or partial_or_invalid:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
