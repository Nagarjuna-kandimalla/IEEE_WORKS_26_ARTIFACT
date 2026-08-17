# Model training lifecycle

This guide explains the CAMP model-training path at row, fold, model, and
task-decision level. It also maps the offline experiment to a possible live
deployment without claiming that this artifact contains a continuously
running prediction service.

The primary implementation is in:

```text
SCRIPTS/RQ2/materialize_offline_data.py
SCRIPTS/RQ2/history.py
SCRIPTS/RQ2/modeling.py
SCRIPTS/RQ2/run_offline_experiment.py
SCRIPTS/RQ2/train_process_tail.py
SCRIPTS/RQ2/config/experiment.json
```

## 1. The full lifecycle in one view

```text
Completed cohort and split manifests
        |
        v
Validate rows, fields, identities, and consumed-byte formula
        |
        v
Build leave-one-out development history and chronological test history
        |
        v
Materialize model fields and missing-value handling
        |
        +--> Cross-fit current-consumption model on development rows
        |         |
        |         +--> Development C-hat: out-of-fold predictions
        |         +--> Test C-hat: final model trained on all development rows
        |
        +--> Cross-fit peak-RSS quantile models on development rows
        |         |
        |         +--> Leakage-controlled predictions for calibration
        |
        +--> Fit final peak-RSS models on all development rows
                  |
                  +--> Frozen test q50/q90/q95/q99/q99.5 predictions
        |
        v
Calibrate first-allocation policy using development outcomes only
        |
        v
Apply the frozen model and policy to chronological test tasks
        |
        v
Reveal each completed task outcome and update prior-history state
```

Three different operations must not be confused:

1. **History construction** creates prior peak and consumption summaries.
2. **Model training** learns how predecision fields map to consumption or peak
   RSS.
3. **Allocation calibration** converts predictions into safety-oriented memory
   requests. It is covered in
   [`15_ALLOCATION_CALIBRATION_LIFECYCLE.md`](15_ALLOCATION_CALIBRATION_LIFECYCLE.md).

## 2. What exists before model training

### 2.1 Completed source rows

The packaged RQ2 cohort is:

```text
DATA/RQ2/camp_ml_cohort_33516.csv.gz
```

It contains 33,516 aligned task rows from six workflows. Each row includes:

- stable logical task identity;
- workflow, process, version, and system configuration;
- task and input identity fields;
- static and staged input sizes;
- worker and requested-resource fields;
- recorded peak memory and runtime;
- STRACE/eBPF measurement fields; and
- the observed OOM flag when available.

`materialize_offline_data.py` expands the compressed CSV into the TSV expected
by the experiment. It does not alter modeling values.

### 2.2 Split manifests

The split manifests assign each logical task a role and an experiment
position. The primary split contains:

| Role | Rows | Use |
|---|---:|---|
| Development | 28,490 | History seed, model fitting, cross-fitting, and calibration |
| Test | 5,026 | Frozen evaluation after model and policy selection |

The split is chronological. Test outcomes do not enter model fitting or policy
selection.

### 2.3 Frozen configuration

`config/experiment.json` fixes:

- the split and allowed outer seeds;
- history support thresholds;
- five grouped cross-fitting folds;
- three model-member seeds;
- five peak-RSS quantiles;
- 350 LightGBM estimators per model;
- learning rate 0.03;
- calibration targets and candidate rules; and
- the 1.5 retry multiplier.

Changing these values defines a different experiment.

## 3. Validation before any model fit

`run_offline_experiment.py` rejects a run when observed row counts, workflow
counts, or process counts differ from the configuration. It also verifies:

```text
ebpf_total_consumed_bytes
  = ebpf_read_return_bytes + ebpf_mmap_page_fault_bytes
```

This occurs before fitting the scaler or a LightGBM model. A malformed cohort
therefore fails early instead of silently changing the target.

## 4. History construction before model features

### 4.1 Signature scaler

