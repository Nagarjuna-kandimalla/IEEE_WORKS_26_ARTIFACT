# W1 reproduction protocol for the current paper

1. Verify hashes of the bundled original inputs and historical reference tables.
2. Validate seed 1996, paired task identities, recorded peaks and runtimes,
   the original RQ2 split, and Sizey's reconstructed per-process split.
3. Reproduce original native metrics before applying any scaling.
4. For each baseline, compute `sum(CAMP requests) / sum(baseline requests)`
   and multiply every baseline request by that factor. Compare underallocated
   tasks on the identical cohort. For Access, also compute recorded unused
   memory-time under the original runtime convention.
5. For CAMP and Sizey separately, select the smallest empirical aggregate-budget
   threshold covering at least `ceil(0.95 * 21337)` tasks. Apply the corresponding
   uniform multiplier to all requests and compare total requested memory.
6. Check regenerated results against the pre-organization results and every
   rounded W1 value printed in the current manuscript.
7. Write summary tables, task-level audit files, validation records, and output
   checksums. No model training, workload execution, bootstrap, or plots are run.

The reported evidence remains conditional on the original fixed cohorts and
prediction histories. The detailed machine-readable protocol is `protocol.json`.
