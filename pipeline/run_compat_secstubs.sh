#!/usr/bin/env bash
set -euo pipefail

: "${EVALUATION_ROOT:=$(pwd)}"
REFERENCE="${1:-$EVALUATION_ROOT/config/policies/reference/all.secstubs}"
EXISTING="${2:-$EVALUATION_ROOT/config/policies/policy.secstubs}"
OUTPUT="${3:-$EVALUATION_ROOT/config/policies/policy.secstubs}"
INVENTORY="${4:-$EVALUATION_ROOT/benchmark/compatibility/method-inventory.tsv}"

mkdir -p "$(dirname "$OUTPUT")" "$(dirname "$INVENTORY")"

mapfile -t WORKSPACES < <(
  find "$EVALUATION_ROOT/work/pilot" \
    -type d \
    -path '*/D2/attempt-5/workspace' \
    -print \
    | sort
)

if (( ${#WORKSPACES[@]} == 0 )); then
  echo "ERROR: no D2 attempt-5 workspaces found" >&2
  exit 2
fi

args=(
  "$EVALUATION_ROOT/pipeline/build_compat_secstubs.py"
  "${WORKSPACES[@]}"
  --reference "$REFERENCE"
  --output "$OUTPUT"
  --inventory "$INVENTORY"
)

if [[ -f "$EXISTING" ]]; then
  args+=(--existing "$EXISTING")
fi

python3 "${args[@]}"
sha256sum "$OUTPUT" > "$EVALUATION_ROOT/benchmark/compatibility/policy-secstubs.sha256"
