# Pilot Success Criteria

## Benchmark
- At least 8 projects pass the complete pipeline.
- At least 2 included projects are multi-module.
- At least 3 build-size or duration categories are represented.
- Every project has pinned provenance and a recorded inclusion reason.

## Correctness
- Nexus-only execution preserves expected build outcomes.
- Symmaries treatments preserve expected tests, dependencies and artifacts, or differences are explained.
- Controlled treatments do not bypass Nexus.
- Symmaries records reconcile with client-side observations.

## Repeatability
- Cache and summary states are recreated programmatically.
- Every run has an immutable run manifest and unique run ID.
- Missing, failed and incomplete runs remain visible in the dataset.
