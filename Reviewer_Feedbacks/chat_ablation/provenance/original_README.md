# CAMP C-hat ablation: reproducibility package

This directory is a self-contained copy of the code, fixed input population,
split manifests, retained predictions, and expected outputs for the four-way
CAMP feature ablation. It does not require the restored S3 mount.

## Experiment contract

- Population: 33,516 matched tasks from six workflows.
- Development set: 28,490 tasks.
- Held-out test set: 5,026 tasks.
- Split: fixed seed-1996, 85/15 chronological split within each workflow.
- Test labels are not used for training or allocation-policy selection.
- Models: the pinned LightGBM ensemble and allocation code in `pipeline/`.
- Paper configuration: A+P uses the global route; CAMP variants use process-tail
  models when eligible and the global fallback otherwise.
- Route-controlled analysis: all four variants are also evaluated under the
  same global route and under the same process-tail route.

The four feature views are:

| Variant | Included information |
|---|---|
| A+P | Static prelaunch attributes and causal prior peak-RSS history |
| A+P+CH | A+P plus causal consumed-byte history from earlier tasks |
| A+P+CHAT | A+P plus predicted current-task consumed bytes (C-hat) |
| A+P+CH+CHAT | A+P plus both consumed history and C-hat; named `A+P+C` in code |

## Directory map

- `pipeline/`: all feature construction, modeling, calibration, allocation, and
  process-tail source code used by the experiment.
- `pipeline/data/`: the fixed source cohort and the six workflow split manifests.
- `reference_predictions/`: retained predictions from the completed elastic run.
- `published_reference/`: frozen paper endpoint predictions used for the
  reproduction check.
- `expected_results/`: reference analysis tables.
- `logs/`: retained Slurm and checksum logs from the completed run.
- `RESULTS.md`: compact interpretation of the results.

## Environment

Python 3.9 or a compatible newer Python is required. Create the pinned
environment with:

```bash
cd /work/Reviewer_Feedbacks_WORKS/ablation_test
./setup.sh
```

`setup.sh` installs the exact versions in `requirements.txt`. Installing them
may contact the configured Python package index.

## Reproduce the analysis from retained predictions

This recomputes every reported metric and the 5,000-repeat grouped bootstrap,
then compares the outputs with the retained expected results:

```bash
./run_reference_analysis.sh
```

Outputs are written to `results/paper_configuration/` and
`results/route_analysis/`.

## Rebuild models and the complete ablation

The full run rebuilds causal histories, trains C-hat, trains all four peak-RSS
variants, applies both routing configurations, recomputes the analyses, and
validates them:

```bash
./run_full_ablation.sh
```

For the existing 16-vCPU elastic Slurm worker:

```bash
sbatch slurm_full_ablation.sbatch
```

If package-owned model or result directories already exist, the full script
stops to protect them. Use `./run_full_ablation.sh --fresh` to replace those
generated directories. Model fitting can have very small floating point
differences across LightGBM builds or CPU architectures; validation uses tight
numeric tolerances and enforces the exact population and experiment contract.

## Integrity

Run `sha256sum -c MANIFEST.sha256` from this directory to verify the copied
source, data, reference predictions, and expected results. Generated models,
analysis outputs, virtual environments, and Python caches are excluded from the
manifest.
