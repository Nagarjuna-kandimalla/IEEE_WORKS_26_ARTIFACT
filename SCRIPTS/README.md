# Scripts

## RQ1 postprocessing

For the Slurm route, start this section only after the six Exp0/Exp1/Exp2
workflow triplets and the Exp1/Exp2 audit summaries have been created under a
single `RUN_ROOT`, as documented in [`WORKFLOWS/README.md`](../WORKFLOWS/README.md)
and [`AUDIT/README.md`](../AUDIT/README.md). Run RQ1 Steps 1–4 below in order.

For the packaged-CSV route, skip Steps 1–3 and begin at **RQ1 Step 4: generate
and verify the figure**.

RQ1 postprocessing is under `RQ1/postprocessing`:

```text
normalize_nextflow_baseline_trace.py  Nextflow trace to task metrics
enrich_training_task_inputs.py        add staged input-byte fields
build_workflows_run_results.py         aggregate the selected completed runs
build_ml_ready_data.py                 optional downstream cohort construction
build_rq1_figure_inputs.py             add GATK and create both figure CSVs
```

### RQ1 Step 1: normalize and enrich

The first normalization command needs the run's Nextflow trace and matching
static task manifest. The exact manifests used for all five workflows handled
by the common aggregator are packaged below `WORKFLOWS/<workflow>/data`.
For Exp1 and Exp2, first create the audit summaries as described in
`AUDIT/README.md`, then normalize and enrich every completed run with:

```bash
artifact_root="$PWD"
RUN_ROOT="${RUN_ROOT:-$artifact_root/RQ1_RUNS}"

declare -A dataset_ids=(
  [minimap2]=minimap2_synthetic_1500
  [sarek]=NA12878_WGS_30x_GRCh38_1495
  [seqinspector]=ENA_metagenomic_WGS_3000_standard8_seqinspector
  [taxprofiler]=ENA_metagenomic_WGS_5998_standard8
  [viralmetagenome]=ENA_metagenomic_WGS_3000_standard8_viralmetagenome
)
declare -A static_manifests=(
  [minimap2]=WORKFLOWS/minimap2/data/static_task_manifest_6003.tsv
  [sarek]=WORKFLOWS/sarek/data/static_task_manifest_1495.tsv
  [seqinspector]=WORKFLOWS/seqinspector/data/static_task_manifest_3000.tsv
  [taxprofiler]=WORKFLOWS/taxprofiler/data/static_task_manifest_5998.tsv
  [viralmetagenome]=WORKFLOWS/viralmetagenome/data/static_task_manifest_3000.tsv
)

for workflow in minimap2 sarek seqinspector taxprofiler viralmetagenome; do
  for mode in exp0 exp1 exp2; do
    run_dir="$RUN_ROOT/$workflow/$mode"
    audit_arguments=()
    case "$mode" in
      exp0) instrumentation=none ;;
      exp1)
        instrumentation=strace
        audit_arguments=(
          --strace-summary "$run_dir/results/metrics/strace_task_summary.tsv"
        )
        ;;
      exp2)
        instrumentation=ebpf
        audit_arguments=(
          --ebpf-merged "$run_dir/results/metrics/ebpf_task_summary.tsv"
        )
        ;;
    esac

    test -s "$run_dir/nextflow_trace.tsv"
    test -s "$artifact_root/${static_manifests[$workflow]}"
    if [ "$mode" = exp1 ]; then
      test -s "${audit_arguments[1]}"
    elif [ "$mode" = exp2 ]; then
      test -s "${audit_arguments[1]}"
    fi

    python3 SCRIPTS/RQ1/postprocessing/normalize_nextflow_baseline_trace.py \
      --workflow "$workflow" \
      --trace "$run_dir/nextflow_trace.tsv" \
      --out "$run_dir/training_task_metrics.tsv" \
      --dataset-id "${dataset_ids[$workflow]}" \
      --static-manifest "$artifact_root/${static_manifests[$workflow]}" \
      --instrumentation "$instrumentation" \
      "${audit_arguments[@]}"

    python3 SCRIPTS/RQ1/postprocessing/enrich_training_task_inputs.py \
      --input "$run_dir/training_task_metrics.tsv" \
      --output "$run_dir/training_task_metrics_with_inputs.tsv" \
      --workflow-root "$artifact_root/WORKFLOWS/$workflow"
  done
done
```

The normalizer keeps only `COMPLETED` and `CACHED` trace rows. For Exp1, use
the STRACE parser's `--successful-only` option so retry attempts are also
removed before normalization. The static manifests cover the repeated
data-parallel tasks used to identify the fixed cohort; support/reporting tasks
are retained from the Nextflow trace and obtain their staged-input fields from
the enrichment step.

