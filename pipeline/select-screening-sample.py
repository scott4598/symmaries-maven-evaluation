#!/usr/bin/env python3
"""
Select a deterministic screening cohort from Reproducible Central Maven
build specifications.

This script is intended for the first-stage pilot screening process. It does
not produce the final performance benchmark. The selected projects must still
be reproduced, profiled, and checked for Symmaries compatibility before the
final 8 to 12-project pilot benchmark is chosen.

Default pilot policy
--------------------
- Include standard, container-compatible Maven builds only.
- Exclude special SHELL builds.
- Include Maven 3 builds only.
- Include JDK 8, 11, 17, and 21.
- Prefer stable releases.
- Prefer releases with explicit Maven versions.
- Prefer releases ending at verify over package, then other phases.
- Expand known Reproducible Central variables before repository deduplication.
- Select at most one release from each expanded source repository.
- Produce a primary sample and a reserve sample.
- Balance primary and reserve samples across JDK families.
- Preserve deterministic output using a fixed random seed.

Example
-------
pipeline/select-screening-sample \
    --candidates benchmark/candidates.csv \
    --primary-output benchmark/screening-sample.csv \
    --reserve-output benchmark/screening-reserve.csv \
    --report benchmark/manifests/screening-sample-report.json \
    --primary-size 40 \
    --reserve-size 20 \
    --seed 20260727 \
    --jdk 8 \
    --jdk 11 \
    --jdk 17 \
    --jdk 21
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


EXPECTED_COLUMNS = [
    "candidate_id",
    "buildspec_path",
    "buildspec_sha256",
    "group_id",
    "artifact_id",
    "version",
    "build_tool",
    "maven_version",
    "jdk_version",
    "git_repository",
    "git_revision",
    "source_distribution",
    "build_command",
    "reference_repository",
    "container_compatible",
    "special_shell_command",
    "status",
]

OUTPUT_EXTRA_COLUMNS = [
    "expanded_git_repository",
    "expanded_git_revision",
    "expanded_source_distribution",
    "project_key",
    "tool_family",
    "declared_maven_version",
    "maven_family",
    "jdk_family",
    "source_kind",
    "release_kind",
    "lifecycle_endpoint",
    "tests_skipped",
    "selection_score",
    "selection_cohort",
    "selection_stratum",
    "selection_reason",
    "sampling_seed",
    "sampling_rank",
]


def clean(value: str | None) -> str:
    """Return a stripped string, replacing None with an empty string."""

    return (value or "").strip()


def parse_bool(value: str | None) -> bool:
    """Parse common true-like values."""

    return clean(value).lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def stable_hash(seed: int, value: str) -> int:
    """
    Return a deterministic integer derived from the seed and supplied value.

    This is used instead of Python's built-in hash because Python randomises
    hash values between interpreter processes.
    """

    digest = hashlib.sha256(
        f"{seed}:{value}".encode("utf-8")
    ).digest()

    return int.from_bytes(digest[:8], byteorder="big")


def expand_known_variables(
    value: str,
    row: dict[str, str],
) -> str:
    """
    Expand Reproducible Central variables whose values are known from the row.

    Both braced and simple shell forms are supported, for example:

        ${artifactId}
        $artifactId
        ${version}
        $version

    Unknown variables are retained unchanged so they can be detected later.
    """

    result = clean(value)

    replacements = {
        "artifactId": clean(row.get("artifact_id")),
        "groupId": clean(row.get("group_id")),
        "version": clean(row.get("version")),
    }

    for variable, replacement in replacements.items():
        result = result.replace(
            "${" + variable + "}",
            replacement,
        )

        result = re.sub(
            rf"\${re.escape(variable)}\b",
            lambda _: replacement,
            result,
        )

    return result


def has_unresolved_variable(value: str) -> bool:
    """Return True if a shell-style variable remains in the value."""

    return bool(
        re.search(
            r"\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*",
            clean(value),
        )
    )


def normalise_repository_url(value: str) -> str:
    """
    Convert repository URLs into a stable project identity.

    The normalisation:
    - removes git+ prefixes;
    - handles git@host:path SSH notation;
    - removes trailing .git;
    - removes trailing slashes;
    - lowercases the resulting identity.
    """

    result = clean(value)

    result = re.sub(
        r"^git\+",
        "",
        result,
        flags=re.IGNORECASE,
    )

    ssh_match = re.match(
        r"^git@([^:]+):(.+)$",
        result,
    )

    if ssh_match:
        result = (
            f"https://{ssh_match.group(1)}/"
            f"{ssh_match.group(2)}"
        )

    result = re.sub(
        r"\.git$",
        "",
        result,
        flags=re.IGNORECASE,
    )

    result = result.rstrip("/")

    return result.lower()


def normalise_source_distribution(value: str) -> str:
    """Normalise a source-distribution URL or path for use as a project key."""

    return clean(value).rstrip("/").lower()


def parse_jdk_family(value: str) -> str:
    """
    Extract the JDK major version.

    Examples:
        17       -> 17
        17.0.12  -> 17
        21.ea    -> 21
    """

    match = re.match(
        r"^\s*(\d+)",
        clean(value),
    )

    if not match:
        return "unknown"

    return match.group(1)


def parse_tool_information(
    declared_tool: str,
) -> tuple[str, str, str]:
    """
    Parse a Reproducible Central Maven tool identifier.

    Returns:
        tool_family
        declared_maven_version
        maven_family

    Examples:
        mvn
            -> maven, "", default

        mvn-3.9.11
            -> maven, 3.9.11, 3.9

        mvn-4.0.0-rc-3
            -> maven, 4.0.0-rc-3, 4.x
    """

    tool = clean(declared_tool)

    if tool == "mvn":
        return "maven", "", "default"

    match = re.match(
        r"^mvn-(.+)$",
        tool,
    )

    if not match:
        return "unknown", "", "unknown"

    version = match.group(1)

    major_minor = re.match(
        r"^(\d+)\.(\d+)",
        version,
    )

    if not major_minor:
        return "maven", version, "unknown"

    major = major_minor.group(1)
    minor = major_minor.group(2)

    if major == "4":
        family = "4.x"
    else:
        family = f"{major}.{minor}"

    return "maven", version, family


def is_maven_4(
    declared_tool: str,
) -> bool:
    """Return True for Maven 4 tool declarations."""

    _, declared_version, family = parse_tool_information(
        declared_tool
    )

    return (
        family == "4.x"
        or declared_version.startswith("4.")
    )


def classify_release(version: str) -> str:
    """
    Classify a project version as stable or prerelease.

    Recognised prerelease markers include alpha, beta, RC, CR, milestone,
    snapshot, preview, candidate, early-access, and M-number releases.
    """

    value = clean(version)

    prerelease_pattern = re.compile(
        r"""
        (?:^|[._+\-])
        (
            alpha
            | beta
            | rc
            | cr
            | milestone
            | snapshot
            | preview
            | candidate
            | ea
            | m\d+
        )
        (?:[._+\-]?\d*)?
        (?:$|[._+\-])
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    if prerelease_pattern.search(value):
        return "prerelease"

    return "stable"