`SignatureScaler.fit(initial)` uses development rows only. Its signature
contains input sizes, requested resources, worker resources, coverage,
interval length, and the STR-region indicator.

For numeric signature fields it:

1. converts values to numbers;
2. clips negative values to zero;
3. applies `log1p`;
4. replaces missing values with the development median;
5. centers by that median;
6. scales by the interquartile range; and
7. clips standardized values to `[-12, 12]`.

The fitted scaler is saved as:

```text
SCRIPTS/RQ2/models/seed_<seed>/signature_scaler.joblib
```

### 4.2 History scopes

For every task, `history.py` creates six possible scopes:

| Scope | Evidence represented |
|---|---|
| I0 | Same workflow/process/version/system plus exact task instance and split group |
| I1 | Same context plus matching input identity |
| I2 | Up to 48 nearest tasks within the same workflow/process/version/system context |
| I3 | Same workflow/process/version/system context |
| I4 | Same workflow/version/system |
| I5 | Same system configuration |

Each supported scope can provide:

- support count;
- peak-memory p50, p95, and p99;
- consumed-byte p50, p95, and p99;
- median consumed-to-static-input ratio;
- peak log spread; and
- confidence.

I2 additionally records median and minimum signature distance.

The system chooses the first scope meeting its support threshold:

```text
I0 >= 2 rows
I1 >= 3 rows
I2 >= 8 rows
I3 >= 16 rows
I4 >= 32 rows
I5 >= 1 row
otherwise: cold_start
```

### 4.3 What happens to the first development row

The development set uses **warm leave-one-out history**. The code first loads
all 28,490 completed development rows into the history index. It then creates
the history snapshot for each development row while excluding that row's own
task ID.

Therefore, the first row in the development file is not treated as the first
task ever seen by the system:

- it does not train a model by itself;
- it cannot use its own outcome;
- it may use other completed development rows as history; and
- it receives `cold_start` only when no other supported history exists.

This is appropriate because the development population is an already
completed historical seed. It is different from a live system's first-ever
task, described later.

### 4.4 What happens to test history

Test rows are sorted by `experiment_position`, workflow, process, and logical
task ID. For each test task:

1. create its snapshot from the current history index;
2. do not include that task's own outcome;
3. save the snapshot; and
4. only then add the completed task outcome to the index.

The first test task can use development history but no test outcomes. The
second test task can use the first test task after it has been revealed. This
models chronological prior-history growth.

## 5. Materializing model fields

`materialize_model_features()` creates the columns consumed by the model.

### 5.1 Static/access fields

Categorical fields are:

```text
workflow
process
version
system_config_id
```

Numeric fields include:

```text
log1p_static_input_bytes
log1p_staged_original_input_bytes
log1p_staged_intermediate_input_bytes
log1p_staged_external_input_bytes
worker_count
worker_vcpu
worker_memory_gib
requested_threads
requested_java_heap_mb
coverage
interval_length_bp
has_str_region_numeric
```

### 5.2 Prior peak-history fields

The Access Baseline adds supported peak-history values, support, spread,
confidence, distance, and selected history scope.

### 5.3 Consumption fields

CAMP additionally receives:

- prior consumed-byte quantiles;
- consumed-to-input history;
- consumption-history support and confidence; and
- `log1p_c_hat_bytes`, the predicted current consumption.

The measured consumption of the current task is not a model input.

### 5.4 Fields that are forbidden

The feature contract explicitly rejects these current-row outcomes:

```text
peak_memory_bytes
ebpf_total_consumed_bytes
ebpf_read_return_bytes
ebpf_mmap_page_fault_bytes
runtime_seconds
observed_oom_flag
```

They may be targets or retrospective evaluation fields, but they cannot
determine their own prediction.

## 6. Missing values and categorical conversion

Preprocessing is fitted inside each training fold and later fitted again on
the complete development set for final models.

### 6.1 Numeric fields

For a missing numeric value:

1. learn the median from the current fit population;
2. replace the missing value with that median; and
3. create an additional indicator showing that the original value was
   missing.

