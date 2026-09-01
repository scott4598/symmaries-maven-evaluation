timestamp="$(date +%Y%m%d-%H%M%S)"

mv \
  benchmark/scenario-smoke-3-results.csv \
  benchmark/scenario-smoke-3-results.csv.before-rebuild-"$timestamp"
  
python3 - <<'PY'
import csv

fields = [
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

with open(
    "benchmark/scenario-smoke-3-results.csv",
    "w",
    newline="",
    encoding="utf-8",
) as stream:
    csv.DictWriter(
        stream,
        fieldnames=fields
    ).writeheader()
PY

rm -rf \
  results/scenario-smoke-3

rm -rf \
  work/pilot/RCM-01420
  
WORKER_POD="$(
  kubectl get pods \
    -n symproto \
    -o name |
  grep symproto-worker |
  head -n 1
)"

kubectl exec \
  -n symproto \
  "$WORKER_POD" \
  -- sha256sum \
  /app/JSymCompiler.jar

python3 pipeline/run-symmaries-pilot.py \
  --selection benchmark/scenario-smoke-3-selection.csv \
  --sample benchmark/scenario-smoke-3-sample.csv \
  --modules config/scenario-smoke-3-modules.csv \
  --scenarios config/scenario-smoke-scenarios.csv \
  --candidate RCM-01420 \
  --scenario B0 \
  --attempt 1 \
  --timeout-minutes 30 \
  --results benchmark/scenario-smoke-3-results.csv \
  --results-root results/scenario-smoke-3/runs \
  --build-environments config/candidate-build-environments.json

python3 pipeline/run-symmaries-pilot.py \
  --selection benchmark/scenario-smoke-3-selection.csv \
  --sample benchmark/scenario-smoke-3-sample.csv \
  --modules config/scenario-smoke-3-modules.csv \
  --scenarios config/scenario-smoke-scenarios.csv \
  --candidate RCM-01420 \
  --scenario C0 \
  --attempt 1 \
  --timeout-minutes 30 \
  --results benchmark/scenario-smoke-3-results.csv \
  --results-root results/scenario-smoke-3/runs \
  --build-environments config/candidate-build-environments.json
  
python3 pipeline/run-symmaries-pilot.py \
  --selection benchmark/scenario-smoke-3-selection.csv \
  --sample benchmark/scenario-smoke-3-sample.csv \
  --modules config/scenario-smoke-3-modules.csv \
  --scenarios config/scenario-smoke-scenarios.csv \
  --candidate RCM-01420 \
  --scenario W0 \
  --scenario I1 \
  --attempt 1 \
  --timeout-minutes 30 \
  --results benchmark/scenario-smoke-3-results.csv \
  --results-root results/scenario-smoke-3/runs \
  --build-environments config/candidate-build-environments.json


