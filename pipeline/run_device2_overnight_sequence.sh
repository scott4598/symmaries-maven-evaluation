#!/usr/bin/env bash

set -uo pipefail

export EVALUATION_ROOT="${EVALUATION_ROOT:-$HOME/SymmariesProjectBits/symmaries-maven-evaluation}"
export RC_ROOT="${RC_ROOT:-$HOME/SymmariesProjectBits/reproducible-central}"
export SECSERV_ROOT="${SECSERV_ROOT:-$HOME/Desktop/SecServ}"

cd "$EVALUATION_ROOT"

if [[ ! -s config/pilot-local.env ]]; then
    echo "ERROR: config/pilot-local.env is missing" >&2
    exit 2
fi

source config/pilot-local.env

mkdir -p results/device2-overnight

main_log="results/device2-overnight/sequence.log"
state_file="results/device2-overnight/state.txt"

log() {
    printf '%s %s\n' \
        "$(date --iso-8601=seconds)" \
        "$*" |
    tee -a "$main_log"
}

set_state() {
    printf '%s\n' "$1" > "$state_file"
    log "STATE=$1"
}

run_stage() {
    local stage_name="$1"
    shift

    set_state "${stage_name}_RUNNING"
    log "Starting ${stage_name}"

    "$@" \
        > >(tee -a "$main_log") \
        2> >(tee -a "$main_log" >&2)

    local exit_status=$?

    log "${stage_name} exit status: ${exit_status}"

    if [[ "$exit_status" -eq 0 ]]; then
        set_state "${stage_name}_COMPLETE"
    else
        set_state "${stage_name}_FAILED"
    fi

    return "$exit_status"
}

log "Device 2 overnight sequence starting"
set_state "PREFLIGHT_RUNNING"

required_files=(
    "benchmark/d2-candidate-selection.csv"
    "benchmark/d2-candidate-sample.csv"
    "config/d2-analysis-modules.csv"
    "benchmark/next-300-selection.csv"
    "benchmark/next-300-sample.csv"
    "config/next-300-modules.csv"
    "benchmark/next-300-source-preparation.csv"
    "config/candidate-build-environments.json"
    "config/pilot-scenarios.csv"
    "config/settings-pilot-host.xml"
    "config/policies/empty-sources-and-sinks.xml"
    "benchmark/policy/all.secstubs"
    "pipeline/run_d2_overnight.py"
    "pipeline/run_d1_overnight.py"
    "pipeline/run-symmaries-pilot.py"
    "pipeline/refresh_d2_scan_statuses.py"
)

preflight_failed=0

for file in "${required_files[@]}"; do
    if [[ -s "$file" ]]; then
        log "OK: $file"
    else
        log "ERROR: missing or empty required file: $file"
        preflight_failed=1
    fi
done

plugin_seed=(
    "benchmark/maven-seed/com/symmaries/"
    "symmaries-maven-plugin/0.1.1-pilot"
)

plugin_seed="${plugin_seed[*]}"
plugin_seed="${plugin_seed// /}"

for file in \
    "$plugin_seed/symmaries-maven-plugin-0.1.1-pilot.jar" \
    "$plugin_seed/symmaries-maven-plugin-0.1.1-pilot.pom"
do
    if [[ -s "$file" ]]; then
        log "OK: $file"
    else
        log "ERROR: missing plugin seed file: $file"
        preflight_failed=1
    fi
done

if [[ "$preflight_failed" -ne 0 ]]; then
    set_state "PREFLIGHT_FAILED"
    exit 3
fi

python3 -m py_compile \
    pipeline/run_d2_overnight.py \
    pipeline/run_d1_overnight.py \
    pipeline/run-symmaries-pilot.py \
    pipeline/refresh_d2_scan_statuses.py

compile_status=$?

if [[ "$compile_status" -ne 0 ]]; then
    log "ERROR: Python compilation failed"
    set_state "PREFLIGHT_FAILED"
    exit 4
fi

d2_count="$(
    awk 'END {print NR - 1}' \
        benchmark/d2-candidate-selection.csv
)"

next_300_count="$(
    awk 'END {print NR - 1}' \
        benchmark/next-300-selection.csv
)"

prepared_count="$(
    python3 - <<'PY'
import csv

with open(
    "benchmark/next-300-source-preparation.csv",
    newline="",
    encoding="utf-8",
) as stream:
    print(sum(
        1
        for row in csv.DictReader(stream)
        if row["status"] in {
            "PREPARED",
            "EXISTS",
        }
    ))
PY
)"

log "D2 candidate count: ${d2_count}"
log "Next-300 selection count: ${next_300_count}"
log "Next-300 prepared source count: ${prepared_count}"

if [[ "$d2_count" -lt 1 ]]; then
    log "ERROR: D2 candidate selection is empty"
    set_state "PREFLIGHT_FAILED"
    exit 5
fi

if [[ "$next_300_count" -lt 1 ]]; then
    log "ERROR: next-300 selection is empty"
    set_state "PREFLIGHT_FAILED"
    exit 6
fi

set_state "PREFLIGHT_COMPLETE"

run_stage \
    "D2_FULL" \
    python3 pipeline/run_d2_overnight.py \
        --selection benchmark/d2-candidate-selection.csv \
        --sample benchmark/d2-candidate-sample.csv \
        --modules config/d2-analysis-modules.csv \
        --attempt 1 \
        --timeout-minutes 20 \
        --results benchmark/d2-validation-results.csv \
        --results-root results/d2-validation-runs \
        --summary benchmark/d2-validation-summary.csv

d2_status=$?

if [[ "$d2_status" -ne 0 ]]; then
    log "WARNING: D2 batch process returned non-zero."
    log "The independent D1 stage will still run."
fi

if [[ -s benchmark/d2-validation-summary.csv ]]; then
    run_stage \
        "D2_STATUS_REFRESH" \
        python3 pipeline/refresh_d2_scan_statuses.py \
            --input benchmark/d2-validation-summary.csv \
            --output benchmark/d2-validation-summary-terminal.csv

    refresh_status=$?

    if [[ "$refresh_status" -ne 0 ]]; then
        log "WARNING: D2 terminal-status refresh failed."
        log "The independent D1 stage will still run."
    fi
else
    log "WARNING: D2 summary was not created."
    log "D2 terminal-status refresh skipped."
fi

log "Beginning independent next-300 D1 validation"

run_stage \
    "D1_NEXT_300" \
    python3 pipeline/run_d1_overnight.py \
        --selection benchmark/next-300-selection.csv \
        --sample benchmark/next-300-sample.csv \
        --modules config/next-300-modules.csv \
        --source-report benchmark/next-300-source-preparation.csv \
        --attempt 1 \
        --timeout-minutes 20 \
        --results benchmark/next-300-d1-results.csv \
        --results-root results/next-300-d1-runs \
        --output-selection benchmark/next-300-d1-runnable.csv \
        --output-sample benchmark/next-300-d1-runnable-sample.csv \
        --output-modules config/next-300-d1-runnable-modules.csv \
        --exclusions benchmark/next-300-d1-exclusions.csv \
        --summary benchmark/next-300-d1-summary.csv

d1_status=$?

if [[ "$d1_status" -eq 0 ]]; then
    set_state "SEQUENCE_COMPLETE"
    log "Device 2 overnight sequence completed"
    exit 0
fi

set_state "SEQUENCE_COMPLETED_WITH_D1_ERROR"
log "Device 2 overnight sequence completed with a D1 batch error"
exit "$d1_status"
