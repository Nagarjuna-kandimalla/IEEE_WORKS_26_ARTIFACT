# Reviewer package verification — 2026-09-28

All four `reproduce.sh` commands were executed successfully **from the copied
artifact directories**, using the existing compatible Python 3.9 environment.
They read their packaged inputs and write their own results. No cloud jobs,
model-training experiments, or live workflows were launched for this check.

| Package | Executed verification | Result |
|---|---|---|
| W1 | Original input checksums, native-result/split validation, recalculation of equal-budget and 95%-coverage points, and every rounded paper value | Passed; all historical reference values and paper values match |
| W2 | Input/source fingerprints, six contract tests, regeneration from all 25 paired cases' task-level outcomes, full per-seed and summary-table comparisons | Passed; primary means 265.8/305.8, 13.1% fewer events and 2.4% more requests |
| W3 | Training-entry imports, prepared-feature parity, paired task/peak checks, original prediction hashes, recalculated native counts, requests and MdAPE | Passed; original 32/30 native underallocations reproduced |
| W5 | Recalculation of all 8 budgets × 5 seeds and comparison of all summary/per-seed values | Passed; positive net budgets remain 5%, 10%, 15%, with 20% approximately break-even |

Numerical table comparisons use `rtol=1e-12`, `atol=1e-10` where specified in
the validators; W1's pre-existing comparison uses `atol=1e-8` for raw aggregate
tables and exact matching for the printed paper values. Integer counts and
task identities must match exactly. PDF byte equality is not a validation
criterion because generated PDF metadata can differ; numerical tables are.

## What this verifies

The standard reproduction commands genuinely recompute results from recorded
task-level evidence; they do not copy the frozen expected summaries into the
output. The expected summaries are used only after computation as checks.
W2 also reruns its leakage, observed-history, equal-count selection, and
request-matching contract tests.

Fresh W2 and W3 model-training commands are included, with separate output
directories and checks against the recorded results. Their command-line
interfaces were checked, but the full training runs were not repeated during
packaging. Successful postprocessing does not establish fresh-training
reproducibility across different machines.

Setup scripts were checked for shell syntax. The executions used an already
installed pinned environment; installation into a new environment was not
repeated. Refer to each requirements file for the matching versions.

## Evidence

- [W1 log](W1/verification/reproduce.log) and [completion record](W1/results/completion.json)
- [W2 log](W2/verification/reproduce.log) and [validation](W2/results/reproduction_validation.json)
- [W3 log](W3/verification/reproduce.log) and [validation](W3/results/reproduction_validation.json)
- [W5 log](W5/verification/reproduce.log)
- [Machine-readable verification](VERIFICATION.json)
- [Copy provenance](COPY_PROVENANCE.json)

The original 16 artifact source files recorded by W1 still match their
previous hashes. The original reviewer workspaces were copied, not moved.
This artifact update adds `Reviewer_Feedbacks/` and a link in the root README;
the existing experiment data, code, and result files are retained.
