#!/usr/bin/env bash

set -uo pipefail

: "${EVALUATION_ROOT:=$HOME/SymmariesProjectBits/symmaries-maven-evaluation}"

cd "$EVALUATION_ROOT"

remote="${GIT_RESULTS_REMOTE:-origin}"
branch="${GIT_RESULTS_BRANCH:-$(git branch --show-current)}"

if [[ -z "$branch" ]]; then
    echo "ERROR: unable to determine current Git branch" >&2
    exit 2
fi

if ! git remote get-url "$remote" >/dev/null 2>&1; then
    echo "ERROR: Git remote does not exist: $remote" >&2
    exit 3
fi

if ! git diff --check; then
    echo "ERROR: Git whitespace/error check failed" >&2
    exit 4
fi

paths=(
    "pipeline/build_compat_secstubs.py"
    "pipeline/run_next_300_compat_secstubs.sh"
    "pipeline/validate_candidate_secstubs.py"
    "pipeline/deploy_candidate_secstubs.sh"
    "pipeline/run_device2_batch_2_sequence.sh"

    "benchmark/portable-d2-passes-24"

    "benchmark/next-300-d2-validation-results.csv"
    "benchmark/next-300-d2-validation-summary.csv"
    "benchmark/next-300-d2-validation-summary.stats.txt"
    "benchmark/next-300-d2-validation-summary-terminal.csv"
    "benchmark/next-300-d2-failure-audit.csv"
    "benchmark/next-300-d2-failure-audit.stats.txt"

    "benchmark/batch-2-300-d1-results.csv"
    "benchmark/batch-2-300-d1-summary.csv"
    "benchmark/batch-2-300-d1-summary.stats.txt"
    "benchmark/batch-2-300-d1-exclusions.csv"
    "benchmark/batch-2-300-d1-runnable.csv"
    "benchmark/batch-2-300-d1-runnable-sample.csv"
    "config/batch-2-300-d1-runnable-modules.csv"

    "benchmark/next-300-d2-secstub-review/evidence/candidate-progress.tsv"
    "benchmark/next-300-d2-secstub-review/evidence/candidate-statistics.json"
    "benchmark/next-300-d2-secstub-review/evidence/method-inventory.tsv"
    "benchmark/next-300-d2-secstub-review/evidence/candidate-secstub-validation.stats.txt"
    "benchmark/next-300-d2-secstub-review/evidence/new-generated-lines.txt"
    "benchmark/next-300-d2-secstub-review/evidence/new-sensitive-reflection-lines.txt"
    "benchmark/next-300-d2-secstub-review/evidence/deployed-candidate-secstubs.sha256"
)

existing_paths=()

for path in "${paths[@]}"; do
    if [[ -e "$path" ]]; then
        existing_paths+=("$path")
    fi
done

if (( ${#existing_paths[@]} == 0 )); then
    echo "ERROR: no publishable result files exist" >&2
    exit 5
fi

large_file_failed=0

for path in "${existing_paths[@]}"; do
    while IFS= read -r file; do
        size="$(
            stat -c '%s' "$file"
        )"

        if (( size > 20 * 1024 * 1024 )); then
            echo "ERROR: refusing file over 20 MiB: $file ($size bytes)" >&2
            large_file_failed=1
        fi
    done < <(
        find "$path" \
          -type f \
          -print
    )
done

if [[ "$large_file_failed" -ne 0 ]]; then
    exit 6
fi

git add -- \
    "${existing_paths[@]}"

if git diff \
    --cached \
    --quiet
then
    echo "No result changes to commit"
    exit 0
fi

commit_message="$(
    printf \
      'Record device-2 batch-2 results (%s)' \
      "$(date --iso-8601=seconds)"
)"

git commit \
    -m "$commit_message"

if [[ "${GIT_ALLOW_PUSH:-0}" != "1" ]]; then
    echo "Commit created locally."
    echo "Push skipped because GIT_ALLOW_PUSH is not 1."
    exit 0
fi

export GIT_TERMINAL_PROMPT=0

git push \
    "$remote" \
    "HEAD:$branch"

echo "RESULTS COMMITTED AND PUSHED"
echo "Remote: $remote"
echo "Branch: $branch"
