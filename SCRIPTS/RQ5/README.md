# RQ5: live Bowtie2 online feedback

RQ5 compares a Bowtie2 cold run with a subsequent warm run. The cold run
starts with no Bowtie2 history. Its completed outcomes update the model during
execution and seed the warm run. A and A+P remain shadow predictions; direct
A+P+C is the only policy that selects submitted memory.

Choose one route:

- **Packaged-data route:** regenerate the retained figure from the two
  compressed 6,000-task tables.
- **Full Slurm route:** rebuild the initial state, execute cold and warm
  Bowtie2 runs, publish their task tables, and generate the same figure.

Run all commands from the artifact root.

## Packaged-data route

This route does not require Slurm, Nextflow, Apptainer, eBPF, BCC, Java, raw
FASTQs, or model training.

```bash
artifact_root="$(pwd -P)"
rq5_figure_environment="${TMPDIR:-/tmp}/ieee-works-rq5-figures"
python3.9 -m venv "$rq5_figure_environment"
"$rq5_figure_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ5/figures/requirements.txt
"$rq5_figure_environment/bin/python" \
  SCRIPTS/RQ5/figures/generate_rq5_bowtie_feedback.py
```

Expected outputs:

```text
RESULTS/RQ5/figures/fig_rq5_bowtie_feedback.png
RESULTS/RQ5/figures/fig_rq5_bowtie_feedback.pdf
```

The PNG is deterministic with the pinned environment:

```bash
echo '83975dd337287ca6985bf85067dba30d040b7fbf4c9e2f21244cb24cab6b85f5  RESULTS/RQ5/figures/fig_rq5_bowtie_feedback.png' |
  sha256sum -c -
```

## Full Slurm route

### System configuration

The retained run used 13 shared-filesystem worker nodes. Each node provided 32
vCPUs and approximately 256 GB RAM (244.140625 GiB recorded in the task
features). `mempred-worker-01` through `mempred-worker-12` executed workflow
tasks, giving a Nextflow queue size of 384. `mempred-worker-13` hosted the
Nextflow controller, prediction service, and 16-CPU/64-GB online learner.

Equivalent node names may be supplied through the environment variables below.
All nodes must see the artifact and work directory at identical absolute paths.
They also require outbound HTTPS access for the ENA FASTQs and Ensembl
reference.

Install the following before starting:

- Linux x86-64, Python 3.9, Java 17, Nextflow, Slurm, and Apptainer;
- the Python packages pinned in `requirements.txt`;
- `/usr/bin/time` on every task node;
- BCC, matching kernel headers, and the eBPF prerequisites in
  [`../../AUDIT/README.md`](../../AUDIT/README.md); and
- passwordless non-interactive `sudo` for the packaged eBPF tracer on the 12
  task workers.

The initial model build uses one 32-CPU node with 128 GB RAM. Generated models,
raw FASTQs, Nextflow work files, and live outputs belong in external shared
storage, not in Git.

### 1. Create the Python environment and external workspace

```bash
artifact_root="$(pwd -P)"
rq5_work="${CAMP_RQ5_WORK_ROOT:?set CAMP_RQ5_WORK_ROOT to shared storage}"
rq5_environment="$rq5_work/python-environment"

python3.9 -m venv "$rq5_environment"
"$rq5_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ5/requirements.txt

"$rq5_environment/bin/python" SCRIPTS/RQ5/prepare_workspace.py \
  --artifact-root "$artifact_root" \
  --work-root "$rq5_work"

experiment_root="$rq5_work/fresh_experiment"
```

`prepare_workspace.py` materializes the existing 33,516-task RQ2 cohort,
expands the frozen train/calibration/test split, and copies the exact model and
online source into the external experiment directory. It refuses to overwrite
an existing workspace.

### 2. Test the APC-only policy

```bash
cd "$experiment_root/design_3/live_online_learning_apc_only"
"$rq5_environment/bin/python" -m unittest -v tests/test_apc_only_policy.py
cd "$artifact_root"
```

