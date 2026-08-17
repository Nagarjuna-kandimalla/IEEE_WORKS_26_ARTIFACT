# Experiment RQ2: causal allocation and variance

RQ2 compares Access (`A+P`) with CAMP (`A+P+C`) on aligned chronological
holdout tasks, then repeats the experiment over five model seeds and five
development/test splits.

## Outputs

```text
RESULTS/RQ2/csv/a_plus_p_task_predictions.tsv.gz
RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz
RESULTS/RQ2/csv/repetition_allocator_metrics.csv
RESULTS/RQ2/csv/camp_only_seed_variance_summary.csv
RESULTS/RQ2/csv/route_contract_repetition_effects.csv
RESULTS/RQ2/figures/fig_rq2_allocation_tradeoff.png
RESULTS/RQ2/figures/fig_camp_only_seed_variance_underallocations.{png,pdf}
RESULTS/RQ2/figures/fig_offline_seed_variance_underallocations.{png,pdf}
```

## Route A: packaged tables to figures

```bash
python3 -m venv .venv-rq2-figures
.venv-rq2-figures/bin/python -m pip install --upgrade pip
.venv-rq2-figures/bin/python -m pip install \
  -r SCRIPTS/RQ2/figures/requirements.txt

.venv-rq2-figures/bin/python \
  SCRIPTS/RQ2/figures/generate_seed_variance_figure.py
.venv-rq2-figures/bin/python \
  SCRIPTS/RQ2/figures/generate_split_testing_figure.py
.venv-rq2-figures/bin/python \
  SCRIPTS/RQ2/figures/generate_allocation_oom_figure.py
```

Verify the retained PNGs with [`../RESULTS/README.md`](../RESULTS/README.md).

## Route B: cohort to figures

### 1. Install and materialize

The completed runs used Python 3.9. A direct primary run needs at least eight
logical CPUs; each full variance run was provisioned with 32 CPUs and 220 GiB
RAM. Slurm is optional.

```bash
python3 -m venv .venv-rq2
.venv-rq2/bin/python -m pip install --upgrade pip
.venv-rq2/bin/python -m pip install -r SCRIPTS/RQ2/requirements.txt

.venv-rq2/bin/python SCRIPTS/RQ2/materialize_offline_data.py \
  --source DATA/RQ2/camp_ml_cohort_33516.csv.gz \
  --output DATA/RQ2/generated/canonical_matched_tasks.tsv
```

The source has 33,516 tasks. The primary manifests assign 28,490 to
development and 5,026 to chronological testing.

### 2. Primary seed-1996 comparison

Run from a clean generated-output state. The programs reject conflicting
result paths rather than silently mix runs.

```bash
.venv-rq2/bin/python SCRIPTS/RQ2/run_offline_experiment.py \
  --seed 1996 --n-jobs 8

result_root="SCRIPTS/RQ2/results/seed_1996"
model_root="SCRIPTS/RQ2/models/seed_1996"
test ! -e "$result_root/a_plus_p_plus_c_global_base"
cp -a "$result_root/a_plus_p_plus_c" \
  "$result_root/a_plus_p_plus_c_global_base"

.venv-rq2/bin/python SCRIPTS/RQ2/train_process_tail.py \
  --seed 1996 --variant A+P+C --n-jobs 8 \
  --result-root "$result_root" --model-root "$model_root"
.venv-rq2/bin/python SCRIPTS/RQ2/summarize_rq2.py --seed 1996
```

The copy preserves the global-tail route before the final process-tail stage
rewrites A+P+C. Publish the aligned task tables:

```bash
gzip -n -c "$result_root/a_plus_p/task_predictions.tsv" \
  > RESULTS/RQ2/csv/a_plus_p_task_predictions.tsv.gz
gzip -n -c "$result_root/a_plus_p_plus_c/task_predictions.tsv" \
  > RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz

test "$(gzip -dc RESULTS/RQ2/csv/a_plus_p_task_predictions.tsv.gz | wc -l)" \
  -eq 5027
test "$(gzip -dc RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz | wc -l)" \
  -eq 5027
```

The extra row is the header. The retained seed-1996 result has 87 A+P and 31
A+P+C first-request underallocations and zero unresolved tasks after retry.

### 3. Five seeds over five splits

The sequential form below defines 25 independent model runs. Each loop body
can instead be one Slurm array task.

