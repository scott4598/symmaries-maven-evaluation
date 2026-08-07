#!/usr/bin/env bash
set -euo pipefail
: "${EVALUATION_ROOT:?source config/pilot-local.env first}"
SELECTION="${1:-benchmark/full-screening-selection.csv}"
RESULTS="${2:-benchmark/full-screening-results.csv}"
ATTEMPT="${3:-1}"
SCENARIOS="${SCREENING_SCENARIOS:-D1 D2}"
cd "$EVALUATION_ROOT"
mkdir -p results/screening-driver
exec > >(tee -a "results/screening-driver/attempt-${ATTEMPT}.log") 2>&1
python3 -m py_compile pipeline/run-symmaries-pilot.py
pipeline/check-pilot-services.sh
mapfile -t IDS < <(python3 - "$SELECTION" <<'PY'
import csv,sys
with open(sys.argv[1],newline='',encoding='utf-8') as f:
    for r in csv.DictReader(f):
        if r.get('candidate_id'): print(r['candidate_id'])
PY
)
for id in "${IDS[@]}"; do
  for scenario in $SCENARIOS; do
    echo "===== $(date --iso-8601=seconds) $id $scenario ====="
    python3 pipeline/run-symmaries-pilot.py \
      --selection "$SELECTION" \
      --sample benchmark/full-screening-sample.csv \
      --modules config/full-analysis-modules.csv \
      --candidate "$id" \
      --scenario "$scenario" \
      --attempt "$ATTEMPT" \
      --settings config/settings-pilot-host.xml \
      --results "$RESULTS" \
      --fallback-policy \
        config/policies/empty-sources-and-sinks.xml \
      --build-environments \
        config/candidate-build-environments.json \
      --timeout-minutes "${TIMEOUT_MINUTES:-45}" \
      || true
  done
done
python3 pipeline/summarize_screening.py --results "$RESULTS" --output benchmark/full-screening-summary.tsv
