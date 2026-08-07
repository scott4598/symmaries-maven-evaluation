#!/usr/bin/env python3
"""Inventory potentially unsupported Java calls and build a compatibility secstubs file.

Uses the JDK's jar and javap tools. No third-party Python packages are required.

The output secstubs file is the union of:
  1. an optional existing compatibility secstubs file;
  2. exact matching entries found in a reference all.secstubs file;
  3. conservative generated draft entries for missing signatures, unless --no-drafts.

Generated entries are intentionally conservative placeholders. Review the manifest before
using them in measured experiments because stubbing changes analysis semantics and workload.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

CALL_RE = re.compile(
    r"//\s+(?:InterfaceMethod|Method)\s+"
    r"(?P<owner>[A-Za-z0-9_$/]+)\."
    r"(?P<name>\"?<[^>]+>\"?|[A-Za-z0-9_$]+):"
    r"(?P<descriptor>\([^\s]*\).+)$"
)
CURRENT_METHOD_RE = re.compile(r"^\s{2,}(?P<decl>.+\));\s*$")
DESCRIPTOR_RE = re.compile(r"^\((?P<args>.*)\)(?P<ret>.+)$")

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
    "forName", "newInstance", "invoke", "getMethod", "getDeclaredMethod",
    "getMethods", "getDeclaredMethods", "getField", "getDeclaredField",
    "getFields", "getDeclaredFields", "getConstructor", "getDeclaredConstructor",
    "getConstructors", "getDeclaredConstructors", "setAccessible", "trySetAccessible",
    "unreflect", "findVirtual", "findStatic", "findSpecial", "findConstructor",
    "defineClass", "loadClass", "newProxyInstance",
}
CONCURRENCY_PREFIXES = (
    "java/lang/Thread",
    "java/lang/ThreadLocal",
    "java/util/concurrent/",
    "java/util/Timer",
    "java/util/TimerTask",
)
CONCURRENCY_NAMES = {
    "start", "run", "sleep", "join", "interrupt", "wait", "notify", "notifyAll",
    "execute", "submit", "invokeAll", "invokeAny", "schedule", "scheduleAtFixedRate",
    "scheduleWithFixedDelay", "fork", "compute", "get", "put", "take", "lock",
    "unlock", "await", "signal", "signalAll", "compareAndSet",
}
NATIVE_TARGETS = {
    ("java/lang/System", "load"),
    ("java/lang/System", "loadLibrary"),
    ("java/lang/Runtime", "load"),
    ("java/lang/Runtime", "loadLibrary"),
}

PRIMITIVES = {
    "B": "byte", "C": "char", "D": "double", "F": "float", "I": "int",
    "J": "long", "S": "short", "Z": "boolean", "V": "void",
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
        return (self.owner, self.name, self.descriptor)


def run(cmd: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout[-4000:]}")
    return proc.stdout


def require_tools() -> None:
    missing = [name for name in ("jar", "javap") if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Missing required JDK tools: " + ", ".join(missing))


def parse_type(desc: str, pos: int = 0) -> tuple[str, int]:
    dimensions = 0
    while pos < len(desc) and desc[pos] == "[":
        dimensions += 1
        pos += 1
    if pos >= len(desc):
        raise ValueError(f"Invalid descriptor: {desc}")
    token = desc[pos]
    if token == "L":
        end = desc.index(";", pos)
        typ = desc[pos + 1:end].replace("/", ".")
        pos = end + 1
    else:
        typ = PRIMITIVES[token]
        pos += 1
    return typ + "[]" * dimensions, pos


def parse_method_descriptor(desc: str) -> tuple[list[str], str]:
    match = DESCRIPTOR_RE.match(desc)
    if not match:
        raise ValueError(f"Invalid method descriptor: {desc}")
    args_desc = match.group("args")
    args: list[str] = []
    pos = 0
    while pos < len(args_desc):
        typ, pos = parse_type(args_desc, pos)
        args.append(typ)
    ret, end = parse_type(match.group("ret"), 0)
    if end != len(match.group("ret")):
        raise ValueError(f"Invalid return descriptor: {desc}")
    return args, ret


def secstub_signature(owner: str, name: str, descriptor: str) -> str:
    args, ret = parse_method_descriptor(descriptor)
    owner_java = owner.replace("/", ".")
    args_java = ",".join(args)
    if name in {"<init>", '"<init>"'}:
        return f"{owner_java}({args_java})"
    return f"{ret} {owner_java}:{name}({args_java})"


def generated_stub(owner: str, name: str, descriptor: str) -> str:
    args, ret = parse_method_descriptor(descriptor)
    signature = secstub_signature(owner, name, descriptor)
    if name in {"<init>", '"<init>"'}:
        return f"{signature}{{-<~;.*<~@;}}"
    if ret == "void":
        return f"{signature}{{-<~;}}"
    return f"{signature}{{-<~;return *;}}"


def categorize(owner: str, name: str) -> str | None:
    if owner in REFLECTION_OWNERS and name in REFLECTION_NAMES:
        return "reflection"
    if owner.startswith(CONCURRENCY_PREFIXES) and (
        name in CONCURRENCY_NAMES or owner.startswith("java/util/concurrent/")
    ):
        return "concurrency"
    if (owner, name) in NATIVE_TARGETS:
        return "native"
    return None


def candidate_from_path(path: Path) -> str:
    parts = path.parts
    if "pilot" in parts:
        idx = parts.index("pilot")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return path.parent.name or path.stem


def locate_jars(inputs: list[Path]) -> list[Path]:
    jars: set[Path] = set()
    for item in inputs:
        if item.is_file() and item.suffix == ".jar":
            jars.add(item.resolve())
        elif item.is_dir():
            for jar_path in item.rglob("*.jar"):
                name = jar_path.name
                relative_parts = jar_path.relative_to(item).parts
                excluded_parts = {
                    "src", "test", "test-data", "testdata", "fixtures",
                    "remote-repo", "repository", ".m2", ".mvn", "modules", "node_modules",
                }
                if any(part in excluded_parts for part in relative_parts):
                    continue
                if any(x in name for x in ("-sources", "-javadoc", "-tests")):
                    continue
                if not zipfile.is_zipfile(jar_path):
                    print(f"SKIP invalid JAR archive: {jar_path}", file=sys.stderr)
                    continue
                jars.add(jar_path.resolve())
    return sorted(jars)


def list_classes(jar_path: Path) -> list[str]:
    values = []
    with zipfile.ZipFile(jar_path) as zf:
        for name in zf.namelist():
            if not name.endswith(".class") or name.startswith("META-INF/versions/"):
                continue
            if name in {"module-info.class", "package-info.class"}:
                continue
            values.append(name[:-6].replace("/", "."))
    return values


def scan_class(jar_path: Path, class_name: str, candidate: str) -> list[Finding]:
    text = run(["javap", "-classpath", str(jar_path), "-c", "-p", "-s", class_name], check=False)
    findings: list[Finding] = []
    current_method = "<unknown>"
    for raw in text.splitlines():
        method_match = CURRENT_METHOD_RE.match(raw)
        if method_match and not raw.strip().startswith("descriptor:"):
            current_method = method_match.group("decl").strip()
        call = CALL_RE.search(raw)
        if not call:
            continue
        owner = call.group("owner")
        name = call.group("name").strip('"')
        descriptor = call.group("descriptor")
        category = categorize(owner, name)
        if category:
            findings.append(Finding(
                candidate=candidate,
                jar=str(jar_path),
                caller_class=class_name,
                caller_method=current_method,
                category=category,
                owner=owner,
                name=name,
                descriptor=descriptor,
                evidence=raw.strip(),
            ))
    return findings


def scan_native_declarations(jar_path: Path, class_name: str, candidate: str) -> list[Finding]:
    text = run(["javap", "-classpath", str(jar_path), "-p", "-s", class_name], check=False)
    lines = text.splitlines()
    findings: list[Finding] = []
    pending: str | None = None
    for raw in lines:
        stripped = raw.strip()
        if " native " in f" {stripped} " and stripped.endswith(";"):
            pending = stripped
            continue
        if pending and stripped.startswith("descriptor:"):
            descriptor = stripped.split("descriptor:", 1)[1].strip()
            method_name_match = re.search(r"([A-Za-z0-9_$]+)\([^)]*\);$", pending)
            if method_name_match:
                name = method_name_match.group(1)
                findings.append(Finding(
                    candidate=candidate, jar=str(jar_path), caller_class=class_name,
                    caller_method=pending, category="native_declaration",
                    owner=class_name.replace(".", "/"), name=name,
                    descriptor=descriptor, evidence=pending,
                ))
            pending = None
    return findings


def load_reference(path: Path | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path:
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        signature = line.split("{", 1)[0].strip()
        result.setdefault(signature, line)
    return result


def load_existing(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def write_inventory(path: Path, findings: list[Finding], statuses: dict[tuple[str, str, str], str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        fields = ["candidate", "jar", "caller_class", "caller_method", "category",
                  "owner", "name", "descriptor", "secstub_signature", "stub_status", "evidence"]
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for finding in findings:
            writer.writerow({
                "candidate": finding.candidate, "jar": finding.jar,
                "caller_class": finding.caller_class, "caller_method": finding.caller_method,
                "category": finding.category, "owner": finding.owner, "name": finding.name,
                "descriptor": finding.descriptor,
                "secstub_signature": secstub_signature(*finding.key),
                "stub_status": statuses[finding.key], "evidence": finding.evidence,
            })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path,
                        help="JAR files or directories containing built JARs")
    parser.add_argument("--reference", type=Path,
                        help="Reference all.secstubs/allSecstubs.txt")
    parser.add_argument("--existing", type=Path,
                        help="Existing compatibility secstubs to preserve and extend")
    parser.add_argument("--output", type=Path, required=True,
                        help="Output compatibility .secstubs file")
    parser.add_argument("--inventory", type=Path, required=True,
                        help="Output TSV evidence inventory")
    parser.add_argument("--no-drafts", action="store_true",
                        help="Do not add generated draft stubs when reference has no exact match")
    args = parser.parse_args()

    require_tools()
    jars = locate_jars(args.inputs)
    if not jars:
        print("ERROR: no JAR files found", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    scanned_jars = 0
    skipped_jars = 0
    for index, jar_path in enumerate(jars, 1):
        candidate = candidate_from_path(jar_path)
        print(f"[{index}/{len(jars)}] {candidate}: {jar_path}", file=sys.stderr)
        try:
            classes = list_classes(jar_path)
        except (zipfile.BadZipFile, OSError) as exc:
            skipped_jars += 1
            print(f"SKIP unreadable JAR: {jar_path}: {exc}", file=sys.stderr)
            continue
        scanned_jars += 1
        for class_name in classes:
            findings.extend(scan_class(jar_path, class_name, candidate))
            findings.extend(scan_native_declarations(jar_path, class_name, candidate))

    # Dedupe evidence rows while preserving candidate/caller evidence.
    findings = list(dict.fromkeys(findings))
    reference = load_reference(args.reference)
    existing_lines = load_existing(args.existing)
    output_lines = list(dict.fromkeys(existing_lines))
    output_signatures = {line.split("{", 1)[0].strip() for line in output_lines}
    statuses: dict[tuple[str, str, str], str] = {}

    for key in sorted({finding.key for finding in findings}):
        signature = secstub_signature(*key)
        if signature in output_signatures:
            statuses[key] = "already_existing"
            continue
        if signature in reference:
            output_lines.append(reference[signature])
            output_signatures.add(signature)
            statuses[key] = "added_from_reference"
            continue
        if args.no_drafts:
            statuses[key] = "missing_needs_review"
            continue
        output_lines.append(generated_stub(*key))
        output_signatures.add(signature)
        statuses[key] = "added_generated_draft"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    write_inventory(args.inventory, findings, statuses)

    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    counts: dict[str, int] = {}
    for value in statuses.values():
        counts[value] = counts.get(value, 0) + 1
    print(f"JAR candidates: {len(jars)}")
    print(f"JARs scanned: {scanned_jars}")
    print(f"JARs skipped: {skipped_jars}")
    print(f"Evidence rows: {len(findings)}")
    print(f"Unique methods: {len(statuses)}")
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    print(f"Output: {args.output}")
    print(f"Inventory: {args.inventory}")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
