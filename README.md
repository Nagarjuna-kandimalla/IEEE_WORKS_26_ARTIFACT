# IEEE WORKS 2026 Artifact

## What this artifact contains

This artifact supports the experiments for Consumption-Aware Memory Prediction
(CAMP). CAMP measures how many bytes a workflow task actually consumes, keeps
causal history from completed tasks, and uses that information to improve
memory allocation for later tasks.

The artifact currently covers RQ1 through RQ5:

- **RQ1:** compare an unaudited workflow run with full STRACE and full eBPF
  auditing, including signal collection and runtime overhead;
- **RQ2:** compare memory allocation using static plus prior peak-RSS history
  (A+P) with allocation that additionally uses consumed-data history and a
  prediction of current consumption (A+P+C); and
- **RQ3:** compare CAMP with Sizey on the same six-workflow task population and
  train/test assignments; and
- **RQ4:** compare CAMP selective auditing with uniform random and
  process-stratified random auditing at equal task budgets; and
- **RQ5:** evaluate live online feedback using a Bowtie2 cold run followed by
  a warm run seeded with the completed cold outcomes.

Frozen result tables and figures are included for RQ1 through RQ5. RQ4 also
includes the frozen inputs needed to repeat its evaluation, and RQ5 includes
the live CAMP implementation and Bowtie2 workflow integration.

Run all commands in this README from the artifact root:

```bash
cd /path/to/IEEE_WORKS_26_ARTIFACT
artifact_root="$PWD"
```

## Choose one reproduction path

All commands use the artifact root as the working directory unless a step
explicitly changes it.

### Path A: regenerate results from packaged CSV/TSV files

This path does not use Slurm or rerun a workflow or model experiment:

1. Run **Quick verification of packaged results** below.
2. Run the RQ1 through RQ5 commands, in order, under **Regenerate the packaged
   figures** below. Each script reads its corresponding `RESULTS/RQ*/csv`
   files and writes to `RESULTS/RQ*/figures`.
3. Run the checksum commands in `RESULTS/README.md` to confirm the generated
   PNG files.

Path A does not require the workflow, audit, or model-experiment READMEs.

### Path B: reproduce each experiment end to end

Use the following README sequence; do not skip ahead to postprocessing before
the preceding outputs exist:

| Research question | README order |
|---|---|
| RQ1, Slurm workflows | 1. this README's RQ1 system setup; 2. [`WORKFLOWS/README.md`](WORKFLOWS/README.md) through dataset preparation; 3. [`AUDIT/README.md`](AUDIT/README.md) through worker smoke tests; 4. return to `WORKFLOWS/README.md` and run all six workflows in Exp0/Exp1/Exp2; 5. `AUDIT/README.md` to summarize Exp1/Exp2; 6. [`SCRIPTS/README.md`](SCRIPTS/README.md) RQ1 Steps 1–4 to normalize, aggregate, publish the CSVs, and draw the figure |
| RQ2, offline models | [`SCRIPTS/README.md`](SCRIPTS/README.md) RQ2 Steps 1–5: materialize, run the seed-1996 experiment, publish its task tables, run the repeated matrix, aggregate, and draw the figures |
| RQ3, Sizey comparison | [`SCRIPTS/RQ3/README.md`](SCRIPTS/RQ3/README.md), from setup through its final figure section |
| RQ4, selective auditing | [`SCRIPTS/RQ4/README.md`](SCRIPTS/RQ4/README.md), using its **Run the experiment end to end** sequence |
| RQ5, Bowtie2 online feedback | [`SCRIPTS/RQ5/README.md`](SCRIPTS/RQ5/README.md), using its **Full Slurm route** from workspace creation through cold, warm, and figure generation |

`DATA/README.md` explains the packaged inputs, and `RESULTS/README.md` explains
the final files. They are reference pages rather than additional execution
steps.

## Directory map

| Directory | Contents | Start here |
|---|---|---|
| `WORKFLOWS/` | Included custom workflows, fixed nf-core configurations, samplesheets, dataset manifests, and download/run commands. | [`WORKFLOWS/README.md`](WORKFLOWS/README.md) |
| `AUDIT/` | STRACE and eBPF installation, syscall/event definitions, parsers, and Nextflow helper configurations for Exp0, Exp1, and Exp2. | [`AUDIT/README.md`](AUDIT/README.md) |
| `DATA/` | Compressed six-workflow inputs for the offline allocation and selective-auditing experiments. | [`DATA/README.md`](DATA/README.md) |
| `SCRIPTS/` | Postprocessing, offline experiments, comparisons, and figure-generation programs grouped by research question. | [`SCRIPTS/README.md`](SCRIPTS/README.md) |
| `RESULTS/` | Frozen CSV/TSV inputs and the figures produced from them, grouped by research question. | [`RESULTS/README.md`](RESULTS/README.md) |
| `Reviewer_Feedbacks/` | W1, W2, W3, and W5 reviewer experiments, local reproduction inputs, and tested reproduction instructions. | [`Reviewer_Feedbacks/README.md`](Reviewer_Feedbacks/README.md) |