### RQ1 Step 2: aggregate five workflows

`build_workflows_run_results.py` is the aggregation program for Minimap2,
Sarek, Seqinspector, Taxprofiler, and Viralmetagenome. The canonical
`RQ1_RUNS/<workflow>/exp0|exp1|exp2` layout is created by
`WORKFLOWS/README.md`:

```bash
aggregate_root="${RQ1_AGGREGATE_ROOT:-${TMPDIR:-/tmp}/rq1-aggregate}"
python3 SCRIPTS/RQ1/postprocessing/build_workflows_run_results.py \
  --workflows-root "$RUN_ROOT" \
  --output-root "$aggregate_root" \
  --skip-ml-ready
test -s "$aggregate_root/paper_tables/strace_vs_ebpf_workflow_summary.csv"
```

The aggregator recreates its output directory, so `aggregate_root` must not
hold other files that need to be preserved.

`--skip-ml-ready` omits the separate downstream ML-cohort construction; it
does not omit any timing or consumed-data field used by the RQ1 figure.

### RQ1 Step 3: add GATK and publish the figure-input CSVs

Add the completed GATK triplet to the five-workflow aggregation and create both
figure inputs with:

```bash
python3 SCRIPTS/RQ1/postprocessing/build_rq1_figure_inputs.py \
  --workflow-summary "$aggregate_root/paper_tables/strace_vs_ebpf_workflow_summary.csv" \
  --gatk-runs-root "$RUN_ROOT"

test -s RESULTS/RQ1/csv/strace_vs_ebpf_workflow_summary.csv
test -s RESULTS/RQ1/csv/rq1_workflow_overhead.csv
```

`--gatk-runs-root` accepts the `gatk/exp0`, `gatk/exp1`, and `gatk/exp2`
layout created by `WORKFLOWS/README.md`, or the original named GATK run
directories. Each run must contain `nextflow.log` and
`results/metrics/haplotypecaller_task_metrics.tsv`. The script calculates the
GATK signal from the 500 task rows, calculates its direct timing from the
complete Nextflow lifecycle log, combines it with the five-workflow builder
output, and writes the two result tables. For a fresh non-resumed Sarek
triplet it uses the three direct builder timings. The packaged historical
summary lacks a valid STRACE makespan because that run crossed resumed
sessions, so only that retained case uses
`sarek_reconstructed_active_runtime.csv`.

```text
RESULTS/RQ1/csv/strace_vs_ebpf_workflow_summary.csv
RESULTS/RQ1/csv/rq1_workflow_overhead.csv
```

### Optional: verify the packaged post-run transformation

This check is independent of the Slurm route. It recreates the two figure
inputs from the retained summary data without rerunning workflows:

```bash
test_dir="$(mktemp -d)"
python3 SCRIPTS/RQ1/postprocessing/build_rq1_figure_inputs.py \
  --signal-output "$test_dir/strace_vs_ebpf_workflow_summary.csv" \
  --overhead-output "$test_dir/rq1_workflow_overhead.csv"
cmp "$test_dir/strace_vs_ebpf_workflow_summary.csv" \
  RESULTS/RQ1/csv/strace_vs_ebpf_workflow_summary.csv
cmp "$test_dir/rq1_workflow_overhead.csv" \
  RESULTS/RQ1/csv/rq1_workflow_overhead.csv
find "$test_dir" -depth -delete
```

### RQ1 Step 4: generate and verify the figure

This is the entry point for the packaged-CSV route and the final step of the
Slurm route:

```bash
rq1_environment="${TMPDIR:-/tmp}/camp-rq1-environment"
python3 -m venv "$rq1_environment"
"$rq1_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ1/figures/requirements.txt
"$rq1_environment/bin/python" \
  SCRIPTS/RQ1/figures/make_rq1_signal_overhead.py

test -s RESULTS/RQ1/figures/fig_rq1_signal_overhead.png
test -s RESULTS/RQ1/figures/fig_rq1_signal_overhead.pdf
echo 'ddbb37e2d3a47a503fdc7435be89c98649113875a833768f9dc10938db2cab00  RESULTS/RQ1/figures/fig_rq1_signal_overhead.png' |
  sha256sum -c -
```

`make_rq1_signal_overhead.py` and `requirements.txt` are the RQ1 figure source
and its pinned Python dependencies. The script reads `RESULTS/RQ1/csv`
directly and writes the figure into `RESULTS/RQ1/figures`.

## RQ2 offline experiment

There are two entry points:

- **Packaged-CSV route:** skip RQ2 Steps 1–4 and run **RQ2 Step 5**.
- **Full offline route:** run RQ2 Steps 1–5 in order. Slurm is optional; if it
  is used, each loop body in Step 3 is one independent model job.

