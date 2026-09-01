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

sequence_root="results/device2-batch-2"
main_log="$sequence_root/sequence.log"
state_file="$sequence_root/state.txt"

mkdir -p "$sequence_root"

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
    local name="$1"
    shift

    set_state "${name}_RUNNING"
    log "Starting ${name}"

    "$@" \
        > >(tee -a "$main_log") \
        2> >(tee -a "$main_log" >&2)

    local status=$?

    log "${name} exit status: ${status}"

    if [[ "$status" -eq 0 ]]; then
        set_state "${name}_COMPLETE"
    else
        set_state "${name}_FAILED"
    fi

    return "$status"
}

log "Device 2 batch-2 sequence starting"
set_state "PREFLIGHT_RUNNING"

required_files=(
    "benchmark/next-300-d2-candidate-selection.csv"
    "benchmark/next-300-d2-candidate-sample.csv"
    "config/next-300-d2-analysis-modules.csv"
    "benchmark/next-300-d2-bytecode-audit.csv"
    "benchmark/batch-2-300-selection.csv"
    "benchmark/batch-2-300-sample.csv"
    "config/batch-2-300-modules.csv"
    "benchmark/batch-2-300-source-preparation.csv"
    "config/candidate-build-environments.json"
    "config/pilot-scenarios.csv"
    "config/settings-pilot-host.xml"
    "config/policies/empty-sources-and-sinks.xml"
    "benchmark/policy/all.secstubs"
    "pipeline/run_d2_overnight.py"
    "pipeline/run_d1_overnight.py"
    "pipeline/run-symmaries-pilot.py"
    "pipeline/refresh_d2_scan_statuses.py"
    "pipeline/audit_d2_failures.py"
    "pipeline/run_next_300_compat_secstubs.sh"
    "pipeline/build_compat_secstubs.py"
    "pipeline/validate_candidate_secstubs.py"
    "benchmark/next-300-d2-secstub-selection.csv"
    "benchmark/next-300-d2-secstub-review/proposed/all.secstubs.before-candidate-generation"
    "pipeline/deploy_candidate_secstubs.sh"
)

preflight_failed=0

for file in "${required_files[@]}"; do
    if [[ -s "$file" ]]; then
        log "OK: $file"
    else
        log "ERROR: missing or empty: $file"
        preflight_failed=1
    fi
done

plugin_root="benchmark/maven-seed/com/symmaries/symmaries-maven-plugin/0.1.1-pilot"

for file in \
    "$plugin_root/symmaries-maven-plugin-0.1.1-pilot.jar" \
    "$plugin_root/symmaries-maven-plugin-0.1.1-pilot.pom"
do
    if [[ -s "$file" ]]; then
        log "OK: $file"
    else
        log "ERROR: missing plugin seed: $file"
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
    pipeline/refresh_d2_scan_statuses.py \
    pipeline/audit_d2_failures.py \
    pipeline/build_compat_secstubs.py \
    pipeline/validate_candidate_secstubs.py

compile_status=$?

if [[ "$compile_status" -ne 0 ]]; then
    log "ERROR: Python compilation failed"
    set_state "PREFLIGHT_FAILED"
    exit 4
fi

d2_count="$(
    awk 'END {print NR - 1}' \
        benchmark/next-300-d2-candidate-selection.csv
)"

d2_sample_count="$(
    awk 'END {print NR - 1}' \
        benchmark/next-300-d2-candidate-sample.csv
)"

d2_module_count="$(
    awk 'END {print NR - 1}' \
        config/next-300-d2-analysis-modules.csv
)"

d1_selection_count="$(
    awk 'END {print NR - 1}' \
        benchmark/batch-2-300-selection.csv
)"

d1_source_count="$(
    awk 'END {print NR - 1}' \
        benchmark/batch-2-300-source-preparation.csv
)"

d1_prepared_count="$(
    python3 - <<'PY'
import csv

