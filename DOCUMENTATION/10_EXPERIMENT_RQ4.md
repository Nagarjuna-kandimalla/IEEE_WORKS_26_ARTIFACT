# Experiment RQ4: selective auditing

RQ4 compares CAMP's three-gate task selector with uniform-random and
process-stratified-random selection at equal task-count budgets.

## Outputs

```text
RESULTS/RQ4/csv/budget_repetitions.csv.gz
RESULTS/RQ4/figures/fig_rq4_selective_audit.png
RESULTS/RQ4/figures/fig_rq4_selective_audit.pdf
```

The retained table is the A+P underallocation-recall result: 1,000 repetitions
for three policies at six budgets, or 18,000 data rows.

## Route A: packaged table to figure

```bash
artifact_root="$(pwd -P)"
rq4_environment="${TMPDIR:-/tmp}/ieee-works-rq4-environment"
python3.9 -m venv "$rq4_environment"
"$rq4_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ4/figures/requirements.txt
"$rq4_environment/bin/python" \
  SCRIPTS/RQ4/figures/generate_rq4_selective_audit.py

test -s RESULTS/RQ4/figures/fig_rq4_selective_audit.png
test -s RESULTS/RQ4/figures/fig_rq4_selective_audit.pdf
```

## Route B: frozen inputs to evaluation and figure

Use Linux x86-64 with Python 3.9, GCC 11-compatible runtime libraries, at
least eight logical CPUs, and space for compressed and expanded inputs. RQ4
does not need Nextflow, Slurm, Apptainer, STRACE, eBPF, BCC, or kernel headers.

### 1. Install and expand

```bash
artifact_root="$(pwd -P)"
rq4_environment="${TMPDIR:-/tmp}/ieee-works-rq4-environment"
python3.9 -m venv "$rq4_environment"
"$rq4_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ4/requirements.txt

cd "$artifact_root/SCRIPTS/RQ4"
"$rq4_environment/bin/python" prepare_inputs.py
```

This expands frozen feature, prediction, and `C-hat` inputs into the working
tree required by the evaluator.

### 2. Run both scenarios and all policies

```bash
"$rq4_environment/bin/python" scripts/evaluate_selective_gates.py \
  --seed 1996 \
  --n-jobs 8 \
  --repetitions 1000 \
  --sensitivity-repetitions 200 \
  --budgets 0.05 0.10 0.20 0.30 0.50 1.00
```

The evaluator processes A+P and A+P+C, fits and cross-fits the three gates,
constructs equal-count CAMP and random plans, repeats randomized selection,
computes core targets, and writes diagnostics below:

```text
SCRIPTS/RQ4/results/seed_1996/selective_gating/
```

The full repetition table has 36,000 rows. The directory also contains every
20%-budget task decision, task gate scores, validation tables, sensitivity
results, workflow summaries, comparisons, and diagnostic figures.

### 3. Publish the retained A+P table

```bash
generated="$artifact_root/SCRIPTS/RQ4/results/seed_1996/selective_gating"
result_csv="$artifact_root/RESULTS/RQ4/csv"
mkdir -p "$result_csv"
awk -F, 'NR == 1 || $1 == "a_plus_p"' \
  "$generated/budget_repetitions.csv" | cut -d, -f1,2,3,4,6 | gzip -n \
  > "$result_csv/budget_repetitions.csv.gz"

test "$(gzip -dc "$result_csv/budget_repetitions.csv.gz" | wc -l)" \
  -eq 18001
```

The retained columns are scenario, budget, policy, repetition, and
underallocation recall. The larger diagnostic result remains generated data.

### 4. Plot and verify

```bash
cd "$artifact_root"
"$rq4_environment/bin/python" \
  SCRIPTS/RQ4/figures/generate_rq4_selective_audit.py

gzip -t RESULTS/RQ4/csv/budget_repetitions.csv.gz
echo 'fb48f6c5bf0e7541be11e38b4350e02b7a7d5a4ed709700be3dc394a4546c396  RESULTS/RQ4/csv/budget_repetitions.csv.gz' |
  sha256sum -c -
echo '6aff8bf1b5030a78c115715d66e0f85b4e808f2ccc57445563dfa4b0b49794a8  RESULTS/RQ4/figures/fig_rq4_selective_audit.png' |
  sha256sum -c -
```

## Retained result interpretation

At 5%, 10%, 20%, 30%, and 50% budgets, mean three-gate underallocation recall
is approximately 7.5%, 12.9%, 27.5%, 39.9%, and 57.9%. Uniform and
process-stratified selection stay close to their nominal budget fractions. At
100%, every policy has recall one. Equal selected counts isolate the quality
of prioritization from the quantity audited.

| Budget | CAMP three-gate recall | Uniform-random recall | Process-stratified recall |
|---:|---:|---:|---:|
| 5% | 0.0752 | 0.0502 | 0.0498 |
| 10% | 0.1288 | 0.1002 | 0.1005 |
| 20% | 0.2748 | 0.1988 | 0.1987 |
| 30% | 0.3990 | 0.3017 | 0.2997 |
| 50% | 0.5785 | 0.4990 | 0.4992 |
| 100% | 1.0000 | 1.0000 | 1.0000 |

Each value is the mean over 1,000 A+P repetitions in the committed table.
