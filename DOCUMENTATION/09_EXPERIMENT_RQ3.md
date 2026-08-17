# Experiment RQ3: Sizey versus CAMP

RQ3 evaluates Sizey and three CAMP views on the same six-workflow task
population and identical seed-1996 assignments.

## Compared methods and population

```text
Sizey: native A with peak-feedback behavior
CAMP A
CAMP A+P
CAMP A+P+C
```

The primary cohort contains 30,478 eligible tasks: 9,141 initial tasks and
21,337 replay-test tasks. Every method is summarized on the same test task IDs.

## Outputs

```text
RESULTS/RQ3/csv/global_metrics.csv
RESULTS/RQ3/figures/fig_sizey_vs_camp.png
RESULTS/RQ3/figures/fig_sizey_vs_camp.pdf
```

## Route A: packaged table to figure

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

## Route B: complete matched comparison

### 1. Environment and Sizey

Use Linux x86-64 with Python 3.9. A 32-CPU, 220-GiB host covers the procedure.
The final CAMP job used 32 CPUs, 200 GiB, and a three-hour limit. Nextflow and
auditing dependencies are not required.

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
  joblib==1.5.3 lightgbm==4.6.0 matplotlib==3.9.4 \
  numpy==1.26.4 pandas==2.3.3 scikit-learn==1.6.1 \
  scipy==1.13.1 statsmodels==0.14.5 patsy==1.0.1

export PYTHONHASHSEED=0
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
```

The downloaded `SCRIPTS/RQ3/source_sizey` directory is ignored by Git.

### 2. Prepare the matched cohort

```bash
cd "$artifact_root"
python SCRIPTS/RQ2/materialize_offline_data.py \
  --source DATA/RQ2/camp_ml_cohort_33516.csv.gz \
  --output data/generated/canonical_matched_tasks.tsv

cd "$artifact_root/SCRIPTS/RQ3/harness"
python scripts/prepare_data.py --cohorts primary
```

The preparation validates required fields, applies common eligibility rules,
creates Sizey inputs, and fixes the shared split assignments.

### 3. Run and normalize Sizey

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

The wrapper executes the pinned Sizey source in isolated result directories.
The normalizer converts its output into the common task-result schema.

### 4. Stage identical inputs for CAMP

```bash
comparison="$artifact_root/SCRIPTS/RQ3"
harness="$comparison/harness"
mkdir -p "$comparison/inputs" "$comparison/data/split_manifests" \
  "$comparison/baseline/sizey" "$comparison/manifests"

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

### 5. Run CAMP and summarize

```bash
cd "$artifact_root/SCRIPTS/RQ3"
python scripts/preflight.py
python scripts/run_camp.py --seed 1996 --n-jobs 32

for variant in A A+P A+P+C
do
  python scripts/train_process_tail.py \
    --seed 1996 --variant "$variant" --n-jobs 32
done

python scripts/summarize_comparison.py
```

Preflight validates task identity, population, assignments, and staged Sizey
outputs. The CAMP replay generates A, A+P, and A+P+C task predictions. The
summarizer checks alignment and computes global, workflow, heavy-tail, and
paired-bootstrap results.

### 6. Publish and plot

```bash
cd "$artifact_root"
test -s SCRIPTS/RQ3/results/comparison/global_metrics.csv
cp SCRIPTS/RQ3/results/comparison/global_metrics.csv \
  RESULTS/RQ3/csv/global_metrics.csv
```

Run Route A to generate and verify the final figure.

## Retained result interpretation

On 21,337 test tasks, first-request underallocation counts are 152 for CAMP A,
275 for CAMP A+P, 287 for CAMP A+P+C, and 3,597 for Sizey. Retry behavior
resolves every task for all methods. Sizey requests less total memory but has
substantially lower first-attempt coverage. Read the result as a multi-metric
safety/efficiency comparison, not a single scalar ranking.

| Method/view | MAE MiB | Median APE | First underallocations | First-attempt coverage | Request/peak ratio |
|---|---:|---:|---:|---:|---:|
| CAMP A | 415.92 | 6.99% | 152 | 99.29% | 2.726 |
| CAMP A+P | 617.91 | 12.68% | 275 | 98.71% | 3.024 |
| CAMP A+P+C | 612.68 | 16.14% | 287 | 98.65% | 3.059 |
| Sizey native A with peak feedback | 538.28 | 27.04% | 3,597 | 83.14% | 1.357 |

These are the retained global values from `RESULTS/RQ3/csv/global_metrics.csv`.
The table illustrates why accuracy, safety, and reservation size must be
reported separately.