The RQ2 code under `RQ2/` has the following roles:

```text
materialize_offline_data.py  convert the compressed cohort to experiment TSV
common.py                    configuration, paths, hashes, and row loading
history.py                   causal A+P and A+P+C history features
modeling.py                  LightGBM training and quantile prediction
calibration.py               policy selection and sequential calibration
run_offline_experiment.py    complete A+P and A+P+C experiment entry point
train_process_tail.py        process-specific tail-model fitting
summarize_rq2.py             paired per-run metrics and checks
aggregate_variance.py        five-seed/five-split result aggregation
aggregate_route_contracts.py global-tail/process-tail comparison
config/experiment.json       frozen seed, feature, model, and policy settings
data/split_manifests/        six workflow split assignments for seed 1996
data/variance_manifests/     exact 30/70 through 90/10 split assignments
requirements.txt             pinned full experiment dependencies
```

### RQ2 Step 1: install dependencies and materialize the cohort

Run all commands from the artifact root. Install the dependencies and
materialize the 33,516-row compressed cohort:

```bash
python3 -m venv .venv-rq2
.venv-rq2/bin/python -m pip install --upgrade pip
.venv-rq2/bin/python -m pip install -r SCRIPTS/RQ2/requirements.txt

.venv-rq2/bin/python SCRIPTS/RQ2/materialize_offline_data.py \
  --source DATA/RQ2/camp_ml_cohort_33516.csv.gz \
  --output DATA/RQ2/generated/canonical_matched_tasks.tsv
```

### RQ2 Step 2: reproduce and publish the seed-1996 allocation result

The packaged manifests divide the cohort into 28,490 development rows and
5,026 chronological test rows across six workflows. In a clean checkout, run
the seed-1996 A+P and A+P+C experiment, apply the process-tail phase used by
the retained result, summarize it, and publish its two task tables:

```bash
.venv-rq2/bin/python SCRIPTS/RQ2/run_offline_experiment.py \
  --seed 1996 \
  --n-jobs 8

result_root="SCRIPTS/RQ2/results/seed_1996"
model_root="SCRIPTS/RQ2/models/seed_1996"
test ! -e "$result_root/a_plus_p_plus_c_global_base"
cp -a "$result_root/a_plus_p_plus_c" \
  "$result_root/a_plus_p_plus_c_global_base"

.venv-rq2/bin/python SCRIPTS/RQ2/train_process_tail.py \
  --seed 1996 --variant A+P+C --n-jobs 8 \
  --result-root "$result_root" --model-root "$model_root"
.venv-rq2/bin/python SCRIPTS/RQ2/summarize_rq2.py --seed 1996

gzip -n -c "$result_root/a_plus_p/task_predictions.tsv" \
  > RESULTS/RQ2/csv/a_plus_p_task_predictions.tsv.gz
gzip -n -c "$result_root/a_plus_p_plus_c/task_predictions.tsv" \
  > RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz

test "$(gzip -dc RESULTS/RQ2/csv/a_plus_p_task_predictions.tsv.gz | wc -l)" \
  -eq 5027
test "$(gzip -dc RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz | wc -l)" \
  -eq 5027
```

The experiment writes generated models to `SCRIPTS/RQ2/models/seed_1996` and
generated tables to `SCRIPTS/RQ2/results/seed_1996`. It trains only A+P and
A+P+C. The two compressed copies under `RESULTS/RQ2/csv` are the inputs to the
87-versus-31 allocation figure in Step 5.

### RQ2 Step 3: run the repeated seed/split matrix

The seed-variance and chronological-split results require the complete 25-run
matrix. The exact split manifests are packaged; no split-generation program
or original provenance manifest is needed. Run the matrix sequentially with:

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
      --output-root "$result_root" \
      --model-root "$model_root"

    cp -a "$result_root/a_plus_p_plus_c" \
      "$result_root/a_plus_p_plus_c_global_base"

    .venv-rq2/bin/python SCRIPTS/RQ2/train_process_tail.py \
      --seed "$seed" --variant A+P+C --n-jobs 32 \
      --result-root "$result_root" --model-root "$model_root"

    test_fraction=$(awk -v value="${train_fraction[$split]}" 'BEGIN { print 1-value }')
    .venv-rq2/bin/python SCRIPTS/RQ2/summarize_rq2.py \
      --seed "$seed" --result-root "$result_root" \
      --train-fraction "${train_fraction[$split]}" \
      --test-fraction "$test_fraction" \
      --expected-train-rows "${train_rows[$split]}" \
      --expected-test-rows "${test_rows[$split]}"
  done