Generated virtual environments, raw sequencing files, downloaded workflow
repositories, Nextflow work directories, container caches, uncompressed
cohorts and model directories should remain outside
the committed artifact.

## Experiment map and expected outputs

| Experiment | What is executed | What to expect | Packaged output |
|---|---|---|---|
| RQ1 Exp0 | Workflow without runtime auditing | Baseline wall time and normal Nextflow/Slurm task measurements | `RESULTS/RQ1/csv/` |
| RQ1 Exp1 | Same workflow and inputs under STRACE | Successful read-family return bytes, syscall counts, and STRACE overhead | `RESULTS/RQ1/csv/strace_vs_ebpf_workflow_summary.csv` |
| RQ1 Exp2 | Same workflow and inputs under eBPF | Successful read bytes plus mmap page-fault bytes and eBPF overhead | `RESULTS/RQ1/csv/strace_vs_ebpf_workflow_summary.csv` |
| RQ2 allocation | Offline causal experiment with A+P and A+P+C on 5,026 held-out tasks | 87 A+P underallocations versus 31 A+P+C underallocations; zero unresolved tasks after retry | `RESULTS/RQ2/csv/` and `fig_rq2_allocation_tradeoff.png` |
| RQ2 variance | Five seeds over five chronological development/holdout splits | Seed variation and split sensitivity for the allocation results | RQ2 variance and split-testing figures |
| RQ3 comparison | Sizey and CAMP evaluated on the same 21,337 test tasks | Global accuracy, underallocation, requested-memory, and memory-time metrics | `RESULTS/RQ3/csv/global_metrics.csv` and `fig_sizey_vs_camp.png` |
| RQ4 selective auditing | CAMP three-gate evaluation against uniform random and process-stratified random selection at six equal task budgets | Packaged A+P risk-target recall repetition table and figure | `RESULTS/RQ4/csv/budget_repetitions.csv.gz` and `RESULTS/RQ4/figures/` |
| RQ5 online feedback | Fresh Bowtie2 cold run followed by a warm run initialized from all 6,000 cold outcomes | 6,000 successful tasks per run; reduced requested capacity and memory-time waste | `RESULTS/RQ5/csv/` and `fig_rq5_bowtie_feedback.png` |

RQ2 underallocations are offline diagnostics: the first memory request is
compared with recorded peak RSS. They are not observed scheduler OOM exits.

## Quick verification of packaged results

The following checks do not rerun workflow or model training:

```bash
gzip -t DATA/RQ2/camp_ml_cohort_33516.csv.gz
gzip -t RESULTS/RQ2/csv/a_plus_p_task_predictions.tsv.gz
gzip -t RESULTS/RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz
echo 'ddbb37e2d3a47a503fdc7435be89c98649113875a833768f9dc10938db2cab00  RESULTS/RQ1/figures/fig_rq1_signal_overhead.png' |
  sha256sum -c -
echo '31dfe2052c4ba2a8ba0bd1828db0173806e7566b32ed9193473e8b1c2e1dc3cf  RESULTS/RQ2/figures/fig_camp_only_seed_variance_underallocations.png' |
  sha256sum -c -
echo '41f7e941b33ea68b1c4decf33bbaf8e8cebc0f5cd7b1750688e8feb29a2f9bb6  RESULTS/RQ2/figures/fig_offline_seed_variance_underallocations.png' |
  sha256sum -c -
echo '4ddb9e460f9f321ce73435435fe831e59447a123802bfc6d44eb1317b934faf7  RESULTS/RQ2/figures/fig_rq2_allocation_tradeoff.png' |
  sha256sum -c -
echo 'ce723e0b25f5eefe5a5c89e2ec719d8e3b4014318c140f9e86db8ed4b739f55f  RESULTS/RQ3/figures/fig_sizey_vs_camp.png' |
  sha256sum -c -
echo 'fb48f6c5bf0e7541be11e38b4350e02b7a7d5a4ed709700be3dc394a4546c396  RESULTS/RQ4/csv/budget_repetitions.csv.gz' |
  sha256sum -c -
echo '6aff8bf1b5030a78c115715d66e0f85b4e808f2ccc57445563dfa4b0b49794a8  RESULTS/RQ4/figures/fig_rq4_selective_audit.png' |
  sha256sum -c -
gzip -t RESULTS/RQ5/csv/bowtie2_cold_task_instances.tsv.gz
gzip -t RESULTS/RQ5/csv/bowtie2_warm_task_instances.tsv.gz
echo '83975dd337287ca6985bf85067dba30d040b7fbf4c9e2f21244cb24cab6b85f5  RESULTS/RQ5/figures/fig_rq5_bowtie_feedback.png' |
  sha256sum -c -
```