Example:

```text
requested_threads              = missing
training-fold median            = 8
transformed requested_threads   = 8
requested_threads_missing       = 1
```

An actual value of 8 would have the same numeric value but a missing indicator
of 0.

### 6.2 Categorical fields

Static categorical fields are first converted to strings, with missing values
represented as `unknown`. Missing history scope becomes `cold_start`. The
pipeline also contains most-frequent categorical imputation as a final guard.

One-hot encoding then creates binary columns. A workflow value of `gatk` may
produce:

```text
workflow_gatk = 1
workflow_sarek = 0
workflow_minimap2 = 0
...
```

`handle_unknown="ignore"` prevents failure when a test category was absent
from training. All known columns for that unseen category are zero.

The transformed sparse matrix is passed directly to LightGBM. It is not
written as a separate CSV.

## 7. Grouped cross-fitting

Cross-fitting creates development predictions that do not come from a model
trained on the predicted row's group.

### 7.1 Fold assignment

The grouping unit is:

```text
(workflow, split_group_id)
```

Within each workflow, group IDs are ordered by a stable SHA-256 hash and
assigned round-robin to five folds. Rows sharing a split group remain in the
same fold.

### 7.2 One fold in detail

For fold 0:

1. use folds 1–4 as the fitting rows;
2. fit numeric medians and categorical levels on those fitting rows only;
3. transform fitting and fold-0 rows with that fitted preprocessor;
4. fit the requested LightGBM quantile models on fitting rows; and
5. write predictions only into fold-0 positions.

Repeat this for folds 1–4. Every development row ends with a prediction from a
model that excluded its group.

### 7.3 Model members and quantiles

For each requested quantile, three LightGBM members use three fixed seeds.
The target is transformed with `log1p`, and model output returns to the
original scale with `expm1`.

For one row and quantile:

1. obtain three member predictions;
2. take their median;
3. clip the result at zero; and
4. apply a cumulative maximum across quantiles so q90 cannot fall below q50,
   q95 cannot fall below q90, and so on.

For the five-fold, five-quantile peak model, cross-fitting fits:

```text
5 folds x 5 quantiles x 3 members = 75 LightGBM models per feature view
```

These fold models produce development predictions for calibration. They are
not the final models served to test tasks.

## 8. Predicting current consumed bytes

CAMP cannot use the current measured consumption before the task runs. It
therefore trains a separate median model called C-hat.

### 8.1 Development C-hat

The target is `ebpf_total_consumed_bytes`. Cross-fitting uses Access Baseline
features and q50 only:

```text
5 folds x 1 quantile x 3 members = 15 fold models
```

Each development row receives an out-of-fold C-hat value.

### 8.2 Test C-hat

A final q50 consumption model is fitted on all development rows and predicts
every test row. `log1p_c_hat_bytes` is then added to the CAMP peak-RSS feature
set.

### 8.3 Consumed-to-memory diagnostic

The runner also models a scaled peak-to-consumed ratio and combines it with
C-hat to produce `consumed_derived_rss_mib`. The retained runner records this
as a diagnostic value. The primary CAMP q50 remains the direct CAMP peak-RSS
model; it is not replaced by this diagnostic.

## 9. Training peak-RSS models

The runner internally supports a static-only diagnostic, the Access Baseline,
and CAMP. The primary comparison uses the latter two.

For each feature view:

1. use `peak_memory_bytes / MiB` as the target;
2. cross-fit q50, q90, q95, q99, and q99.5 development predictions;
3. fit a final preprocessor on every development row;
4. fit three final model members per quantile;
5. predict all test rows;
6. take the member median; and
7. enforce non-crossing quantiles.

The current RQ2 runner uses the direct q50 prediction for each feature view.
Although point-stack settings remain in the configuration, the final runner
records that point stacking is disabled and does not blend a static-only q50
into CAMP.

## 10. Final per-process tail stage

After the initial run, `train_process_tail.py` replaces CAMP q90, q95, q99,
and q99.5 global tails with per-process tail ensembles.

