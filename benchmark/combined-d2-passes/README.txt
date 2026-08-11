Combined D1+D2 passing candidate population

Population:
  78 unique candidates.

Constituent cohorts:
  24 original Device 2 D2 passes.
  54 passes from the next-300 D2 attempt 1 run.

Qualification:
  Each candidate has:
  - a prior D1 pass;
  - D2 runner status PASS;
  - terminal scan status done;
  - failure-audit category PASS.

Purpose:
  This is the common candidate population for subsequent:
  - cross-device validation;
  - baseline and warm reruns;
  - cache experiments;
  - method reuse experiments;
  - selected incremental mutations;
  - final Symmaries Maven integration testing.

The constituent cohorts retain separate provenance in:
  candidate-provenance.csv