The tests check A independence, causal residual updates, version-matched
calibration, local learner databases, learner liveness monitoring, and the
fixed submission-rate limit.

### 3. Rebuild the cold-start state

```bash
export RQ5_PYTHON="$rq5_environment/bin/python"
mkdir -p "$rq5_work/logs"

sbatch --wait \
  --partition="${CAMP_SLURM_QUEUE:-main}" \
  --constraint="${CAMP_WORKER_CONSTRAINT:-standard}" \
  --output="$rq5_work/logs/rq5-build-%j.out" \
  --error="$rq5_work/logs/rq5-build-%j.err" \
  SCRIPTS/RQ5/slurm_build_initial_state.sh \
  "$artifact_root" "$experiment_root"
```

This deterministically rebuilds the seed-1996 A, A+P, and A+P+C models,
process-tail policies, APC-only policy, 28,490-row non-Bowtie history, SQLite
history, and workflow-cold model. The generated state is written to:

```text
$experiment_root/artifacts/
```

Do not rerun this command over an existing build. Create another fresh
workspace when a clean rebuild is required.

### 4. Obtain the workflow containers

```bash
mkdir -p "$rq5_work/containers"
apptainer pull \
  "$rq5_work/containers/bowtie2-2.4.2.sif" \
  docker://quay.io/biocontainers/bowtie2:2.4.2--py38h1c8e9b9_1
apptainer pull \
  "$rq5_work/containers/samtools-1.20.sif" \
  docker://quay.io/biocontainers/samtools:1.20--h50ea8bc_0
```

The Bowtie2 workflow manifest contains the exact HTTPS URLs, byte counts, and
chunk counts. `DOWNLOAD_RUN` streams those paired FASTQs during execution; no
separate bulk download step is required.

### 5. Configure the cluster and run cold

```bash
export RQ5_ARTIFACT_ROOT="$artifact_root"
export RQ5_RUN_ROOT="$rq5_work/live"
export RQ5_PYTHON="$rq5_environment/bin/python"
export RQ5_NEXTFLOW="$(command -v nextflow)"
export CAMP_EBPF_TRACER="$artifact_root/AUDIT/helper_scripts/exp2/ebpf_audit_py39.py"
export CAMP_BOWTIE2_IMAGE="$rq5_work/containers/bowtie2-2.4.2.sif"
export CAMP_SAMTOOLS_IMAGE="$rq5_work/containers/samtools-1.20.sif"
export CAMP_APPTAINER_BIND="$artifact_root:$artifact_root,$rq5_work:$rq5_work"
export CAMP_SLURM_QUEUE="${CAMP_SLURM_QUEUE:-main}"
export CAMP_WORKER_NODELIST="${CAMP_WORKER_NODELIST:-mempred-worker-[01-12]}"
export CAMP_LEARNER_NODELIST="${CAMP_LEARNER_NODELIST:-mempred-worker-13}"
export CAMP_WORKER_CONSTRAINT="${CAMP_WORKER_CONSTRAINT:-standard}"
export CAMP_QUEUE_SIZE="${CAMP_QUEUE_SIZE:-384}"

online_root="$experiment_root/design_3/live_online_learning_apc_only"
initial="$experiment_root/artifacts"
cold_label="rq5_bowtie2_cold"

sbatch --wait --export=ALL \
  --partition="$CAMP_SLURM_QUEUE" \
  --nodelist="$CAMP_LEARNER_NODELIST" \
  --constraint="$CAMP_WORKER_CONSTRAINT" \
  --output="$rq5_work/logs/rq5-cold-%j.out" \
  --error="$rq5_work/logs/rq5-cold-%j.err" \
  "$online_root/slurm/run_fresh_bowtie2.slurm" \
  "$experiment_root" \
  "$cold_label" \
  "$initial/initial_model" \
  "$initial/initial_history_features.tsv" \
  "$initial/initial_history.sqlite3" \
  "$initial/initial_model/base_training_frame.tsv" \
  0
```

