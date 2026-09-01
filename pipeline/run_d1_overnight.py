#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--timeout-minutes", type=int, default=45)
    parser.add_argument("--selection", default="benchmark/d1-prepared-selection.csv")
    parser.add_argument("--sample", default="benchmark/d1-prepared-sample.csv")
    parser.add_argument("--modules", default="config/d1-prepared-modules.csv")
    parser.add_argument("--source-report", default="benchmark/source-preparation-results.csv")
    parser.add_argument("--environments", default="config/candidate-build-environments.json")
    parser.add_argument("--results", default="benchmark/d1-validation-results.csv")
    parser.add_argument("--results-root", default="results/d1-validation-runs")
    parser.add_argument("--output-selection", default="benchmark/d1-runnable-selection.csv")
    parser.add_argument("--output-sample", default="benchmark/d1-runnable-sample.csv")
    parser.add_argument("--output-modules", default="config/d1-runnable-modules.csv")
    parser.add_argument("--exclusions", default="benchmark/d1-runtime-exclusions.csv")
    parser.add_argument("--summary", default="benchmark/d1-validation-summary.csv")
    args = parser.parse_args()

    root = Path.cwd().resolve()
    required = [
        root / args.selection,
        root / args.sample,
        root / args.modules,
        root / args.source_report,
        root / args.environments,
        root / "config/pilot-scenarios.csv",
        root / "config/settings-pilot-host.xml",
        root / "config/policies/empty-sources-and-sinks.xml",
        root / "pipeline/run-symmaries-pilot.py",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        print("Missing required files:", *missing, sep="\n  ", file=sys.stderr)
        return 2

    selection_rows = load_csv(root / args.selection)
    sample_rows = {row["candidate_id"]: row for row in load_csv(root / args.sample)}
    module_rows = {row["candidate_id"]: row for row in load_csv(root / args.modules)}
    prepared = {
        row["candidate_id"]
        for row in load_csv(root / args.source_report)
        if row.get("status") in {"PREPARED", "EXISTS"}
    }
    environments = json.loads((root / args.environments).read_text(encoding="utf-8"))
    default_env = environments.get("default", {})

    runnable: list[dict[str, str]] = []
    exclusions: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in selection_rows:
        candidate = row["candidate_id"]
        if candidate in seen:
            exclusions.append({"candidate_id": candidate, "reason": "DUPLICATE_CANDIDATE_ID"})
            continue
        seen.add(candidate)
        if candidate not in prepared:
            exclusions.append({"candidate_id": candidate, "reason": "SOURCE_NOT_PREPARED"})
            continue
        if candidate not in sample_rows:
            exclusions.append({"candidate_id": candidate, "reason": "SAMPLE_METADATA_MISSING"})
            continue
        if candidate not in module_rows:
            exclusions.append({"candidate_id": candidate, "reason": "MODULE_MAPPING_MISSING"})
            continue
        requested = row.get("jdk_version", "").split(".", 1)[0]
        selected_env = dict(default_env)
        selected_env.update(environments.get(candidate, {}))
        java_home = selected_env.get("java_home", "")
        java_binary = Path(java_home) / "bin" / "java"
        if requested not in {"8", "11", "17", "21"}:
            exclusions.append({"candidate_id": candidate, "reason": f"UNSUPPORTED_JDK_{requested or 'UNKNOWN'}"})
            continue
        if not java_binary.is_file():
            exclusions.append({"candidate_id": candidate, "reason": f"JAVA_HOME_UNAVAILABLE:{java_home}"})
            continue
        runnable.append(row)

    if not runnable:
        print("No runnable candidates", file=sys.stderr)
        return 3

    selection_fields = list(selection_rows[0].keys())
    write_csv(root / args.output_selection, runnable, selection_fields)
    ids = {row["candidate_id"] for row in runnable}
    sample_out = [sample_rows[candidate] for candidate in ids]
    module_out = [module_rows[candidate] for candidate in ids]
    write_csv(root / args.output_sample, sample_out, list(sample_out[0].keys()))
    write_csv(root / args.output_modules, module_out, list(module_out[0].keys()))
    write_csv(root / args.exclusions, exclusions, ["candidate_id", "reason"])

    log_path = root / "results/d1-screening-driver" / f"attempt-{args.attempt}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    driver_path = root / "results/d1-screening-driver" / f"attempt-{args.attempt}-driver.csv"
    driver_rows: list[dict[str, str]] = []

    print(f"Runnable candidates: {len(runnable)}")
    print(f"Excluded candidates: {len(exclusions)}")
    print(f"Started: {utc_now()}")

    with log_path.open("a", encoding="utf-8") as log:
        for index, row in enumerate(runnable, start=1):
            candidate = row["candidate_id"]
            started = utc_now()
            heading = f"===== {started} [{index}/{len(runnable)}] {candidate} D1 ====="
            print(heading, flush=True)
            log.write(heading + "\n")
            log.flush()
            command = [
                sys.executable,
                "pipeline/run-symmaries-pilot.py",
                "--selection", args.output_selection,
                "--sample", args.output_sample,
                "--modules", args.output_modules,
                "--scenarios", "config/pilot-scenarios.csv",
                "--candidate", candidate,
                "--scenario", "D1",
                "--attempt", str(args.attempt),
                "--results", args.results,
                "--results-root", args.results_root,
                "--fallback-policy", "config/policies/empty-sources-and-sinks.xml",
                "--settings", "config/settings-pilot-host.xml",
                "--build-environments", args.environments,
                "--timeout-minutes", str(args.timeout_minutes),
            ]
            try:
                process = subprocess.run(
                    command,
                    cwd=root,
                    env=os.environ.copy(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    timeout=(
                        args.timeout_minutes * 60
                        + 300
                    ),
                )
                output = process.stdout or ""
                driver_exit_code = str(
                    process.returncode
                )
                driver_status = "INVOKED"

            except subprocess.TimeoutExpired as error:
                output = (
                    error.stdout or ""
                )

                if isinstance(output, bytes):
                    output = output.decode(
                        errors="replace"
                    )

                output += (
                    "\nDRIVER ERROR: runner exceeded "
                    "Maven timeout plus five minutes\n"
                )

                driver_exit_code = "124"
                driver_status = "RUNNER_TIMEOUT"

            except Exception as error:
                output = (
                    "\nDRIVER ERROR: "
                    + repr(error)
                    + "\n"
                )
                driver_exit_code = "-1"
                driver_status = "RUNNER_EXCEPTION"
            output = process.stdout or ""
            print(output, end="", flush=True)
            log.write(output)
            log.flush()
            driver_rows.append({
                "candidate_id": candidate,
                "started_at": started,
                "completed_at": utc_now(),
		"driver_exit_code": driver_exit_code,
		"driver_status": driver_status,
            })
            write_csv(driver_path, driver_rows, list(driver_rows[0].keys()))

    result_rows = load_csv(root / args.results) if (root / args.results).is_file() else []
    latest: dict[str, dict[str, str]] = {}
    for row in result_rows:
        if row.get("scenario_id") == "D1" and row.get("attempt") == str(args.attempt):
            latest[row["candidate_id"]] = row

    summaries: list[dict[str, str]] = []
    for row in runnable:
        candidate = row["candidate_id"]
        result = latest.get(candidate, {})
        run_env = parse_env(root / args.results_root / candidate / "D1" / f"attempt-{args.attempt}" / "run.env")
        summaries.append({
            "candidate_id": candidate,
            "jdk_requested": row.get("jdk_version", ""),
            "status": result.get("status", "NO_RESULT"),
            "exit_code": result.get("exit_code", ""),
            "duration_seconds": result.get("duration_seconds", ""),
            "failure_category": result.get("failure_category", ""),
            "maven_version": run_env.get("maven_version", ""),
            "java_version": run_env.get("java_version", ""),
            "java_home": run_env.get("java_home", ""),
            "module_path": result.get("module_path", row.get("module_path", "")),
            "log_path": result.get("log_path", ""),
        })
    summary_fields = list(summaries[0].keys())
    write_csv(root / args.summary, summaries, summary_fields)

    counts = Counter(row["status"] for row in summaries)
    stats_path = (root / args.summary).with_suffix(".stats.txt")
    lines = [
        f"attempt={args.attempt}",
        f"runnable={len(runnable)}",
        f"excluded={len(exclusions)}",
        *(f"status_{key}={value}" for key, value in sorted(counts.items())),
        f"completed_at={utc_now()}",
    ]
    stats_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"Summary: {root / args.summary}")
    print(f"Stats: {stats_path}")
    print(f"Log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
