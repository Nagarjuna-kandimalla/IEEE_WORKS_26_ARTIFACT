# Component: modeling, calibration, and allocation

This component implements the offline causal memory experiment used by RQ2
and reused by the CAMP side of RQ3. Its configuration is
`SCRIPTS/RQ2/config/experiment.json`.

## Feature contracts

### A: static prelaunch information

Categorical fields are workflow, process, workflow version, and system
configuration. Numeric fields include log-transformed static and staged input
sizes, worker count/vCPUs/memory, requested threads, requested Java heap,
coverage, interval length, and the string-region indicator.

### P: prior peak-RSS information

P adds causal historical peak-memory summaries over scopes `i0` through `i5`:
support, peak p50/p95/p99, log spread, confidence, similarity/distance fields,
and the selected history scope. Only completed earlier tasks contribute.

### C: consumed-data information

C adds causal historical consumed p50/p95/p99 values,
consumed-to-input history, and `log1p_c_hat_bytes`. `C-hat` is a prediction of
the current task's consumption learned from leakage-free historical/static
features; it is not the current task's measured consumption.

The code explicitly excludes current-row peak memory, measured consumption,
read/fault bytes, runtime, and observed OOM status from feature columns.

## Causal history hierarchy

`history.py` builds an index using static-signature scaling and hierarchical
keys. It moves from exact evidence through input/context similarity and then
to broader process, workflow, or global evidence when support is insufficient.
The frozen history settings are:

| Setting | Value |
|---|---:|
| Nearest neighbours | 48 |
| Scope window | 512 |
| Exact minimum support | 2 |
| Input minimum support | 3 |
| Nearest minimum support | 8 |
| Process minimum support | 16 |
| Workflow minimum support | 32 |

Initial development rows use warm leave-one-out history. Test rows are replayed
chronologically, and earlier completed test tasks may update causal history
when the configured path enables sequential updates.

## Model family

The implementation uses LightGBM quantile regression inside scikit-learn
preprocessing pipelines. Numeric values receive median imputation with missing
indicators; categorical values receive most-frequent imputation and one-hot
encoding with unknown-category support.

Frozen model settings are five grouped cross-fitting folds, three ensemble
members, 350 estimators, learning rate 0.03, and quantiles 0.5, 0.9, 0.95,
0.99, and 0.995. Grouped folds and out-of-fold predictions prevent a training
row from supplying its own fitted prediction. Final models fit development
data and predict the held-out replay population.

Outer seeds 1996 through 2000 shift the three model seeds deterministically.
They change model and independently selected policy randomness while keeping
the split manifests fixed.

## Point stack

The enhanced point prediction is constrained to retain a strong static
anchor. The minimum A weight is 0.9, the search grid step is 0.1, and a small
tie penalty favors A. This prevents causal-history additions from freely
overriding the static prediction when their evidence is weak.

## Allocation candidates

Calibration turns quantile predictions into a first request. Candidate base
rules are:

```text
point_q50
neighbour_p95
neighbour_p99
model_q90
model_q95
model_q99
model_q995
```

For a model quantile, the base is never below the q50 point prediction. For a
neighbour rule, the base is the maximum of q50 and the matching historical
neighbour peak quantile.

The base is multiplied by an exponentiated historical log-residual correction
at one of these quantiles:

```text
0.90, 0.95, 0.975, 0.99, 0.995, 0.999
```

The corrected value is rounded upward to MiB.

## Policy selection

Development rows are deterministically divided by hashed logical task ID:
60% build the residual reference index and 40% evaluate candidates. The
configured targets are 99% global coverage and at least 97.5% coverage in
every workflow. Among candidates meeting both constraints, selection chooses
the smallest request-to-observed-peak ratio, then fewer underallocations, then
the lower residual quantile and stable policy order. If no candidate meets the
constraints, the best available coverage-first candidate is retained and its
status records that the constraints were unmet.

This selection uses development outcomes only. Holdout outcomes are used only
after allocation to evaluate the frozen decision.

## Sequential allocation

For each test task, the selected base prediction and history-derived residual
correction create the first request. The task's recorded peak RSS determines
whether that request would have underallocated. If the offline retry policy is
evaluated, requests are multiplied by 1.5 until they cover the recorded peak;
the summary records failed attempts and unresolved tasks.

An RQ2 underallocation is therefore an offline comparison between first
request and recorded peak RSS. It is not proof that the scheduler observed an
actual OOM exit during a new workflow execution.

## Process-tail phase

`run_offline_experiment.py` initially creates a global-tail A+P+C result.
Before applying the final process-tail stage, the commands preserve that
directory as `a_plus_p_plus_c_global_base`. `train_process_tail.py` then
replaces mixed-scale global tail predictions with per-process LightGBM tail
ensembles for q90, q95, q99, and q99.5, reselects the allocation policy, and
rewrites the A+P+C task table. The q50 point prediction is retained, so tail
postprocessing can change underallocations without changing q50 error.

The same route must be used for every split when making a split comparison.
`aggregate_route_contracts.py` keeps uniform-global, uniform-process, and
legacy-mixed contracts distinct so they are not silently combined.

## Primary output tree

For a seed, the runner and process-tail stage produce:

```text
SCRIPTS/RQ2/results/seed_<seed>/
  initial_causal_features.tsv
  test_causal_features.tsv
  c_hat_initial_oof.tsv
  c_hat_test.tsv
  a_plus_p/
    initial_oof_predictions.tsv
    calibration_candidates.csv
    selected_policy.json
    task_predictions.tsv
  a_plus_p_plus_c_global_base/
    ...preserved pre-process-tail result...
  a_plus_p_plus_c/
    initial_oof_predictions.tsv
    calibration_candidates.csv
    selected_policy.json
    process_tail_selected_policy.json
    task_predictions.tsv
  run_manifest.json
```

`summarize_rq2.py` validates aligned A+P/A+P+C task IDs and writes overall,
per-workflow, and paired outcomes. The variance aggregators combine completed
split/seed summaries into the committed CSVs.

## Metrics and interpretation

- point MAE and MdAPE assess prediction error;
- first-attempt coverage is the fraction whose first request covers peak RSS;
- underallocations and underallocations per 1,000 measure safety failures;
- requested memory and request-to-peak ratio measure reservation cost;
- unused memory-time weights excess allocation by runtime;
- paired rescued/introduced counts show which task outcomes changed;
- mean, sample SD, standard error, t-based 95% CI, minimum, and maximum assess
  model-and-policy seed sensitivity.

These metrics can move in different directions. In particular, median error
can improve while upper-tail calibration worsens.
