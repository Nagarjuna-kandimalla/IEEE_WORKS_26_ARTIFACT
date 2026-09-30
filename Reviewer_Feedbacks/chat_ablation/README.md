# CAMP C-hat ablation: reproducibility package

This directory is a self-contained copy of the code, fixed input population,
split manifests, retained predictions, and expected outputs for the four-way
CAMP feature ablation. It does not require the restored S3 mount.

## Verified reference result

| Feature view | Held-out tasks | Underallocations |
|---|---:|---:|
| A+P | 5,026 | **85** |
| A+P+CH | 5,026 | **46** |
| A+P+CHAT | 5,026 | **52** |
| A+P+CH+CHAT | 5,026 | **30** |

These are the latest completed ablation results. The original paper's frozen
Access/CAMP endpoints are 87/31; those predictions are retained separately in
`published_reference/` and labeled in `paper_endpoint_reproduction.csv`.
The ablation and original endpoints must not be combined into an 85/31 result.
This experiment is separate from the read-versus-read+mmap experiment in
[`../W3/`](../W3/README.md).

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
- `verification/`: local reproduction log and validation record.
- `provenance/`: source checksums and original copies of adapted launch files.

## Environment

Linux, Python 3.9, and `libgomp` are required for the tested environment. Create the pinned
environment with:

```bash
cd Reviewer_Feedbacks/chat_ablation  # from the artifact repository root
bash setup.sh
```

`setup.sh` installs the exact versions in `requirements.txt`. Installing them
may contact the configured Python package index.

## Reproduce the analysis from retained predictions

This recomputes every reported metric and the 5,000-repeat grouped bootstrap,
then compares the outputs with the retained expected results:

```bash
bash reproduce.sh
```

To use an existing environment with the pinned dependencies:

```bash
REVIEWER_PYTHON=/absolute/path/to/python bash reproduce.sh
```

Outputs are written to `results/paper_configuration/` and
`results/route_analysis/`. `run_reference_analysis.sh` is the underlying
analysis entry point and accepts the `PYTHON` environment variable.
The reproduction command also verifies input checksums, the 33,516-task
population, disjoint development/test sets, paired task identities and peaks,
feature membership, and the exact 85/46/52/30 counts. It uses local inputs and
does not train models or submit a Slurm job.

Underallocation means `first_allocation_mib < actual_peak_mib`; all 5,026 tasks
enter coverage and request totals. For unused GiB-hours, the recorded convention
assigns zero runtime to 449 tasks with missing runtimes. This applies equally
to all four variants.

## Rebuild models and the complete ablation

The full run rebuilds causal histories, trains C-hat, trains all four peak-RSS
variants, applies both routing configurations, recomputes the analyses, and
validates them:

```bash
bash run_full_ablation.sh
```

For the existing 16-vCPU elastic Slurm worker:

```bash
sbatch --chdir="$PWD" slurm_full_ablation.sbatch
```

If package-owned model or result directories already exist, the full script
stops to protect them. Use `./run_full_ablation.sh --fresh` to replace those
generated directories. The default `N_JOBS=8` matches the recorded worker run;
the optional Slurm script requests 16 CPUs but retains eight model threads.
Model fitting can have very small floating point
differences across LightGBM builds or CPU architectures; validation uses tight
numeric tolerances and enforces the exact population and experiment contract.
Fresh training was not repeated when adding this package to the artifact.
See [verification/README.md](verification/README.md) for what was executed.

## Integrity

Run `sha256sum -c MANIFEST.sha256` from this directory to verify the copied
source, data, reference predictions, and expected results. Generated models,
analysis outputs, virtual environments, and Python caches are excluded from the
manifest.