def version_sort_key(version: str) -> tuple:
    """
    Produce a practical sortable key for mixed Maven version strings.

    This is not intended to fully implement Maven ComparableVersion. It is
    used only to prefer newer-looking stable releases within the same
    repository, after prereleases have already been classified.

    Numeric components sort numerically. Text components sort lexically.
    """

    value = clean(version).lower()

    tokens = re.findall(
        r"\d+|[a-z]+",
        value,
    )

    key: list[tuple[int, int | str]] = []

    for token in tokens:
        if token.isdigit():
            key.append((1, int(token)))
        else:
            key.append((0, token))

    return tuple(key)


def identify_lifecycle_endpoint(
    command: str,
) -> str:
    """
    Infer the furthest explicitly named standard Maven lifecycle phase.

    This is a command-text classification. It does not attempt to reconstruct
    forked lifecycles or phases invoked internally by plugins.
    """

    value = clean(command)

    phases = [
        "validate",
        "initialize",
        "generate-sources",
        "process-sources",
        "generate-resources",
        "process-resources",
        "compile",
        "process-classes",
        "generate-test-sources",
        "process-test-sources",
        "generate-test-resources",
        "process-test-resources",
        "test-compile",
        "process-test-classes",
        "test",
        "prepare-package",
        "package",
        "pre-integration-test",
        "integration-test",
        "post-integration-test",
        "verify",
        "install",
        "deploy",
    ]

    discovered: list[tuple[int, str]] = []

    for index, phase in enumerate(phases):
        if re.search(
            rf"(?<![A-Za-z0-9_.:\-])"
            rf"{re.escape(phase)}"
            rf"(?![A-Za-z0-9_.:\-])",
            value,
        ):
            discovered.append((index, phase))

    if not discovered:
        return "other"

    return max(discovered)[1]


