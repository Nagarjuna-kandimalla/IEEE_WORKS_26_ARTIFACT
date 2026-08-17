# Allocation calibration lifecycle

This guide explains how CAMP converts trained memory predictions into a
first memory request. It follows the implemented RQ2 code from development
predictions through policy selection, chronological testing, retry simulation,
and the equivalent decisions required in a live scheduler.

The primary implementation is in:

```text
SCRIPTS/RQ2/calibration.py
SCRIPTS/RQ2/run_offline_experiment.py
SCRIPTS/RQ2/train_process_tail.py
SCRIPTS/RQ2/config/experiment.json
```

Model fitting and feature construction are explained separately in
[`14_MODEL_TRAINING_LIFECYCLE.md`](14_MODEL_TRAINING_LIFECYCLE.md).

## 1. What calibration does

A model prediction and a scheduler request answer different questions:

- a q50 prediction estimates a central or typical peak;
- a high model quantile estimates a tail of the learned peak distribution;
- calibration checks how model bounds behaved on completed development tasks;
- the calibrated first request adds a nonnegative empirical safety correction;
  and
- retry handles the separate case in which the first request was still too
  small.

Calibration does not retrain LightGBM. It selects a frozen rule for converting
already generated prediction columns into allocation requests.

## 2. The full allocation lifecycle

```text
Leakage-controlled development predictions and known development peaks
        |
        v
Deterministically divide development rows into reference and selection rows
        |
        v
For each candidate model/history base:
  compute reference residuals and build the residual history index
        |
        v
For each candidate residual quantile:
  turn base x residual correction into whole-MiB requests
        |
        v
Measure coverage and request cost on selection rows
        |
        v
Choose and freeze one eligible base/quantile policy
        |
        v
For each chronological test task:
  choose base -> obtain prior residual correction -> freeze first request
        |
        +--> request sufficient: task completes without retry
        |
        +--> request insufficient: simulate native 1.5x/history-guided retry
        |
        v
After the task outcome is revealed, append its residual for later test tasks
```

The current configuration selects one global allocation-policy pair for each
feature view. The residual correction is nevertheless hierarchical and can be
calculated from a task's local residual-history scope. Per-process tail models,
residual-correction scope, and allocation-policy selection scope are three
different concepts: the final CAMP tail predictions are process-specific when
possible, the correction can use I0--I5 evidence, and the selected base-policy
name and residual quantile remain global in this configuration.

## 3. Information available before calibration

Calibration runs after cross-fitting. For every development row it receives:

- a stable logical task ID;
- workflow, process, version, system, task, and input identities;
- the scaled task signature used for similarity search;
- prior-history features created without the row's own outcome;
- out-of-fold q50, q90, q95, q99, and q99.5 peak predictions; and
- the observed peak RSS, used only as a completed development outcome.

Out-of-fold predictions are essential. If calibration used predictions from a
model fitted on the same rows, those predictions could be unrealistically
accurate and the selected request rule could be too small for future tasks.

The test peaks are not used here. They remain hidden from model fitting and
policy selection.

## 4. Reference and selection partitions

`select_allocation_policy()` divides the development population a second time:

```text
stable_fraction(logical_task_id) < 0.60  -> calibration reference
stable_fraction(logical_task_id) >= 0.60 -> policy selection
```

The fraction is produced from a stable hash of the logical task ID. It is not
affected by file order or a process-local random-number generator.

The two parts have different jobs:

| Part | Approximate share | Job |
|---|---:|---|
| Reference | 60% | Measure past model underprediction residuals |
| Selection | 40% | Compare candidate request policies on rows not used to build those residual distributions |

Both parts come only from the development population. The test population is
still untouched.

If either part is empty, the implementation stops with an error instead of
selecting a policy from an invalid partition.

## 5. Candidate allocation bases

The base is the prediction or history bound before residual calibration.
`allocation_base()` implements these candidates:

| Candidate | Base before correction |
|---|---|
| `point_q50` | model q50 |
| `neighbour_p95` | maximum of model q50 and I2 neighbor-history peak p95 |
| `neighbour_p99` | maximum of model q50 and I2 neighbor-history peak p99 |
| `model_q90` | maximum of q50 and model q90 |
| `model_q95` | maximum of q50 and model q95 |
| `model_q99` | maximum of q50 and model q99 |
| `model_q995` | maximum of q50 and model q99.5 |