with open(
    "benchmark/batch-2-300-source-preparation.csv",
    newline="",
    encoding="utf-8",
) as stream:
    print(sum(
        1
        for row in csv.DictReader(stream)
        if row.get("status") in {
            "PREPARED",
            "EXISTS",
        }
    ))
PY
)"

log "D2 selection count: $d2_count"
log "D2 sample count: $d2_sample_count"
log "D2 module count: $d2_module_count"
log "D1 selection count: $d1_selection_count"
log "D1 source-report count: $d1_source_count"
log "D1 prepared-source count: $d1_prepared_count"

if [[ "$d2_count" -ne 118 ]]; then
    log "ERROR: expected 118 D2 candidates"
    set_state "PREFLIGHT_FAILED"
    exit 5
fi

if [[ "$d2_sample_count" -ne 118 ]]; then
    log "ERROR: expected 118 D2 sample rows"
    set_state "PREFLIGHT_FAILED"
    exit 6
fi

if [[ "$d2_module_count" -ne 118 ]]; then
    log "ERROR: expected 118 D2 module rows"
    set_state "PREFLIGHT_FAILED"
    exit 7
fi

if [[ "$d1_selection_count" -ne 300 ]]; then
    log "ERROR: expected 300 D1 selections"
    set_state "PREFLIGHT_FAILED"
    exit 8
fi

if [[ "$d1_source_count" -ne 300 ]]; then
    log "ERROR: expected 300 source-report rows"
    set_state "PREFLIGHT_FAILED"
    exit 9
fi

if [[ "$d1_prepared_count" -ne 225 ]]; then
    log "ERROR: expected 225 prepared D1 sources"
    set_state "PREFLIGHT_FAILED"
    exit 10
fi

set_state "PREFLIGHT_COMPLETE"

run_stage \
    "NEXT_300_COMPAT_SECSTUB_GENERATION" \
    timeout \
        --signal=TERM \
        --kill-after=60s \
        6h \
    pipeline/run_next_300_compat_secstubs.sh \
        "$EVALUATION_ROOT/benchmark/policy/all.secstubs" \
        "$EVALUATION_ROOT/benchmark/next-300-d2-secstub-review/proposed/all.secstubs.before-candidate-generation" \
        "$EVALUATION_ROOT/benchmark/next-300-d2-secstub-review/proposed/all.secstubs.candidate-generated" \
        "$EVALUATION_ROOT/benchmark/next-300-d2-secstub-review/evidence/method-inventory.tsv"

secstub_generation_status=$?

if [[ "$secstub_generation_status" -eq 0 ]]; then
    run_stage \
        "NEXT_300_COMPAT_SECSTUB_VALIDATION" \
        python3 pipeline/validate_candidate_secstubs.py \
            --baseline benchmark/policy/all.secstubs \
            --proposal benchmark/next-300-d2-secstub-review/proposed/all.secstubs.candidate-generated \
            --evidence-dir benchmark/next-300-d2-secstub-review/evidence

    secstub_validation_status=$?
else
    log "WARNING: compatibility secstub generation failed"
    log "D2 will continue with the validated active baseline"
    secstub_validation_status=1
fi

if [[ "$secstub_validation_status" -eq 0 ]]; then
    log "Compatibility secstub proposal generated and validated"

    run_stage \
        "NEXT_300_COMPAT_SECSTUB_DEPLOYMENT" \
        pipeline/deploy_candidate_secstubs.sh \
            "$EVALUATION_ROOT/benchmark/next-300-d2-secstub-review/proposed/all.secstubs.candidate-generated" \
            "$EVALUATION_ROOT/benchmark/next-300-d2-secstub-review/evidence"

    secstub_deployment_status=$?
else
    log "Compatibility secstub proposal did not pass automated validation"
    log "D2 will continue with the validated active baseline"

    secstub_deployment_status=1
fi

