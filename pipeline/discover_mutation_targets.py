#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import re

from pathlib import Path


RETURN_PATTERN = re.compile(
    r"^(?P<indent>\s*)"
    r"return\s+"
    r"(?P<literal>"
    r"true|false|"
    r"-?[0-9]+(?:[lLfFdD])?|"
    r'"(?:[^"\\]|\\.)*"'
    r")"
    r"\s*;\s*$"
)


def load_csv(
    path: Path,
) -> list[dict[str, str]]:
    with path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        return list(csv.DictReader(stream))


def source_checkout(
    rc_root: Path,
    buildspec_path: str,
) -> Path:
    buildcache = (
        rc_root
        / Path(buildspec_path).parent
        / "buildcache"
    )

    checkouts = [
        path
        for path in buildcache.iterdir()
        if path.is_dir()
        and (path / ".git").exists()
    ]

    if len(checkouts) != 1:
        raise RuntimeError(
            "Expected one checkout in "
            f"{buildcache}, found {len(checkouts)}"
        )

    return checkouts[0]


def replacement(
    literal: str,
) -> str | None:
    if literal == "true":
        return "false"

    if literal == "false":
        return "true"

    if literal.startswith('"'):
        value = literal[1:-1]

        if not value:
            return '"symmaries-mutation"'

        return (
            '"'
            + value
            + "-symmaries-mutation"
            + '"'
        )

    suffix = ""

    if literal[-1:] in {
        "l",
        "L",
        "f",
        "F",
        "d",
        "D",
    }:
        suffix = literal[-1]
        number_text = literal[:-1]
    else:
        number_text = literal

    try:
        if any(
            character in number_text
            for character in ".eE"
        ):
            number = float(number_text)

            return (
                str(number + 1.0)
                + suffix
            )

        number = int(number_text)

        return (
            str(number + 1)
            + suffix
        )

    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--selection",
        type=Path,
        default=Path(
            "benchmark/"
            "scenario-smoke-3-selection.csv"
        ),
    )

    parser.add_argument(
        "--modules",
        type=Path,
        default=Path(
            "config/"
            "scenario-smoke-3-modules.csv"
        ),
    )

    parser.add_argument(
        "--rc-root",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "benchmark/"
            "scenario-smoke-3-"
            "mutation-candidates.csv"
        ),
    )

    args = parser.parse_args()

    selection = load_csv(
        args.selection
    )

    modules = {
        row["candidate_id"]: row
        for row in load_csv(
            args.modules
        )
    }

    results = []

    for selected in selection:
        candidate = selected[
            "candidate_id"
        ]

        module = modules[candidate][
            "module_path"
        ].strip()

        root = source_checkout(
            args.rc_root,
            selected["buildspec_path"],
        )

        module_root = (
            root
            if module in {
                "",
                ".",
            }
            else root / module
        )

        source_root = (
            module_root
            / "src"
            / "main"
            / "java"
        )

        if not source_root.is_dir():
            print(
                candidate,
                "NO_MAIN_JAVA",
                source_root,
                sep="\t",
            )
            continue

        for path in sorted(
            source_root.rglob("*.java")
        ):
            relative = path.relative_to(
                root
            )

            lines = path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()

            for line_number, line in enumerate(
                lines,
                1,
            ):
                match = RETURN_PATTERN.match(
                    line
                )

                if not match:
                    continue

                literal = match.group(
                    "literal"
                )

                new_literal = replacement(
                    literal
                )

                if new_literal is None:
                    continue

                old_statement = (
                    "return "
                    + literal
                    + ";"
                )

                new_statement = (
                    "return "
                    + new_literal
                    + ";"
                )

                full_text = "\n".join(
                    lines
                )

                occurrence_count = (
                    full_text.count(
                        old_statement
                    )
                )

                results.append({
                    "candidate_id": candidate,
                    "relative_source_path": (
                        str(relative)
                    ),
                    "line_number": (
                        line_number
                    ),
                    "old_literal": (
                        old_statement
                    ),
                    "new_literal": (
                        new_statement
                    ),
                    "occurrence_count": (
                        occurrence_count
                    ),
                    "source_line": (
                        line.strip()
                    ),
                })

    fields = [
        "candidate_id",
        "relative_source_path",
        "line_number",
        "old_literal",
        "new_literal",
        "occurrence_count",
        "source_line",
    ]

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with args.output.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(results)

    print("Candidate literals:", len(results))
    print("Output:", args.output)

    for candidate in sorted({
        row["candidate_id"]
        for row in selection
    }):
        unique = [
            row
            for row in results
            if row["candidate_id"]
            == candidate
            and row[
                "occurrence_count"
            ] == 1
        ]

        print(
            candidate,
            "unique_candidates="
            + str(len(unique)),
            sep="\t",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
