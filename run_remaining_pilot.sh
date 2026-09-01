#!/usr/bin/env bash
set -euo pipefail

for candidate in \
  RCM-01341 \
  RCM-00141
do
  echo
  echo "===================================================="
  echo "Running $candidate"
  echo "===================================================="

  python3 pipeline/run-symmaries-pilot.py \
    --selection benchmark/scenario-smoke-3-selection.csv \
    --sample benchmark/scenario-smoke-3-sample.csv \
    --modules config/scenario-smoke-3-modules.csv \
    --scenarios config/scenario-smoke-scenarios.csv \
    --candidate "$candidate" \
    --scenario B0 \
    --scenario C0 \
    --scenario W0 \
    --scenario I1 \
    --attempt 1 \
    --timeout-minutes 30 \
    --results benchmark/scenario-smoke-3-results.csv \
    --results-root results/scenario-smoke-3/runs \
    --build-environments config/candidate-build-environments.json
done