Every candidate is bounded below by q50. A high-quantile or history candidate
therefore cannot accidentally reduce the base below the point prediction.

When a neighbor-history value is missing or nonnumeric, it falls back to q50.
The packaged Access Baseline and CAMP feature views use hierarchical
calibration and evaluate all seven bases. The implementation also has an
I5-only `global` correction mode; that mode limits candidates to model
quantiles plus `point_q50`, but it is not the mode used by these two packaged
feature views.

## 6. Residual meaning and calculation

For a completed reference row, the log residual is:

```text
residual = log(actual peak MiB / candidate base MiB)
```

It has a direct interpretation:

| Relationship | Residual | Meaning |
|---|---:|---|
| Actual equals base | 0 | Base was exact |
| Actual exceeds base | positive | Base underpredicted |
| Actual is below base | negative | Base overpredicted |

Example:

```text
candidate base = 4,000 MiB
actual peak    = 5,000 MiB
residual       = log(5,000 / 4,000)
               = log(1.25)
               = 0.2231
```

Converting the residual back with `exp` gives the multiplicative miss: 1.25.

The implementation protects the division and logarithm with a small positive
lower bound. Non-finite residuals are not added to the index.

## 7. Residual-history index

The residual index asks: for prior completed tasks related to the new task,
how much did this particular candidate base underpredict?

Each reference row contributes its scaled signature and residual to the same
identity hierarchy used for task history:

| Scope | Residual evidence |
|---|---|
| I0 | Exact task instance and split group in the same context |
| I1 | Same input identity in the same context |
| I2 | Up to 48 nearest signatures in the same workflow/process/version/system context |
| I3 | Same workflow/process/version/system context |
| I4 | Same workflow/version/system |
| I5 | Same system configuration |

Only the most recent 512 values in a selected scope contribute to a residual
quantile. Scope support thresholds are:

```text
I0 >= 2
I1 >= 3
I2 >= 8
I3 >= 16
I4 >= 32
I5 >= 1
```

The first supported scope in that order is selected. If no narrower scope is
supported, the code attempts I5.

### 7.1 Confidence

The selected scope receives a confidence value based on:

1. **support**: `n / (n + 16)`, which rises as more residuals exist;
2. **stability**: `1 / (1 + spread)`, where spread is residual p95 minus
   residual p50; and
3. **distance** for I2: `exp(-median nearest-neighbor distance)`.

For scopes other than I2, the distance factor is 1.

High support, a tight residual distribution, and close neighbors produce
higher confidence. Sparse, unstable, or distant evidence produces lower
confidence.

### 7.2 Parent blending in hierarchical mode

In hierarchical mode, the local residual quantile is blended with a broader
parent:

```text
I0, I1, or I2 -> parent I3
I3            -> parent I4
I4 or I5      -> parent I5

blended residual
  = confidence x local residual
  + (1 - confidence) x parent residual
```

If local or parent evidence is absent, the implementation falls back toward
the broader available value and finally zero.

The packaged Access Baseline and CAMP runs use this hierarchical correction
mode. Separately, `enhanced_policy_scope` is set to `global`, so a single
base-policy/residual-quantile pair is selected for the entire feature view.
That configuration value does not force every task's correction to I5.

## 8. Candidate residual quantiles

The frozen configuration tests these empirical residual probabilities:

```text
0.90, 0.95, 0.975, 0.99, 0.995, 0.999
```

The residual quantile uses NumPy's conservative `higher` rule. It selects an
observed value at or above the requested empirical position rather than
interpolating a smaller value between two observed residuals.

After selection or blending, the log correction is clipped at zero:

```text
correction = max(residual_quantile_value, 0)
```

Therefore calibration can preserve or increase a candidate base, but it
cannot reduce it. Negative residuals mean the base was already high; they do
not justify shrinking that base in this policy.

## 9. Turning a base into the first request

For a task and candidate pair:

```text
calibration factor = exp(nonnegative log correction)

local residual bound MiB
  = allocation base MiB x calibration factor

first allocation MiB
  = ceiling(local residual bound MiB)
```

The ceiling converts the continuous model result into a whole-MiB request and
ensures the rounded request is not below the calculated bound.

### 9.1 Numeric example

Assume:

```text
q50             = 2,800.0 MiB
q99             = 4,600.4 MiB
selected base   = model_q99
log correction  = 0.04879
```

