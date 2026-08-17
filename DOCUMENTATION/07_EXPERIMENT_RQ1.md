# Experiment RQ1: audit signal and overhead

RQ1 executes six workflows without auditing, with STRACE, and with eBPF. It
compares the consumption signal captured by the two audit mechanisms and the
runtime increase relative to the matched baseline.

## Outputs

```text
RESULTS/RQ1/csv/strace_vs_ebpf_workflow_summary.csv
RESULTS/RQ1/csv/rq1_workflow_overhead.csv
RESULTS/RQ1/csv/sarek_reconstructed_active_runtime.csv
RESULTS/RQ1/figures/fig_rq1_signal_overhead.png
RESULTS/RQ1/figures/fig_rq1_signal_overhead.pdf
```

The first two tables are the figure inputs. The Sarek timing file supports only
the packaged historical transformation; a fresh non-resumed run uses direct
timings.

## Route A: packaged tables to figure

Run from the artifact root:

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

This route does not execute Nextflow, Slurm, STRACE, or eBPF.

## Route B: workflows to figure

### 1. Prepare the system

Use one Nextflow coordinator and 13 Slurm workers, each with 32 vCPUs and
256 GiB RAM. The configured queue size is 416. Install Java 17, Nextflow,
Slurm, Apptainer, Python 3, Git, workflow tools/containers, STRACE, BCC, Python
BCC bindings, and matching kernel headers.

```bash
java -version
nextflow -version
python3 --version
git --version
apptainer --version
command -v sbatch srun squeue sinfo
sinfo -N -l
srun -p "${CAMP_SLURM_QUEUE:-main}" -N13 --ntasks-per-node=1 \
  bash -lc 'hostname; command -v apptainer python3 strace'
```

Run the STRACE and eBPF smoke tests in
[`../AUDIT/README.md`](../AUDIT/README.md) in the same worker/container context
used by tasks. Exp2 also requires:

```bash
export EBPF_PYTHON=/usr/bin/python3
export EBPF_TRACER="$PWD/AUDIT/helper_scripts/exp2/ebpf_audit_py39.py"
sudo -n true
sudo -n "$EBPF_PYTHON" -c 'from bcc import BPF'
```

### 2. Acquire workflows and inputs

```bash
artifact_root="$PWD"
reproduction_root="${TMPDIR:-/tmp}/camp-workflow-reproduction"
mkdir -p "$reproduction_root/nxf-home" "$reproduction_root/work" \
  "$reproduction_root/results" "$reproduction_root/apptainer-cache"
export NXF_HOME="$reproduction_root/nxf-home"
export NXF_APPTAINER_CACHEDIR="$reproduction_root/apptainer-cache"

nextflow pull nf-core/sarek -r 3.6.0
nextflow pull nf-core/seqinspector -r 1.1.0
nextflow pull nf-core/taxprofiler -r 2.0.1
nextflow pull nf-core/viralmetagenome -r 1.1.3
```

Complete the workflow README in this order: verify fixed source revisions;
define its ENA download helper; prepare Kraken2; prepare Sarek; prepare the
shared Seqinspector/Viralmetagenome inputs and samplesheets; prepare
Taxprofiler and its database sheet; decompress the GATK reference and provide
its jar/images; and confirm Minimap2/Samtools on workers. Predownload remote
inputs for a controlled overhead comparison.

### 3. Execute eighteen fresh runs

```bash
artifact_root="$PWD"
export RUN_ROOT="${RUN_ROOT:-$artifact_root/RQ1_RUNS}"
mkdir -p "$RUN_ROOT"
```

Execute the complete `run_nfcore` function and loop under **Run all nf-core
workflows with the audit helpers** in
[`../WORKFLOWS/README.md`](../WORKFLOWS/README.md). Execute its `run_custom`
function for GATK and Minimap2 after configuring:

```bash
export GATK_JAR=/path/to/gatk-package-4.5.0.0-local.jar
export BWA_IMAGE=/path/to/bwa.sif
export SAMTOOLS_IMAGE=/path/to/samtools.sif
export APPTAINER_BIND="$artifact_root,$RUN_ROOT"
```

Each run writes to `$RUN_ROOT/<workflow>/exp0|exp1|exp2`. Each directory must
contain `nextflow.log`, trace, report, timeline, DAG, work data, and results.
Use a different empty work directory per mode and do not use `-resume` for
fresh overhead measurements.

### 4. Summarize Exp1 and Exp2