## Regenerate the packaged figures

Each plotting command writes to its corresponding directory under `RESULTS`.
Use a disposable copy of the artifact if the packaged files must remain
untouched while testing.

### RQ1 figure

```bash
rq1_environment="${TMPDIR:-/tmp}/ieee-works-rq1-figures"
python3 -m venv "$rq1_environment"
"$rq1_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ1/figures/requirements.txt
"$rq1_environment/bin/python" \
  SCRIPTS/RQ1/figures/make_rq1_signal_overhead.py
```

Expected output:

```text
RESULTS/RQ1/figures/fig_rq1_signal_overhead.png
RESULTS/RQ1/figures/fig_rq1_signal_overhead.pdf
```

### RQ2 figures

```bash
rq2_figure_environment="${TMPDIR:-/tmp}/ieee-works-rq2-figures"
python3 -m venv "$rq2_figure_environment"
"$rq2_figure_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ2/figures/requirements.txt

"$rq2_figure_environment/bin/python" \
  SCRIPTS/RQ2/figures/generate_seed_variance_figure.py
"$rq2_figure_environment/bin/python" \
  SCRIPTS/RQ2/figures/generate_split_testing_figure.py
"$rq2_figure_environment/bin/python" \
  SCRIPTS/RQ2/figures/generate_allocation_oom_figure.py
```

Expected outputs are the variance, split-testing, and 87-versus-31 allocation
figures under `RESULTS/RQ2/figures`.

### RQ3 figure

```bash
rq3_figure_environment="${TMPDIR:-/tmp}/ieee-works-rq3-figures"
python3 -m venv "$rq3_figure_environment"
"$rq3_figure_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ3/figures/requirements.txt
"$rq3_figure_environment/bin/python" \
  SCRIPTS/RQ3/figures/generate_sizey_vs_camp.py
```

Expected outputs:

```text
RESULTS/RQ3/figures/fig_sizey_vs_camp.png
RESULTS/RQ3/figures/fig_sizey_vs_camp.pdf
```

### RQ4 figure

The packaged repetition table can be plotted directly. To recompute it first,
complete the evaluation and result-placement commands in
`SCRIPTS/RQ4/README.md`. Then run:

```bash
rq4_figure_environment="${TMPDIR:-/tmp}/ieee-works-rq4-figures"
python3 -m venv "$rq4_figure_environment"
"$rq4_figure_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ4/figures/requirements.txt
"$rq4_figure_environment/bin/python" \
  SCRIPTS/RQ4/figures/generate_rq4_selective_audit.py
```

Expected outputs:

```text
RESULTS/RQ4/figures/fig_rq4_selective_audit.png
RESULTS/RQ4/figures/fig_rq4_selective_audit.pdf
```

### RQ5 figure

```bash
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

## RQ1: audit signal and overhead

RQ1 executes the same workflow/input combination in three modes:

- **Exp0:** no runtime audit;
- **Exp1:** full STRACE audit; and
- **Exp2:** full eBPF audit.

Every mode must use the same worker allocation, workflow inputs, process
resources, and software environment. Start each mode with a separate empty
Nextflow work directory and do not use `-resume` when measuring overhead.

The experiment covers GATK, Minimap2, Sarek, Seqinspector, Taxprofiler, and
Viralmetagenome. Custom workflow definitions are included. Fixed nf-core
releases and their acquisition commands are documented in
`WORKFLOWS/README.md`.

### RQ1 system configuration

Use one coordinator node to launch Nextflow and 13 Slurm worker nodes. Each
worker must provide:

- 32 vCPUs;
- 256 GiB RAM; and
- access to the same shared filesystem containing the artifact, inputs, work
  directories, and results.

Set the Nextflow executor queue size to 416 task slots (`13 workers x 32
vCPUs`). The supplied helper configurations already set this value. The Slurm
partition defaults to `main`; set `CAMP_SLURM_QUEUE` when the cluster uses a
different partition.

Required software is Java 17, Nextflow, Slurm, and the workflow-specific tools
or containers. Exp1 additionally requires STRACE on every worker. Exp2
requires BCC/eBPF support, matching kernel headers, and non-interactive tracer
privileges on every worker.

### Install the common RQ1 prerequisites

On an Amazon Linux 2023 coordinator:

```bash
sudo dnf install -y java-17-amazon-corretto-headless curl python3
nextflow_installer="$(mktemp)"
curl -fsSL https://get.nextflow.io -o "$nextflow_installer"
bash "$nextflow_installer"
sudo install -m 0755 nextflow /usr/local/bin/nextflow
rm -f "$nextflow_installer" nextflow

