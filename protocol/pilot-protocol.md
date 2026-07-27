# Pilot Protocol

## Objective
Validate benchmark construction, Nexus routing, Symmaries integration, state preparation, equivalence checks, and measurement collection before the larger study.

## Pilot treatments
- B0: upstream Maven/Reproducible Central baseline
- B1: Maven through Nexus, Symmaries absent
- S0: Nexus plus Symmaries integration loaded, analysis disabled
- S1: Nexus plus Symmaries, empty summary store
- S2: Nexus plus Symmaries, compatible summaries available

## Pilot sample
Target 8 to 12 pinned Maven releases selected from a larger screened candidate set.

## Repetitions
To be finalised after smoke testing. Initial target: five measured repetitions per project and treatment.

## Required controls
- Pinned source, buildspec, JDK, Maven, images, Nexus and Symmaries revisions
- Scripted local Maven, Nexus and summary-store state
- Randomised treatment order within project blocks
- Retention of failed and incomplete runs
