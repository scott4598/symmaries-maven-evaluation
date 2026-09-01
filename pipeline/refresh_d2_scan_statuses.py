#!/usr/bin/env python3

import argparse
import csv
import json
import os
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()

parser.add_argument(
    "--input",
    required=True,
)

parser.add_argument(
    "--output",
    required=True,
)

parser.add_argument(
    "--api-url",
    default="http://localhost:8000",
)

args = parser.parse_args()

api_key = os.environ.get(
    "SYMMARIES_API_KEY",
    "",
).strip()

if not api_key:
    raise SystemExit(
        "SYMMARIES_API_KEY is not set"
    )

input_path = Path(args.input)
output_path = Path(args.output)

with input_path.open(
    newline="",
    encoding="utf-8",
) as stream:
    reader = csv.DictReader(stream)
    rows = list(reader)
    input_fields = reader.fieldnames or []

extra_fields = [
    "terminal_scan_status",
    "terminal_scan_error",
    "server_started_at",
    "server_finished_at",
]

fields = input_fields + [
    field
    for field in extra_fields
    if field not in input_fields
]

for row in rows:
    scan_id = row.get(
        "scan_id",
        "",
    ).strip()

    row["terminal_scan_status"] = ""
    row["terminal_scan_error"] = ""
    row["server_started_at"] = ""
    row["server_finished_at"] = ""

    if not scan_id:
        continue

    url = (
        args.api_url.rstrip("/")
        + "/scans/"
        + scan_id
    )

    request = urllib.request.Request(
        url,
        headers={
            "X-Api-Key": api_key,
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            document = json.load(response)

        row["terminal_scan_status"] = str(
            document.get("status", "")
        )

        row["terminal_scan_error"] = str(
            document.get("error", "") or ""
        ).replace(
            "\r",
            " ",
        ).replace(
            "\n",
            " ",
        )

        row["server_started_at"] = str(
            document.get("started_at", "") or ""
        )

        row["server_finished_at"] = str(
            document.get("finished_at", "") or ""
        )

    except Exception as error:
        row["terminal_scan_status"] = (
            "STATUS_LOOKUP_FAILED"
        )

        row["terminal_scan_error"] = repr(
            error
        )

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

print(
    f"Wrote {len(rows)} records to "
    f"{output_path}"
)
