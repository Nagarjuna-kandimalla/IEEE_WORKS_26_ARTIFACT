# Component: workflows and execution

This component produces the repeated task executions used by RQ1. The
canonical acquisition, input preparation, and launch commands are in
[`../WORKFLOWS/README.md`](../WORKFLOWS/README.md).

## Workflow inventory

| Workflow | Implementation | Frozen version/source | Main task population |
|---|---|---|---:|
| GATK | Included custom Nextflow | GATK 4.5.0.0 | 500 HaplotypeCaller tasks |
| Minimap2 | Included custom Nextflow | Artifact source | 1,500 synthetic windows plus supporting tasks |
| Sarek | nf-core | 3.6.0 | 1,495 intervals, 6,012 matched rows |
| Seqinspector | nf-core | 1.1.0 | 3,000 paired runs, 6,001 matched rows |
| Taxprofiler | nf-core | 2.0.1 | 5,998 paired runs, 5,999 matched rows |
| Viralmetagenome | nf-core | 1.1.3 | 3,000 paired runs, 9,003 matched rows |

Sarek and Taxprofiler additionally have verified source commits documented in
the canonical workflow README. Every nf-core launch must retain its `-r`
release argument.

## Runtime architecture

RQ1 uses one Nextflow coordinator and 13 Slurm workers. Each worker provides
32 vCPUs and 256 GiB RAM; the configured Nextflow queue size is 416 task slots.
The coordinator, workers, inputs, work directories, outputs, and container
cache must see compatible shared paths. The default partition is `main`,
overridable with `CAMP_SLURM_QUEUE` where supported by the helper configuration.

Common requirements are Linux, Java 17, Nextflow, Git, Slurm, and Apptainer.
GATK additionally needs the GATK jar and BWA/Samtools images. Minimap2 needs
`minimap2` and `samtools` on workers. STRACE and eBPF requirements are separate
and are described in [`04_COMPONENT_AUDITING_AND_SIGNALS.md`](04_COMPONENT_AUDITING_AND_SIGNALS.md).

## Three matched execution modes

Every workflow is executed in three modes:

| Mode | Overlay | Meaning |
|---|---|---|
| Exp0 | `AUDIT/helper_scripts/exp0/*` | Baseline, no runtime audit |
| Exp1 | `AUDIT/helper_scripts/exp1/*` | STRACE full audit |
| Exp2 | `AUDIT/helper_scripts/exp2/*` | eBPF full audit |

Only the audit overlay, output directory, and empty work directory should
change between modes. Inputs, workflow release, process resources, worker
pool, queue size, container policy, and software environment must remain
matched. Do not use `-resume` for an overhead measurement because cached tasks
do not execute. A documented infrastructure recovery may use `-resume`, but
its timing must not be silently mixed with fresh-run timing.

## Reproduction-root layout

Keep downloaded workflow assets, raw inputs, work data, and containers outside
the committed repository. The canonical setup is:

```bash
artifact_root="$PWD"
reproduction_root="${TMPDIR:-/tmp}/camp-workflow-reproduction"
mkdir -p "$reproduction_root/nxf-home" "$reproduction_root/work" \
  "$reproduction_root/results" "$reproduction_root/apptainer-cache"
export NXF_HOME="$reproduction_root/nxf-home"
export NXF_APPTAINER_CACHEDIR="$reproduction_root/apptainer-cache"
```

The four nf-core releases are acquired with:

```bash
nextflow pull nf-core/sarek -r 3.6.0
nextflow pull nf-core/seqinspector -r 1.1.0
nextflow pull nf-core/taxprofiler -r 2.0.1
nextflow pull nf-core/viralmetagenome -r 1.1.3
```

Prepare local inputs before timed overhead runs so network transfer is not
part of one mode but not another. The exact ENA, Sarek, Kraken2, GATK, and
Minimap2 preparation commands are in `WORKFLOWS/README.md`.

## Configuration composition

For nf-core workflows, load the workflow-specific configuration first and one
generic audit overlay second:

```text
-c WORKFLOWS/<workflow>/experiment.config
-c AUDIT/helper_scripts/<mode>/<baseline|strace|ebpf>.config
```

For custom workflows, run the included `main.nf` and load the matching custom
overlay:

```text
-c AUDIT/helper_scripts/<mode>/<gatk|minimap2>.config
```

The later overlay is intentional: it adds or modifies instrumentation while
preserving the workflow definition.

## Required run layout

All eighteen runs must use the same layout because the audit summarizers and
postprocessors consume it directly:

```text
$RUN_ROOT/
  <workflow>/
    exp0/
      nextflow.log
      nextflow_trace.tsv
      nextflow_report.html
      nextflow_timeline.html
      nextflow_dag.html
      work/
      results/
    exp1/
      ...same lifecycle files...
      results/strace/...
    exp2/
      ...same lifecycle files...
      results/ebpf/...
```

Use one unchanged `RUN_ROOT` value through workflow execution, audit
summarization, RQ1 normalization, and aggregation.

## Canonical launch sequence

1. Validate Java, Nextflow, Git, Apptainer, Slurm, and worker access.
2. Create the reproduction root and set `NXF_HOME` and the Apptainer cache.
3. Pull and verify the four nf-core releases.
4. Prepare or predownload every dataset and reference.
5. Install and smoke-test STRACE and BCC/eBPF on worker nodes.
6. Export `EBPF_PYTHON` and `EBPF_TRACER` before Exp2.
7. Run nf-core Exp0, Exp1, and Exp2 using the `run_nfcore` function in the
   canonical README.
8. Prepare GATK dependencies and run the three custom GATK modes.
9. Confirm Minimap2 executables are available and run its three modes.
10. Check lifecycle outputs for all eighteen runs.
11. Summarize audit outputs before starting normalization.

The complete shell functions are kept in the canonical workflow README to
avoid maintaining a second launcher implementation here.

## Immediate outputs and consumers

Every completed run produces a Nextflow trace and lifecycle reports. Exp1
also produces per-task STRACE directories; Exp2 produces per-task eBPF
directories. Audit summarization creates task summary TSVs. RQ1 normalization
then produces:

```text
training_task_metrics.tsv
training_task_metrics_with_inputs.tsv
```

Those normalized tables feed the five-workflow aggregator. GATK is joined by
the dedicated RQ1 figure-input builder using its task metrics and lifecycle
log. The exact continuation is in
[`07_EXPERIMENT_RQ1.md`](07_EXPERIMENT_RQ1.md).

## Execution invariants

- Never share a Nextflow work directory between Exp0, Exp1, and Exp2.
- Never omit the nf-core release.
- Keep the same task inputs and resources across modes.
- Keep network staging outside timing when performing a controlled comparison.
- Keep the complete `nextflow.log`, not only the trace.
- Treat a missing trace, audit summary, or task-metrics file as a failed stage.
- Do not start aggregation until every required workflow/mode output exists.