Then:

```text
base             = max(2,800.0, 4,600.4) = 4,600.4 MiB
factor           = exp(0.04879)           = 1.05
local bound      = 4,600.4 x 1.05         = 4,830.42 MiB
first allocation = ceil(4,830.42)         = 4,831 MiB
```

The current task's actual peak has not been observed or used in this
calculation.

## 10. Evaluating candidate policies

Every base and residual-quantile pair is evaluated on the 40% selection
partition. The code records:

- total selection rows;
- global coverage, the fraction with request at least actual peak;
- coverage for each workflow;
- minimum workflow coverage;
- selection OOM count, where request is below actual peak; and
- request-to-peak ratio, calculated as total requested MiB divided by total
  actual peak MiB.

The configured constraints are:

```text
global coverage             >= 99.0%
minimum per-workflow coverage >= 97.5%
```

Among candidates meeting both constraints, the selector chooses in this
order:

1. smallest request-to-peak ratio;
2. fewer selection OOM tasks;
3. smaller residual quantile; and
4. stable base-policy name ordering.

This attempts to meet safety coverage while avoiding unnecessarily large
requests.

If no candidate meets both constraints, the run does not silently claim
success. It chooses the best available candidate by minimum workflow
coverage, then global coverage, then request ratio, and writes
`unmet_best_available` as the constraint status.

The code also supports choosing a policy separately for each workflow/process
when `enhanced_policy_scope` is set to `per_process`. That mode targets 99%
coverage per process. It is implemented, but it is not the scope selected by
the packaged primary configuration.

## 11. The retained RQ2 policies

The packaged task-level results show the policies used for the primary seed:

| Feature view | Selected base | Residual quantile | Test rows | First-request underallocations |
|---|---|---:|---:|---:|
| Access Baseline (`A+P`) | `model_q99` | 0.95 | 5,026 | 87 |
| CAMP (`A+P+C`) | `model_q995` | 0.99 | 5,026 | 31 |

These values can be inspected in:

```text
RESULTS/RQ2/csv/a_plus_p_task_predictions.tsv.gz
RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz
```

They are results of the frozen experiment, not hard-coded universal choices.
A changed cohort, split, feature contract, model, or calibration target may
select a different pair.

## 12. Applying the frozen policy to test tasks

`apply_policy_sequentially()` performs the chronological test application.

Before the first test task, it:

1. calculates the chosen base for every development row;
2. builds a residual index from all development rows for that base;
3. calculates the chosen base for every test row; and
4. transforms each test signature using the development-fitted scaler.

Using all completed development rows here is intentional. The 60/40 partition
was used to make an honest policy choice. After the rule is frozen, all
available development outcomes can seed the operational residual history.

For each test row in experiment order, it:

1. identifies the already selected policy;
2. obtains the row's q50 and selected high-quantile prediction or history
   bound;
3. calculates the selected residual correction using prior completed rows;
4. multiplies base by the correction factor;
5. rounds upward to whole MiB;
6. records the first allocation; and
7. only then reveals the actual test peak for evaluation and history update.

When test updating is enabled, the completed row's residual is added to every
needed residual index after its first allocation has been frozen. The next
test row may use it. Model weights, preprocessing medians, categorical levels,
base-policy choice, and residual-quantile choice do not change during test.

## 13. What each task-level allocation field means

The retained task TSVs include:

| Column | Meaning |
|---|---|
| `allocation_base_policy` | Frozen candidate type selected during calibration |
| `allocation_residual_quantile` | Frozen residual probability selected during calibration |
| `allocation_base_mib` | q50-bounded prediction/history base for this task |
| `calibration_log_correction` | Nonnegative residual correction on log scale |
| `calibration_factor` | `exp(calibration_log_correction)` |
| `calibration_scope` | Residual-history scope used for this task |
| `calibration_support` | Number of prior residuals supporting that scope |
| `calibration_confidence` | Support/stability/similarity confidence |
| `local_residual_bound_mib` | Base multiplied by calibration factor before rounding |
| `first_allocation_mib` | Whole-MiB first request sent to the task |
| `initial_oom` | Whether first request was below recorded actual peak |
| `native_retry_count` | Number of simulated retries needed to cover the peak |
| `native_final_allocation_mib` | Request after simulated retry growth |
| `native_unresolved_oom` | Whether retry remained below the peak after the limit |

