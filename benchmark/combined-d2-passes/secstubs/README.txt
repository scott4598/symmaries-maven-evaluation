Secstub conditions for the combined D2-pass population

Condition S0 - baseline
  all.secstubs.baseline

  Previously validated all.secstubs used for the original
  24-candidate D2 population.

Condition S1 - enhanced exact-reference union
  all.secstubs.enhanced

  Union of the baseline and exact reference-file matches observed
  while profiling the later 118-candidate D2 population.

  Generation used --no-drafts.
  No mechanically generated draft summaries were added.

Do not concatenate these files during experiments.

Use S0 for:
  - reproduction of the original D2 baseline;
  - cross-device comparability.

Use S1 for:
  - enhanced-coverage experiments;
  - reproduction of the latest Device 2 D2 run;
  - secstub coverage comparison.

The difference between S0 and S1 is recorded in:
  enhanced-secstub-additions.txt
