#!/usr/bin/env python3

import json
import re
import sys
from pathlib import Path

METHOD_PATTERNS = [
    re.compile(
        r"in method [`']"
        r"(?P<class>[A-Za-z0-9_.$]+):"
        r"(?P<method>[A-Za-z0-9_$<>]+)"
        r"(?:\([^`']*\))?"
        r"[`']"
    ),
    re.compile(
        r"(?:unknown|unsupported|failed).*method\s+"
        r"[`']?"
        r"(?P<class>[A-Za-z0-9_.$]+):"
        r"(?P<method>[A-Za-z0-9_$<>]+)"
    ),
]

seen = set()

for filename in sys.argv[1:]:
    path = Path(filename)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        text = str(data.get("error", ""))
    except (json.JSONDecodeError, OSError):
        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    for pattern in METHOD_PATTERNS:
        for match in pattern.finditer(text):
            key = (
                match.group("class"),
                match.group("method"),
            )

            if key in seen:
                continue

            seen.add(key)

            print(
                match.group("class"),
                match.group("method"),
                sep="\t",
            )