java -version
nextflow -version
python3 --version
```

Install Apptainer on every worker:

```bash
sudo dnf install -y \
  https://github.com/apptainer/apptainer/releases/download/v1.5.3/apptainer-1.5.3-1.x86_64.rpm
apptainer --version
apptainer exec docker://alpine cat /etc/alpine-release
```

Slurm must already be configured. Check the scheduler and workers before
starting RQ1:

```bash
command -v sbatch srun squeue sinfo
sinfo -N -l
srun -p "${CAMP_SLURM_QUEUE:-main}" -N13 --ntasks-per-node=1 \
  bash -lc 'hostname; command -v apptainer python3'
```

Install audit dependencies on every worker:

```bash
sudo dnf install -y strace bcc bcc-tools python3-bcc \
  "kernel-devel-$(uname -r)"
```

Verify them through Slurm:

```bash
srun -p "${CAMP_SLURM_QUEUE:-main}" -N13 --ntasks-per-node=1 \
  bash -lc '
    set -e
    strace --version | head -n 1
    test -d "/lib/modules/$(uname -r)/build"
    sudo -n true
    sudo -n /usr/bin/python3 -c "from bcc import BPF"
  '
```

If the exact `kernel-devel` package is unavailable, boot into a kernel with
matching headers before running Exp2. Detailed audit smoke tests, system-call
semantics, Nextflow overlays, parsers, and end-to-end commands are in
`AUDIT/README.md`. Workflow and dataset commands are in
`WORKFLOWS/README.md`. Postprocessing commands are in `SCRIPTS/README.md`.

## RQ2: offline allocation experiment

RQ2 is an offline Python experiment over the complete 33,516-task matched cohort.
The packaged seed-1996 manifests assign 28,490 tasks to development and 5,026
to chronological testing across six workflows.

The requested comparison contains two feature views:

- **A+P:** static prelaunch information and causal prior peak-RSS history;
- **A+P+C:** A+P plus causal consumed-data history and predicted current
  consumption.

Do not preload the 5,026 test outcomes; the offline experiment reconstructs
causal histories from the cohort and split manifests and reveals each outcome
only after its decision.

### RQ2 system configuration and dependencies

RQ2 does not require Nextflow, Slurm, Apptainer, STRACE, eBPF, BCC, or kernel
headers. Use Linux x86-64 with Python 3.9 or newer. The repeated training jobs
used 32 CPUs and 220 GiB RAM per job. The single-seed command in
`SCRIPTS/README.md` uses eight parallel model workers. Figure generation needs
only one CPU and a few GiB of RAM.

For the packaged-CSV route, run **RQ2 Step 5** in
[`SCRIPTS/README.md`](SCRIPTS/README.md). For the full offline route, run RQ2
Steps 1–5 there in order. Those steps connect dependency installation, cohort
materialization, the seed-1996 process-tail result, publication of its task
tables, the 25 repeated runs, aggregation, and all three figures.

Generated models are written below `SCRIPTS/RQ2/models`; generated experiment
tables are written below `SCRIPTS/RQ2/results`; and the compact published files
are written below `RESULTS/RQ2`.

The RQ2 scenario check verified dependency installation, cohort
materialization, all five row/split counts, experiment imports and command-line
startup, byte-identical regeneration of all retained variance aggregates from
the completed runs, and byte-identical generation of all three RQ2 figures.
It did not execute the complete 25-run LightGBM refit. See
[`SCRIPTS/README.md`](SCRIPTS/README.md) and
[`DATA/README.md`](DATA/README.md) for details.

## RQ3: Sizey versus CAMP

RQ3 evaluates Sizey and CAMP on the same six-workflow population. The primary
cohort contains 30,478 tasks. Seed 1996 assigns 9,141 tasks to initial training
and 21,337 tasks to replay testing.

The experiment performs these stages:

1. download the pinned Sizey revision;
2. materialize the packaged cohort and create common task assignments;
3. run and normalize Sizey separately for each workflow;
4. stage Sizey's normalized predictions with the shared split manifests;
5. run the CAMP replay and process-tail models; and
6. summarize both systems into one task-matched comparison and figure.

The comparison reports point accuracy, first-attempt coverage and
underallocations, total requested memory, allocation-to-peak ratios, and
runtime-weighted unused memory. The packaged comparison contains 21,337 test
tasks for every reported method and zero unresolved OOMs after native retry.

### RQ3 system configuration and dependencies

The completed final CAMP comparison used one Slurm node with one task, 32 CPUs,
200 GiB RAM, and a three-hour limit on the `main` partition with the `standard`
constraint. Its model commands used `--n-jobs 32`, `PYTHONHASHSEED=0`,
`OMP_NUM_THREADS=1`, and `MKL_NUM_THREADS=1`. The earlier Sizey executions used
one 32-CPU, 220-GiB allocation per workflow. A single Linux x86-64 host with 32
logical CPUs and 220 GiB RAM therefore covers the full RQ3 procedure.

Python 3.9 was used for the completed experiment. RQ3 does not require
Nextflow, Apptainer, STRACE, eBPF, BCC, or kernel headers, and Slurm is optional
when the commands are run directly. Sizey is downloaded at the exact commit
recorded in `SCRIPTS/RQ3/README.md`; generated sources, models, and intermediate
results remain below `SCRIPTS/RQ3` and should not be committed.

Follow the ordered full commands in
[`SCRIPTS/RQ3/README.md`](SCRIPTS/RQ3/README.md).

After the comparison completes, its compact table is copied to
`RESULTS/RQ3/csv/global_metrics.csv`, and the final PNG/PDF are generated in
`RESULTS/RQ3/figures`.

## RQ4: selective auditing

RQ4 evaluates the three-gate selector using A+P and A+P+C on the same
seed-1996 split used by RQ3. It fits the gates on 9,141 initial tasks and
evaluates 21,337 tasks from six workflows at 5%, 10%, 20%, 30%, 50%, and 100%
audit budgets. Each result is compared with equal-count uniform random and
process-stratified random selection.

### RQ4 system configuration and dependencies

RQ4 is configured as a single-host offline Python experiment. The experiment
environment used Python 3.9.25 on Linux with GCC 11.5.0, used `--n-jobs 8`, and
submitted no Slurm jobs or live workflows. Use a Linux x86-64 host with at least
eight logical CPUs. The experiment environment did not record a host RAM
allocation, so RAM is not presented as an experimental constant. The packaged
RQ4 inputs are 18 MiB compressed; additional space is needed for expanded
inputs, models, repetition tables, and figures.

RQ4 does not require Nextflow, Slurm, Apptainer, STRACE, eBPF, BCC, or kernel
headers. Its pinned Python environment and full end-to-end commands are in
`SCRIPTS/RQ4/README.md`; input details are in `DATA/RQ4/README.md`, and generated
outputs belong under `RESULTS/RQ4`.

## RQ5: live Bowtie2 online feedback

RQ5 runs a fresh 6,000-task Bowtie2 workflow twice. The cold run begins with
28,490 completed non-Bowtie tasks and no Bowtie2 history. Its online learner
publishes a new model after 128 outcomes and every 128 outcomes thereafter.
The warm run begins from the terminal 6,000-outcome cold model and history,
without using Nextflow cache or `-resume`.

The full route needs the RQ1 eBPF worker prerequisites plus the pinned Python
modeling environment, 12 workflow workers, and one learner/controller worker.
The initial model is rebuilt outside Git from the packaged RQ2 cohort and RQ5
split manifest. Follow the complete ordered commands in
[`SCRIPTS/RQ5/README.md`](SCRIPTS/RQ5/README.md).

## Reproduction levels

Use the level appropriate to the available system:

- **Inspect:** read the packaged CSV/TSV files and figures; no dependencies are
  required beyond standard decompression tools.
- **Regenerate figures:** install only the small pinned plotting environments;
  no Slurm cluster or model training is required.
- **Run model experiments:** use the packaged cohort and split manifests to refit RQ2,
  carry out the Sizey-versus-CAMP procedure for RQ3, or repeat the RQ4
  equal-budget selective-auditing evaluation.
- **Re-execute workflows:** acquire the fixed workflow releases and datasets,
  then run RQ1 Exp0/Exp1/Exp2 or the RQ5 Bowtie2 cold/warm pair on the
  specified Slurm environment.

For exact file meanings, expected row counts, commands, and checksums, continue
with the README in the directory relevant to the selected experiment.
