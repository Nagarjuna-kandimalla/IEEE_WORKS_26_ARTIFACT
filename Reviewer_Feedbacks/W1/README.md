# R3_W1 — reproduce the W1 comparisons reported in the paper

This package reproduces the W1 passages in Sections IV and V of the
**eight-page editorial paper**, using the original seed-1996 predictions:

1. CAMP versus Access at equal aggregate memory requests, including recorded
   unused memory-time.
2. CAMP versus Sizey at equal aggregate memory requests.
3. CAMP versus Sizey at matched 95% observed coverage.

The original data, prediction tables, split-validation code, and numerical
reference results are bundled here. Reproduction needs no sibling repository,
S3 access, network access, elastic worker, or model training once dependencies
are installed. It reproduces the W1 reanalysis from recorded predictions;
it does not regenerate the original predictors or workflow measurements.

## Run

After installing dependencies with `bash setup.sh`:

```bash
cd Reviewer_Feedbacks/W1
bash reproduce.sh
```

For a copied package on another machine, use Python 3.9 and install the pinned
dependencies once:

```bash
python3.9 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
bash reproduce.sh
```

The runner locates the package relative to itself. An existing environment can
be selected with `W1_PYTHON=/absolute/path/to/python`. Pass
`--output /absolute/path/to/results` to write results elsewhere.

## Files

| Path | Purpose |
|---|---|
| `scripts/reproduce_paper.py` | Equal-budget and matched-coverage calculations, numerical regression checks, and manuscript-value checks |
| `scripts/paper_metrics.py` | Uniform scaling, coverage, requests, and recorded unused memory-time |
| `scripts/validate_inputs.py` | Original task identities, seed, measurements, reconstructed split, and native-result validation |
| `inputs/original_artifact/` | Exact local copies of required original artifact files; includes validation code |
| `inputs/archived_rq3_matched_task_predictions.tsv.gz` | Original matched predictions, retained whole for provenance and native validation |
| `inputs/reference_results/` | Pre-organization results used only as regression references |
| `inputs/paper_claims.json` | Exact W1 manuscript passages and expected displayed values |
| `input_manifest.json` | Relative input paths, original locations, and SHA-256 hashes |
| `protocol.json` | Cohorts, seed, scaling rule, coverage target, and metric conventions |
| `results/paper_results.md` | Human-readable reproduced paper results |
| `results/paper_values.json` | Every W1 number at the precision printed in the manuscript |
| `results/native_and_equal_budget.csv` | Native and equal-budget results |
| `results/rq3_operating_points.csv` | Native, equal-budget, and minimum-95%-coverage points |
| `results/rq*_paired_requests.tsv.gz` | Task-level requests and measurements for auditability |
| `results/validation_manifest.json` | Original-result validation |
| `results/completion.json` | Reproduction status, time, software, and source hashes |
| `logs/reproduce_paper.log` | Recorded execution of the paper reproduction command |

## Preserved conventions

- All 5,026 RQ2 tasks remain included. For memory-time only, the original zero
  weighting for the same 449 missing runtimes is preserved for both methods.
- RQ3 uses the original 21,337 eligible test tasks and 9,141 development tasks.
- First requests, predictions, measured peaks, and native histories remain fixed;
  scaling is continuous, without rounding requests to MiB.
- Coverage matching uses the recorded test peaks retrospectively and applies
  one uniform multiplier to every request from each method. It is not a
  deployment calibration procedure.
- Budget means the sum of first requests in GiB, not simultaneous RAM or GiB-hours.
- The full archived prediction table contains other original feature views;
  these are retained to validate provenance, not evaluated as additional W1
  experiments.

## Material outside the paper

Earlier diagnostics, bootstrap analyses, operating curves, and historical
material remain in the working archive outside this published package.
That archive is not a dependency of this package. The current command does not
generate those analyses. The organization change does not alter the manuscript.

## Verification in this artifact

`bash reproduce.sh` regenerates and validates the paper values. See
`verification/reproduce.log`. No model training is needed for W1.
