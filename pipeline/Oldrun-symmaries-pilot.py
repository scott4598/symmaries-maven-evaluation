#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from datetime import datetime, timezone
from pathlib import Path


RESULT_FIELDS = [
    "candidate_id",
    "selection_role",
    "scenario_id",
    "attempt",
    "status",
    "exit_code",
    "started_at",
    "completed_at",
    "duration_seconds",
    "workspace_path",
    "maven_repo_path",
    "module_path",
    "build_command",
    "plugin_mode",
    "force_scan",
    "submit_only",
    "fail_on_error",
    "scan_id",
    "scan_status",
    "deduplicated",
    "jar_sha256",
    "result_zip",
    "metadata_file",
    "log_path",
    "failure_category",
    "notes",
]


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        return list(csv.DictReader(stream))


def ensure_results(path: Path) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if path.exists():
        return

    with path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=RESULT_FIELDS,
        )
        writer.writeheader()


def append_result(
    path: Path,
    row: dict[str, object],
) -> None:
    with path.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=RESULT_FIELDS,
        )

        writer.writerow({
            field: row.get(field, "")
            for field in RESULT_FIELDS
        })

        stream.flush()
        os.fsync(stream.fileno())


def completed_keys(
    path: Path,
) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()

    return {
        (
            row["candidate_id"],
            row["scenario_id"],
            row["attempt"],
        )
        for row in load_csv(path)
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def sudo_ready() -> bool:
    process = subprocess.run(
        ["sudo", "-n", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    return process.returncode == 0


def check_required_environment() -> None:
    required = [
        "RC_ROOT",
        "SYMMARIES_API_KEY",
        "SYMPROTO_NEXUS_USERNAME",
        "SYMPROTO_NEXUS_PASSWORD",
    ]

    missing = [
        variable
        for variable in required
        if not os.environ.get(variable, "").strip()
    ]

    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
        )


def locate_source_checkout(
    rc_root: Path,
    buildspec_path: str,
) -> Path:
    candidate_directory = (
        rc_root
        / Path(buildspec_path).parent
    )

    buildcache = (
        candidate_directory
        / "buildcache"
    )

    if not buildcache.is_dir():
        raise RuntimeError(
            f"Build cache does not exist: {buildcache}"
        )

    checkouts = [
        path
        for path in buildcache.iterdir()
        if path.is_dir()
        and (path / ".git").exists()
    ]

    if len(checkouts) != 1:
        raise RuntimeError(
            "Expected exactly one candidate checkout under "
            f"{buildcache}, found {len(checkouts)}"
        )

    return checkouts[0]


def prepare_workspace(
    source: Path,
    destination: Path,
) -> None:
    if destination.exists():
        shutil.rmtree(destination)

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    process = subprocess.run(
        [
            "git",
            "clone",
            "--local",
            "--no-hardlinks",
            str(source),
            str(destination),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "Unable to clone candidate workspace:\n"
            + process.stdout
        )

    source_head = subprocess.check_output(
        [
            "git",
            "-C",
            str(source),
            "rev-parse",
            "HEAD",
        ],
        text=True,
    ).strip()

    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "checkout",
            "--detach",
            source_head,
        ],
        check=True,
    )


def prepare_maven_repository(
    seed_repository: Path,
    destination: Path,
    cache_state: str,
) -> None:
    if destination.exists():
        shutil.rmtree(destination)

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    if cache_state == "warm":
        shutil.copytree(
            seed_repository,
            destination,
            dirs_exist_ok=True,
        )
        return

    # A cold scenario still needs the locally installed pilot plugin.
    plugin_relative = Path(
        "com/symmaries/"
        "symmaries-maven-plugin/"
        "0.1.0-pilot"
    )

    plugin_source = (
        seed_repository
        / plugin_relative
    )

    plugin_destination = (
        destination
        / plugin_relative
    )

    if not plugin_source.is_dir():
        raise RuntimeError(
            "Pilot plugin is missing from Maven seed: "
            f"{plugin_source}"
        )

    plugin_destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copytree(
        plugin_source,
        plugin_destination,
    )


def find_scan_metadata(
    workspace: Path,
    started_epoch: float,
) -> list[Path]:
    files: list[Path] = []

    for path in workspace.rglob("*-scan.json"):
        try:
            if path.stat().st_mtime >= started_epoch - 2:
                files.append(path)
        except OSError:
            continue

    return sorted(
        files,
        key=lambda path: path.stat().st_mtime,
    )


def find_summary_zips(
    workspace: Path,
    started_epoch: float,
) -> list[Path]:
    files: list[Path] = []

    for path in workspace.rglob("*-symmaries.zip"):
        try:
            if path.stat().st_mtime >= started_epoch - 2:
                files.append(path)
        except OSError:
            continue

    return sorted(
        files,
        key=lambda path: path.stat().st_mtime,
    )


def copy_outputs(
    sources: list[Path],
    destination: Path,
) -> list[Path]:
    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    copied: list[Path] = []

    for index, source in enumerate(
        sources,
        start=1,
    ):
        target = (
            destination / f"{index:03d}-{source.name}"
        )

        shutil.copy2(
            source,
            target,
        )

        copied.append(target)

    return copied



def classify(
    exit_code: int,
    metadata_files: list[Path],
    scenario: dict[str, str],
) -> tuple[str, str]:
    if exit_code == 124:
        return "FAIL_TIMEOUT", "TIMEOUT"

    if exit_code != 0:
        if (
            scenario["fail_on_error"].lower()
            == "false"
        ):
            return (
                "FAIL_BUILD",
                "BUILD_FAILED_IN_ADVISORY_SCENARIO",
            )

        return "FAIL_BUILD", "MAVEN_OR_PLUGIN_FAILURE"

    if (
        scenario["symmaries_enabled"].lower()
        == "true"
        and not metadata_files
    ):
        return (
            "FAIL_EVIDENCE",
            "SCAN_METADATA_MISSING",
        )

    return "PASS", ""


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--selection",
        default=(
            "benchmark/"
            "final-pilot-selection.csv"
        ),
    )

    parser.add_argument(
        "--scenarios",
        default="config/pilot-scenarios.csv",
    )

    parser.add_argument(
        "--modules",
        default=(
            "config/"
            "pilot-analysis-modules.csv"
        ),
    )

    parser.add_argument(
        "--sample",
        default="benchmark/screening-sample.csv",
    )

    parser.add_argument(
        "--results",
        default=(
            "benchmark/"
            "pilot-run-results.csv"
        ),
    )

    parser.add_argument(
        "--results-root",
        default="results/pilot-runs",
    )

    parser.add_argument(
        "--settings",
        default="config/settings-pilot-host.xml",
    )

    parser.add_argument(
        "--maven-seed",
        default="benchmark/maven-seed",
    )

    parser.add_argument(
        "--candidate",
        action="append",
    )

    parser.add_argument(
        "--scenario",
        action="append",
    )

    parser.add_argument(
        "--attempt",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--timeout-minutes",
        type=int,
        default=60,
    )

    parser.add_argument(
        "--rerun",
        action="store_true",
    )

    args = parser.parse_args()

    evaluation_root = Path.cwd().resolve()

    try:
        check_required_environment()
    except RuntimeError as exception:
        print(
            f"ERROR: {exception}",
            file=sys.stderr,
        )
        return 2

    if not sudo_ready():
        print(
            "ERROR: sudo credential unavailable. "
            "Run sudo -v.",
            file=sys.stderr,
        )
        return 3

    rc_root = Path(
        os.environ["RC_ROOT"]
    ).resolve()

    selection_rows = load_csv(
        evaluation_root / args.selection
    )

    scenario_rows = load_csv(
        evaluation_root / args.scenarios
    )

    module_rows = {
        row["candidate_id"]: row
        for row in load_csv(
            evaluation_root / args.modules
        )
    }

    sample_rows = {
        row["candidate_id"]: row
        for row in load_csv(
            evaluation_root / args.sample
        )
    }

    wanted_candidates = set(
        args.candidate or []
    )

    wanted_scenarios = set(
        args.scenario or []
    )

    results_path = (
        evaluation_root / args.results
    )

    ensure_results(results_path)

    done = completed_keys(results_path)

    settings_path = (
        evaluation_root / args.settings
    )

    seed_repository = (
        evaluation_root / args.maven_seed
    )

    results_root = (
        evaluation_root / args.results_root
    )

    workspaces_root = (
        evaluation_root
        / "work"
        / "pilot"
    )

    for selected in selection_rows:
        if (
            selected["selection_role"]
            != "pilot-primary"
        ):
            continue

        candidate_id = selected["candidate_id"]

        if (
            wanted_candidates
            and candidate_id
            not in wanted_candidates
        ):
            continue

        module = module_rows.get(candidate_id)

        if module is None:
            print(
                f"SKIP {candidate_id}: "
                "module mapping missing"
            )
            continue

        if module["artifact_selector"] == "none":
            print(
                f"SKIP {candidate_id}: "
                "no concrete JAR module selected"
            )
            continue

        sample = sample_rows[candidate_id]
        buildspec_path = sample[
            "buildspec_path"
        ]

        try:
            source_checkout = (
                locate_source_checkout(
                    rc_root,
                    buildspec_path,
                )
            )
        except RuntimeError as exception:
            print(
                f"SKIP {candidate_id}: {exception}"
            )
            continue

        for scenario in scenario_rows:
            scenario_id = scenario[
                "scenario_id"
            ]

            if (
                wanted_scenarios
                and scenario_id
                not in wanted_scenarios
            ):
                continue

            key = (
                candidate_id,
                scenario_id,
                str(args.attempt),
            )

            if key in done and not args.rerun:
                print(
                    f"SKIP {candidate_id} "
                    f"{scenario_id}: already recorded"
                )
                continue

            run_root = (
                results_root
                / candidate_id
                / scenario_id
                / f"attempt-{args.attempt}"
            )

            workspace = (
                workspaces_root
                / candidate_id
                / scenario_id
                / f"attempt-{args.attempt}"
                / "workspace"
            )

            maven_repo = (
                run_root
                / "m2"
            )

            log_path = run_root / "maven.log"
            environment_path = run_root / "run.env"
            output_directory = (
                run_root / "symmaries"
            )

            run_root.mkdir(
                parents=True,
                exist_ok=True,
            )

            started_at = utc_now()
            started_epoch = time.time()
            started = time.monotonic()

            exit_code = -1
            status = "FAIL_ENVIRONMENT"
            failure_category = ""
            command_text = ""
            metadata_files = []
            copied_metadata = []
            copied_summaries = []

            try:
                prepare_workspace(
                    source_checkout,
                    workspace,
                )

                prepare_maven_repository(
                    seed_repository,
                    maven_repo,
                    scenario["cache_state"],
                )

                module_path = module[
                    "module_path"
                ].strip()

                working_directory = (
                    workspace
                    if module_path in {"", "."}
                    else workspace / module_path
                )

                if not (
                    working_directory / "pom.xml"
                ).is_file():
                    raise RuntimeError(
                        "Selected module has no pom.xml: "
                        f"{working_directory}"
                    )

                command = [
                    "timeout",
                    f"{args.timeout_minutes}m",
                    "mvn",
                    "-B",
                    "-ntp",
                    "-s",
                    str(settings_path),
                    "-Dmaven.repo.local="
                    + str(maven_repo),
                ]

                plugin_enabled = (
                    scenario[
                        "symmaries_enabled"
                    ].lower()
                    == "true"
                )

                if plugin_enabled:
                    command.extend([
                        (
                            "com.symmaries:"
                            "symmaries-maven-plugin:"
                            "0.1.0-pilot:analyse"
                        ),
                        (
                            "-Dsymmaries.apiUrl="
                            "http://host.docker.internal:"
                            "8000"
                        ),
                        (
                            "-Dsymmaries.force="
                            + scenario["force_scan"]
                        ),
                        (
                            "-Dsymmaries.submitOnly="
                            + scenario["submit_only"]
                        ),
                        (
                            "-Dsymmaries.failOnError="
                            + scenario["fail_on_error"]
                        ),
                        (
                            "-Dsymmaries.timeoutSeconds="
                            "1800"
                        ),
                        (
                            "-Dsymmaries."
                            "pollIntervalSeconds=5"
                        ),
                        (
                            "-Dsymmaries."
                            "outputDirectory="
                            + str(output_directory)
                        ),
                    ])
                else:
                    command.extend([
                        "package",
                        "-DskipTests",
                    ])

                command_text = shlex.join(command)

                child_environment = os.environ.copy()

                child_environment["PATH"] = (
                    str(
                        evaluation_root
                        / "pipeline"
                        / "docker-wrapper"
                    )
                    + os.pathsep
                    + child_environment.get(
                        "PATH",
                        "",
                    )
                )

                child_environment[
                    "RB_OCI_ENGINE"
                ] = "docker"

                child_environment.pop(
                    "CI",
                    None,
                )

                with log_path.open(
                    "w",
                    encoding="utf-8",
                ) as log:
                    process = subprocess.run(
                        command,
                        cwd=working_directory,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        text=True,
                        env=child_environment,
                        check=False,
                    )

                exit_code = process.returncode

                metadata_files = (
                    find_scan_metadata(
                        workspace,
                        started_epoch,
                    )
                )

                summary_files = (
                    find_summary_zips(
                        workspace,
                        started_epoch,
                    )
                )

                copied_metadata = copy_outputs(
                    metadata_files,
                    output_directory
                    / "metadata",
                )

                copied_summaries = copy_outputs(
                    summary_files,
                    output_directory
                    / "summaries",
                )

                status, failure_category = (
                    classify(
                        exit_code,
                        metadata_files,
                        scenario,
                    )
                )

            except Exception as exception:
                failure_category = (
                    "RUNNER_EXCEPTION"
                )

                with log_path.open(
                    "a",
                    encoding="utf-8",
                ) as log:
                    log.write(
                        "\nRUNNER ERROR: "
                        + repr(exception)
                        + "\n"
                    )

            duration = round(
                time.monotonic() - started,
                3,
            )

            completed_at = utc_now()

            scan_id = ""
            scan_status = ""
            deduplicated = ""
            jar_sha256 = ""

            if copied_metadata:
                metadata_content = json.loads(
                    copied_metadata[-1].read_text(
                        encoding="utf-8",
                    )
                )

                scan_id = str(
                    metadata_content.get(
                        "scan_id",
                        "",
                    )
                )

                scan_status = str(
                    metadata_content.get(
                        "status",
                        "",
                    )
                )

                deduplicated = str(
                    metadata_content.get(
                        "deduplicated",
                        "",
                    )
                ).lower()

                jar_sha256 = str(
                    metadata_content.get(
                        "jar_sha256",
                        "",
                    )
                )

            environment_path.write_text(
                "\n".join([
                    f"candidate_id={candidate_id}",
                    f"scenario_id={scenario_id}",
                    f"attempt={args.attempt}",
                    f"started_at={started_at}",
                    f"completed_at={completed_at}",
                    f"duration_seconds={duration}",
                    f"exit_code={exit_code}",
                    f"status={status}",
                    (
                        "failure_category="
                        f"{failure_category}"
                    ),
                    f"workspace={workspace}",
                    f"maven_repo={maven_repo}",
                    (
                        "module_path="
                        f"{module['module_path']}"
                    ),
                    (
                        "scan_id="
                        f"{scan_id}"
                    ),
                    (
                        "scan_status="
                        f"{scan_status}"
                    ),
                    (
                        "deduplicated="
                        f"{deduplicated}"
                    ),
                    (
                        "jar_sha256="
                        f"{jar_sha256}"
                    ),
                ]) + "\n",
                encoding="utf-8",
            )

            append_result(
                results_path,
                {
                    "candidate_id": candidate_id,
                    "selection_role": selected[
                        "selection_role"
                    ],
                    "scenario_id": scenario_id,
                    "attempt": args.attempt,
                    "status": status,
                    "exit_code": exit_code,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "duration_seconds": duration,
                    "workspace_path": str(
                        workspace.relative_to(
                            evaluation_root
                        )
                    ),
                    "maven_repo_path": str(
                        maven_repo.relative_to(
                            evaluation_root
                        )
                    ),
                    "module_path": module[
                        "module_path"
                    ],
                    "build_command": command_text,
                    "plugin_mode": (
                        "explicit"
                        if scenario[
                            "symmaries_enabled"
                        ].lower()
                        == "true"
                        else "none"
                    ),
                    "force_scan": scenario[
                        "force_scan"
                    ],
                    "submit_only": scenario[
                        "submit_only"
                    ],
                    "fail_on_error": scenario[
                        "fail_on_error"
                    ],
                    "scan_id": scan_id,
                    "scan_status": scan_status,
                    "deduplicated": deduplicated,
                    "jar_sha256": jar_sha256,
                    "result_zip": (
                        str(copied_summaries[-1])
                        if copied_summaries
                        else ""
                    ),
                    "metadata_file": (
                        str(copied_metadata[-1])
                        if copied_metadata
                        else ""
                    ),
                    "log_path": str(
                        log_path.relative_to(
                            evaluation_root
                        )
                    ),
                    "failure_category": (
                        failure_category
                    ),
                    "notes": (
                        "Initial explicit-goal "
                        "Symmaries pilot runner"
                    ),
                },
            )

            print(
                f"{candidate_id} {scenario_id}: "
                f"{status} exit={exit_code} "
                f"duration={duration}s "
                f"scan={scan_id or 'NA'}",
                flush=True,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
