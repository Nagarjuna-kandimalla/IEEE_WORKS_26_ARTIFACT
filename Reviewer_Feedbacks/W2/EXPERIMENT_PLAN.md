> Workspace scope: this folder now retains only 30/70 comparison split (rq4). The text below is the original frozen two-cohort plan, retained for provenance. Use workspace.py for cohort-scoped execution.

# Selective auditing and subsequent underallocation

Status: prepared protocol; full experiment has NOT been run. Small validation
runs, when present, are implementation checks and are not paper results.

## Question and controlled comparison

Do CAMP-selected consumption observations reduce underallocation on subsequent
tasks compared with workflow-matched random auditing? Within each cohort,
both arms begin with the same models, development observations, calibration,
and trained audit gates. Architecture, features, model seeds, update schedule,
and allocation settings are identical. Only audit selections differ.

Primary budget: 20%. Secondary budgets: 5%, 10%, 30%, 50%.
Five predefined sampling seeds: 1996–2000. These repeat random selection and
the selective policy's exploration lane; model seeds remain fixed. They do
not measure variation from new dataset splits or independent workflows.

## Data and exact assignments

| Cohort | Initial development | Evaluation | Workflows | Workflow–process pairs in development |
|---|---:|---:|---:|---:|
| CAMP RQ2 original 85/15 membership | 28,490 | 5,026 | 6 | 32 |
| Sizey-comparison / RQ4 30/70 membership | 9,141 | 21,337 | 6 | 14 |

Both are from the submitted local artifact. The second uses its task cohort,
not Sizey's predictions. The cohorts overlap and are not independent datasets.
No restored/S3 sources or new workflow executions are required.

Every task has peak RSS and total consumed bytes. RQ2 has 449 evaluation tasks
with missing runtime. All are retained for coverage/request/shortfall metrics;
optional memory-time diagnostics report their available-positive-runtime
denominator explicitly. Runtime does not determine audit selection or budgets.

`prepared/<cohort>/*_assignment.tsv` freezes all task IDs and roles. Each initial
workflow's split groups are deterministically assigned 60% fit, 20% calibration,
20% audit-gate training (fractions apply to groups, not necessarily row counts):

| Cohort | Fit rows | Calibration rows | Gate-training rows |
|---|---:|---:|---:|
| RQ2 | 17,085 | 5,683 | 5,722 |
| RQ4 | 5,486 | 1,836 | 1,819 |

The entire initial cohort is common, fully observed warm-start history. Model
fitting and label-based calibration/gate training use the disjoint roles above.
This is a new controlled experiment, not an exact reproduction of the native
paper scores or their fitting/calibration procedure. Initial audit cost is
common to both arms and excluded from incremental audit budgets.

This is a warm-start comparison. The relatively large common initial history,
especially in RQ2, may limit the incremental benefit of additional audits. A
small or negative selective-audit effect remains a valid experimental outcome.

## Pending batches and update order

Use five fixed, balanced pending batches. Within each workflow–process pair,
retain the source replay ordering (RQ4) or decision-time ordering (RQ2), then
divide its rows into five consecutive pieces. Combine each corresponding piece
across processes to make a pending batch. Batch sizes are:

* RQ2: 1,014; 1,004; 1,004; 1,004; 1,000.
* RQ4: 4,270; 4,270; 4,266; 4,266; 4,265.

All prior batches are treated as completed before the next batch is predicted.
No within-batch outcome is available for predictions or audit decisions in that
batch. The experiment schedules these batches; it does not reproduce the
original recorded wall-clock concurrency. In particular, RQ4's original split
is not chronological, and the initial cohort is explicitly treated as a
pre-existing warm start. Do not claim that this partition is a chronological
train/test division or a fresh live eBPF execution.

For each arm and batch:

1. Construct features from only the common initial history and that arm's
   observations from completed batches.
2. Predict consumption, global q50 and routed upper-tail quantiles; calibrate
   and persist each integer-MiB request.
3. Score the frozen audit gates using prelaunch inputs only. Select audits
   and persist decisions before exposing that batch's outcomes.
4. Expose peak RSS for every completed task, but consumption only for selected
   tasks. All other consumption values remain missing in history and training.
5. Update history, then refit the consumption and peak-memory ensembles before
   the next batch. Rebuild calibration for the new model version.

The first batch is a parity check: requests must be identical between arms.
The **primary downstream analysis uses batches 2–5** (4,012 RQ2 and 17,067 RQ4
tasks); also report results over all evaluation tasks. Final-batch audits are
recorded for a consistent ongoing budget but cannot affect measured later tasks
within this finite experiment.

## Budgets and selectors

For a cohort and fraction B, choose exactly round-half-up(B × evaluation count)
audits in total. Allocate that integer count proportionally across batches;
within each batch use the artifact's proportional workflow quotas, identical
in both arms. Quotas and assignment depend only on task counts, never outcomes.

