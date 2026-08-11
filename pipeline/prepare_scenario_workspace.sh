#!/usr/bin/env bash

set -euo pipefail

candidate="${1:?candidate required}"
scenario="${2:?scenario required}"
source="${3:?source workspace required}"
destination="${4:?destination required}"

if [[ ! -d "$source" ]]; then
    echo "ERROR: source workspace missing: $source" >&2
    exit 2
fi

if [[ -e "$destination" ]]; then
    rm -rf "$destination"
fi

mkdir -p "$(dirname "$destination")"

cp -a "$source" "$destination"

printf 'candidate=%s\n' "$candidate"
printf 'scenario=%s\n' "$scenario"
printf 'source=%s\n' "$source"
printf 'destination=%s\n' "$destination"
