# Symmaries Maven Evaluation

Control repository for constructing a pinned Reproducible Central benchmark and running the Symmaries/Nexus pilot evaluation.

## Initial workflow

1. Complete `protocol/pilot-protocol.md` and `protocol/pilot-success-criteria.md`.
2. Pin Reproducible Central in `benchmark/reproducible-central.lock`.
3. Run `pipeline/discover-buildspecs` against the pinned clone.
4. Reproduce and profile candidates.
5. Freeze the pilot sample in `benchmark/selected-projects.csv`.

Generated results should remain under `results/` and should not be committed unless explicitly selected for release.