if [[ "$secstub_deployment_status" -eq 0 ]]; then
    log "Candidate-generated compatibility secstubs are deployed"
    log "The following D2 stage will use the generated proposal"
else
    if [[ "$secstub_validation_status" -eq 0 ]]; then
        log "ERROR: validated proposal failed deployment verification"
        log "Stopping to avoid running D2 with an uncertain policy state"
        set_state "NEXT_300_COMPAT_SECSTUB_DEPLOYMENT_FAILED"
        exit 11
    fi

    log "Candidate-generated secstubs were not deployed"
    log "The following D2 stage will use the existing verified baseline"
fi

run_stage \
    "NEXT_300_D2_FULL" \
    python3 pipeline/run_d2_overnight.py \
        --selection benchmark/next-300-d2-candidate-selection.csv \
        --sample benchmark/next-300-d2-candidate-sample.csv \
        --modules config/next-300-d2-analysis-modules.csv \
        --attempt 1 \
        --timeout-minutes 20 \
        --results benchmark/next-300-d2-validation-results.csv \
        --results-root results/next-300-d2-validation-runs \
        --summary benchmark/next-300-d2-validation-summary.csv

d2_status=$?

if [[ "$d2_status" -ne 0 ]]; then
    log "WARNING: D2 batch returned non-zero"
    log "Continuing with status collection and independent D1"
fi

if [[ -s benchmark/next-300-d2-validation-summary.csv ]]; then
    run_stage \
        "NEXT_300_D2_STATUS_REFRESH" \
        python3 pipeline/refresh_d2_scan_statuses.py \
            --input benchmark/next-300-d2-validation-summary.csv \
            --output benchmark/next-300-d2-validation-summary-terminal.csv

    refresh_status=$?
else
    log "WARNING: D2 summary missing"
    refresh_status=1
fi

if [[ "$refresh_status" -ne 0 ]]; then
    log "WARNING: D2 status refresh returned non-zero"
fi

if [[ -s benchmark/next-300-d2-validation-summary-terminal.csv ]]; then
    run_stage \
        "NEXT_300_D2_AUDIT" \
        python3 pipeline/audit_d2_failures.py \
            --input benchmark/next-300-d2-validation-summary-terminal.csv \
            --results-root results/next-300-d2-validation-runs \
            --attempt 1 \
            --output benchmark/next-300-d2-failure-audit.csv \
            --stats benchmark/next-300-d2-failure-audit.stats.txt

    audit_status=$?
else
    log "WARNING: terminal D2 summary missing"
    audit_status=1
fi

if [[ "$audit_status" -ne 0 ]]; then
    log "WARNING: D2 audit returned non-zero"
fi

log "Continuing to independent batch-2 D1 screening"

run_stage \
    "BATCH_2_D1_FULL" \
    python3 pipeline/run_d1_overnight.py \
        --selection benchmark/batch-2-300-selection.csv \
        --sample benchmark/batch-2-300-sample.csv \
        --modules config/batch-2-300-modules.csv \
        --source-report benchmark/batch-2-300-source-preparation.csv \
        --attempt 1 \
        --timeout-minutes 20 \
        --results benchmark/batch-2-300-d1-results.csv \
        --results-root results/batch-2-300-d1-runs \
        --output-selection benchmark/batch-2-300-d1-runnable.csv \
        --output-sample benchmark/batch-2-300-d1-runnable-sample.csv \
        --output-modules config/batch-2-300-d1-runnable-modules.csv \
        --exclusions benchmark/batch-2-300-d1-exclusions.csv \
        --summary benchmark/batch-2-300-d1-summary.csv

d1_status=$?

if [[ "$d1_status" -eq 0 ]]; then
    set_state "SEQUENCE_COMPLETE"
    log "Device 2 batch-2 sequence completed"
    exit 0
fi

set_state "SEQUENCE_COMPLETED_WITH_D1_ERROR"
log "Device 2 batch-2 sequence completed with D1 batch error"
exit "$d1_status"
