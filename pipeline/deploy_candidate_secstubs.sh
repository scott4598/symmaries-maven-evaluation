#!/usr/bin/env bash

set -euo pipefail

: "${EVALUATION_ROOT:=$HOME/SymmariesProjectBits/symmaries-maven-evaluation}"

namespace="${SYMPROTO_NAMESPACE:-symproto}"
deployment="${SYMPROTO_WORKER_DEPLOYMENT:-symproto-worker}"

proposal="${1:-$EVALUATION_ROOT/benchmark/next-300-d2-secstub-review/proposed/all.secstubs.candidate-generated}"

evidence_dir="${2:-$EVALUATION_ROOT/benchmark/next-300-d2-secstub-review/evidence}"

if [[ ! -s "$proposal" ]]; then
    echo "ERROR: proposal is missing or empty: $proposal" >&2
    exit 2
fi

mkdir -p "$evidence_dir"

if grep -qE \
    'slowCheckMemberAccess' \
    "$proposal"
then
    echo "ERROR: prohibited slowCheckMemberAccess entry present" >&2
    exit 3
fi

expected="$(
    sha256sum "$proposal" |
    awk '{print $1}'
)"

desired_replicas="$(
    kubectl get deployment \
        "$deployment" \
        -n "$namespace" \
        -o jsonpath='{.spec.replicas}'
)"

if [[ -z "$desired_replicas" ]]; then
    echo "ERROR: unable to determine desired worker replicas" >&2
    exit 4
fi

echo "Proposal: $proposal"
echo "Expected checksum: $expected"
echo "Desired workers: $desired_replicas"

kubectl get configmap \
    symproto-policy \
    -n "$namespace" \
    -o yaml \
> "$evidence_dir/configmap-before-candidate-generated.yaml"

kubectl create configmap \
    symproto-policy \
    -n "$namespace" \
    --from-file=all.secstubs="$proposal" \
    --dry-run=client \
    -o yaml |
kubectl apply -f -

kubectl get configmap \
    symproto-policy \
    -n "$namespace" \
    -o yaml \
> "$evidence_dir/configmap-candidate-generated.yaml"

kubectl rollout restart \
    "deployment/$deployment" \
    -n "$namespace"

kubectl rollout status \
    "deployment/$deployment" \
    -n "$namespace" \
    --timeout=300s

deadline=$((SECONDS + 300))

while true; do
    mapfile -t ready_pods < <(
        kubectl get pods \
            -n "$namespace" \
            -o json |
        jq -r '
          .items[]
          | select(
              .metadata.name
              | startswith("symproto-worker-")
            )
          | select(
              .metadata.deletionTimestamp == null
            )
          | select(
              .status.phase == "Running"
            )
          | select(
              any(
                .status.containerStatuses[]?;
                .ready == true
              )
            )
          | .metadata.name
        '
    )

    mapfile -t active_pods < <(
        kubectl get pods \
            -n "$namespace" \
            -o json |
        jq -r '
          .items[]
          | select(
              .metadata.name
              | startswith("symproto-worker-")
            )
          | select(
              .metadata.deletionTimestamp == null
            )
          | .metadata.name
        '
    )

    echo \
      "Active non-terminating workers: ${#active_pods[@]}; ready: ${#ready_pods[@]}; desired: $desired_replicas"

    if [[ ${#active_pods[@]} -eq desired_replicas && ${#ready_pods[@]} -eq desired_replicas ]]; then
        break
    fi

    if ((SECONDS >= deadline)); then
        echo "ERROR: worker set did not converge within 300 seconds" >&2

        kubectl get pods \
            -n "$namespace" \
            -o wide >&2

        exit 5
    fi

    sleep 5
done

failed=0

for pod in "${ready_pods[@]}"; do
    echo "===== pod/$pod ====="

    if ! kubectl exec \
        -n "$namespace" \
        "$pod" \
        -- test -s /policy/all.secstubs
    then
        echo "ERROR: /policy/all.secstubs is missing"
        failed=1
        continue
    fi

    actual="$(
        kubectl exec \
            -n "$namespace" \
            "$pod" \
            -- sha256sum /policy/all.secstubs |
        awk '{print $1}'
    )"

    echo "Expected: $expected"
    echo "Actual:   $actual"

    if [[ "$actual" != "$expected" ]]; then
        echo "ERROR: worker checksum mismatch"
        failed=1
    else
        echo "OK: worker proposal verified"
    fi
done

if [[ "$failed" -ne 0 ]]; then
    echo "ERROR: candidate secstub deployment verification failed" >&2
    exit 6
fi

printf '%s  %s\n' \
    "$expected" \
    "$proposal" \
> "$evidence_dir/deployed-candidate-secstubs.sha256"

kubectl get pods \
    -n "$namespace" \
    -o wide \
> "$evidence_dir/worker-pods-after-candidate-secstub-deployment.txt"

echo "CANDIDATE SECSTUBS DEPLOYED AND VERIFIED"
