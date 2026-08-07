#!/usr/bin/env python3
"""Run Reproducible Central screening builds and record structured results.

Run `sudo -v` before starting. The script uses `sudo -n` so it fails cleanly
rather than prompting inside a long campaign. It resumes by skipping candidate
attempts already present in the results CSV unless --rerun is supplied.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESULT_FIELDS = [
    "candidate_id", "cohort", "attempt", "status", "exit_code",
    "started_at", "completed_at", "duration_seconds",
    "buildcompare_path", "buildcompare_ko", "failure_category",
    "log_path", "notes",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def completed_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    return {
        (row["candidate_id"], row["attempt"])
        for row in load_rows(path)
    }


def ensure_results(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as stream:
            csv.DictWriter(stream, fieldnames=RESULT_FIELDS).writeheader()


def append_result(path: Path, row: dict[str, object]) -> None:
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writerow({key: row.get(key, "") for key in RESULT_FIELDS})
        stream.flush()
        os.fsync(stream.fileno())


def parse_ko(compare_path: Path) -> str:
    if not compare_path.is_file():
        return ""
    for line in compare_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("ko="):
            return line.split("=", 1)[1].strip()
    return ""

def discover_compare_files(
    candidate_root: Path,
    started_at_epoch: float,
    ) -> list[Path]:
    discovered = []

    if not candidate_root.is_dir():
        return discovered

    for path in candidate_root.rglob("*.buildcompare"):
        try:
            if path.stat().st_mtime >= started_at_epoch - 2:
                discovered.append(path)
        except OSError:
            continue

    return sorted(
        discovered,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

def classify(exit_code: int, ko: str, log_text: str) -> tuple[str, str]:
    if exit_code == 124:
        return "FAIL_TIMEOUT", "TIMEOUT"
    if "permission denied while trying to connect to the docker API" in log_text:
        return "FAIL_ENVIRONMENT", "DOCKER_PERMISSION"
    if "dos2unix: command not found" in log_text:
        return "FAIL_ENVIRONMENT", "MISSING_DOS2UNIX"
    if exit_code != 0:
        if re.search(r"repository not found|could not read from remote repository|fatal:.*not found", log_text, re.I):
            return "FAIL_SOURCE", "SOURCE_RETRIEVAL"
        if re.search(r"could not resolve|failed to collect dependencies|artifact.*not found", log_text, re.I):
            return "FAIL_DEPENDENCY", "DEPENDENCY_RESOLUTION"
        return "FAIL_BUILD", "RUNNER_OR_BUILD_FAILURE"
    if ko == "0":
        return "PASS_EXACT", ""
    if ko:
        return "PASS_BUILD_DIFFERENT", "ARTIFACT_DIFFERENCE"
    if re.search(
        r"BUILD SUCCESS",
        log_text,
        re.I,
    ):
        return "PASS_BUILD_NO_COMPARE", "NO_BUILD_COMPARE"

    return "FAIL_ENVIRONMENT", "NO_BUILD_COMPARE"


def sudo_ready() -> bool:
    result = subprocess.run(
        ["sudo", "-n", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def repair_ownership(rc_root: Path, owner: str, group: str) -> None:
    subprocess.run(
        [
            "sudo", "-n", "find", str(rc_root), "-xdev", "-user", "root",
            "-exec", "chown", f"{owner}:{group}", "{}", "+",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", default="benchmark/screening-sample.csv")
    parser.add_argument("--cohort", default="primary")
    parser.add_argument("--results", default="benchmark/reproduction-results.csv")
    parser.add_argument("--results-root", default="results/reproduction")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--timeout-minutes", type=int, default=20)
    parser.add_argument("--candidate", action="append", help="Run only this candidate; repeatable")
    parser.add_argument("--exclude", action="append", default=["RCM-08439"], help="Candidate to skip")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--rerun", action="store_true")
    args = parser.parse_args()

    evaluation_root = Path.cwd().resolve()
    rc_value = os.environ.get("RC_ROOT", "").strip()
    if not rc_value:
        print("ERROR: RC_ROOT is not set. Run: set -a; source .env; set +a", file=sys.stderr)
        return 2
    rc_root = Path(rc_value).resolve()
    if not (rc_root / "rebuild.sh").is_file():
        print(f"ERROR: rebuild.sh not found under RC_ROOT: {rc_root}", file=sys.stderr)
        return 2
    if shutil.which("dos2unix") is None:
        print("ERROR: dos2unix is not installed or not on PATH.", file=sys.stderr)
        return 2
    if not sudo_ready():
        print("ERROR: sudo credential is unavailable. Run `sudo -v` first.", file=sys.stderr)
        return 3

    sample_path = evaluation_root / args.sample
    results_path = evaluation_root / args.results
    results_root = evaluation_root / args.results_root
    logs_dir = results_root / "logs"
    metadata_dir = results_root / "metadata"
    logs_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    ensure_results(results_path)

    rows = load_rows(sample_path)
    wanted = set(args.candidate or [])
    excluded = set(args.exclude or [])
    done = completed_keys(results_path)
    selected = []

    for row in rows:
        cid = row["candidate_id"]
        key = (cid, str(args.attempt))

        if wanted and cid not in wanted:
            continue

        if cid in excluded:
            print(f"SKIP {cid}: explicit exclusion")
            continue

        build_command = row.get(
            "build_command",
            "",
        ).strip()

        if not build_command.startswith("mvn "):
            print(
                f"SKIP {cid}: non-standard or interactive "
                f"build command: {build_command}"
            )
            continue

        if key in done and not args.rerun:
            print(
                f"SKIP {cid}: attempt "
                f"{args.attempt} already recorded"
            )
            continue

        selected.append(row)
    if args.limit is not None:
        selected = selected[:args.limit]

    owner = os.environ.get("SUDO_USER") or os.environ.get("USER") or str(os.getuid())
    group = subprocess.check_output(["id", "-gn"], text=True).strip()

    for index, row in enumerate(selected, start=1):
        cid = row["candidate_id"]
        buildspec = row["buildspec_path"]
        buildspec_abs = rc_root / buildspec
        log_rel = Path(args.results_root) / "logs" / f"{cid}-attempt-{args.attempt}.log"
        log_abs = evaluation_root / log_rel
        compare_rel = Path(
            buildspec
        ).with_suffix(".buildcompare")

        compare_abs = rc_root / compare_rel

        artifact_compare_rel = (
            Path(buildspec).parent
            / (
                f"{row['artifact_id']}-"
                f"{row['version']}.buildcompare"
            )
        )

        artifact_compare_abs = (
            rc_root
            / artifact_compare_rel
        )

        compare_copy = (
            metadata_dir
            / f"{cid}-attempt-{args.attempt}.buildcompare"
        )

        env_file = (
            metadata_dir
            / f"{cid}-attempt-{args.attempt}.env"
        )

        # Remove comparison outputs from previous runs. A failed build must
        # not inherit a stale ko value.
        stale_compare_paths = {
            compare_abs,
            artifact_compare_abs,
        }

        subprocess.run(
            [
                "sudo",
                "-n",
                "rm",
                "-f",
                *(
                    str(path)
                    for path in stale_compare_paths
                ),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if compare_copy.exists():
            compare_copy.unlink()

        if not buildspec_abs.is_file():
            append_result(results_path, {
                "candidate_id": cid, "cohort": args.cohort, "attempt": args.attempt,
                "status": "FAIL_ENVIRONMENT", "exit_code": "",
                "started_at": utc_now(), "completed_at": utc_now(), "duration_seconds": 0,
                "buildcompare_path": str(compare_rel), "buildcompare_ko": "",
                "failure_category": "BUILDSPEC_MISSING", "log_path": str(log_rel),
                "notes": "Buildspec missing before execution",
            })
            continue

        if not sudo_ready():
            print("ERROR: sudo credential expired. Refresh with `sudo -v` and resume.", file=sys.stderr)
            return 3

        print(f"[{index}/{len(selected)}] RUN {cid} {buildspec}", flush=True)
        started_at = utc_now()
        started_epoch = time.time()
        started = time.monotonic()
        command = [
            "timeout",
            f"{args.timeout_minutes}m",
            "./rebuild.sh",
            buildspec,
        ]
        child_env = os.environ.copy()

        docker_wrapper_dir = (
            evaluation_root
            / "pipeline"
            / "docker-wrapper"
        )

        child_env["PATH"] = (
            str(docker_wrapper_dir)
            + os.pathsep
            + child_env.get("PATH", "")
        )

        child_env["RB_OCI_ENGINE"] = "docker"
        child_env.pop("CI", None)
        
        with log_abs.open("w", encoding="utf-8") as log:
            process = subprocess.run(
                command,
                cwd=rc_root,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                env=child_env,
            )
        duration = round(time.monotonic() - started, 3)
        completed_at = utc_now()
        exit_code = process.returncode
        log_text = log_abs.read_text(encoding="utf-8", errors="replace")
        ko = parse_ko(compare_abs)
        discovered_compare = ""

        # Initially record the buildspec-derived comparison path.
        selected_compare_abs = compare_abs
        recorded_compare_rel = compare_rel

        # Reproducible Central may name the comparison from the Maven
        # artifactId instead of the buildspec filename.
        if (
            not selected_compare_abs.is_file()
            and artifact_compare_abs.is_file()
        ):
            selected_compare_abs = artifact_compare_abs
            recorded_compare_rel = artifact_compare_rel
            discovered_compare = str(
                artifact_compare_rel
            )
            ko = parse_ko(
                selected_compare_abs
            )

        # As a final fallback, search for newly generated comparison
        # files. Select automatically only when exactly one exists.
        if not selected_compare_abs.is_file():
            candidate_root = buildspec_abs.parent

            discovered = discover_compare_files(
                candidate_root,
                started_epoch,
            )

            if len(discovered) == 1:
                selected_compare_abs = discovered[0]

                recorded_compare_rel = (
                    selected_compare_abs.relative_to(
                        rc_root
                    )
                )

                discovered_compare = str(
                    recorded_compare_rel
                )

                ko = parse_ko(
                    selected_compare_abs
                )

            elif len(discovered) > 1:
                discovered_compare = ";".join(
                    str(path.relative_to(rc_root))
                    for path in discovered
                )

        # The remaining copy and evidence logic uses the selected path.
        compare_abs = selected_compare_abs

        status, failure = classify(
            exit_code,
            ko,
            log_text,
        )

        log_claims_comparison = bool(
            re.search(
                r"rebuild comparison result:"
                r".*files match"
                r"|No issue found in "
                r".*\.buildcompare",
                log_text,
                re.I,
            )
        )

        if (
            status == "PASS_BUILD_NO_COMPARE"
            and log_claims_comparison
        ):
            failure = (
                "LOG_COMPARISON_BUT_FILE_MISSING"
            )

        evidence_copy_ok = False
        evidence_copy_error = ""

        if compare_abs.is_file():
            copy_process = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "cp",
                    str(compare_abs),
                    str(compare_copy),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )

            if copy_process.returncode == 0:
                chown_process = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "chown",
                        f"{owner}:{group}",
                        str(compare_copy),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )

                if (
                    chown_process.returncode == 0
                    and compare_copy.is_file()
                ):
                    evidence_copy_ok = True
                else:
                    evidence_copy_error = (
                        chown_process.stdout.strip()
                        or "Evidence chown failed"
                    )
            else:
                evidence_copy_error = (
                    copy_process.stdout.strip()
                    or "Evidence copy failed"
                )
                
        if (
            status == "PASS_EXACT"
            and not evidence_copy_ok
        ):
            failure = "EXACT_EVIDENCE_COPY_FAILED"

        env_file.write_text(
            "\n".join([
                f"candidate_id={cid}", f"cohort={args.cohort}",
                f"attempt={args.attempt}", f"buildspec_path={buildspec}",
                f"started_at={started_at}", f"completed_at={completed_at}",
                f"duration_seconds={duration}", f"exit_code={exit_code}",
                f"status={status}",
                f"failure_category={failure}",
                f"buildcompare_ko={ko}",
                (
                    "buildcompare_path="
                    f"{recorded_compare_rel}"
                ),
                "host_execution_user=normal",
                "docker_invocation=sudo-wrapper",
                "rb_oci_engine=docker",
                "ci_mode=false",
                (
                    "discovered_buildcompare="
                    f"{discovered_compare}"
                ),
                (
                    "evidence_copy="
                    f"{compare_copy.relative_to(evaluation_root)}"
                ),
                (
                    "evidence_copy_ok="
                    f"{str(evidence_copy_ok).lower()}"
                ),
                (
                    "evidence_copy_error="
                    f"{evidence_copy_error}"
                ),
            ]) + "\n",
            encoding="utf-8",
        )

        append_result(results_path, {
            "candidate_id": cid, "cohort": args.cohort, "attempt": args.attempt,
            "status": status, "exit_code": exit_code,
            "started_at": started_at, "completed_at": completed_at,
            "duration_seconds": duration,
            "buildcompare_path": str(recorded_compare_rel), "buildcompare_ko": ko,
            "failure_category": failure, "log_path": str(log_rel),
            "notes": (
                "Normal-user Reproducible Central host execution "
                "with sudo Docker wrapper and filtered Docker TTY flags"
            ),
        })
        repair_ownership(rc_root, owner, group)
        print(f"[{index}/{len(selected)}] {cid}: {status} exit={exit_code} ko={ko or 'NA'} duration={duration}s", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
