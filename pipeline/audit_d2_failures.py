#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


SUCCESS_STATES = {
    "done",
    "completed",
    "succeeded",
    "success",
    "passed",
}


def load_csv(
    path: Path,
) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(
            f"ERROR: file does not exist: {path}"
        )

    if path.stat().st_size == 0:
        raise SystemExit(
            f"ERROR: file is empty: {path}"
        )

    with path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        rows = list(csv.DictReader(stream))

    if not rows:
        raise SystemExit(
            f"ERROR: file has no data rows: {path}"
        )

    return rows


def load_text(
    path: Path,
) -> str:
    if not path.is_file():
        return ""

    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


def classify_failure(
    row: dict[str, str],
    log_text: str,
) -> tuple[str, str, str]:
    runner_status = row.get(
        "status",
        "",
    ).strip()

    scan_status = row.get(
        "terminal_scan_status",
        "",
    ).strip().lower()

    terminal_error = row.get(
        "terminal_scan_error",
        "",
    )

    combined = (
        terminal_error
        + "\n"
        + log_text
    )

    lower = combined.lower()

    if scan_status in SUCCESS_STATES:
        return (
            "PASS",
            "NO",
            "Terminal backend success",
        )

    if runner_status == "PASS":
        return (
            "PASS",
            "NO",
            "Runner reported success",
        )

    if runner_status == "FAIL_ENVIRONMENT":
        return (
            "SETUP_OR_RUNNER_FAILURE",
            "YES",
            (
                "Inspect runner exception, paths, "
                "configuration, and toolchain"
            ),
        )

    if (
        runner_status == "FAIL_TIMEOUT"
        or row.get("exit_code", "") == "124"
    ):
        return (
            "TIMEOUT",
            "POSSIBLY",
            (
                "Determine whether Maven, frontend, "
                "engine, queue processing, or polling "
                "timed out"
            ),
        )

    scan_id = row.get(
        "scan_id",
        "",
    ).strip()

    if not scan_id:
        if (
            "could not resolve dependencies" in lower
            or "dependencyresolutionexception" in lower
            or "could not find artifact" in lower
        ):
            return (
                "MAVEN_DEPENDENCY_FAILURE",
                "POSSIBLY",
                (
                    "Inspect Nexus routing, repository "
                    "metadata, and missing dependencies"
                ),
            )

        if (
            "compilation failure" in lower
            or "compilation error" in lower
        ):
            return (
                "MAVEN_COMPILATION_FAILURE",
                "POSSIBLY",
                (
                    "Compare with D1 and verify module, "
                    "JDK, Maven, and profile consistency"
                ),
            )

        if (
            "pilot plugin is missing" in lower
            or "pluginresolutionexception" in lower
            or "could not find goal" in lower
        ):
            return (
                "PLUGIN_CONFIGURATION_FAILURE",
                "YES",
                (
                    "Verify Maven seed and plugin "
                    "coordinates"
                ),
            )

        return (
            "SUBMISSION_NOT_REACHED",
            "YES",
            "Inspect Maven and plugin logs",
        )

    if (
        "nestmember requires asm7" in lower
        or "nesthost requires asm7" in lower
        or "record requires asm" in lower
        or "permittedsubclass" in lower
    ):
        return (
            "FRONTEND_ASM_BYTECODE_LIMIT",
            "TOOL_UPDATE",
            (
                "Requires a newer ASM, Soot, or "
                "JSymCompiler frontend"
            ),
        )

    if (
        "unsupported class file major version"
        in lower
    ):
        return (
            "FRONTEND_CLASSFILE_VERSION_LIMIT",
            "TOOL_UPDATE",
            (
                "Frontend lacks support for the "
                "submitted class-file version"
            ),
        )

    if (
        "engine analysis pass failed" in lower
        and "syntax error" in lower
    ):
        return (
            "GENERATED_METH_SYNTAX",
            "TOOL_FIX",
            (
                "Frontend generated a method file "
                "that the Syrs parser rejected"
            ),
        )

    if (
        "slowcheckmemberaccess" in lower
        or "java/lang/reflect/accessibleobject"
        in lower
        or "java.lang.reflect.accessibleobject"
        in lower
        or "setaccessible" in lower
        or "trysetaccessible" in lower
    ):
        return (
            "PROBLEMATIC_REFLECTION_PATH",
            "EXCLUDE",
            (
                "Exclude under the unresolved "
                "reflection criterion"
            ),
        )

    if "frontend produced no method files" in lower:
        if (
            "phantomref" in lower
            or "phantom class" in lower
            or "classnotfoundexception" in lower
            or "noclassdeffounderror" in lower
            or "could not find class" in lower
            or "cannot find class" in lower
        ):
            return (
                "FRONTEND_MISSING_CLASSPATH",
                "POSSIBLY",
                (
                    "Review dependency capture and "
                    "worker classpath construction"
                ),
            )

        if (
            "methodshelper" in lower
            or "processinvokation" in lower
            or "processinvocation" in lower
            or "processassignment" in lower
            or "processassigment" in lower
            or "processmethodbody" in lower
        ):
            return (
                "FRONTEND_TRANSLATION_FAILURE",
                "TOOL_FIX",
                (
                    "JSymCompiler failed while "
                    "translating method bytecode"
                ),
            )

        return (
            "FRONTEND_NO_METHOD_FILES",
            "INVESTIGATE",
            "Inspect the complete frontend exception",
        )

    if (
        "policy" in lower
        and (
            "not found" in lower
            or "missing" in lower
            or "parse error" in lower
            or "invalid policy" in lower
        )
    ):
        return (
            "POLICY_CONFIGURATION_FAILURE",
            "YES",
            (
                "Verify submitted sources-and-sinks "
                "and mounted all.secstubs"
            ),
        )

    if (
        "connection refused" in lower
        or "connection reset" in lower
        or "service unavailable" in lower
        or "status_lookup_failed" in lower
        or "http 500" in lower
        or "http 502" in lower
        or "http 503" in lower
    ):
        return (
            "SERVICE_OR_NETWORK_FAILURE",
            "YES",
            (
                "Inspect API, worker, Docker, and "
                "cluster availability"
            ),
        )

    if scan_status in {
        "queued",
        "running",
    }:
        return (
            "NON_TERMINAL_SCAN",
            "YES",
            (
                "Re-query the API and inspect worker "
                "queue processing"
            ),
        )

    if scan_status == "failed":
        return (
            "ANALYZER_FAILURE_OTHER",
            "INVESTIGATE",
            "Inspect the terminal backend error",
        )

    return (
        "UNCLASSIFIED",
        "INVESTIGATE",
        "Manual review required",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify D2 outcomes using runner, "
            "terminal API, and Maven evidence."
        )
    )

    parser.add_argument(
        "--input",
        default=(
            "benchmark/"
            "d2-validation-summary-terminal.csv"
        ),
    )

    parser.add_argument(
        "--results-root",
        default="results/d2-validation-runs",
    )

    parser.add_argument(
        "--attempt",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--output",
        default="benchmark/d2-failure-audit.csv",
    )

    parser.add_argument(
        "--stats",
        default=(
            "benchmark/"
            "d2-failure-audit.stats.txt"
        ),
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    results_root = Path(args.results_root)
    output_path = Path(args.output)
    stats_path = Path(args.stats)

    rows = load_csv(input_path)
    audited = []

    for row in rows:
        candidate = row.get(
            "candidate_id",
            "",
        ).strip()

        if not candidate:
            continue

        possible_logs = []

        recorded_log = row.get(
            "log_path",
            "",
        ).strip()

        if recorded_log:
            possible_logs.append(
                Path(recorded_log)
            )

        possible_logs.append(
            results_root
            / candidate
            / "D2"
            / f"attempt-{args.attempt}"
            / "maven.log"
        )

        log_path = next(
            (
                path
                for path in possible_logs
                if path.is_file()
            ),
            possible_logs[-1],
        )

        log_text = load_text(log_path)

        (
            category,
            adjustable,
            recommended_action,
        ) = classify_failure(
            row,
            log_text,
        )

        terminal_error = row.get(
            "terminal_scan_error",
            "",
        ).replace(
            "\r",
            " ",
        ).replace(
            "\n",
            " ",
        )

        audited.append({
            "candidate_id": candidate,
            "runner_status": row.get(
                "status",
                "",
            ),
            "exit_code": row.get(
                "exit_code",
                "",
            ),
            "duration_seconds": row.get(
                "duration_seconds",
                "",
            ),
            "scan_id": row.get(
                "scan_id",
                "",
            ),
            "initial_scan_status": row.get(
                "scan_status",
                "",
            ),
            "terminal_scan_status": row.get(
                "terminal_scan_status",
                "",
            ),
            "category": category,
            "adjustable": adjustable,
            "recommended_action": (
                recommended_action
            ),
            "terminal_error": terminal_error,
            "log_path": str(log_path),
        })

    fields = [
        "candidate_id",
        "runner_status",
        "exit_code",
        "duration_seconds",
        "scan_id",
        "initial_scan_status",
        "terminal_scan_status",
        "category",
        "adjustable",
        "recommended_action",
        "terminal_error",
        "log_path",
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
        writer.writerows(audited)

    categories = Counter(
        row["category"]
        for row in audited
    )

    adjustability = Counter(
        row["adjustable"]
        for row in audited
    )

    runner_states = Counter(
        row["runner_status"] or "missing"
        for row in audited
    )

    terminal_states = Counter(
        row["terminal_scan_status"] or "missing"
        for row in audited
    )

    lines = [
        f"total={len(audited)}",
    ]

    for key, value in sorted(
        runner_states.items()
    ):
        lines.append(
            f"runner_{key}={value}"
        )

    for key, value in sorted(
        terminal_states.items()
    ):
        lines.append(
            f"terminal_{key}={value}"
        )

    for key, value in sorted(
        categories.items()
    ):
        lines.append(
            f"category_{key}={value}"
        )

    for key, value in sorted(
        adjustability.items()
    ):
        lines.append(
            f"adjustable_{key}={value}"
        )

    stats_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    stats_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote {len(audited)} rows to "
        f"{output_path}"
    )

    print(
        f"Wrote statistics to {stats_path}"
    )

    print("\nCategories:")

    for category, count in (
        categories.most_common()
    ):
        print(
            count,
            category,
            sep="\t",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
