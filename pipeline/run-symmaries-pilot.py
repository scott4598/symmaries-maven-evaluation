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
    "policy_source",
    "policy_original_path",
    "policy_submitted_path",
    "policy_sha256",
    "build_command",
    "plugin_mode",
    "force_scan",
    "submit_only",
    "fail_on_error",
    "scan_id",
    "scan_status",
    "predecessor_scenario",
    "previous_scan_id",
    "expected_reuse_mode",
    "reuse_mode",
    "reuse_of",
    "incremental",
    "server_config_key",
    "method_hash_schema",
    "pa_archive_schema",
    "server_message",
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

def resolve_build_environment(
    candidate_id: str,
    working_directory: Path,
    configuration_path: Path,
) -> tuple[str, dict[str, str]]:
    configuration = json.loads(
        configuration_path.read_text(
            encoding="utf-8",
        )
    )

    selected = dict(
        configuration.get("default", {})
    )

    selected.update(
        configuration.get(candidate_id, {})
    )

    wrapper = working_directory / "mvnw"

    if wrapper.is_file():
        wrapper.chmod(
            wrapper.stat().st_mode | 0o111
        )
        maven_executable = str(wrapper)
    else:
        maven_executable = selected.get(
            "maven",
            "mvn",
        )

    executable_path = Path(maven_executable)

    if (
        maven_executable != "mvn"
        and not executable_path.is_file()
    ):
        raise RuntimeError(
            "Configured Maven executable does not exist: "
            + maven_executable
        )

    environment = os.environ.copy()

    java_home = selected.get("java_home", "")

    if java_home:
        java_home_path = Path(java_home)

        if not (
            java_home_path / "bin" / "java"
        ).is_file():
            raise RuntimeError(
                "Configured JAVA_HOME is invalid: "
                + java_home
            )

        environment["JAVA_HOME"] = java_home

        environment["PATH"] = (
            str(java_home_path / "bin")
            + os.pathsep
            + environment.get("PATH", "")
        )

    return maven_executable, environment

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

def remove_remote_repository_markers(
    repository_root: Path,
) -> None:
    marker_names = {
        "_remote.repositories",
        "resolver-status.properties",
    }

    for path in repository_root.rglob("*"):
        if not path.is_file():
            continue

        if (
            path.name in marker_names
            or path.name.endswith(".lastUpdated")
        ):
            path.unlink()

