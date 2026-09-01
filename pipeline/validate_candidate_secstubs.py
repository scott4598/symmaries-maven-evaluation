#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path


def substantive_lines(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--baseline",
        required=True,
    )

    parser.add_argument(
        "--proposal",
        required=True,
    )

    parser.add_argument(
        "--evidence-dir",
        required=True,
    )

    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    proposal_path = Path(args.proposal)
    evidence_dir = Path(args.evidence_dir)

    if not baseline_path.is_file():
        raise SystemExit(f"ERROR: baseline missing: {baseline_path}")

    if not proposal_path.is_file():
        raise SystemExit(f"ERROR: proposal missing: {proposal_path}")

    if proposal_path.stat().st_size == 0:
        raise SystemExit(f"ERROR: proposal empty: {proposal_path}")

    evidence_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    baseline = substantive_lines(baseline_path)

    proposal = substantive_lines(proposal_path)

    missing = sorted(baseline - proposal)

    added = sorted(proposal - baseline)

    prohibited_pattern = re.compile(
        r"slowCheckMemberAccess",
        re.IGNORECASE,
    )

    sensitive_pattern = re.compile(
        r"java\.lang\.reflect"
        r"|AccessibleObject"
        r"|setAccessible"
        r"|trySetAccessible"
        r"|slowCheckMemberAccess",
        re.IGNORECASE,
    )

    concurrency_pattern = re.compile(
        r"java\.util\.concurrent"
        r"|java\.lang\.Thread"
        r"|java\.lang\.Runnable",
        re.IGNORECASE,
    )

    exception_pattern = re.compile(
        r"java\.lang\.Throwable"
        r"|java\.lang\.Exception"
        r"|java\.lang\.RuntimeException",
        re.IGNORECASE,
    )

    collection_pattern = re.compile(
        r"java\.util\.Collection"
        r"|java\.util\.List"
        r"|java\.util\.Set"
        r"|java\.util\.Map"
        r"|java\.util\.Optional",
        re.IGNORECASE,
    )

    prohibited = [
        line for line in added if prohibited_pattern.search(line)
    ]

    sensitive = [
        line for line in added if sensitive_pattern.search(line)
    ]

    concurrency = [
        line for line in added if concurrency_pattern.search(line)
    ]

    exceptions = [
        line for line in added if exception_pattern.search(line)
    ]

    collections = [
        line for line in added if collection_pattern.search(line)
    ]

    outputs = {
        "new-generated-lines.txt": added,
        "missing-baseline-lines.txt": missing,
        "prohibited-reflection-lines.txt": prohibited,
        "new-sensitive-reflection-lines.txt": sensitive,
        "new-concurrency-lines.txt": concurrency,
        "new-exception-lines.txt": exceptions,
        "new-collection-lines.txt": collections,
    }

    for name, lines in outputs.items():
        path = evidence_dir / name

        path.write_text(
            "\n".join(lines) + ("\n" if lines else ""),
            encoding="utf-8",
        )

    report = [
        f"baseline_lines={len(baseline)}",
        f"proposal_lines={len(proposal)}",
        f"new_lines={len(added)}",
        f"missing_baseline_lines={len(missing)}",
        f"prohibited_reflection_lines={len(prohibited)}",
        f"sensitive_reflection_lines={len(sensitive)}",
        f"concurrency_lines={len(concurrency)}",
        f"exception_lines={len(exceptions)}",
        f"collection_lines={len(collections)}",
    ]

    report_path = (
        evidence_dir / "candidate-secstub-validation.stats.txt"
    )

    report_path.write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )

    print("\n".join(report))

    if missing:
        raise SystemExit("ERROR: proposal dropped baseline content")

    if prohibited:
        raise SystemExit("ERROR: slowCheckMemberAccess was added")

    print("Proposal passed automated integrity checks")

    if sensitive:
        print(
            "NOTICE: sensitive reflection additions "
            "require manual review before deployment"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