def command_skips_tests(command: str) -> bool:
    """Return True when common Maven test-skipping properties occur."""

    value = clean(command)

    patterns = [
        r"(?:^|\s)-DskipTests(?:\s|=|$)",
        r"(?:^|\s)-Dmaven\.test\.skip(?:\s|=|$)",
        r"(?:^|\s)-DskipITs(?:\s|=|$)",
    ]

    return any(
        re.search(pattern, value)
        for pattern in patterns
    )


def lifecycle_preference(endpoint: str) -> int:
    """
    Assign a preference score to lifecycle endpoints.

    Higher values indicate greater pilot interest. Verify is preferred because
    it can exercise later lifecycle behaviour. Package remains fully eligible.
    """

    preferences = {
        "deploy": 7,
        "install": 6,
        "verify": 5,
        "post-integration-test": 4,
        "integration-test": 4,
        "pre-integration-test": 4,
        "package": 3,
        "test": 2,
        "test-compile": 2,
        "compile": 1,
        "other": 0,
    }

    return preferences.get(endpoint, 0)


def maven_preference(
    declared_maven_version: str,
    maven_family: str,
) -> int:
    """
    Prefer explicit stable Maven 3 versions over the unspecified default.

    This does not claim that explicit Maven versions are intrinsically better.
    They are preferred for the screening sample because they improve
    environmental traceability.
    """

    if declared_maven_version.startswith("3.9."):
        return 4

    if declared_maven_version.startswith("3.8."):
        return 3

    if declared_maven_version.startswith("3."):
        return 2

    if maven_family == "default":
        return 1

    return 0


def source_kind(row: dict[str, str]) -> str:
    """Classify the source mechanism."""

    has_git = bool(
        clean(row.get("expanded_git_repository"))
    )

    has_distribution = bool(
        clean(row.get("expanded_source_distribution"))
    )

    if has_git and has_distribution:
        return "git_and_distribution"

    if has_git:
        return "git"

    if has_distribution:
        return "distribution"

    return "unknown"


def choose_project_key(
    row: dict[str, str],
) -> str:
    """
    Produce a repository-level identity used for project deduplication.

    Expanded Git repositories are preferred. If no Git source exists, the
    source distribution is used. Coordinates provide a final fallback.
    """

    repository = normalise_repository_url(
        row.get("expanded_git_repository", "")
    )

    if repository:
        return f"git:{repository}"

    distribution = normalise_source_distribution(
        row.get("expanded_source_distribution", "")
    )

    if distribution:
        return f"distribution:{distribution}"

    return (
        "coordinate:"
        f"{clean(row.get('group_id'))}:"
        f"{clean(row.get('artifact_id'))}"
    )


def secondary_stratum(
    row: dict[str, str],
) -> tuple[str, str]:
    """Return the Maven/lifecycle stratum used for within-JDK diversity."""

    return (
        clean(row.get("maven_family")),
        clean(row.get("lifecycle_endpoint")),
    )


def release_selection_score(
    row: dict[str, str],
) -> tuple:
    """
    Rank releases within the same project and JDK family.

    Ordering preference:
    1. stable release;
    2. explicit Maven version;
    3. Maven 3.9, then 3.8, then other Maven 3/default;
    4. verify or later lifecycle;
    5. Git-backed source;
    6. newer-looking version;
    7. deterministic candidate ID tie-break.
    """

    stable_score = (
        1
        if row["release_kind"] == "stable"
        else 0
    )

    explicit_maven_score = (
        1
        if row["declared_maven_version"]
        else 0
    )

    source_score = (
        1
        if row["source_kind"] in {
            "git",
            "git_and_distribution",
        }
        else 0
    )

    return (
        stable_score,
        explicit_maven_score,
        maven_preference(
            row["declared_maven_version"],
            row["maven_family"],
        ),
        lifecycle_preference(
            row["lifecycle_endpoint"]
        ),
        source_score,
        version_sort_key(row["version"]),
        row["candidate_id"],
    )


