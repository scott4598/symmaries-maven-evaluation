#!/usr/bin/env python3

import argparse
import csv
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()

parser.add_argument(
    "--selection",
    required=True,
)

parser.add_argument(
    "--rc-root",
    required=True,
)

parser.add_argument(
    "--report",
    required=True,
)

args = parser.parse_args()

selection = Path(args.selection)
rc_root = Path(args.rc_root).resolve()
report = Path(args.report)

with selection.open(
    newline="",
    encoding="utf-8",
) as stream:
    rows = list(csv.DictReader(stream))

results = []

for row in rows:
    candidate = row["candidate_id"]
    artifact = row["artifact_id"]
    version = row["version"]

    repository = row["git_repository"]
    revision = row["git_revision"]

    replacements = {
        "${artifactId}": artifact,
        "${version}": version,
    }

    for source, target in replacements.items():
        repository = repository.replace(
            source,
            target,
        )

        revision = revision.replace(
            source,
            target,
        )

    buildspec = Path(row["buildspec_path"])

    buildcache = (
        rc_root
        / buildspec.parent
        / "buildcache"
    )

    checkout = buildcache / candidate

    status = "FAILED"
    reason = ""

    try:
        buildcache.mkdir(
            parents=True,
            exist_ok=True,
        )

        if (
            checkout.is_dir()
            and (checkout / ".git").exists()
        ):
            status = "EXISTS"

        else:
            if checkout.exists():
                raise RuntimeError(
                    "Checkout path exists but is not "
                    f"a Git repository: {checkout}"
                )

            subprocess.run(
                [
                    "git",
                    "clone",
                    "--no-checkout",
                    repository,
                    str(checkout),
                ],
                check=True,
            )

            subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout),
                    "checkout",
                    "--detach",
                    revision,
                ],
                check=True,
            )

            status = "PREPARED"

    except Exception as error:
        reason = repr(error)

    print(
        candidate,
        status,
        reason,
        sep="\t",
        flush=True,
    )

    results.append({
        "candidate_id": candidate,
        "repository": repository,
        "revision": revision,
        "checkout": str(checkout),
        "status": status,
        "reason": reason,
    })

report.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with report.open(
    "w",
    newline="",
    encoding="utf-8",
) as stream:
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "candidate_id",
            "repository",
            "revision",
            "checkout",
            "status",
            "reason",
        ],
    )

    writer.writeheader()
    writer.writerows(results)

print(f"Report: {report}")