The cold preflight rejects any initial SQLite history containing Bowtie2 rows.
During the run, the learner first updates at 128 completed tasks and then every
128 completions. Every model decision remains immutable after it is made.

Verify cold completion before starting warm:

```bash
cold_run="$RQ5_RUN_ROOT/runs/$cold_label"
grep -q 'Execution complete' "$cold_run/.nextflow.log"
test "$(wc -l < "$cold_run/results/metrics/camp_design3/task_instances.tsv")" \
  -eq 6001
test -s "$cold_run/camp/postrun_history.sqlite3"
test -s "$cold_run/camp/current_model.json"
```

### 6. Start warm from the terminal cold model

```bash
eval "$("$RQ5_PYTHON" - "$cold_run/camp/current_model.json" <<'PY'
import json
import shlex
import sys

pointer = json.load(open(sys.argv[1], encoding="utf-8"))
print("cold_model_root=" + shlex.quote(pointer["model_root"]))
print("cold_history_features=" + shlex.quote(pointer["history_features"]))
PY
)"

test -d "$cold_model_root"
test -s "$cold_history_features"
test -s "$cold_model_root/base_training_frame.tsv"

warm_label="rq5_bowtie2_warm"
sbatch --wait --export=ALL \
  --partition="$CAMP_SLURM_QUEUE" \
  --nodelist="$CAMP_LEARNER_NODELIST" \
  --constraint="$CAMP_WORKER_CONSTRAINT" \
  --output="$rq5_work/logs/rq5-warm-%j.out" \
  --error="$rq5_work/logs/rq5-warm-%j.err" \
  "$online_root/slurm/run_fresh_bowtie2.slurm" \
  "$experiment_root" \
  "$warm_label" \
  "$cold_model_root" \
  "$cold_history_features" \
  "$cold_run/camp/postrun_history.sqlite3" \
  "$cold_model_root/base_training_frame.tsv" \
  6000
```

The warm preflight requires exactly 6,000 Bowtie2 history rows. It does not
reuse the cold Nextflow work directory or cache.

Verify warm completion:

```bash
warm_run="$RQ5_RUN_ROOT/runs/$warm_label"
grep -q 'Execution complete' "$warm_run/.nextflow.log"
test "$(wc -l < "$warm_run/results/metrics/camp_design3/task_instances.tsv")" \
  -eq 6001
test -s "$warm_run/results/metrics/camp_design3/summary.json"
```

### 7. Publish task tables and generate the figure

Run this in a disposable artifact copy if the retained tables must remain
untouched:

```bash
gzip -n -c \
  "$cold_run/results/metrics/camp_design3/task_instances.tsv" \
  > RESULTS/RQ5/csv/bowtie2_cold_task_instances.tsv.gz
gzip -n -c \
  "$warm_run/results/metrics/camp_design3/task_instances.tsv" \
  > RESULTS/RQ5/csv/bowtie2_warm_task_instances.tsv.gz

"$rq5_environment/bin/python" \
  SCRIPTS/RQ5/figures/generate_rq5_bowtie_feedback.py --new-run
```

The plotting script still requires exactly 6,000 unique logical tasks in each
table. `--new-run` skips only the retained numeric-value checks because a new
execution can differ slightly from the preserved measurements. Omit the flag
when validating or recreating the packaged figure.

## Retained result interpretation

The cold and warm observed peak totals are 238.50 and 238.82 GiB. Requested
capacity falls from 898.39 to 251.93 GiB, and unused memory-time falls from
23.954 to 0.018 GiB-h. Tasks requested within 10% of peak increase from 75.25%
to 92.73%; tasks above a 25% margin fall from 1,004 to 16. The complete
panel-by-panel analysis is in
[`../../DOCUMENTATION/13_EXPERIMENT_RQ5.md`](../../DOCUMENTATION/13_EXPERIMENT_RQ5.md).