def release_selection_reason(
    row: dict[str, str],
) -> str:
    """Create a readable explanation of why a release was retained."""

    parts = [
        row["release_kind"],
        f"jdk-{row['jdk_family']}",
        (
            f"maven-{row['declared_maven_version']}"
            if row["declared_maven_version"]
            else "maven-default"
        ),
        f"endpoint-{row['lifecycle_endpoint']}",
        f"source-{row['source_kind']}",
    ]

    if row["tests_skipped"] == "true":
        parts.append("tests-skipped")
    else:
        parts.append("tests-not-explicitly-skipped")

    return "; ".join(parts)


def annotate_row(
    source_row: dict[str, str],
) -> dict[str, str]:
    """Add normalised and derived fields to a source candidate row."""

    row = dict(source_row)

    expanded_repository = expand_known_variables(
        row.get("git_repository", ""),
        row,
    )

    expanded_revision = expand_known_variables(
        row.get("git_revision", ""),
        row,
    )

    expanded_distribution = expand_known_variables(
        row.get("source_distribution", ""),
        row,
    )

    tool_family, declared_version, maven_family = (
        parse_tool_information(
            row.get("build_tool", "")
        )
    )

    row["expanded_git_repository"] = (
        expanded_repository
    )

    row["expanded_git_revision"] = (
        expanded_revision
    )

    row["expanded_source_distribution"] = (
        expanded_distribution
    )

    row["tool_family"] = tool_family

    row["declared_maven_version"] = (
        declared_version
    )

    row["maven_family"] = maven_family

    row["jdk_family"] = parse_jdk_family(
        row.get("jdk_version", "")
    )

    row["release_kind"] = classify_release(
        row.get("version", "")
    )

    row["lifecycle_endpoint"] = (
        identify_lifecycle_endpoint(
            row.get("build_command", "")
        )
    )

    row["tests_skipped"] = (
        "true"
        if command_skips_tests(
            row.get("build_command", "")
        )
        else "false"
    )

    row["source_kind"] = source_kind(row)

    row["project_key"] = choose_project_key(row)

    row["selection_score"] = repr(
        release_selection_score(row)
    )

    return row


def validate_input_columns(
    fieldnames: list[str],
) -> None:
    """Check that all required candidate columns are present."""

    missing = [
        column
        for column in EXPECTED_COLUMNS
        if column not in fieldnames
    ]

    if missing:
        raise ValueError(
            "Candidate CSV is missing required columns: "
            + ", ".join(missing)
        )


def filter_eligible_rows(
    rows: Iterable[dict[str, str]],
    allowed_jdks: set[str],
    include_prereleases: bool,
    include_maven_4: bool,
    include_shell: bool,
    require_git: bool,
) -> tuple[
    list[dict[str, str]],
    Counter,
]:
    """Apply the pilot screening eligibility policy."""

    eligible: list[dict[str, str]] = []
    exclusions: Counter = Counter()

    for source_row in rows:
        row = annotate_row(source_row)

        if clean(row.get("status")) != "DISCOVERED":
            exclusions["STATUS_NOT_DISCOVERED"] += 1
            continue

        if not include_shell and parse_bool(
            row.get("special_shell_command")
        ):
            exclusions["SPECIAL_SHELL_BUILD"] += 1
            continue

        if not include_shell and not parse_bool(
            row.get("container_compatible")
        ):
            exclusions[
                "NOT_CONTAINER_COMPATIBLE"
            ] += 1
            continue

        if row["tool_family"] != "maven":
            exclusions["NOT_MAVEN"] += 1
            continue

        if (
            not include_maven_4
            and is_maven_4(
                row.get("build_tool", "")
            )
        ):
            exclusions["MAVEN_4_EXCLUDED"] += 1
            continue

        if (
            allowed_jdks
            and row["jdk_family"] not in allowed_jdks
        ):
            exclusions[
                "JDK_OUTSIDE_ALLOWED_SET"
            ] += 1
            continue

        if (
            not include_prereleases
            and row["release_kind"] == "prerelease"
        ):
            exclusions["PRERELEASE_EXCLUDED"] += 1
            continue

        if row["source_kind"] == "unknown":
            exclusions["NO_SOURCE_LOCATION"] += 1
            continue

        if require_git and row["source_kind"] not in {
            "git",
            "git_and_distribution",
        }:
            exclusions["GIT_SOURCE_REQUIRED"] += 1
            continue

        if has_unresolved_variable(
            row["expanded_git_repository"]
        ):
            exclusions[
                "UNRESOLVED_REPOSITORY_VARIABLE"
            ] += 1
            continue
            
         if (
            "$(" in row["expanded_git_repository"]
            or "`" in row["expanded_git_repository"]
        ):
            exclusions[
                "UNRESOLVED_REPOSITORY_COMMAND"
            ] += 1
            continue   

        if (
            row["source_kind"] in {
                "git",
                "git_and_distribution",
            }
            and not clean(
                row["expanded_git_revision"]
            )
        ):
            exclusions["MISSING_GIT_REVISION"] += 1
            continue

        if not clean(row.get("build_command")):
            exclusions["MISSING_BUILD_COMMAND"] += 1
            continue

        eligible.append(row)

    return eligible, exclusions


