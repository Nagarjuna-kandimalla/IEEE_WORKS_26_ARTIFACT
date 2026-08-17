# RQ4: selective auditing

RQ4 compares the CAMP three-gate selector with equal-count uniform-random and
process-stratified selection. The packaged figure reports A+P risk-target
recall at 5%, 10%, 20%, 30%, and 50% audit budgets.

Choose exactly one starting section:

- **Packaged-CSV route:** run **Regenerate the packaged figure**, then
  **Verify the packaged result**.
- **Full frozen-input route:** run **Run the experiment end to end**, which
  already finishes by generating the figure, then run **Verify the packaged
  result**.

The full route is a single-host Python experiment, not a Slurm workflow run.

## Required files

```text
DATA/RQ4/inputs/seed_1996/             compressed frozen experiment inputs
SCRIPTS/RQ4/prepare_inputs.py          expand the frozen inputs
SCRIPTS/RQ4/scripts/evaluate_selective_gates.py
SCRIPTS/RQ4/scripts/{calibration,common,selective_gates,history,modeling}.py
SCRIPTS/RQ4/config/experiment.json
SCRIPTS/RQ4/models/seed_1996/signature_scaler.joblib
SCRIPTS/RQ4/policies/seed_1996/{a_plus_p,a_plus_p_plus_c}.json
SCRIPTS/RQ4/figures/generate_rq4_selective_audit.py
RESULTS/RQ4/csv/budget_repetitions.csv.gz
```

The compressed input contains 9,141 calibration tasks and 21,337 experiment
tasks from six workflows. The evaluator uses only information available before
each task outcome is revealed.

## System and dependencies

Use a Linux x86-64 host with Python 3.9, GCC 11-compatible runtime libraries,
at least eight logical CPUs, and space for the 18 MiB compressed input plus
expanded inputs and generated tables. The experiment uses `--n-jobs 8`.

Nextflow, Slurm, Apptainer, STRACE, eBPF, BCC, and kernel headers are not
required. Python package versions are pinned in `requirements.txt`.

## Regenerate the packaged figure

The packaged repetition table is sufficient to regenerate the PNG and PDF.
Run these commands from the artifact root:

```bash
artifact_root="$(pwd -P)"
rq4_environment="${TMPDIR:-/tmp}/ieee-works-rq4-environment"

python3.9 -m venv "$rq4_environment"
"$rq4_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ4/figures/requirements.txt
"$rq4_environment/bin/python" \
  SCRIPTS/RQ4/figures/generate_rq4_selective_audit.py
```

Expected outputs:

```text
RESULTS/RQ4/figures/fig_rq4_selective_audit.png
RESULTS/RQ4/figures/fig_rq4_selective_audit.pdf
```

## Run the experiment end to end

The following commands start with the frozen compressed inputs, repeat the
equal-budget evaluation, retain only the A+P rows used by the figure, and
regenerate the final PNG and PDF. Run from the artifact root:

```bash
artifact_root="$(pwd -P)"
rq4_environment="${TMPDIR:-/tmp}/ieee-works-rq4-environment"

python3.9 -m venv "$rq4_environment"
"$rq4_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ4/requirements.txt

cd "$artifact_root/SCRIPTS/RQ4"
"$rq4_environment/bin/python" prepare_inputs.py
"$rq4_environment/bin/python" scripts/evaluate_selective_gates.py \
  --seed 1996 \
  --n-jobs 8 \
  --repetitions 1000 \
  --sensitivity-repetitions 200 \
  --budgets 0.05 0.10 0.20 0.30 0.50 1.00

generated="$artifact_root/SCRIPTS/RQ4/results/seed_1996/selective_gating"
result_csv="$artifact_root/RESULTS/RQ4/csv"
mkdir -p "$result_csv"
awk -F, 'NR == 1 || $1 == "a_plus_p"' \
  "$generated/budget_repetitions.csv" | cut -d, -f1,2,3,4,6 | gzip -n \
  > "$result_csv/budget_repetitions.csv.gz"

cd "$artifact_root"
"$rq4_environment/bin/python" \
  SCRIPTS/RQ4/figures/generate_rq4_selective_audit.py
```

The evaluator creates working files under
`SCRIPTS/RQ4/results/seed_1996/selective_gating/`; this path is ignored by Git.
Its full repetition table has 36,000 rows across A+P, A+P+C, three policies,
six budgets, and 1,000 repetitions. It also writes every task's three-gate
selection decision at the 20% budget to `selected_tasks_20_percent.tsv`, the
per-task gate scores to `task_gate_scores.tsv`, and the full diagnostic
figures. These are regenerated working outputs. Only the filtered A+P
repetition table and final PNG/PDF belong in `RESULTS/RQ4`.

## Verify the packaged result

Run from the artifact root:

```bash
gzip -t RESULTS/RQ4/csv/budget_repetitions.csv.gz
echo 'fb48f6c5bf0e7541be11e38b4350e02b7a7d5a4ed709700be3dc394a4546c396  RESULTS/RQ4/csv/budget_repetitions.csv.gz' | sha256sum -c -
echo '6aff8bf1b5030a78c115715d66e0f85b4e808f2ccc57445563dfa4b0b49794a8  RESULTS/RQ4/figures/fig_rq4_selective_audit.png' | sha256sum -c -
```

The packaged repetition table contains 18,000 rows: 1,000 repetitions for
three policies at six budgets. The plotting script uses the five budgets from
5% through 50%.
