# W3 — explicit-read versus read+mmap ablation

This package reproduces the paper's primary seed-1996 comparison:
**32 native underallocations with explicit reads and 30 with read+mmap**,
over the same 5,026 tasks. Each arm uses its own development-selected requests;
there is no request scaling in this reported comparison.

The separate C-hat ablation is not this experiment. The broader five-seed
diagnostics remain in the reviewer working archive; this package focuses on
the primary comparison reported for W3.

## Install and reproduce

Use Linux, Python 3.9, and `libgomp` for LightGBM:

```bash
bash setup.sh
bash reproduce.sh
```

Or use an existing environment matching `requirements.lock.txt`:

```bash
REVIEWER_PYTHON=/absolute/path/to/python bash reproduce.sh
```

This checks that the training entry point imports successfully, validates the
two arms' prepared feature parity, and computes native counts, request totals,
and q50 MdAPE from the recorded task predictions. It verifies paired identities,
recorded peaks, seed, all primary numerical results, and the printed 32/30 counts.

Outputs:

- `results/paper_native_metrics.csv`
- `results/reproduction_validation.json`
- `results/feature_validation.json`

The tested command's log is `verification/reproduce.log`. Its validation
recomputes metrics from original predictions; it does not claim that the two
models were freshly trained during packaging.

## Fresh training of both primary arms

```bash
.venv/bin/python fresh_training.py /absolute/path/to/new-w3-run --threads 8
```

The destination must not exist. This copies the required code and prepared
histories, fits the consumption and global/process-tail memory models for both
arms, independently selects policies on development data, replays allocation,
and compares the resulting native metrics against the retained references.
It does not use stored prediction caches in the fresh destination. The original
read-only seed-1996 arm took about 7.4 minutes on the elastic worker; elapsed
time and numerical reproducibility can depend on the machine.

The prepared histories are intentionally bundled: the original experiment
prepared them on the controller, and a different worker produced small history
differences. Fresh training uses those same frozen histories for consistency.
To recreate histories from the canonical rows, `scripts/run_arm.py --prepare-only`
supports that route after the corresponding `data/<mode>/` cache is removed
in a disposable copy; compare regenerated histories before treating the result
as an exact reproduction.

## Contents and portability

- `pipeline/`: original model/history/calibration implementation; configuration
  input paths were made relative, without changing scientific settings.
- `scripts/run_arm.py`: original training runner with local input/output paths.
- `data/`: canonical rows and frozen prepared histories for both signal arms.
- `inputs/`: canonical compressed source, split manifests, and original
  completion metrics used as numerical references.
- `runs/`: completed primary prediction tables, development predictions,
  policy records, and caches. Model checkpoints are omitted.
- `provenance/`: original protocol, input hashes, completion manifest, and
  before-portability copies of the runner/configuration.

No other workspace, S3 mount, Slurm service, or elastic worker is needed for
the standard result-reproduction command.