```bash
for workflow in minimap2 sarek seqinspector taxprofiler viralmetagenome; do
  run_dir="$RUN_ROOT/$workflow/exp1"
  python3 AUDIT/helper_scripts/exp1/parse_strace_logs.py \
    --root "$run_dir/results/strace" \
    --out "$run_dir/results/metrics/strace_task_summary.tsv" \
    --trace "$run_dir/nextflow_trace.tsv" \
    --successful-only

  run_dir="$RUN_ROOT/$workflow/exp2"
  python3 AUDIT/helper_scripts/exp2/summarize_ebpf.py \
    --root "$run_dir/results/ebpf" \
    --out "$run_dir/results/metrics/ebpf_task_summary.tsv"
done
```

GATK writes audit values directly into
`results/metrics/haplotypecaller_task_metrics.tsv`.

### 5. Normalize and enrich

```bash
artifact_root="$PWD"
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
        audit_arguments=(--strace-summary "$run_dir/results/metrics/strace_task_summary.tsv")
        ;;
      exp2)
        instrumentation=ebpf
        audit_arguments=(--ebpf-merged "$run_dir/results/metrics/ebpf_task_summary.tsv")
        ;;
    esac

    test -s "$run_dir/nextflow_trace.tsv"
    test -s "$artifact_root/${static_manifests[$workflow]}"
    [ "$mode" = exp0 ] || test -s "${audit_arguments[1]}"

    python3 SCRIPTS/RQ1/postprocessing/normalize_nextflow_baseline_trace.py \
      --workflow "$workflow" --trace "$run_dir/nextflow_trace.tsv" \
      --out "$run_dir/training_task_metrics.tsv" \
      --dataset-id "${dataset_ids[$workflow]}" \
      --static-manifest "$artifact_root/${static_manifests[$workflow]}" \
      --instrumentation "$instrumentation" "${audit_arguments[@]}"

    python3 SCRIPTS/RQ1/postprocessing/enrich_training_task_inputs.py \
      --input "$run_dir/training_task_metrics.tsv" \
      --output "$run_dir/training_task_metrics_with_inputs.tsv" \
      --workflow-root "$artifact_root/WORKFLOWS/$workflow"
  done
done
```

### 6. Aggregate and add GATK

```bash
aggregate_root="${RQ1_AGGREGATE_ROOT:-${TMPDIR:-/tmp}/rq1-aggregate}"
python3 SCRIPTS/RQ1/postprocessing/build_workflows_run_results.py \
  --workflows-root "$RUN_ROOT" --output-root "$aggregate_root" \
  --skip-ml-ready
test -s "$aggregate_root/paper_tables/strace_vs_ebpf_workflow_summary.csv"

python3 SCRIPTS/RQ1/postprocessing/build_rq1_figure_inputs.py \
  --workflow-summary "$aggregate_root/paper_tables/strace_vs_ebpf_workflow_summary.csv" \
  --gatk-runs-root "$RUN_ROOT"

test -s RESULTS/RQ1/csv/strace_vs_ebpf_workflow_summary.csv
test -s RESULTS/RQ1/csv/rq1_workflow_overhead.csv
```

The aggregator recreates its output directory. Do not place unrelated files
there. `--skip-ml-ready` omits a separate downstream cohort build, not an RQ1
figure field.

### 7. Plot

Run Route A's figure command. A new empirical run is validated structurally
and scientifically; a packaged checksum matches only when its input tables and
rendering environment are identical.

## Interpretation boundary

The final signal table separates STRACE reads, eBPF reads, and eBPF mmap-fault
bytes. The overhead table separates direct and reconstructed timing bases. The
historical Sarek execution must be interpreted with its reconstructed timing
and comparison-grade fields rather than treated as a fresh controlled direct
triplet.

## Retained results

| Workflow | STRACE read GiB | eBPF read GiB | eBPF mmap-fault GiB | eBPF total GiB | STRACE overhead | eBPF overhead | Timing basis |
|---|---:|---:|---:|---:|---:|---:|---|
| GATK | 25.441 | 25.441 | 5.187 | 30.628 | 5.55% | 5.54% | direct |
| Minimap2 | 51.589 | 50.932 | 52.366 | 103.298 | 27.18% | 138.85% | direct |
| Sarek | 3,182.052 | 3,407.885 | 244.422 | 3,652.307 | 126.05% | 1.99% | reconstructed active timing |
| Seqinspector | 776.814 | 745.546 | 64.088 | 809.635 | 26.64% | 38.86% | direct |
| Taxprofiler | 908.561 | 878.392 | 62.201 | 940.593 | 58.55% | 52.15% | direct |
| Viralmetagenome | 365.837 | 336.391 | 83.622 | 420.013 | 13.25% | 84.82% | direct |

The signal quantities come from
`strace_vs_ebpf_workflow_summary.csv`. The overhead values come from
`rq1_workflow_overhead.csv`, which deliberately uses reconstructed active
timing for the retained Sarek case. Do not combine the Sarek direct lifecycle
fields from the signal table with the reconstructed overhead fields as if they
were one controlled fresh run.
