#!/usr/bin/env bash
set -euo pipefail

evaluation_root="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

wrapper="$evaluation_root/pipeline/docker-wrapper/docker"

required_variables=(
    SYMMARIES_API_KEY
    SYMPROTO_NEXUS_USERNAME
    SYMPROTO_NEXUS_PASSWORD
)

for variable in "${required_variables[@]}"; do
    if [[ -z "${!variable:-}" ]]; then
        printf 'ERROR: %s is not set\n' "$variable" >&2
        exit 1
    fi
done

if ! sudo -n true 2>/dev/null; then
    echo "ERROR: sudo credential unavailable. Run sudo -v." >&2
    exit 1
fi

echo "Checking host API..."

curl \
    --fail \
    --silent \
    --show-error \
    http://localhost:8000/healthz

echo
echo "Checking container API route..."

"$wrapper" run \
    --rm \
    curlimages/curl:latest \
    curl \
    --fail \
    --silent \
    --show-error \
    http://host.docker.internal:8000/healthz

echo
echo "Checking container Nexus route..."

"$wrapper" run \
    --rm \
    curlimages/curl:latest \
    curl \
    --silent \
    --show-error \
    --output /dev/null \
    --write-out 'Nexus HTTP %{http_code}\n' \
    http://host.docker.internal:8081/service/rest/v1/status

echo "Checking forwarded environment names..."

"$wrapper" run \
    --rm \
    alpine:latest \
    sh -c '
        test -n "$SYMMARIES_API_KEY"
        test -n "$SYMPROTO_NEXUS_USERNAME"
        test -n "$SYMPROTO_NEXUS_PASSWORD"
    '

echo "Pilot services and Docker routing are ready."