def choose_best_release_per_project_and_jdk(
    rows: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Select one preferred release for every project/JDK combination.

    A project may remain represented in several JDK pools at this stage. The
    final cohort selection enforces global repository uniqueness.
    """

    grouped: dict[
        tuple[str, str],
        list[dict[str, str]],
    ] = defaultdict(list)

    for row in rows:
        key = (
            row["project_key"],
            row["jdk_family"],
        )

        grouped[key].append(row)

    selected: list[dict[str, str]] = []

    for releases in grouped.values():
        releases.sort(
            key=release_selection_score,
            reverse=True,
        )

        selected.append(releases[0])

    return selected


def balanced_targets(
    total_size: int,
    jdk_families: list[str],
) -> dict[str, int]:
    """
    Divide a sample size as evenly as possible among JDK families.

    Any remainder is allocated to earlier JDKs in the user-provided order.
    """

    if not jdk_families:
        return {}

    base = total_size // len(jdk_families)
    remainder = total_size % len(jdk_families)

    targets = {}

    for index, jdk in enumerate(jdk_families):
        targets[jdk] = (
            base
            + (1 if index < remainder else 0)
        )

    return targets


def order_jdk_pool(
    rows: list[dict[str, str]],
    seed: int,
    cohort_name: str,
) -> list[dict[str, str]]:
    """
    Order a JDK pool using a round-robin across Maven/lifecycle strata.

    This avoids filling an entire JDK quota with the largest secondary stratum.
    """

    strata: dict[
        tuple[str, str],
        list[dict[str, str]],
    ] = defaultdict(list)

    for row in rows:
        strata[secondary_stratum(row)].append(row)

    for stratum, members in strata.items():
        members.sort(
            key=lambda row: stable_hash(
                seed,
                (
                    f"{cohort_name}:"
                    f"{stratum}:"
                    f"{row['project_key']}:"
                    f"{row['candidate_id']}"
                ),
            )
        )

    stratum_keys = sorted(
        strata,
        key=lambda key: (
            stable_hash(
                seed,
                f"{cohort_name}:{key}",
            ),
            key,
        ),
    )

    result: list[dict[str, str]] = []

    index = 0

    while True:
        added = False

        for key in stratum_keys:
            members = strata[key]

            if index < len(members):
                result.append(members[index])
                added = True

        if not added:
            break

        index += 1

    return result


def select_cohort(
    candidate_rows,
    jdk_families,
    target_size,
    seed,
    cohort_name,
    already_used_projects=None,
):
    """
    Select a JDK-balanced cohort while enforcing project uniqueness.

    The function first tries to satisfy each JDK target. If a JDK pool cannot
    supply enough unique projects, the remaining places are redistributed
    across the other JDK pools.
    """

    used_projects = set(already_used_projects or set())

    targets = balanced_targets(
        target_size,
        jdk_families,
    )

    pools = {}

    for jdk in jdk_families:
        members = [
            row
            for row in candidate_rows
            if row["jdk_family"] == jdk
        ]

        pools[jdk] = order_jdk_pool(
            members,
            seed,
            cohort_name,
        )

    selected = []
    selected_by_jdk = Counter()

    positions = {
        jdk: 0
        for jdk in jdk_families
    }

    def take_next(jdk):
        pool = pools[jdk]

        while positions[jdk] != len(pool):
            row = pool[positions[jdk]]
            positions[jdk] += 1

            if row["project_key"] in used_projects:
                continue

            used_projects.add(row["project_key"])
            selected.append(row)
            selected_by_jdk[jdk] += 1

            return True

        return False

    jdk_order = sorted(
        jdk_families,
        key=lambda jdk: stable_hash(
            seed,
            f"{cohort_name}:jdk:{jdk}",
        ),
    )

    # First pass: fill each JDK's assigned quota.
    for jdk in jdk_order:
        while True:
            if selected_by_jdk[jdk] >= targets[jdk]:
                break

            if len(selected) >= target_size:
                break

            if not take_next(jdk):
                break

    # Second pass: redistribute unfilled places.
    while True:
        if len(selected) >= target_size:
            break

        progress = False

        redistribution_order = sorted(
            jdk_families,
            key=lambda jdk: (
                selected_by_jdk[jdk],
                stable_hash(
                    seed,
                    f"{cohort_name}:redistribute:{jdk}",
                ),
            ),
        )

        for jdk in redistribution_order:
            if len(selected) >= target_size:
                break

            if take_next(jdk):
                progress = True

        if not progress:
            break

    # Defensive size check. This prevents accidental oversampling even if
    # selection logic is modified later.
    selected = selected[:target_size]

    selected.sort(
        key=lambda row: stable_hash(
            seed,
            (
                f"{cohort_name}:output:"
                f"{row['project_key']}:"
                f"{row['candidate_id']}"
            ),
        )
    )

    final_distribution = Counter(
        row["jdk_family"]
        for row in selected
    )

    return selected, dict(final_distribution)


def write_cohort_csv(
    output_path: Path,
    rows: list[dict[str, str]],
    input_fieldnames: list[str],
    cohort_name: str,
    seed: int,
) -> None:
    """Write a selected cohort to CSV."""

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_fields = (
        input_fieldnames
        + OUTPUT_EXTRA_COLUMNS
    )

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=output_fields,
            extrasaction="ignore",
        )

        writer.writeheader()

        for rank, source_row in enumerate(
            rows,
            start=1,
        ):
            row = dict(source_row)

            row["selection_cohort"] = cohort_name

            row["selection_stratum"] = (
                f"jdk={row['jdk_family']}|"
                f"maven={row['maven_family']}|"
                f"endpoint={row['lifecycle_endpoint']}"
            )

            row["selection_reason"] = (
                release_selection_reason(row)
            )

            row["sampling_seed"] = str(seed)

            row["sampling_rank"] = str(rank)

            writer.writerow(row)


def candidate_ids(
    rows: Iterable[dict[str, str]],
) -> list[str]:
    """Return candidate IDs from selected rows."""

    return [
        row["candidate_id"]
        for row in rows
    ]


def distribution(
    rows: Iterable[dict[str, str]],
    field: str,
) -> dict[str, int]:
    """Return a sorted count distribution for a field."""

    counts = Counter(
        clean(row.get(field))
        for row in rows
    )

    return dict(
        sorted(counts.items())
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Select a deterministic, balanced pilot "
            "screening cohort from Reproducible Central "
            "Maven candidate metadata."
        )
    )

    parser.add_argument(
        "--candidates",
        default="benchmark/candidates.csv",
        help=(
            "Input candidate CSV. "
            "Default: benchmark/candidates.csv"
        ),
    )

    parser.add_argument(
        "--primary-output",
        default="benchmark/screening-sample.csv",
        help=(
            "Primary screening sample CSV. "
            "Default: benchmark/screening-sample.csv"
        ),
    )

    parser.add_argument(
        "--reserve-output",
        default="benchmark/screening-reserve.csv",
        help=(
            "Reserve sample CSV. "
            "Default: benchmark/screening-reserve.csv"
        ),
    )

    parser.add_argument(
        "--report",
        default=(
            "benchmark/manifests/"
            "screening-sample-report.json"
        ),
        help=(
            "Sampling report JSON. "
            "Default: benchmark/manifests/"
            "screening-sample-report.json"
        ),
    )

    parser.add_argument(
        "--primary-size",
        type=int,
        default=40,
        help="Primary cohort size. Default: 40",
    )

    parser.add_argument(
        "--reserve-size",
        type=int,
        default=20,
        help="Reserve cohort size. Default: 20",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=20260727,
        help=(
            "Deterministic sampling seed. "
            "Default: 20260727"
        ),
    )

    parser.add_argument(
        "--jdk",
        dest="jdks",
        action="append",
        help=(
            "Allowed JDK major version. Repeat this option. "
            "Default: 8, 11, 17, and 21."
        ),
    )

    parser.add_argument(
        "--include-prereleases",
        action="store_true",
        help=(
            "Allow alpha, beta, RC, milestone, snapshot, "
            "preview and early-access releases."
        ),
    )

    parser.add_argument(
        "--include-maven-4",
        action="store_true",
        help=(
            "Include Maven 4 alpha, beta and RC builds. "
            "Excluded by default."
        ),
    )

    parser.add_argument(
        "--include-shell",
        action="store_true",
        help=(
            "Include special SHELL builds. "
            "Excluded by default."
        ),
    )

    parser.add_argument(
        "--allow-distribution-only",
        action="store_true",
        help=(
            "Allow source-distribution-only candidates. "
            "By default, a Git source is required."
        ),
    )

    args = parser.parse_args()

    if args.primary_size < 1:
        parser.error(
            "--primary-size must be at least 1"
        )

    if args.reserve_size < 0:
        parser.error(
            "--reserve-size cannot be negative"
        )

    jdk_families = (
        args.jdks
        if args.jdks
        else ["8", "11", "17", "21"]
    )

    # Preserve user order while removing duplicates.
    jdk_families = list(
        dict.fromkeys(jdk_families)
    )

    input_path = Path(args.candidates)
    primary_path = Path(args.primary_output)
    reserve_path = Path(args.reserve_output)
    report_path = Path(args.report)

    if not input_path.is_file():
        print(
            f"ERROR: Candidate CSV not found: "
            f"{input_path}",
            file=sys.stderr,
        )
        return 1

    with input_path.open(
        newline="",
        encoding="utf-8",
    ) as stream:
        reader = csv.DictReader(stream)

        input_fieldnames = (
            reader.fieldnames or []
        )

        validate_input_columns(
            input_fieldnames
        )

        source_rows = list(reader)

    eligible_releases, exclusions = (
        filter_eligible_rows(
            rows=source_rows,
            allowed_jdks=set(jdk_families),
            include_prereleases=(
                args.include_prereleases
            ),
            include_maven_4=(
                args.include_maven_4
            ),
            include_shell=(
                args.include_shell
            ),
            require_git=(
                not args.allow_distribution_only
            ),
        )
    )

    project_jdk_candidates = (
        choose_best_release_per_project_and_jdk(
            eligible_releases
        )
    )

    unique_eligible_projects = {
        row["project_key"]
        for row in project_jdk_candidates
    }

    primary, primary_by_jdk = select_cohort(
        candidate_rows=project_jdk_candidates,
        jdk_families=jdk_families,
        target_size=args.primary_size,
        seed=args.seed,
        cohort_name="primary",
    )

    primary_projects = {
        row["project_key"]
        for row in primary
    }

    reserve, reserve_by_jdk = select_cohort(
        candidate_rows=project_jdk_candidates,
        jdk_families=jdk_families,
        target_size=args.reserve_size,
        seed=args.seed,
        cohort_name="reserve",
        already_used_projects=primary_projects,
    )

    write_cohort_csv(
        output_path=primary_path,
        rows=primary,
        input_fieldnames=input_fieldnames,
        cohort_name="primary",
        seed=args.seed,
    )

    write_cohort_csv(
        output_path=reserve_path,
        rows=reserve,
        input_fieldnames=input_fieldnames,
        cohort_name="reserve",
        seed=args.seed,
    )

    report = {
        "generated_at": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        ),
        "input": str(input_path),
        "input_sha256": hashlib.sha256(
            input_path.read_bytes()
        ).hexdigest(),
        "input_candidate_count": len(
            source_rows
        ),
        "eligible_release_count": len(
            eligible_releases
        ),
        "eligible_project_jdk_count": len(
            project_jdk_candidates
        ),
        "eligible_unique_project_count": len(
            unique_eligible_projects
        ),
        "policy": {
            "allowed_jdks": jdk_families,
            "include_prereleases": (
                args.include_prereleases
            ),
            "include_maven_4": (
                args.include_maven_4
            ),
            "include_shell": (
                args.include_shell
            ),
            "require_git": (
                not args.allow_distribution_only
            ),
            "one_release_per_project": True,
            "repository_variables_expanded": True,
            "stable_releases_preferred": True,
            "explicit_maven_versions_preferred": True,
            "verify_lifecycle_preferred": True,
        },
        "seed": args.seed,
        "requested_primary_size": (
            args.primary_size
        ),
        "actual_primary_size": len(primary),
        "requested_reserve_size": (
            args.reserve_size
        ),
        "actual_reserve_size": len(reserve),
        "exclusions": dict(
            sorted(exclusions.items())
        ),
        "eligible_population": {
            "jdk_distribution": distribution(
                project_jdk_candidates,
                "jdk_family",
            ),
            "maven_distribution": distribution(
                project_jdk_candidates,
                "maven_family",
            ),
            "lifecycle_distribution": distribution(
                project_jdk_candidates,
                "lifecycle_endpoint",
            ),
            "release_distribution": distribution(
                project_jdk_candidates,
                "release_kind",
            ),
            "source_distribution": distribution(
                project_jdk_candidates,
                "source_kind",
            ),
        },
        "primary": {
            "output": str(primary_path),
            "output_sha256": hashlib.sha256(
                primary_path.read_bytes()
            ).hexdigest(),
            "selected_by_jdk": primary_by_jdk,
            "jdk_distribution": distribution(
                primary,
                "jdk_family",
            ),
            "maven_distribution": distribution(
                primary,
                "maven_family",
            ),
            "lifecycle_distribution": distribution(
                primary,
                "lifecycle_endpoint",
            ),
            "tests_skipped_distribution": distribution(
                primary,
                "tests_skipped",
            ),
            "candidate_ids": candidate_ids(
                primary
            ),
            "project_keys": [
                row["project_key"]
                for row in primary
            ],
        },
        "reserve": {
            "output": str(reserve_path),
            "output_sha256": hashlib.sha256(
                reserve_path.read_bytes()
            ).hexdigest(),
            "selected_by_jdk": reserve_by_jdk,
            "jdk_distribution": distribution(
                reserve,
                "jdk_family",
            ),
            "maven_distribution": distribution(
                reserve,
                "maven_family",
            ),
            "lifecycle_distribution": distribution(
                reserve,
                "lifecycle_endpoint",
            ),
            "tests_skipped_distribution": distribution(
                reserve,
                "tests_skipped",
            ),
            "candidate_ids": candidate_ids(
                reserve
            ),
            "project_keys": [
                row["project_key"]
                for row in reserve
            ],
        },
    }

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Input candidates:             "
        f"{len(source_rows)}"
    )

    print(
        f"Eligible releases:            "
        f"{len(eligible_releases)}"
    )

    print(
        f"Eligible project/JDK pairs:   "
        f"{len(project_jdk_candidates)}"
    )

    print(
        f"Eligible unique projects:     "
        f"{len(unique_eligible_projects)}"
    )

    print(
        f"Primary selected:             "
        f"{len(primary)}"
    )

    print(
        f"Reserve selected:             "
        f"{len(reserve)}"
    )

    print(
        f"Seed:                         "
        f"{args.seed}"
    )

    print(
        f"Primary CSV:                  "
        f"{primary_path}"
    )

    print(
        f"Reserve CSV:                  "
        f"{reserve_path}"
    )

    print(
        f"Report:                       "
        f"{report_path}"
    )

    if len(primary) < args.primary_size:
        print(
            "WARNING: The requested primary sample size "
            "could not be filled.",
            file=sys.stderr,
        )

    if len(reserve) < args.reserve_size:
        print(
            "WARNING: The requested reserve sample size "
            "could not be filled.",
            file=sys.stderr,
        )

    if not primary:
        print(
            "ERROR: No primary candidates were selected.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())