For each workflow/process group:

- development predictions are cross-fitted within that process when at least
  two split groups exist;
- a single-parent-group case uses the global out-of-fold tail as fallback;
- final test tails use a model fitted on that process's development rows; and
- an unseen test process uses the global tail ensemble as fallback.

The q50 point estimate is retained. Calibration is then selected again using
the new tail predictions. “Per-process tail” describes model scope; the
current configuration still chooses one global allocation-policy pair for a
feature view rather than a separately tuned allocation policy for every
process.

## 11. What exists after training

Final fitting writes:

```text
models/seed_<seed>/signature_scaler.joblib
models/seed_<seed>/<feature-view>/preprocessor.joblib
models/seed_<seed>/<feature-view>/lightgbm_q<quantile>_member_<n>.joblib
models/seed_<seed>/<feature-view>/metadata.json
```

The experiment also writes:

```text
results/seed_<seed>/initial_causal_features.tsv
results/seed_<seed>/test_causal_features.tsv
results/seed_<seed>/c_hat_initial_oof.tsv
results/seed_<seed>/c_hat_test.tsv
results/seed_<seed>/<feature-view>/initial_oof_predictions.tsv
results/seed_<seed>/<feature-view>/calibration_candidates.csv
results/seed_<seed>/<feature-view>/selected_policy.json
results/seed_<seed>/<feature-view>/task_predictions.tsv
results/seed_<seed>/run_manifest.json
```

The `causal` text in two generated filenames is a retained file-interface name;
the files contain prior-only history features.

## 12. Detailed scenarios

### Scenario A: the first development row

Suppose the first displayed development row is a GATK task.

1. It is already one member of a completed historical population.
2. Its history snapshot excludes its own task ID.
3. It may use other GATK or broader development rows.
4. Its cross-fitted prediction comes from the four folds that exclude its
   split group.
5. Its actual peak and consumption remain targets for the fitting folds and
   later calibration, not inputs to its own prediction.

There is no one-row model fit.

### Scenario B: a later development row with an exact match

If at least two other rows share the exact I0 identity:

1. I0 meets its minimum support;
2. exact peak and consumption quantiles are materialized;
3. I0 becomes `history_scope`;
4. the support/confidence fields tell the model how strong that history is;
   and
5. cross-fitting still excludes the row's entire split group from its fitted
   model.

### Scenario C: the first test task

1. The final preprocessors and models are already frozen.
2. Its history uses development outcomes only.
3. C-hat is predicted from Access Baseline fields.
4. CAMP uses C-hat and prior consumption summaries to predict peak quantiles.
5. Calibration converts the quantiles into a first request.
6. Only after that request is frozen is the recorded test outcome revealed.
7. The outcome becomes available to the next test task's history.

No model weights are updated.

### Scenario D: the 100th test task

The 100th task uses the same frozen model weights as the first test task, but
its prior-history and residual indices may contain the outcomes of the first
99 completed test tasks. Its prediction can therefore differ because its
features and current history differ, not because LightGBM was retrained.

### Scenario E: a missing requested-thread count

1. The field arrives as missing.
2. The final preprocessor substitutes the development median.
3. Its missing indicator is set to 1.
4. The model may learn a separate effect for “missing” versus an observed
   median-valued row.

### Scenario F: a process unseen during training

1. One-hot encoding safely ignores the unseen categorical level.
2. Any available static numeric fields remain active.
3. Prior history may fall back to workflow or system scope.
4. The per-process tail stage uses the frozen global tail ensemble because no
   process-specific model exists.

### Scenario G: a first-ever live task with no seed history

This is not the packaged RQ2 starting condition; RQ2 begins with 28,490
development outcomes. In a truly empty live deployment:

1. every history scope is unsupported;
2. `history_scope` becomes `cold_start`;
3. missing numeric history fields use the trained preprocessor's medians and
   missing indicators;
4. static/access fields carry the prediction; and
5. allocation needs an already fitted global model and calibration fallback.

