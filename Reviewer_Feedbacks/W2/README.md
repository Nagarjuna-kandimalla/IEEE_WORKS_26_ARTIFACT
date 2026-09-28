# W2 — selective auditing and downstream allocation

This package contains the original 30/70 comparison cohort: 9,141 development
tasks and 21,337 evaluation tasks. It includes **25 paired cases** (5 audit
budgets × 5 sampling seeds), with selective and random auditing at equal
per-workflow counts.

The paper's primary 20% budget compares native requests after the first model
update: 265.8 versus 305.8 mean predicted underallocations, a 13.1% reduction,
with 2.4% more total requested memory. The result retains its preliminary status.

## Install and reproduce the recorded result

From this directory, on Linux with Python 3.9 and `libgomp` available:

```bash
bash setup.sh
bash reproduce.sh
```

Alternatively, select an existing environment matching `requirements.lock.txt`:

```bash
REVIEWER_PYTHON=/absolute/path/to/python bash reproduce.sh
```

The command verifies input/source hashes and original experiment fingerprints,
runs the six scientific contract tests, reads every recorded task outcome,
regenerates the summary tables and plots, and compares **all table values**
against `expected_results/` at `rtol=1e-12`, `atol=1e-10`. It also checks the
paper's primary-budget numbers explicitly.

The tables are regenerated in `results/downstream_allocation/`; validation is
in `results/reproduction_validation.json`. The tested execution log is
`verification/reproduce.log`.

## Fresh model-training experiment

Use a new destination to avoid overwriting the recorded results:

```bash
.venv/bin/python fresh_training.py /absolute/path/to/new-w2-run \
  --workers 2 --threads 8
```

This trains the initial predictor and fixed gates, runs all 25 paired cases,
updates predictors between batches, summarizes outcomes, and checks against
the recorded results. It requires a multicore host; `workers × threads` must
fit the available CPUs. Two workers with eight threads each match the supplied
16-CPU launch configuration. It may take hours. No cloud provisioning or Slurm
submission occurs automatically. Numerical agreement of a fresh fit on different
hardware must be checked rather than assumed.

## Included files and verification scope

- `inputs/` and `prepared/`: local source rows, prepared data, and assignments.
- `scripts/`, `vendor/`, `config/`, and `protocol.json`: unchanged scientific code
  and settings. Their original fingerprints are preserved.
- `runs/rq4/`: all recorded decisions, outcomes, and completion records needed
  to regenerate the results. Large fitted model checkpoints are omitted.
- `workspace.py`: cohort-scoped entry point. Use it rather than the historical
  direct `scripts/run.py`, which describes the earlier two-cohort workspace.
- `fresh_training.py`: starts a genuinely fresh run with no completed-run skips.
- `expected_results/`: frozen references used only for comparison after computing
  the new summaries.

The packaging test reaggregated all recorded task outcomes and ran contract tests;
it did **not** repeat the full model-training experiment. Source filenames and
historical manifests may mention the other cohort, but only `rq4` is included
and evaluated by this package.