`actual_peak_mib` appears in the result table because the experiment scores a
completed test population. It is not an input to `first_allocation_mib`.

## 14. Retry is separate from initial calibration

After the first request has been scored, `retry_policy()` simulates the native
retry contract. If the first request is too small, the next request is the
largest of:

```text
ceil(previous request x 1.5)
ceil(exact-history peak p99), when available
ceil(neighbor-history peak p99), when available
```

This repeats until the request covers the known recorded peak or 20 retries
have been simulated.

In the packaged results:

- Access Baseline has 87 first-request underallocations: 85 resolve in one
  retry, one in three retries, and one in six retries; and
- CAMP has 31 first-request underallocations, all resolving in one retry.

The offline code can stop the loop exactly when allocation reaches the stored
actual peak because it is evaluating completed tasks. A live scheduler does
not know peak memory in advance. It learns only whether an attempt failed or
completed, applies the next request after a failure, and stops after success.

## 15. Detailed task scenarios

### Scenario A: first request is sufficient

Suppose CAMP produces a q99.5 base of 7,664.8 MiB and the correction factor is
1.0031.

1. The local bound is about 7,688.4 MiB.
2. The scheduler request is rounded to 7,689 MiB.
3. The task runs once.
4. If its peak is 7,680 MiB, it completes without retry.
5. After completion, the 7,680 MiB outcome may update history for later tasks.

### Scenario B: first request is too small

Suppose the calibrated request is 7,668 MiB and the task later needs 7,680
MiB.

1. The first request was made without knowing 7,680 MiB.
2. The experiment marks `initial_oom = true`.
3. Retry considers 1.5 times 7,668 and available exact/neighbor p99 values.
4. The next whole-MiB request is the largest candidate.
5. If that value covers 7,680 MiB, retry count is one.

### Scenario C: residual history says the base already overpredicts

Suppose a residual quantile is -0.08.

1. `exp(-0.08)` would reduce the base.
2. The implemented policy clips the correction to zero.
3. The factor is therefore `exp(0) = 1`.
4. Calibration leaves the base unchanged.

### Scenario D: exact residual history is sparse

Suppose a task has only one exact I0 match.

1. I0 does not meet its minimum support of two.
2. The selector checks I1, then I2, then broader scopes.
3. If I2 has 48 neighbors, I2 is used.
4. Its confidence reflects support, residual stability, and neighbor distance.
5. The I2 correction is blended toward its I3 parent when confidence is below
   one.

### Scenario E: first test task

1. Models, scaler, and policy already exist.
2. Its prediction uses development history, not test outcomes.
3. Its calibration residual index is seeded with completed development rows.
4. Its first request is frozen.
5. Only after scoring is its outcome appended for later test tasks.

### Scenario F: a later test task

The later task uses the same fitted models and selected policy as the first
test task. It may receive a different correction because completed earlier
test outcomes have expanded its residual history. This is history adaptation,
not model retraining.

### Scenario G: unseen process

1. The model's categorical encoder ignores the unknown process level safely.
2. The per-process tail stage falls back to its frozen global tail model.
3. Residual history falls toward a supported broader scope.
4. The globally selected base/quantile pair is applied using that task's
   hierarchical correction.

### Scenario H: new system configuration

If no I5 residual exists for the new system configuration, its global residual
quantile is non-finite and the correction falls back to zero. The request then
uses the selected model base without an empirical upward correction.

This is a weakly supported deployment state. A production integration should
validate or seed the new system before relying on calibrated coverage; the
artifact does not invent evidence for a system it has never observed.

### Scenario I: missing static values

Missing static fields are handled by the already fitted model preprocessor
before calibration. Calibration receives the resulting quantile predictions
and the scaled signature. Missing numeric signature values use development
medians, so similarity and residual lookup can proceed without reading the
current outcome.

### Scenario J: two tasks submitted at the same time

Neither task may use the other's unfinished outcome. Both should:

1. read the last committed history snapshot;
2. generate predictions and first requests independently;
3. freeze those requests before launch; and
4. append outcomes only after each task completes.

The packaged experiment uses a deterministic serial order. A live deployment
must add transactional snapshot rules; those scheduler/database operations are
not implemented by the artifact.

### Scenario K: coverage targets are not met

If every candidate misses either the 99% global or 97.5% minimum-workflow
target, the selector returns the best available choice and labels it
`unmet_best_available`. This should be treated as a failed calibration target,
not as evidence that the desired reliability was achieved.