def prepare_maven_repository(
    seed_repository: Path,
    destination: Path,
    cache_state: str,
    plugin_required: bool,
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

        remove_remote_repository_markers(
            destination
        )

        return

    if not plugin_required:
        return

    plugin_relative = Path(
        "com/symmaries/"
        "symmaries-maven-plugin/"
        "0.1.1-pilot"
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

    remove_remote_repository_markers(
        plugin_destination
    )

def resolve_candidate_policy(
    working_directory: Path,
    fallback_policy: Path,
) -> tuple[str, Path, str]:
    candidate_policy = (
        working_directory
        / "src"
        / "main"
        / "symproto"
        / "sources-and-sinks.xml"
    )

    if candidate_policy.is_file():
        return (
            "candidate",
            candidate_policy,
            sha256_file(candidate_policy),
        )

    return (
        "benchmark-fallback",
        fallback_policy,
        sha256_file(fallback_policy),
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

def candidate_has_enabled_mutation_target(
    candidate_id: str,
    manifest_path: Path,
) -> bool:
    if not manifest_path.is_file():
        return False

    with manifest_path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        return any(
            row.get(
                "candidate_id",
                "",
            ).strip() == candidate_id
            and row.get(
                "enabled",
                "",
            ).strip().lower() == "true"
            for row in csv.DictReader(stream)
        )

def apply_manifest_mutations(
    candidate_id: str,
    scenario_id: str,
    workspace: Path,
    manifest_path: Path,
    mutation_count: int,
    mutation_percentage: float | None,
    report_path: Path,
) -> dict[str, object]:
    if mutation_count <= 0 and mutation_percentage is None:
        return {
            "eligible_targets": 0,
            "selected_targets": 0,
            "actual_percentage": 0.0,
        }

    if not manifest_path.is_file():
        raise RuntimeError(
            "Mutation manifest is missing: "
            + str(manifest_path)
        )

    with manifest_path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        targets = [
            row
            for row in csv.DictReader(stream)
            if row.get(
                "candidate_id",
                "",
            ).strip() == candidate_id
            and row.get(
                "enabled",
                "",
            ).strip().lower() == "true"
        ]

    targets.sort(
        key=lambda row: row["target_id"]
    )

    if not targets:
        raise RuntimeError(
            "No enabled mutation targets for "
            + candidate_id
        )

    if mutation_percentage is not None:
        requested = max(
            1,
            round(
                len(targets)
                * mutation_percentage
                / 100.0
            ),
        )
    else:
        requested = mutation_count

    selected_count = min(
        requested,
        len(targets),
    )

    selected = targets[
        :selected_count
    ]

    report_rows = []

    for target in selected:
        relative_path = Path(
            target[
                "relative_source_path"
            ]
        )

        source_path = (
            workspace
            / relative_path
        )

        if not source_path.is_file():
            raise RuntimeError(
                "Mutation source is missing: "
                + str(source_path)
            )

        original_bytes = (
            source_path.read_bytes()
        )

        original_sha256 = (
            hashlib.sha256(
                original_bytes
            ).hexdigest()
        )

        text = original_bytes.decode(
            "utf-8"
        )

        old_literal = target[
            "old_literal"
        ]

        new_literal = target[
            "new_literal"
        ]

        occurrences = text.count(
            old_literal
        )

        if occurrences != 1:
            raise RuntimeError(
                "Expected exactly one occurrence "
                f"of {old_literal!r} in "
                f"{source_path}; found "
                f"{occurrences}"
            )

        mutated = text.replace(
            old_literal,
            new_literal,
            1,
        )

        source_path.write_text(
            mutated,
            encoding="utf-8",
        )

        mutated_sha256 = (
            hashlib.sha256(
                source_path.read_bytes()
            ).hexdigest()
        )

        if (
            original_sha256
            == mutated_sha256
        ):
            raise RuntimeError(
                "Mutation did not alter "
                + str(source_path)
            )

        report_rows.append({
            "candidate_id": candidate_id,
            "scenario_id": scenario_id,
            "target_id": target[
                "target_id"
            ],
            "relative_source_path": (
                str(relative_path)
            ),
            "method_label": target[
                "method_label"
            ],
            "mutation_kind": target[
                "mutation_kind"
            ],
            "old_literal": old_literal,
            "new_literal": new_literal,
            "source_sha256_before": (
                original_sha256
            ),
            "source_sha256_after": (
                mutated_sha256
            ),
        })

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fields = [
        "candidate_id",
        "scenario_id",
        "target_id",
        "relative_source_path",
        "method_label",
        "mutation_kind",
        "old_literal",
        "new_literal",
        "source_sha256_before",
        "source_sha256_after",
    ]

    with report_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(
            report_rows
        )

    actual_percentage = (
        100.0
        * selected_count
        / len(targets)
    )

    metadata = {
        "candidate_id": candidate_id,
        "scenario_id": scenario_id,
        "eligible_targets": len(
            targets
        ),
        "selected_targets": (
            selected_count
        ),
        "requested_count": (
            mutation_count
        ),
        "requested_percentage": (
            mutation_percentage
        ),
        "actual_percentage": (
            actual_percentage
        ),
    }

    report_path.with_suffix(
        ".json"
    ).write_text(
        json.dumps(
            metadata,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return metadata

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
        "--fallback-policy",
        default=(
            "config/policies/"
            "empty-sources-and-sinks.xml"
        ),
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

    parser.add_argument(
        "--build-environments",
        default=(
            "config/"
            "candidate-build-environments.json"
        ),
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

    build_environment_path = (
        evaluation_root
        / args.build_environments
    ).resolve()

    fallback_policy = (
        evaluation_root
        / args.fallback_policy
    )

    if not fallback_policy.is_file():
        print(
            "ERROR: fallback policy does not exist: "
            f"{fallback_policy}",
            file=sys.stderr,
        )
        return 2

    results_root = (
        evaluation_root / args.results_root
    )

    workspaces_root = (
        evaluation_root
        / "work"
        / "pilot"
    )

    if not build_environment_path.is_file():
        print(
            "ERROR: build-environment configuration "
            "does not exist: "
            f"{build_environment_path}",
            file=sys.stderr,
        )
        return 2

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

        candidate_scan_ids = {
            row["scenario_id"]: row["scan_id"]
            for row in load_csv(results_path)
            if row.get("candidate_id") == candidate_id
            and row.get("attempt") == str(args.attempt)
            and row.get("scan_status") == "done"
            and row.get("scan_id")
        }

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

            predecessor_scenario = scenario.get(
                "predecessor_scenario",
                "",
            ).strip()
            expected_reuse_mode = scenario.get(
                "expected_reuse_mode",
                "",
            ).strip()
            previous_scan_id = ""

            if predecessor_scenario:
                previous_scan_id = candidate_scan_ids.get(
                    predecessor_scenario,
                    "",
                )
                if not previous_scan_id:
                    print(
                        f"SKIP {candidate_id} {scenario_id}: "
                        f"completed predecessor {predecessor_scenario} "
                        "is unavailable"
                    )
                    continue

            if (
                scenario.get("force_scan", "false").lower() == "true"
                and previous_scan_id
            ):
                print(
                    f"SKIP {candidate_id} {scenario_id}: force_scan=true "
                    "cannot be combined with a predecessor"
                )
                continue

            mutation_count = int(
                scenario.get(
                    "mutation_count",
                    "0",
                ).strip()
                or "0"
            )

            mutation_percentage_text = (
                scenario.get(
                    "mutation_percentage",
                    "",
                ).strip()
            )

            mutation_required = (
                mutation_count > 0
                or bool(
                    mutation_percentage_text
                )
            )

            mutation_manifest_path = (
                evaluation_root
                / "config"
                / "scenario-mutation-targets.csv"
            )

            if (
                mutation_required
                and not candidate_has_enabled_mutation_target(
                    candidate_id,
                    mutation_manifest_path,
                )
            ):
                print(
                    f"SKIP {candidate_id} "
                    f"{scenario_id}: no enabled "
                    "mutation target"
                )
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
            maven_executable = ""
            maven_version = ""
            java_version = ""
            child_environment = os.environ.copy()
            policy_source = ""
            policy_path = None
            policy_sha256 = ""
            metadata_files = []
            copied_metadata = []
            copied_summaries = []
            reuse_mode = ""
            reuse_of = ""
            incremental = ""
            server_config_key = ""
            method_hash_schema = ""
            pa_archive_schema = ""
            server_message = ""

            mutation_metadata = {
                "eligible_targets": 0,
                "selected_targets": 0,
                "actual_percentage": 0.0,
            }

            try:
                prepare_workspace(
                    source_checkout,
                    workspace,
                )

                mutation_percentage = (
                    float(
                        mutation_percentage_text
                    )
                    if mutation_percentage_text
                    else None
                )

                mutation_metadata = (
                    apply_manifest_mutations(
                        candidate_id=candidate_id,
                        scenario_id=scenario_id,
                        workspace=workspace,
                        manifest_path=(
                            mutation_manifest_path
                        ),
                        mutation_count=mutation_count,
                        mutation_percentage=(
                            mutation_percentage
                        ),
                        report_path=(
                            run_root
                            / "mutation-report.csv"
                        ),
                    )
                )

                plugin_enabled = (
                    scenario[
                        "symmaries_enabled"
                    ].lower()
                    == "true"
                )

                prepare_maven_repository(
                    seed_repository,
                    maven_repo,
                    scenario["cache_state"],
                    plugin_enabled,
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

                (
                    maven_executable,
                    child_environment,
                ) = resolve_build_environment(
                    candidate_id,
                    working_directory,
                    build_environment_path,
                )

                maven_version_output = (
                    subprocess.check_output(
                        [
                            maven_executable,
                            "-version",
                        ],
                        cwd=working_directory,
                        env=child_environment,
                        text=True,
                        stderr=subprocess.STDOUT,
                    )
                )

                maven_version = (
                    maven_version_output
                    .splitlines()[0]
                    .strip()
                )

                java_executable = (
                    Path(
                        child_environment[
                            "JAVA_HOME"
                        ]
                    )
                    / "bin"
                    / "java"
                )

                java_version_output = (
                    subprocess.check_output(
                        [
                            str(java_executable),
                            "-version",
                        ],
                        env=child_environment,
                        text=True,
                        stderr=subprocess.STDOUT,
                    )
                )

                java_version = (
                    java_version_output
                    .splitlines()[0]
                    .strip()
                )

                (
                    policy_source,
                    policy_path,
                    policy_sha256,
                ) = resolve_candidate_policy(
                    working_directory,
                    fallback_policy,
                )

                original_arguments = shlex.split(
                    sample["build_command"]
                )

                if (
                    not original_arguments
                    or original_arguments[0]
                    not in {
                        "mvn",
                        "mvnw",
                        "./mvnw",
                    }
                ):
                    raise RuntimeError(
                        "Unsupported candidate build command: "
                        + sample["build_command"]
                    )


                command = [
                    "timeout",
                    f"{args.timeout_minutes}m",
                    maven_executable,
                    "-B",
                    "-ntp",
                    "-s",
                    str(settings_path),
                    (
                        "-Dmaven.repo.local="
                        + str(maven_repo)
                    ),
                    *original_arguments[1:],
                ]

                if plugin_enabled:
                    command.extend([
                        (
                            "com.symmaries:"
                            "symmaries-maven-plugin:"
                            "0.1.1-pilot:analyse"
                        ),
                        (
                            "-Dsymmaries.apiUrl="
                            "http://localhost:8000"
                        ),
                        (
                            "-Dsymmaries.policyFile="
                            + str(policy_path)
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
                            "-Dsymmaries."
                            "timeoutSeconds=1800"
                        ),
                        (
                            "-Dsymmaries."
                            "pollIntervalSeconds=5"
                        ),
                    ])

                    if previous_scan_id:
                        command.append(
                            "-Dsymmaries.previousScanId="
                            + previous_scan_id
                        )

                command_text = shlex.join(command)

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
                reuse_mode = str(
                    metadata_content.get("reuse_mode", "")
                    or ""
                )
                reuse_of = str(
                    metadata_content.get("reuse_of", "")
                    or ""
                )
                incremental = str(
                    metadata_content.get("incremental", "")
                ).lower()
                server_config_key = str(
                    metadata_content.get("config_key", "")
                    or ""
                )
                method_hash_schema = str(
                    metadata_content.get("method_hash_schema", "")
                    or ""
                )
                pa_archive_schema = str(
                    metadata_content.get("pa_archive_schema", "")
                    or ""
                )
                server_message = str(
                    metadata_content.get(
                        "server_message",
                        metadata_content.get("message", ""),
                    )
                    or ""
                )

            if (
                status == "PASS"
                and scan_id
                and scan_status == "done"
            ):
                candidate_scan_ids[scenario_id] = scan_id

            if (
                status == "PASS"
                and expected_reuse_mode
                and expected_reuse_mode != "none"
                and reuse_mode != expected_reuse_mode
            ):
                status = "FAIL_EVIDENCE"
                failure_category = "UNEXPECTED_REUSE_MODE"

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
                    f"maven_version={maven_version}",
                    f"java_version={java_version}",
                    (
                        "failure_category="
                        f"{failure_category}"
                    ),
                    f"workspace={workspace}",
                    f"maven_repo={maven_repo}",
                    (
                        "maven_executable="
                        f"{maven_executable}"
                    ),
                    (
                        "java_home="
                        f"{child_environment.get('JAVA_HOME', '')}"
                    ),
                    (
                        "module_path="
                        f"{module['module_path']}"
                    ),
                    (
                        "policy_source="
                        f"{policy_source}"
                    ),
                    (
                        "policy_original_path="
                        f"{policy_path or ''}"
                    ),
                    (
                        "policy_submitted_path="
                        f"{policy_path or ''}"
                    ),
                    (
                        "policy_sha256="
                        f"{policy_sha256}"
                    ),
                    (
                        "scan_id="
                        f"{scan_id}"
                    ),
                    (
                        "scan_status="
                        f"{scan_status}"
                    ),
                    f"predecessor_scenario={predecessor_scenario}",
                    f"previous_scan_id={previous_scan_id}",
                    f"expected_reuse_mode={expected_reuse_mode}",
                    f"reuse_mode={reuse_mode}",
                    f"reuse_of={reuse_of}",
                    f"incremental={incremental}",
                    f"server_config_key={server_config_key}",
                    f"method_hash_schema={method_hash_schema}",
                    f"pa_archive_schema={pa_archive_schema}",
                    f"server_message={server_message}",
                    (
                        "deduplicated="
                        f"{deduplicated}"
                    ),
                    (
                        "jar_sha256="
                        f"{jar_sha256}"
                    ),
                    (
                        "mutation_target_count="
                        f"{mutation_metadata.get('selected_targets', 0)}"
                    ),
                    (
                        "mutation_eligible_count="
                        f"{mutation_metadata.get('eligible_targets', 0)}"
                    ),
                    (
                        "mutation_actual_percentage="
                        f"{mutation_metadata.get('actual_percentage', 0.0)}"
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
                    "policy_source": (
                        policy_source
                    ),
                    "policy_original_path": (
                        str(policy_path)
                        if policy_path
                        else ""
                    ),
                    "policy_submitted_path": (
                        str(policy_path)
                        if policy_path
                        else ""
                    ),
                    "policy_sha256": (
                        policy_sha256
                    ),
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
                    "predecessor_scenario": predecessor_scenario,
                    "previous_scan_id": previous_scan_id,
                    "expected_reuse_mode": expected_reuse_mode,
                    "reuse_mode": reuse_mode,
                    "reuse_of": reuse_of,
                    "incremental": incremental,
                    "server_config_key": server_config_key,
                    "method_hash_schema": method_hash_schema,
                    "pa_archive_schema": pa_archive_schema,
                    "server_message": server_message,
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
