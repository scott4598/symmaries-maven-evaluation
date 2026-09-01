#!/usr/bin/env python3
import csv
import random
from collections import Counter, defaultdict
from pathlib import Path

SEED = 4598
SIZE = 300

candidates_path = Path("benchmark/project-level-candidate-pool.csv")
used_path = Path("benchmark/full-screening-selection.csv")
output_path = Path("benchmark/next-300-selection.csv")

if not candidates_path.is_file():
    raise SystemExit(f"Error: Candidate file not found at {candidates_path}")

# Load all candidates
with candidates_path.open(newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    allrows = list(reader)
    fields = list(reader.fieldnames or [])

if not allrows:
    raise SystemExit("Error: benchmark/candidates.csv is empty.")

# Load used candidate IDs (if the selection file exists)
used = set()
if used_path.is_file():
    with used_path.open(newline="", encoding="utf-8") as f:
        used = {
            r["candidate_id"]
            for r in csv.DictReader(f)
            if r.get("candidate_id")
        }

# Group candidates by (group_id, artifact_id)
groups = defaultdict(list)
for r in allrows:
    candidate_id = r.get("candidate_id")
    jdk_ver = r.get("jdk_version", "")
    major = jdk_ver.split(".", 1)[0] if jdk_ver else ""

    if (
        candidate_id
        and candidate_id not in used
        and major in {"8", "11", "17", "21"}
    ):
        groups[(r.get("group_id", ""), r.get("artifact_id", ""))].append(r)

if not groups:
    raise SystemExit(
        "Error: No eligible candidates found matching JDK versions 8, 11, 17, or 21."
    )

rng = random.Random(SEED)

# Pick one deterministic representative per project group
project = [rng.choice(groups[k]) for k in sorted(groups)]

# Partition by JDK major version
by = defaultdict(list)
for r in project:
    major = r["jdk_version"].split(".", 1)[0]
    by[major].append(r)

for v in by:
    rng.shuffle(by[v])

total = len(project)
target_size = min(SIZE, total)  # Ensure target doesn't exceed available rows

# Calculate proportional quotas
quotas = {v: round(target_size * len(g) / total) for v, g in by.items()}

# Adjust quotas to sum exactly to target_size
while sum(quotas.values()) > target_size:
    quotas[max(quotas, key=quotas.get)] -= 1

while sum(quotas.values()) < target_size:
    # Pick group with room left to expand
    eligible_keys = [v for v in by if len(by[v]) > quotas[v]]
    if not eligible_keys:
        break
    best_key = max(eligible_keys, key=lambda v: len(by[v]) - quotas[v])
    quotas[best_key] += 1

# Extract selected candidates
out = []
for v, g in by.items():
    out.extend(g[: quotas[v]])

rng.shuffle(out)

# Ensure header includes selection_role
if "selection_role" not in fields:
    fields.append("selection_role")

for r in out:
    r["selection_role"] = "pilot-primary"

output_path.parent.mkdir(parents=True, exist_ok=True)
with output_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(out)

jdk_dist = dict(
    Counter(r["jdk_version"].split(".", 1)[0] for r in out)
)
print(
    f"eligible_unique_projects={total} selected={len(out)} seed={SEED} jdk={jdk_dist}"
)