### Scenario L: a rare large outlier

A high model quantile and high residual quantile can reduce the risk from a
long-tailed peak distribution, but neither guarantees coverage of every future
task. An outlier beyond both learned and empirical bounds can still require a
retry. This is why first-request coverage and retry behavior are reported
separately.

### Scenario M: the model is retrained

A newly trained model changes the meaning and distribution of its residuals.
The old calibration policy must not automatically be assumed valid.

The correct sequence is:

1. create new leakage-controlled development predictions;
2. rebuild candidate residual indices;
3. evaluate candidate policies again;
4. verify coverage constraints on the selection partition; and
5. promote the model and matching policy together.

### Scenario N: a completed task was not audited

Access Baseline allocation can still update from its peak RSS if that peak is
available. CAMP's future consumption-history fields require consumption
measurements, so an unaudited task cannot contribute measured consumed bytes.
A live integration must record measurement availability explicitly rather
than treating missing consumption as zero.

## 16. What must exist before a live prediction

The artifact does not package a continuously running scheduler service. A
faithful live integration would need these already prepared objects:

```text
1. fitted SignatureScaler
2. fitted feature preprocessor
3. fitted C-hat model for CAMP
4. fitted q50 and tail peak models
5. selected allocation-policy JSON
6. committed completed-task history
7. committed residual history for the selected base
8. scheduler adapter able to submit whole-MiB requests
```

For each new task, the scheduler-facing sequence would be:

```text
prelaunch task metadata
  -> static/access fields
  -> prior-only history snapshot
  -> C-hat prediction
  -> peak q50 and tail predictions
  -> selected base
  -> prior residual correction
  -> first allocation
  -> launch
  -> completion or memory failure
  -> outcome append and optional retry
```

No current task peak or current measured consumption exists before launch.
Those values can only be outcomes.

## 17. Offline experiment versus live operation

| Concern | Packaged experiment | Live equivalent |
|---|---|---|
| Policy selection | One batch over development out-of-fold predictions | Periodic batch recalibration after model fitting |
| First request | Calculated for each frozen test row | Calculated when a task is submitted |
| Outcome visibility | Stored in cohort but read only after request is frozen | Does not exist until task finishes or fails |
| Residual update | Deterministic experiment order | Commit after completion with concurrency control |
| Retry stopping rule | Stored peak reveals when request is sufficient | Scheduler observes failure or success |
| Policy changes | None during test | Promote only after separate validation |
| Model changes | None during test | Periodic retraining, followed by recalibration |

The live column is an operational interpretation of the implemented decision
order. It is not a claim that this repository includes a daemon, scheduler
plugin, or online transaction service.

## 18. Reproducing and inspecting calibration

The end-to-end RQ2 commands are documented under “RQ2 offline experiment” in:

```text
SCRIPTS/README.md
```

During a full run, each feature view writes:

```text
results/seed_<seed>/<feature-view>/calibration_candidates.csv
results/seed_<seed>/<feature-view>/selected_policy.json
results/seed_<seed>/<feature-view>/task_predictions.tsv
```

The artifact retains the primary task predictions in compressed form under:

```text
RESULTS/RQ2/csv/
```

For a quick read without modifying the artifact:

```bash
gzip -cd RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz | less -S
```

To view only the header and first task:

```bash
gzip -cd RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz | sed -n '1,2p'
```

## 19. Source map

| Question | Source |
|---|---|
| How are candidate bases calculated? | `SCRIPTS/RQ2/calibration.py` (`allocation_base`) |
| How are reference residuals indexed? | `SCRIPTS/RQ2/calibration.py` (`ResidualIndex`, `build_residual_index`) |
| How is the policy selected? | `SCRIPTS/RQ2/calibration.py` (`select_allocation_policy`) |
| How is it applied chronologically? | `SCRIPTS/RQ2/calibration.py` (`apply_policy_sequentially`) |
| How are retries simulated? | `SCRIPTS/RQ2/run_offline_experiment.py` (`retry_policy`) |
| Where are targets and candidates fixed? | `SCRIPTS/RQ2/config/experiment.json` |
| Where are process-tail predictions recalibrated? | `SCRIPTS/RQ2/train_process_tail.py` |
| Where are retained task-level results? | `RESULTS/RQ2/csv/` |