* Selective: artifact A+P+C gate features and 80/10/10 risk/information/discovery
  lanes. Half the discovery quota is random exploration, with artifact integer
  rounding and process stratification.
* Random: uniform sampling without replacement within each workflow, with
  exactly the same workflow quotas.

These are equal audit counts, not equal audit CPU time or elapsed duration.
Selection controls which already recorded eBPF consumption measurements are
revealed. It does not launch new eBPF instrumentation. The experiment can
measure downstream allocation effects of the collected information, but does
not measure fresh instrumentation overhead or actual OOM/retry behavior.

## Training and calibration details

Reuse the submitted RQ4 module implementations, copied verbatim under `vendor/`.
The experiment-specific behavior is implemented separately in `scripts/`:

* Global consumption q50: three seeded LightGBM members, trained only on rows
  with observed consumption, with A+P inputs. Five grouped cross-fitting folds
  supply C-hat for audited peak-model training rows; the full consumption model
  supplies it for unaudited training rows and inference.
* Peak prediction: global five-quantile ensemble supplies q50 and tail fallback;
  each available workflow–process ensemble supplies the four upper quantiles.
  Three members/quantile, 350 trees/member, same feature schema as the artifact.
  A process with fewer than two fitting rows uses global fallback because
  LightGBM cannot fit one row. Sorting prevents crossed quantiles.
* Training history features are saved at each task's prediction time. They are
  not reconstructed later using measurements that were unavailable then.
  Initial fitting features use leave-one-out history from fitting rows only;
  initial calibration/gate features use that fitting history.
* The consumption-history adapter preserves the artifact's peak-based scope,
  support/confidence, nearest-48 choice, and 512-row windows. Consumption
  quantiles use only observed values inside the same selected window; no
  observation means missing, not zero. All-observed history matches the
  artifact. No new feature columns or inverse-probability weighting are added.
* Reserve 20% of rows in each online process/batch, deterministically by task-ID
  hash, for calibration. Their labels never fit the prediction models. Peak
  observations from all rows still enter causal history. Retraining uses
  initial fitting rows plus completed online fitting rows; calibration uses
  initial calibration rows plus completed online calibration rows.
* After each model refit, recompute reference residuals with that model and
  rebuild the artifact's hierarchical residual index. Old-version residuals
  are not mixed with new-version residuals. Reference rows are always excluded
  from direct model fitting. Calibration-feature history is the recorded
  prelaunch history; C-hat is recomputed under the current consumption model.
* Freeze the original cohort's allocation setting: RQ2 model_q995 / residual
  quantile 0.99; RQ4 model_q99 / 0.95; hierarchical calibration for both.
  No operating point is selected using evaluation outcomes.
* Freeze the gate models after initial development. The information target
  uses a separately fitted A+P q50 comparator, as required by the artifact.
  If a development gate label has one class, use its constant empirical
  probability; if fewer than five shortfalls exist, use a constant conditional
  log-shortfall. Log these degeneracies. This replaces the original helper's
  fail-fast behavior and must be disclosed if it occurs in the full run.

## Outcomes, plots, and uncertainty

Report native underallocation rate/count and total requested GiB together.
Also report shortfall and positive unused memory. Underallocation is
`recorded request < measured peak RSS`; it is not an observed OOM count.

For the controlled memory comparison, multiply all random-arm requests in a
batch by `sum(selective requests) / sum(random requests)`. Only requests enter
the factor. Compare coverage against the same peaks after normalization.
This is an analytical comparison with fractional MiB; native requests retain
the integer-MiB allocator outputs. Do not use normalized requests as actual
decisions, gate features, or training data.

Produce two figures (native and matched memory), each with one panel per
cohort, audit budget on x, downstream underallocation rate on y, and separate
selective/random curves. Tables include paired selective-minus-random rate
differences. Negative differences favor selective auditing.

Bootstrap the five sampling-repetition means (2,000 draws, fixed bootstrap
seed) for paired differences and curve intervals. These intervals describe
sampling variability on the fixed cohorts, not variation over new workflows
or model-training seeds. Five repetitions give limited precision. No best
seed, budget, or cohort is selected after seeing outcomes. Report unfavorable
or inconclusive findings as well as favorable ones.

## Validation and reproducibility

Input hashes, source-copy hashes, exact assignments, protocol, dependency
versions, per-task decisions/outcomes, audit masks/lanes, training sizes,
checkpoints, and elapsed times are retained. `summarize.py` refuses a final
summary if required paired runs are missing or initial requests/quotas differ.

Tests check that hidden consumption and current-task labels cannot affect
history, all-observed history agrees with the artifact, empty consumption is
not zero-filled, selection quotas match, and memory matching does not use peaks.
Small end-to-end runs exercise both cohorts and retraining. They use reduced
data/trees/members and are stored separately from production results.

Manuscript Section IV/V changes come after the measured results are inspected;
no new effectiveness claim is supported by preparing or validating this runner.
