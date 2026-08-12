# RQ3: Sizey versus CAMP

Run these commands from a clean checkout of the artifact. Python 3.9 was used
for the completed experiment.

## Choose the RQ3 route

- **Packaged-CSV route:** the retained `RESULTS/RQ3/csv/global_metrics.csv` is
  already the final comparison table. Skip to **RQ3 Step 6**.
- **Full comparison route:** run RQ3 Steps 1–6 in order. Steps 3 and 5 may be
  submitted to Slurm, but their produced paths and the staging order remain
  the same.

## System configuration

The completed final CAMP comparison used one Slurm node with one task, 32 CPUs,
200 GiB RAM, and a three-hour time limit. The job ran on the `main` partition
with the `standard` constraint. It used all 32 CPUs for model fitting and set
the following environment controls:

```bash
export PYTHONHASHSEED=0
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
```

The six earlier Sizey executions used one 32-CPU, 220-GiB Slurm allocation per
workflow. Reserve one Linux x86-64 host with 32 logical CPUs and 220 GiB RAM to
cover the complete procedure in this README. The commands can be run directly
without Slurm; Nextflow, Apptainer, STRACE, eBPF, BCC, and kernel headers are
not required.

## RQ3 Step 1: set up Python and download Sizey

```bash
artifact_root="$PWD"
rq3_venv="${TMPDIR:-/tmp}/ieee-works-rq3-venv"

python3.9 -m venv "$rq3_venv"
source "$rq3_venv/bin/activate"
python -m pip install --upgrade pip

git clone https://github.com/dos-group/sizey.git \
  "$artifact_root/SCRIPTS/RQ3/source_sizey"
git -C "$artifact_root/SCRIPTS/RQ3/source_sizey" checkout --detach \
  e0dd09f09cd2bf5905c7143792e3500c948eb386
test "$(git -C "$artifact_root/SCRIPTS/RQ3/source_sizey" rev-parse HEAD)" = \
  e0dd09f09cd2bf5905c7143792e3500c948eb386

python -m pip install \
  -r "$artifact_root/SCRIPTS/RQ3/source_sizey/requirements.txt"
python -m pip install \
  joblib==1.5.3 \
  lightgbm==4.6.0 \
  matplotlib==3.9.4 \
  numpy==1.26.4 \
  pandas==2.3.3 \
  scikit-learn==1.6.1 \
  scipy==1.13.1 \
  statsmodels==0.14.5 \
  patsy==1.0.1
```

`SCRIPTS/RQ3/source_sizey/` is ignored by Git and is created only when the
experiment is run.

## RQ3 Step 2: prepare the matched cohort

Materialize the packaged compressed cohort, then create the Sizey inputs and
the common train/test assignments:

```bash
cd "$artifact_root"
python SCRIPTS/RQ2/materialize_offline_data.py \
  --source DATA/RQ2/camp_ml_cohort_33516.csv.gz \
  --output data/generated/canonical_matched_tasks.tsv

cd "$artifact_root/SCRIPTS/RQ3/harness"
python scripts/prepare_data.py --cohorts primary
```

The primary cohort contains 30,478 tasks from `gatk`, `minimap2`, `sarek`,
`seqinspector`, `taxprofiler`, and `viralmetagenome`. Seed 1996 assigns 9,141
tasks to initial training and 21,337 tasks to replay testing.

## RQ3 Step 3: run and normalize Sizey

```bash
cd "$artifact_root/SCRIPTS/RQ3/harness"
for workflow in \
  gatk minimap2 sarek seqinspector taxprofiler viralmetagenome
do
  python scripts/run_sizey.py \
    --workflow "$workflow" --seed 1996 --cohort primary
  python scripts/normalize_sizey.py \
    --workflow "$workflow" --seed 1996 --cohort primary
done
```

Sizey writes each run below
`SCRIPTS/RQ3/harness/results/raw_sizey/primary/seed_1996/`.

## RQ3 Step 4: stage the shared inputs for the CAMP comparison

```bash
comparison="$artifact_root/SCRIPTS/RQ3"
harness="$comparison/harness"

mkdir -p \
  "$comparison/inputs" \
  "$comparison/data/split_manifests" \
  "$comparison/baseline/sizey" \
  "$comparison/manifests"

cp "$harness/data/generated/primary/canonical_tasks.tsv" \
  "$comparison/inputs/canonical_tasks.tsv"
cp "$harness/data/split_manifests/primary/"*_1996.tsv \
  "$comparison/data/split_manifests/"

for workflow in \
  gatk minimap2 sarek seqinspector taxprofiler viralmetagenome
do
  source_run="$harness/results/raw_sizey/primary/seed_1996/$workflow"
  target_run="$comparison/baseline/sizey/$workflow"
  mkdir -p "$target_run"
  cp "$source_run/normalized_task_predictions.tsv" "$target_run/"
  cp "$source_run/run_manifest.json" "$target_run/"
done
```

## RQ3 Step 5: run the CAMP replay and summarize RQ3

The replay creates models and intermediate task-level tables below
`SCRIPTS/RQ3/models/` and `SCRIPTS/RQ3/results/`. Run it in a clean checkout;
the scripts stop if an earlier seed-1996 run is already present.

```bash
cd "$artifact_root/SCRIPTS/RQ3"
python scripts/preflight.py
python scripts/run_design3.py --seed 1996 --n-jobs 32

for variant in A A+P A+P+C
do
  python scripts/train_process_tail.py \
    --seed 1996 --variant "$variant" --n-jobs 32
done

python scripts/summarize_comparison.py
```

The task-level comparison and summary CSV files are written to
`SCRIPTS/RQ3/results/comparison/`.

## RQ3 Step 6: publish the comparison and generate the figure

For the full route only, first copy the generated comparison table:

```bash
artifact_root="$(pwd -P)"
test -s SCRIPTS/RQ3/results/comparison/global_metrics.csv
cp SCRIPTS/RQ3/results/comparison/global_metrics.csv \
  RESULTS/RQ3/csv/global_metrics.csv
```

For either route, start from the artifact root and generate the figure:

```bash
artifact_root="$(pwd -P)"

rq3_figure_environment="${TMPDIR:-/tmp}/ieee-works-rq3-figures"
python3.9 -m venv "$rq3_figure_environment"
"$rq3_figure_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ3/figures/requirements.txt
"$rq3_figure_environment/bin/python" \
  SCRIPTS/RQ3/figures/generate_sizey_vs_camp.py

test -s RESULTS/RQ3/figures/fig_sizey_vs_camp.png
test -s RESULTS/RQ3/figures/fig_sizey_vs_camp.pdf
echo 'ce723e0b25f5eefe5a5c89e2ec719d8e3b4014318c140f9e86db8ed4b739f55f  RESULTS/RQ3/figures/fig_sizey_vs_camp.png' |
  sha256sum -c -
```

The PNG and PDF are written to `RESULTS/RQ3/figures/`.
