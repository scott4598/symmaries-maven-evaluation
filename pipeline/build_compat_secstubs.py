#!/usr/bin/env python3
"""
Build a bounded, resumable compatibility-secstub proposal.

The generator:

* scans application JARs under candidate workspaces;
* excludes test, source, Javadoc, repository, and dependency-cache trees;
* deduplicates identical JARs by SHA-256;
* invokes javap once per class;
* applies a timeout to every javap invocation;
* applies an overall timeout to each candidate;
* writes one checkpoint per completed candidate;
* resumes from valid checkpoints;
* writes progress and diagnostic evidence continuously;
* writes final inventory and secstub outputs atomically.

Generated draft stubs are conservative placeholders and must still be
reviewed before deployment because trusted summaries alter analysis
semantics.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator


CALL_RE = re.compile(
    r"//\s+(?:InterfaceMethod|Method)\s+"
    r"(?P<owner>[A-Za-z0-9_$/]+)\."
    r"(?P<name>\"?<[^>]+>\"?|[A-Za-z0-9_$]+):"
    r"(?P<descriptor>\([^\s]*\).+)$"
)

CURRENT_METHOD_RE = re.compile(
    r"^\s{2,}(?P<decl>.+\));\s*$"
)

DESCRIPTOR_RE = re.compile(
    r"^\((?P<args>.*)\)(?P<ret>.+)$"
)

NATIVE_DECLARATION_RE = re.compile(
    r"([A-Za-z0-9_$]+)\([^)]*\);$"
)


REFLECTION_OWNERS = {
    "java/lang/Class",
    "java/lang/ClassLoader",
    "java/lang/reflect/Method",
    "java/lang/reflect/Constructor",
    "java/lang/reflect/Field",
    "java/lang/reflect/Array",
    "java/lang/reflect/Proxy",
    "java/lang/invoke/MethodHandle",
    "java/lang/invoke/MethodHandles",
    "java/lang/invoke/MethodType",
    "java/lang/invoke/CallSite",
}

REFLECTION_NAMES = {
    "forName",
    "newInstance",
    "invoke",
    "getMethod",
    "getDeclaredMethod",
    "getMethods",
    "getDeclaredMethods",
    "getField",
    "getDeclaredField",
    "getFields",
    "getDeclaredFields",
    "getConstructor",
    "getDeclaredConstructor",
    "getConstructors",
    "getDeclaredConstructors",
    "setAccessible",
    "trySetAccessible",
    "unreflect",
    "findVirtual",
    "findStatic",
    "findSpecial",
    "findConstructor",
    "defineClass",
    "loadClass",
    "newProxyInstance",
}

PROHIBITED_METHOD_NAMES = {
    "slowCheckMemberAccess",
}

CONCURRENCY_PREFIXES = (
    "java/lang/Thread",
    "java/lang/ThreadLocal",
    "java/util/concurrent/",
    "java/util/Timer",
    "java/util/TimerTask",
)

CONCURRENCY_NAMES = {
    "start",
    "run",
    "sleep",
    "join",
    "interrupt",
    "wait",
    "notify",
    "notifyAll",
    "execute",
    "submit",
    "invokeAll",
    "invokeAny",
    "schedule",
    "scheduleAtFixedRate",
    "scheduleWithFixedDelay",
    "fork",
    "compute",
    "get",
    "put",
    "take",
    "lock",
    "unlock",
    "await",
    "signal",
    "signalAll",
    "compareAndSet",
}

NATIVE_TARGETS = {
    ("java/lang/System", "load"),
    ("java/lang/System", "loadLibrary"),
    ("java/lang/Runtime", "load"),
    ("java/lang/Runtime", "loadLibrary"),
}

PRIMITIVES = {
    "B": "byte",
    "C": "char",
    "D": "double",
    "F": "float",
    "I": "int",
    "J": "long",
    "S": "short",
    "Z": "boolean",
    "V": "void",
}

PRUNED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".gradle",
    ".m2",
    ".mvn",
    "node_modules",
    "repository",
    "remote-repo",
    "buildcache",
    "test",
    "tests",
    "test-data",
    "testdata",
    "fixtures",
    "coverage",
    "site",
}

EXCLUDED_JAR_MARKERS = (
    "-sources.jar",
    "-source.jar",
    "-javadoc.jar",
    "-tests.jar",
    "-test.jar",
    "-test-sources.jar",
)

TARGET_ALLOWED_SUBDIRECTORIES = {
    "target",
    "build",
    "out",
    "dist",
    "lib",
    "libs",
}


@dataclass(frozen=True)
class Finding:
    candidate: str
    jar: str
    caller_class: str
    caller_method: str
    category: str
    owner: str
    name: str
    descriptor: str
    evidence: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (
            self.owner,
            self.name,
            self.descriptor,
        )


@dataclass
class CandidateStatistics:
    candidate: str
    input_path: str
    started_at: str
    completed_at: str = ""
    status: str = "RUNNING"
    discovered_jars: int = 0
    unique_jars: int = 0
    duplicate_jars: int = 0
    unreadable_jars: int = 0
    classes_discovered: int = 0
    classes_scanned: int = 0
    classes_timed_out: int = 0
    classes_failed: int = 0
    findings: int = 0
    elapsed_seconds: float = 0.0
    error: str = ""


@dataclass
class JavapResult:
    status: str
    output: str
    elapsed_seconds: float
    return_code: int | None
    error: str


def utc_timestamp() -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(),
    )


def log(message: str) -> None:
    print(
        f"{utc_timestamp()} {message}",
        file=sys.stderr,
        flush=True,
    )


def atomic_write_text(
    path: Path,
    content: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())

    temporary.replace(path)


def atomic_write_json(
    path: Path,
    value: object,
) -> None:
    atomic_write_text(
        path,
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def sha256_file(
    path: Path,
    block_size: int = 1024 * 1024,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        while True:
            block = stream.read(block_size)

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


def require_tools() -> None:
    missing = [
        name
        for name in ("javap",)
        if shutil.which(name) is None
    ]

    if missing:
        raise RuntimeError(
            "Missing required JDK tools: "
            + ", ".join(missing)
        )


def parse_type(
    descriptor: str,
    position: int = 0,
) -> tuple[str, int]:
    dimensions = 0

    while (
        position < len(descriptor)
        and descriptor[position] == "["
    ):
        dimensions += 1
        position += 1

    if position >= len(descriptor):
        raise ValueError(
            f"Invalid descriptor: {descriptor}"
        )

    token = descriptor[position]

    if token == "L":
        end = descriptor.index(
            ";",
            position,
        )

        value = descriptor[
            position + 1:end
        ].replace("/", ".")

        position = end + 1

    else:
        if token not in PRIMITIVES:
            raise ValueError(
                f"Invalid descriptor token "
                f"{token!r}: {descriptor}"
            )

        value = PRIMITIVES[token]
        position += 1

    return (
        value + "[]" * dimensions,
        position,
    )


def parse_method_descriptor(
    descriptor: str,
) -> tuple[list[str], str]:
    match = DESCRIPTOR_RE.match(
        descriptor
    )

    if not match:
        raise ValueError(
            "Invalid method descriptor: "
            + descriptor
        )

    arguments_descriptor = match.group(
        "args"
    )

    arguments: list[str] = []
    position = 0

    while position < len(arguments_descriptor):
        value, position = parse_type(
            arguments_descriptor,
            position,
        )

        arguments.append(value)

    return_value, end = parse_type(
        match.group("ret"),
        0,
    )

    if end != len(match.group("ret")):
        raise ValueError(
            "Invalid return descriptor: "
            + descriptor
        )

    return arguments, return_value


def secstub_signature(
    owner: str,
    name: str,
    descriptor: str,
) -> str:
    arguments, return_value = (
        parse_method_descriptor(descriptor)
    )

    owner_java = owner.replace("/", ".")
    arguments_java = ",".join(arguments)

    if name in {
        "<init>",
        '"<init>"',
    }:
        return (
            f"{owner_java}"
            f"({arguments_java})"
        )

    return (
        f"{return_value} "
        f"{owner_java}:{name}"
        f"({arguments_java})"
    )


def generated_stub(
    owner: str,
    name: str,
    descriptor: str,
) -> str:
    _, return_value = (
        parse_method_descriptor(descriptor)
    )

    signature = secstub_signature(
        owner,
        name,
        descriptor,
    )

    if name in {
        "<init>",
        '"<init>"',
    }:
        return (
            f"{signature}"
            "{-<~;.*<~@;}"
        )

    if return_value == "void":
        return (
            f"{signature}"
            "{-<~;}"
        )

    return (
        f"{signature}"
        "{-<~;return *;}"
    )


def categorize(
    owner: str,
    name: str,
) -> str | None:
    if name in PROHIBITED_METHOD_NAMES:
        return "prohibited_reflection"

    if (
        owner in REFLECTION_OWNERS
        and name in REFLECTION_NAMES
    ):
        return "reflection"

    if (
        owner.startswith(
            CONCURRENCY_PREFIXES
        )
        and (
            name in CONCURRENCY_NAMES
            or owner.startswith(
                "java/util/concurrent/"
            )
        )
    ):
        return "concurrency"

    if (owner, name) in NATIVE_TARGETS:
        return "native"

    return None


def candidate_from_path(
    path: Path,
) -> str:
    parts = path.resolve().parts

    if "pilot" in parts:
        index = parts.index("pilot")

        if index + 1 < len(parts):
            return parts[index + 1]

    for part in parts:
        if re.fullmatch(
            r"RCM-\d+",
            part,
        ):
            return part

    return (
        path.parent.name
        or path.stem
    )


def should_exclude_jar(
    path: Path,
) -> bool:
    lower_name = path.name.lower()

    if any(
        lower_name.endswith(marker)
        for marker in EXCLUDED_JAR_MARKERS
    ):
        return True

    lower_parts = {
        part.lower()
        for part in path.parts
    }

    if lower_parts & PRUNED_DIRECTORY_NAMES:
        return True

    return False


def walk_candidate_jars(
    input_path: Path,
) -> Iterator[Path]:
    if (
        input_path.is_file()
        and input_path.suffix.lower() == ".jar"
    ):
        if not should_exclude_jar(
            input_path
        ):
            yield input_path

        return

    if not input_path.is_dir():
        return

    for root, directories, files in os.walk(
        input_path,
        topdown=True,
        followlinks=False,
    ):
        root_path = Path(root)

        directories[:] = [
            name
            for name in directories
            if name.lower()
            not in PRUNED_DIRECTORY_NAMES
        ]

        relative_parts = (
            root_path.relative_to(
                input_path
            ).parts
        )

        if (
            relative_parts
            and relative_parts[0].lower()
            not in TARGET_ALLOWED_SUBDIRECTORIES
            and any(
                part.lower()
                in {
                    "src",
                    "test",
                    "tests",
                }
                for part in relative_parts
            )
        ):
            directories[:] = []
            continue

        for filename in files:
            if not filename.lower().endswith(
                ".jar"
            ):
                continue

            jar_path = root_path / filename

            if should_exclude_jar(jar_path):
                continue

            yield jar_path


def locate_candidate_jars(
    input_path: Path,
) -> tuple[list[Path], int, int]:
    discovered = sorted(
        set(
            path.resolve()
            for path
            in walk_candidate_jars(
                input_path
            )
        )
    )

    unique_by_digest: dict[str, Path] = {}
    unreadable = 0

    for path in discovered:
        try:
            if not zipfile.is_zipfile(path):
                unreadable += 1
                log(
                    "SKIP invalid JAR archive: "
                    + str(path)
                )
                continue

            digest = sha256_file(path)

        except OSError as error:
            unreadable += 1
            log(
                f"SKIP unreadable JAR: "
                f"{path}: {error}"
            )
            continue

        previous = unique_by_digest.get(
            digest
        )

        if previous is None:
            unique_by_digest[digest] = path

        elif len(str(path)) < len(
            str(previous)
        ):
            unique_by_digest[digest] = path

    unique = sorted(
        unique_by_digest.values()
    )

    duplicates = (
        len(discovered)
        - unreadable
        - len(unique)
    )

    return (
        unique,
        duplicates,
        unreadable,
    )


def list_classes(
    jar_path: Path,
) -> list[str]:
    classes: list[str] = []

    with zipfile.ZipFile(
        jar_path
    ) as archive:
        for name in archive.namelist():
            if not name.endswith(".class"):
                continue

            if name.startswith(
                "META-INF/versions/"
            ):
                continue

            if (
                name == "module-info.class"
                or name.endswith(
                    "/module-info.class"
                )
                or name == "package-info.class"
                or name.endswith(
                    "/package-info.class"
                )
            ):
                continue

            classes.append(
                name[:-6].replace(
                    "/",
                    ".",
                )
            )

    return sorted(set(classes))


def run_javap(
    jar_path: Path,
    class_name: str,
    timeout_seconds: float,
) -> JavapResult:
    command = [
        "javap",
        "-classpath",
        str(jar_path),
        "-c",
        "-p",
        "-s",
        class_name,
    ]

    started = time.monotonic()

    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=max(
                1.0,
                timeout_seconds,
            ),
        )

    except subprocess.TimeoutExpired as error:
        elapsed = (
            time.monotonic()
            - started
        )

        output = ""

        if isinstance(error.stdout, bytes):
            output = error.stdout.decode(
                "utf-8",
                errors="replace",
            )

        elif isinstance(error.stdout, str):
            output = error.stdout

        return JavapResult(
            status="TIMEOUT",
            output=output,
            elapsed_seconds=elapsed,
            return_code=None,
            error=(
                "javap exceeded "
                f"{timeout_seconds:.1f}s"
            ),
        )

    except OSError as error:
        return JavapResult(
            status="EXECUTION_ERROR",
            output="",
            elapsed_seconds=(
                time.monotonic()
                - started
            ),
            return_code=None,
            error=repr(error),
        )

    elapsed = (
        time.monotonic()
        - started
    )

    if completed.returncode != 0:
        return JavapResult(
            status="FAILED",
            output=completed.stdout,
            elapsed_seconds=elapsed,
            return_code=(
                completed.returncode
            ),
            error=(
                completed.stdout[-2000:]
                if completed.stdout
                else "javap returned non-zero"
            ),
        )

    return JavapResult(
        status="OK",
        output=completed.stdout,
        elapsed_seconds=elapsed,
        return_code=0,
        error="",
    )


def parse_javap_output(
    text: str,
    jar_path: Path,
    class_name: str,
    candidate: str,
) -> list[Finding]:
    findings: list[Finding] = []
    current_method = "<unknown>"
    pending_native: str | None = None

    for raw in text.splitlines():
        stripped = raw.strip()

        method_match = CURRENT_METHOD_RE.match(
            raw
        )

        if (
            method_match
            and not stripped.startswith(
                "descriptor:"
            )
        ):
            current_method = (
                method_match.group(
                    "decl"
                ).strip()
            )

        if (
            " native " in f" {stripped} "
            and stripped.endswith(";")
        ):
            pending_native = stripped
            continue

        if (
            pending_native
            and stripped.startswith(
                "descriptor:"
            )
        ):
            descriptor = stripped.split(
                "descriptor:",
                1,
            )[1].strip()

            method_match = (
                NATIVE_DECLARATION_RE.search(
                    pending_native
                )
            )

            if method_match:
                name = method_match.group(1)

                findings.append(
                    Finding(
                        candidate=candidate,
                        jar=str(jar_path),
                        caller_class=class_name,
                        caller_method=(
                            pending_native
                        ),
                        category=(
                            "native_declaration"
                        ),
                        owner=class_name.replace(
                            ".",
                            "/",
                        ),
                        name=name,
                        descriptor=descriptor,
                        evidence=(
                            pending_native
                        ),
                    )
                )

            pending_native = None

        call = CALL_RE.search(raw)

        if not call:
            continue

        owner = call.group("owner")
        name = call.group(
            "name"
        ).strip('"')
        descriptor = call.group(
            "descriptor"
        )

        category = categorize(
            owner,
            name,
        )

        if category is None:
            continue

        findings.append(
            Finding(
                candidate=candidate,
                jar=str(jar_path),
                caller_class=class_name,
                caller_method=current_method,
                category=category,
                owner=owner,
                name=name,
                descriptor=descriptor,
                evidence=stripped,
            )
        )

    return findings


def finding_from_dict(
    value: dict[str, str],
) -> Finding:
    return Finding(
        candidate=value["candidate"],
        jar=value["jar"],
        caller_class=value[
            "caller_class"
        ],
        caller_method=value[
            "caller_method"
        ],
        category=value["category"],
        owner=value["owner"],
        name=value["name"],
        descriptor=value["descriptor"],
        evidence=value["evidence"],
    )


def checkpoint_path(
    checkpoint_directory: Path,
    candidate: str,
) -> Path:
    return (
        checkpoint_directory
        / f"{candidate}.json"
    )


def write_candidate_checkpoint(
    checkpoint_directory: Path,
    statistics: CandidateStatistics,
    findings: list[Finding],
) -> None:
    path = checkpoint_path(
        checkpoint_directory,
        statistics.candidate,
    )

    atomic_write_json(
        path,
        {
            "version": 1,
            "statistics": asdict(
                statistics
            ),
            "findings": [
                asdict(finding)
                for finding in findings
            ],
        },
    )


def load_candidate_checkpoint(
    checkpoint_directory: Path,
    candidate: str,
) -> tuple[
    CandidateStatistics,
    list[Finding],
] | None:
    path = checkpoint_path(
        checkpoint_directory,
        candidate,
    )

    if not path.is_file():
        return None

    try:
        value = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )

        if value.get("version") != 1:
            return None

        statistics = CandidateStatistics(
            **value["statistics"]
        )

        if statistics.status not in {
            "COMPLETE",
            "PARTIAL_TIMEOUT",
            "PARTIAL_FAILURE",
            "NO_JARS",
        }:
            return None

        findings = [
            finding_from_dict(item)
            for item in value[
                "findings"
            ]
        ]

        return statistics, findings

    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
    ) as error:
        log(
            f"IGNORE invalid checkpoint "
            f"{path}: {error}"
        )

        return None


def append_progress(
    progress_path: Path,
    statistics: CandidateStatistics,
) -> None:
    progress_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    new_file = not progress_path.exists()

    with progress_path.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as stream:
        fields = [
            "candidate",
            "input_path",
            "started_at",
            "completed_at",
            "status",
            "discovered_jars",
            "unique_jars",
            "duplicate_jars",
            "unreadable_jars",
            "classes_discovered",
            "classes_scanned",
            "classes_timed_out",
            "classes_failed",
            "findings",
            "elapsed_seconds",
            "error",
        ]

        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
        )

        if new_file:
            writer.writeheader()

        writer.writerow(
            asdict(statistics)
        )

        stream.flush()
        os.fsync(stream.fileno())


def scan_candidate(
    input_path: Path,
    candidate: str,
    candidate_index: int,
    candidate_total: int,
    javap_timeout_seconds: float,
    candidate_timeout_seconds: float,
    checkpoint_directory: Path,
    progress_path: Path,
    class_progress_interval: int,
) -> tuple[
    CandidateStatistics,
    list[Finding],
]:
    started_monotonic = time.monotonic()

    statistics = CandidateStatistics(
        candidate=candidate,
        input_path=str(
            input_path.resolve()
        ),
        started_at=utc_timestamp(),
    )

    log(
        f"[candidate "
        f"{candidate_index}/{candidate_total}] "
        f"{candidate}: locating JARs"
    )

    try:
        (
            jars,
            duplicates,
            unreadable,
        ) = locate_candidate_jars(
            input_path
        )

    except Exception as error:
        statistics.status = (
            "CANDIDATE_DISCOVERY_FAILED"
        )
        statistics.error = repr(error)
        statistics.completed_at = (
            utc_timestamp()
        )
        statistics.elapsed_seconds = round(
            time.monotonic()
            - started_monotonic,
            3,
        )

        write_candidate_checkpoint(
            checkpoint_directory,
            statistics,
            [],
        )

        append_progress(
            progress_path,
            statistics,
        )

        return statistics, []

    statistics.discovered_jars = (
        len(jars) + duplicates
    )
    statistics.unique_jars = len(jars)
    statistics.duplicate_jars = (
        duplicates
    )
    statistics.unreadable_jars = (
        unreadable
    )

    if not jars:
        statistics.status = "NO_JARS"
        statistics.completed_at = (
            utc_timestamp()
        )
        statistics.elapsed_seconds = round(
            time.monotonic()
            - started_monotonic,
            3,
        )

        write_candidate_checkpoint(
            checkpoint_directory,
            statistics,
            [],
        )

        append_progress(
            progress_path,
            statistics,
        )

        log(
            f"[candidate "
            f"{candidate_index}/{candidate_total}] "
            f"{candidate}: NO_JARS"
        )

        return statistics, []

    class_work: list[
        tuple[Path, str]
    ] = []

    for jar_path in jars:
        try:
            classes = list_classes(
                jar_path
            )

        except (
            zipfile.BadZipFile,
            OSError,
        ) as error:
            statistics.unreadable_jars += 1

            log(
                f"{candidate}: skip "
                f"{jar_path}: {error}"
            )
            continue

        for class_name in classes:
            class_work.append(
                (
                    jar_path,
                    class_name,
                )
            )

    statistics.classes_discovered = len(
        class_work
    )

    log(
        f"[candidate "
        f"{candidate_index}/{candidate_total}] "
        f"{candidate}: "
        f"jars={statistics.unique_jars} "
        f"classes={statistics.classes_discovered}"
    )

    findings: list[Finding] = []

    for class_index, (
        jar_path,
        class_name,
    ) in enumerate(class_work, 1):
        elapsed = (
            time.monotonic()
            - started_monotonic
        )

        remaining = (
            candidate_timeout_seconds
            - elapsed
        )

        if remaining <= 0:
            statistics.status = (
                "PARTIAL_TIMEOUT"
            )
            statistics.error = (
                "candidate timeout reached"
            )
            break

        effective_timeout = min(
            javap_timeout_seconds,
            remaining,
        )

        result = run_javap(
            jar_path,
            class_name,
            effective_timeout,
        )

        if result.status == "OK":
            statistics.classes_scanned += 1

            try:
                findings.extend(
                    parse_javap_output(
                        result.output,
                        jar_path,
                        class_name,
                        candidate,
                    )
                )

            except (
                ValueError,
                IndexError,
            ) as error:
                statistics.classes_failed += 1

                log(
                    f"{candidate}: parse failure "
                    f"{class_name}: {error}"
                )

        elif result.status == "TIMEOUT":
            statistics.classes_timed_out += 1

            log(
                f"{candidate}: javap timeout "
                f"{class_name} "
                f"after "
                f"{result.elapsed_seconds:.1f}s"
            )

        else:
            statistics.classes_failed += 1

            log(
                f"{candidate}: javap "
                f"{result.status.lower()} "
                f"{class_name}: "
                f"{result.error[-500:]}"
            )

        if (
            class_index == 1
            or class_index
            % class_progress_interval
            == 0
            or class_index
            == len(class_work)
        ):
            elapsed_now = (
                time.monotonic()
                - started_monotonic
            )

            log(
                f"[candidate "
                f"{candidate_index}/{candidate_total}] "
                f"{candidate}: "
                f"class "
                f"{class_index}/"
                f"{len(class_work)} "
                f"findings={len(findings)} "
                f"timeouts="
                f"{statistics.classes_timed_out} "
                f"failures="
                f"{statistics.classes_failed} "
                f"elapsed={elapsed_now:.1f}s"
            )

    if statistics.status == "RUNNING":
        if (
            statistics.classes_timed_out > 0
            or statistics.classes_failed > 0
        ):
            statistics.status = (
                "PARTIAL_FAILURE"
            )
        else:
            statistics.status = "COMPLETE"

    findings = list(
        dict.fromkeys(findings)
    )

    statistics.findings = len(findings)
    statistics.completed_at = utc_timestamp()
    statistics.elapsed_seconds = round(
        time.monotonic()
        - started_monotonic,
        3,
    )

    write_candidate_checkpoint(
        checkpoint_directory,
        statistics,
        findings,
    )

    append_progress(
        progress_path,
        statistics,
    )

    log(
        f"[candidate "
        f"{candidate_index}/{candidate_total}] "
        f"{candidate}: "
        f"status={statistics.status} "
        f"scanned="
        f"{statistics.classes_scanned}/"
        f"{statistics.classes_discovered} "
        f"findings={len(findings)} "
        f"elapsed="
        f"{statistics.elapsed_seconds:.1f}s"
    )

    return statistics, findings


def load_reference(
    path: Path | None,
) -> dict[str, str]:
    result: dict[str, str] = {}

    if path is None:
        return result

    if not path.is_file():
        raise RuntimeError(
            f"Reference file missing: {path}"
        )

    for raw in path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
        ):
            continue

        signature = line.split(
            "{",
            1,
        )[0].strip()

        result.setdefault(
            signature,
            line,
        )

    return result


def load_existing(
    path: Path | None,
) -> list[str]:
    if (
        path is None
        or not path.exists()
    ):
        return []

    return [
        line.strip()
        for line in path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
        if line.strip()
        and not line.lstrip().startswith(
            "#"
        )
    ]


def write_inventory(
    path: Path,
    findings: list[Finding],
    statuses: dict[
        tuple[str, str, str],
        str,
    ],
) -> None:
    fields = [
        "candidate",
        "jar",
        "caller_class",
        "caller_method",
        "category",
        "owner",
        "name",
        "descriptor",
        "secstub_signature",
        "stub_status",
        "evidence",
    ]

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        newline="",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)

        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
        )

        writer.writeheader()

        for finding in findings:
            try:
                signature = (
                    secstub_signature(
                        *finding.key
                    )
                )

            except (
                ValueError,
                KeyError,
            ):
                signature = (
                    "<invalid-descriptor>"
                )

            writer.writerow({
                "candidate": (
                    finding.candidate
                ),
                "jar": finding.jar,
                "caller_class": (
                    finding.caller_class
                ),
                "caller_method": (
                    finding.caller_method
                ),
                "category": (
                    finding.category
                ),
                "owner": finding.owner,
                "name": finding.name,
                "descriptor": (
                    finding.descriptor
                ),
                "secstub_signature": (
                    signature
                ),
                "stub_status": (
                    statuses.get(
                        finding.key,
                        "invalid_descriptor",
                    )
                ),
                "evidence": (
                    finding.evidence
                ),
            })

        stream.flush()
        os.fsync(stream.fileno())

    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help=(
            "JAR files or candidate workspace "
            "directories containing built JARs"
        ),
    )

    parser.add_argument(
        "--reference",
        type=Path,
        help=(
            "Reference all.secstubs or "
            "allSecstubs.txt"
        ),
    )

    parser.add_argument(
        "--existing",
        type=Path,
        help=(
            "Existing compatibility secstubs "
            "to preserve and extend"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "Output compatibility .secstubs file"
        ),
    )

    parser.add_argument(
        "--inventory",
        type=Path,
        required=True,
        help="Output TSV evidence inventory",
    )

    parser.add_argument(
        "--no-drafts",
        action="store_true",
        help=(
            "Do not add generated draft stubs "
            "when reference has no exact match"
        ),
    )

    parser.add_argument(
        "--javap-timeout-seconds",
        type=float,
        default=30.0,
        help=(
            "Maximum seconds for one javap call "
            "(default: 30)"
        ),
    )

    parser.add_argument(
        "--candidate-timeout-seconds",
        type=float,
        default=600.0,
        help=(
            "Maximum seconds for one candidate "
            "(default: 600)"
        ),
    )

    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        help=(
            "Checkpoint directory. Defaults to "
            "<inventory parent>/checkpoints"
        ),
    )

    parser.add_argument(
        "--progress",
        type=Path,
        help=(
            "Progress TSV. Defaults to "
            "<inventory parent>/candidate-progress.tsv"
        ),
    )

    parser.add_argument(
        "--class-progress-interval",
        type=int,
        default=100,
        help=(
            "Emit progress every N classes "
            "(default: 100)"
        ),
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "Ignore valid completed-candidate "
            "checkpoints"
        ),
    )

    parser.add_argument(
        "--reset-checkpoints",
        action="store_true",
        help=(
            "Delete the checkpoint directory "
            "before starting"
        ),
    )

    args = parser.parse_args()

    if args.javap_timeout_seconds <= 0:
        parser.error(
            "--javap-timeout-seconds "
            "must be positive"
        )

    if args.candidate_timeout_seconds <= 0:
        parser.error(
            "--candidate-timeout-seconds "
            "must be positive"
        )

    if args.class_progress_interval < 1:
        parser.error(
            "--class-progress-interval "
            "must be positive"
        )

    require_tools()

    checkpoint_directory = (
        args.checkpoint_dir
        or args.inventory.parent
        / "checkpoints"
    )

    progress_path = (
        args.progress
        or args.inventory.parent
        / "candidate-progress.tsv"
    )

    if args.reset_checkpoints:
        shutil.rmtree(
            checkpoint_directory,
            ignore_errors=True,
        )

        if progress_path.exists():
            progress_path.unlink()

    checkpoint_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    inputs: list[Path] = []

    seen_inputs: set[Path] = set()

    for item in args.inputs:
        resolved = item.resolve()

        if resolved in seen_inputs:
            continue

        seen_inputs.add(resolved)
        inputs.append(resolved)

    if not inputs:
        print(
            "ERROR: no input paths",
            file=sys.stderr,
        )
        return 2

    all_findings: list[Finding] = []
    all_statistics: list[
        CandidateStatistics
    ] = []

    total = len(inputs)

    for index, input_path in enumerate(
        inputs,
        1,
    ):
        candidate = candidate_from_path(
            input_path
        )

        if not args.no_resume:
            checkpoint = (
                load_candidate_checkpoint(
                    checkpoint_directory,
                    candidate,
                )
            )

            if checkpoint is not None:
                statistics, findings = (
                    checkpoint
                )

                log(
                    f"[candidate "
                    f"{index}/{total}] "
                    f"{candidate}: RESUME "
                    f"status={statistics.status} "
                    f"findings={len(findings)}"
                )

                all_statistics.append(
                    statistics
                )
                all_findings.extend(
                    findings
                )
                continue

        statistics, findings = scan_candidate(
            input_path=input_path,
            candidate=candidate,
            candidate_index=index,
            candidate_total=total,
            javap_timeout_seconds=(
                args.javap_timeout_seconds
            ),
            candidate_timeout_seconds=(
                args.candidate_timeout_seconds
            ),
            checkpoint_directory=(
                checkpoint_directory
            ),
            progress_path=progress_path,
            class_progress_interval=(
                args.class_progress_interval
            ),
        )

        all_statistics.append(
            statistics
        )

        all_findings.extend(findings)

    all_findings = sorted(
        set(all_findings),
        key=lambda item: (
            item.candidate,
            item.jar,
            item.caller_class,
            item.caller_method,
            item.category,
            item.owner,
            item.name,
            item.descriptor,
            item.evidence,
        ),
    )

    reference = load_reference(
        args.reference
    )

    existing_lines = load_existing(
        args.existing
    )

    output_lines = list(
        dict.fromkeys(existing_lines)
    )

    output_signatures = {
        line.split(
            "{",
            1,
        )[0].strip()
        for line in output_lines
    }

    statuses: dict[
        tuple[str, str, str],
        str,
    ] = {}

    unique_keys = sorted({
        finding.key
        for finding in all_findings
    })

    prohibited_keys = [
        key
        for key in unique_keys
        if key[1]
        in PROHIBITED_METHOD_NAMES
    ]

    if prohibited_keys:
        prohibited_path = (
            args.inventory.parent
            / "prohibited-methods.tsv"
        )

        rows = [
            "\t".join(key)
            for key in prohibited_keys
        ]

        atomic_write_text(
            prohibited_path,
            "owner\tname\tdescriptor\n"
            + "\n".join(rows)
            + "\n",
        )

        print(
            "ERROR: prohibited reflection "
            "methods were discovered",
            file=sys.stderr,
        )

        print(
            f"See: {prohibited_path}",
            file=sys.stderr,
        )

        return 3

    for key in unique_keys:
        try:
            signature = secstub_signature(
                *key
            )

        except (
            ValueError,
            KeyError,
        ):
            statuses[key] = (
                "invalid_descriptor"
            )
            continue

        if signature in output_signatures:
            statuses[key] = (
                "already_existing"
            )
            continue

        if signature in reference:
            output_lines.append(
                reference[signature]
            )

            output_signatures.add(
                signature
            )

            statuses[key] = (
                "added_from_reference"
            )
            continue

        if args.no_drafts:
            statuses[key] = (
                "missing_needs_review"
            )
            continue

        output_lines.append(
            generated_stub(*key)
        )

        output_signatures.add(
            signature
        )

        statuses[key] = (
            "added_generated_draft"
        )

    output_content = (
        "\n".join(output_lines)
        + "\n"
    )

    atomic_write_text(
        args.output,
        output_content,
    )

    write_inventory(
        args.inventory,
        all_findings,
        statuses,
    )

    statistics_path = (
        args.inventory.parent
        / "candidate-statistics.json"
    )

    atomic_write_json(
        statistics_path,
        [
            asdict(statistics)
            for statistics
            in all_statistics
        ],
    )

    digest = hashlib.sha256(
        args.output.read_bytes()
    ).hexdigest()

    counts: dict[str, int] = {}

    for value in statuses.values():
        counts[value] = (
            counts.get(value, 0)
            + 1
        )

    candidate_statuses: dict[
        str,
        int,
    ] = {}

    for statistics in all_statistics:
        candidate_statuses[
            statistics.status
        ] = (
            candidate_statuses.get(
                statistics.status,
                0,
            )
            + 1
        )

    print(
        f"Candidate inputs: {len(inputs)}"
    )

    print(
        "Unique evidence rows: "
        f"{len(all_findings)}"
    )

    print(
        f"Unique methods: {len(statuses)}"
    )

    for key in sorted(
        candidate_statuses
    ):
        print(
            "candidate_status_"
            f"{key}: "
            f"{candidate_statuses[key]}"
        )

    for key in sorted(counts):
        print(
            f"{key}: {counts[key]}"
        )

    print(f"Output: {args.output}")
    print(
        f"Inventory: {args.inventory}"
    )
    print(
        f"Progress: {progress_path}"
    )
    print(
        "Statistics: "
        f"{statistics_path}"
    )
    print(
        "Checkpoints: "
        f"{checkpoint_directory}"
    )
    print(f"SHA-256: {digest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