done
```

Each full run was provisioned with 32 CPUs and 220 GiB. On Slurm, submit the
25 loop bodies as an array and limit concurrency to the available memory.

### RQ2 Step 4: aggregate the repeated runs

After all 25 summaries exist, recreate the aggregate CSVs consumed by the
variance and split-testing figures:

```bash
.venv-rq2/bin/python SCRIPTS/RQ2/aggregate_variance.py
.venv-rq2/bin/python SCRIPTS/RQ2/aggregate_route_contracts.py
```

Both aggregators write directly to `RESULTS/RQ2/csv`. The figure commands
below then recreate the three retained RQ2 figures from those CSVs.

### RQ2 Step 5: generate the figures

The figure scripts under `RQ2/figures/` read only packaged files in
`RESULTS/RQ2/csv`:

```text
generate_seed_variance_figure.py   five-seed CAMP variance
generate_split_testing_figure.py   chronological split comparison
generate_allocation_oom_figure.py  A+P versus A+P+C, including 87 versus 31
requirements.txt                   figure-only Python dependencies
```

This step is the packaged-CSV entry point and the final step of the full
offline route. Run it with either the full RQ2 environment above or the
smaller figure-only environment:

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

test -s RESULTS/RQ2/figures/fig_camp_only_seed_variance_underallocations.png
test -s RESULTS/RQ2/figures/fig_offline_seed_variance_underallocations.png
test -s RESULTS/RQ2/figures/fig_rq2_allocation_tradeoff.png
echo '31dfe2052c4ba2a8ba0bd1828db0173806e7566b32ed9193473e8b1c2e1dc3cf  RESULTS/RQ2/figures/fig_camp_only_seed_variance_underallocations.png' |
  sha256sum -c -
echo '41f7e941b33ea68b1c4decf33bbaf8e8cebc0f5cd7b1750688e8feb29a2f9bb6  RESULTS/RQ2/figures/fig_offline_seed_variance_underallocations.png' |
  sha256sum -c -
echo '4ddb9e460f9f321ce73435435fe831e59447a123802bfc6d44eb1317b934faf7  RESULTS/RQ2/figures/fig_rq2_allocation_tradeoff.png' |
  sha256sum -c -
```

These commands write into `RESULTS/RQ2/figures`. Use a disposable copy when
scenario-testing if the packaged figures must remain untouched. In the
isolated scenario test, all three generated PNG files matched the packaged
files byte for byte, and all temporary outputs were removed afterward.

## RQ3 Sizey versus CAMP

The RQ3 scripts are organized under `RQ3/`:

```text
scripts/       final CAMP replay, validation, matching, and analysis scripts
harness/       Sizey and CAMP execution/normalization harness scripts
figures/       Sizey-versus-CAMP paper figure script and dependencies
README.md      Sizey download and end-to-end experiment commands
```

Follow `RQ3/README.md` to download the pinned Sizey revision and rerun the
matched Sizey-versus-CAMP experiment. Generate the figure from the packaged
summary table with:

```bash
python3 SCRIPTS/RQ3/figures/generate_sizey_vs_camp.py
```

## RQ4 selective auditing

RQ4 is under `RQ4/`:

```text
prepare_inputs.py    expand the packaged frozen gate inputs
scripts/             gate fitting and equal-budget evaluation
figures/             RQ4 paper-figure generator
config/              frozen model and allocation settings
models/              packaged signature scaler
README.md            complete input-to-figure commands
```

The RQ4 figure reports A+P risk-target recall for the CAMP three-gate selector
and compares it with uniform random and process-stratified random selection at
5%, 10%, 20%, 30%, and 50% task budgets. The complete experiment also evaluates
the 100% budget and A+P+C gate inputs. Follow
`RQ4/README.md` for the full evaluation, result-placement, and figure command
sequence. After the evaluation table has been placed under `RESULTS/RQ4`, run:

```bash
python3 SCRIPTS/RQ4/figures/generate_rq4_selective_audit.py
```

`RQ4/README.md` gives the complete frozen-input-to-figure command. The retained
result consists of the repetition table and final risk-target PNG/PDF.

## RQ5 live Bowtie2 online feedback

RQ5 is under `RQ5/`:

```text
prepare_workspace.py       create an external fresh experiment tree
build_initial_state.sh     rebuild models, policies, and non-Bowtie history
experiment_template/       finalized APC-only learner and live integration
figures/                   Bowtie2 cold/warm figure generator
README.md                  packaged-data and full Slurm command sequences
```

The full route uses the included Bowtie2 workflow and the RQ1 eBPF tracer,
then executes a 6,000-task cold run followed by a 6,000-task warm run. Follow
`RQ5/README.md`; do not launch the workflow directly because the ordered
runner also starts and monitors the learner and prediction service.