If no fitted model or residual seed exists either, the current artifact cannot
produce a learned safe request. A deployment must bootstrap with a static
default or historical seed before enabling learned allocation.

### Scenario H: two live tasks submitted simultaneously

Neither task may use the other's unfinished outcome. Both should read the last
committed history snapshot, receive predictions, and freeze allocations. Each
outcome is added only when that task completes.

The offline experiment serializes test tasks by `experiment_position`; it does
not implement transaction handling for concurrent live submissions.

### Scenario I: a completed audited live task

After completion, a live integration would append:

- actual peak RSS;
- successful explicit-read bytes;
- attributable mmap-fault bytes;
- total consumed bytes;
- runtime and completion status; and
- task/input/system identities.

Later tasks may use the new prior history. The current model does not retrain
after every append.

### Scenario J: scheduled retraining

A safe retraining cycle would:

1. take a consistent snapshot of completed outcomes;
2. freeze a new development/test or time-based evaluation boundary;
3. rebuild history features;
4. cross-fit development predictions;
5. refit C-hat and peak quantiles;
6. recalibrate allocation;
7. validate against later held-out tasks; and
8. atomically promote the new scaler, preprocessors, models, and policy.

The artifact provides the batch-training path but does not prescribe or
implement an automatic live retraining schedule.

## 13. Illustrative end-to-end row

The following numbers are explanatory, not retained experimental output.

Assume a future task arrives with:

```text
workflow = gatk
process = HaplotypeCaller
static input = 10 GiB
requested_threads = missing
supported I2 history = 48 neighbors
prior peak p99 = 4.6 GiB
prior consumed p50 = 2.1 GiB
```

The live-equivalent path would be:

1. transform 10 GiB with `log1p`;
2. impute requested threads and set its missing indicator;
3. encode workflow/process/version/system categories;
4. predict current consumption, for example 2.4 GiB;
5. add `log1p(2.4 GiB)` and prior consumption summaries to CAMP features;
6. produce ordered peak quantiles, for example q50 2.2 GiB, q99 4.8 GiB,
   and q99.5 5.1 GiB;
7. pass those values to allocation calibration; and
8. freeze the resulting first request before observing current peak RSS.

If the task later peaks at 4.9 GiB, that 4.9 GiB was never an input to steps
1–8. It becomes an outcome for evaluation and later history.

## 14. Offline experiment versus live deployment

| Concern | Packaged experiment | Live deployment equivalent |
|---|---|---|
| Model fitting | Batch fit from frozen development rows | Periodic fit from a completed-outcome snapshot |
| Prediction | All test predictions generated with frozen models | One task predicted at submission time |
| History growth | Serialized by experiment position | Transactional updates after actual completion |
| Current outcome | Present in frozen table but hidden until scoring | Does not exist until task completes |
| Model update | Never during test | Optional later batch retraining, not per task |
| Consumption availability | Frozen cohort contains eBPF targets | Available only for tasks actually audited |
| Concurrency | Deterministic serial order | Requires snapshot and transaction rules outside this artifact |

The live column describes the faithful operational mapping. It is not a claim
that the artifact currently installs a scheduler daemon, feature store, or
automatic model registry.

## 15. Source map

| Question | Source |
|---|---|
| How are splits loaded and ordered? | `SCRIPTS/RQ2/common.py` |
| How is prior history built? | `SCRIPTS/RQ2/history.py` |
| Which fields enter each model? | `SCRIPTS/RQ2/modeling.py` |
| How are folds and ensembles built? | `SCRIPTS/RQ2/modeling.py` |
| How is C-hat trained? | `SCRIPTS/RQ2/run_offline_experiment.py` |
| How are peak models trained? | `SCRIPTS/RQ2/run_offline_experiment.py` |
| How are per-process tails fitted? | `SCRIPTS/RQ2/train_process_tail.py` |
| Which parameters are frozen? | `SCRIPTS/RQ2/config/experiment.json` |