```bash
declare -A train_rows=(
  [30_70]=10057 [50_50]=16758 [70_30]=23460 [85_15]=28490 [90_10]=30166
)
declare -A test_rows=(
  [30_70]=23459 [50_50]=16758 [70_30]=10056 [85_15]=5026 [90_10]=3350
)
declare -A train_fraction=(
  [30_70]=0.30 [50_50]=0.50 [70_30]=0.70 [85_15]=0.85 [90_10]=0.90
)

for split in 30_70 50_50 70_30 85_15 90_10; do
  for seed in 1996 1997 1998 1999 2000; do
    result_root="SCRIPTS/RQ2/results/split_${split}/seed_${seed}"
    model_root="SCRIPTS/RQ2/models/split_${split}/seed_${seed}"

    .venv-rq2/bin/python SCRIPTS/RQ2/run_offline_experiment.py \
      --seed "$seed" --n-jobs 32 \
      --split-manifest-root "SCRIPTS/RQ2/data/variance_manifests/$split" \
      --split-seed 1996 \
      --expected-train-rows "${train_rows[$split]}" \
      --expected-test-rows "${test_rows[$split]}" \
      --output-root "$result_root" --model-root "$model_root"

    cp -a "$result_root/a_plus_p_plus_c" \
      "$result_root/a_plus_p_plus_c_global_base"

    .venv-rq2/bin/python SCRIPTS/RQ2/train_process_tail.py \
      --seed "$seed" --variant A+P+C --n-jobs 32 \
      --result-root "$result_root" --model-root "$model_root"

    test_fraction=$(awk -v value="${train_fraction[$split]}" \
      'BEGIN { print 1-value }')
    .venv-rq2/bin/python SCRIPTS/RQ2/summarize_rq2.py \
      --seed "$seed" --result-root "$result_root" \
      --train-fraction "${train_fraction[$split]}" \
      --test-fraction "$test_fraction" \
      --expected-train-rows "${train_rows[$split]}" \
      --expected-test-rows "${test_rows[$split]}"
  done
done
```

Every split must receive the same process-tail treatment. Do not mix final
process-tail and preserved global-base results across splits.

### 4. Aggregate only after all 25 runs complete

```bash
.venv-rq2/bin/python SCRIPTS/RQ2/aggregate_variance.py
.venv-rq2/bin/python SCRIPTS/RQ2/aggregate_route_contracts.py
```

The aggregators write the three committed aggregate CSVs directly into
`RESULTS/RQ2/csv`.

### 5. Plot and verify

Run Route A. Confirm all three PNGs and PDFs exist, then use the checksum
commands in `RESULTS/README.md` for the packaged files.

## What each program contributes

| Program | Responsibility |
|---|---|
| `materialize_offline_data.py` | Expand and validate the compressed cohort |
| `history.py` | Create leakage-safe causal peak and consumed history |
| `modeling.py` | Fit grouped-cross-fitted and final LightGBM quantile ensembles |
| `calibration.py` | Select and sequentially apply allocation policies |
| `run_offline_experiment.py` | Build features, `C-hat`, A+P, and A+P+C results |
| `train_process_tail.py` | Replace global tail estimates with per-process tail ensembles |
| `summarize_rq2.py` | Validate paired tasks and compute run metrics |
| `aggregate_variance.py` | Combine seed/split metrics and confidence intervals |
| `aggregate_route_contracts.py` | Keep route definitions comparable and explicit |

## Variance interpretation

Mean CAMP underallocations per 1,000 are approximately 32.57, 7.35, 32.70,
12.14, and 23.70 at the 30/70 through 90/10 splits. The increase at 90/10 is
not a raw-count artifact. The last 10% is a different temporal holdout, each
split refits and reselects its policy, and all five 90/10 seeds choose
`model_q995` plus a 0.95 residual correction. Median point error improves, but
the upper-tail request is less conservative and holdout coverage falls. The
increase concentrates mainly in Seqinspector, Taxprofiler, and
Viralmetagenome. More development rows therefore do not imply monotonic
improvement under temporal shift and discrete policy selection.

| Split | Test tasks per seed | Access mean underallocations/1,000 | CAMP mean underallocations/1,000 | CAMP sample SD/1,000 |
|---|---:|---:|---:|---:|
| 30/70 | 23,459 | 23.94 | 32.57 | 7.01 |
| 50/50 | 16,758 | 17.35 | 7.35 | 1.48 |
| 70/30 | 10,056 | 10.30 | 32.70 | 1.93 |
| 85/15 | 5,026 | 24.91 | 12.14 | 8.18 |
| 90/10 | 3,350 | 7.70 | 23.70 | 4.10 |

The Access means are calculated from the paired repetition table; the CAMP
means and sample SDs are retained directly in
`camp_only_seed_variance_summary.csv`. Variation is between model/policy seeds
on fixed split populations, not between newly executed workflows.
