# CAMP end-to-end integration

This guide connects the executable stages without merging experiments that
have different inputs or system requirements. Use it as the practical master
sequence; use the component guides to understand internals and the RQ guides
for exact commands.

## Reproduction endpoints

There are two valid endpoints for every research question:

1. Start from committed result tables and recreate the reported figure.
2. Start from the earliest packaged executable input for that RQ, rerun its
   experiment, rebuild the result table, and create the figure.

RQ1 and RQ5 include live workflow execution. RQ2 begins with the packaged
33,516-task matched cohort. RQ3 begins with that cohort and the pinned Sizey
source. RQ4 begins with frozen compressed features and predictions. RQ5 uses
the RQ2 cohort to rebuild its initial non-Bowtie model, then executes a separate
Bowtie2 cold/warm pair. A newly executed RQ1 triplet is not implicitly
substituted for any retained downstream input.

## Overall practical graph

```text
RQ1 live route
fixed workflow sources + raw datasets + system configuration
    -> Exp0 / Exp1 / Exp2 Nextflow runs
    -> STRACE and eBPF task summaries
    -> trace normalization and input enrichment
    -> five-workflow aggregation + GATK combination
    -> RQ1 signal/overhead CSVs
    -> RQ1 figure

RQ2 offline route
packaged 33,516-task cohort + fixed chronological manifests
    -> materialized canonical TSV
    -> causal history + C-hat + A+P/A+P+C quantile models
    -> policy calibration + sequential allocation
    -> preserved global base + final process-tail result
    -> paired seed-1996 task tables
    -> 25 seed/split runs
    -> variance and route-contract CSVs
    -> three RQ2 figures

RQ3 comparison route
packaged cohort + pinned Sizey source
    -> eligible 30,478-task comparison cohort and shared assignments
    -> six Sizey runs and normalized predictions
    -> staged common inputs
    -> CAMP A/A+P/A+P+C replay and process tails
    -> aligned comparison summary CSV
    -> RQ3 figure

RQ4 selective-audit route
frozen 9,141 initial + 21,337 experiment task inputs
    -> expanded working inputs
    -> A+P and A+P+C cross-fitted/final gate scores
    -> equal-budget three-gate and random repetitions
    -> filtered A+P underallocation-recall table
    -> RQ4 figure

RQ5 live-feedback route
packaged RQ2 cohort + RQ5 primary split + Bowtie2 workflow manifest
    -> rebuilt initial APC-only model and 28,490-row history
    -> 6,000-task cold run with periodic online updates
    -> terminal cold model + completed cold history
    -> fresh 6,000-task warm run without Nextflow cache reuse
    -> cold/warm logical-task tables
    -> RQ5 requested-capacity, wastage, and margin figure
```

## System profiles

| Route | Required resources and software |
|---|---|
| RQ1 packaged figure | Python 3 and pinned plotting packages |
| RQ1 live experiment | Coordinator; 13 Slurm workers; 32 vCPU and 256 GiB per worker; Java 17; Nextflow; Apptainer; workflow tools; STRACE; BCC/eBPF; matching headers; trace privileges |
| RQ2 packaged figures | Python 3 and pinned plotting packages |
| RQ2 primary offline run | Python 3.9 environment; at least 8 logical CPUs |
| RQ2 variance matrix | 25 independent runs; completed runs used 32 CPUs and 220 GiB per run; Slurm optional |
| RQ3 full comparison | Linux x86-64; Python 3.9; pinned Sizey revision; up to 32 CPUs and 220 GiB |
| RQ4 full evaluation | Linux x86-64; Python 3.9; GCC 11-compatible libraries; at least 8 CPUs; no workflow/audit stack |
| RQ5 packaged figure | Python 3.9 and pinned plotting packages |
| RQ5 full experiment | 12 Slurm task workers plus one learner/controller worker; 32 vCPU and approximately 256 GB per node; Java 17; Nextflow; Apptainer; Python 3.9; BCC/eBPF; matching headers; trace privileges |

Do not install all dependencies into one environment. RQ1 and RQ5 audit
helpers rely on system/kernel packages, while RQ2–RQ5 pin Python stacks for
modeling or plotting.

## Fast path: all figures from committed tables

Run from the artifact root. Each RQ guide contains its environment creation,
plotting command, expected outputs, and checksum:

1. RQ1 Route A in [`07_EXPERIMENT_RQ1.md`](07_EXPERIMENT_RQ1.md).
2. RQ2 Route A in [`08_EXPERIMENT_RQ2.md`](08_EXPERIMENT_RQ2.md).
3. RQ3 Route A in [`09_EXPERIMENT_RQ3.md`](09_EXPERIMENT_RQ3.md).
4. RQ4 Route A in [`10_EXPERIMENT_RQ4.md`](10_EXPERIMENT_RQ4.md).
5. RQ5 packaged-data route in [`../SCRIPTS/RQ5/README.md`](../SCRIPTS/RQ5/README.md).

The five routes are independent. A plotting failure should be diagnosed from
that RQ's requirements and committed CSV/TSV inputs; it does not require a
workflow or model rerun.

## Full RQ1 state sequence

| Step | Required input | Action | Required output before continuing |
|---:|---|---|---|
| 1 | Cluster | Verify 13 workers, filesystem, Slurm, Java, Nextflow, Apptainer | All coordinator/worker checks succeed |
| 2 | Worker kernels | Install and smoke-test STRACE/BCC/eBPF | STRACE log and successful BCC import/tracing privilege |
| 3 | Workflow sources | Pull four fixed nf-core releases; use included custom workflows | All `--help` and fixed-revision checks succeed |
| 4 | Dataset manifests | Download/verify or configure local Sarek, ENA, Kraken2 inputs; prepare custom inputs | Every samplesheet path and referenced file exists |
| 5 | Fresh work roots | Execute six workflows in Exp0/Exp1/Exp2 | Eighteen run directories with lifecycle files |
| 6 | Exp1/Exp2 outputs | Parse STRACE and summarize eBPF | Ten common audit-summary TSVs; GATK direct metrics |
| 7 | Traces, summaries, static manifests | Normalize and enrich five common workflows in all modes | Fifteen `training_task_metrics_with_inputs.tsv` files |
| 8 | Normalized runs | Aggregate five workflows | Common workflow summary |
| 9 | Common summary + GATK triplet | Build RQ1 figure inputs | Two committed-result CSV shapes populated |
| 10 | Figure input CSVs | Plot and verify | RQ1 PNG/PDF |

The single invariant connecting Steps 5–9 is `RUN_ROOT`. Changing it midway
breaks discovery of audit and normalized outputs. GATK follows a dedicated
combination path and must not be sent through the common five-workflow parser.

## Full RQ2 state sequence

| Step | Required input | Action | Required output before continuing |
|---:|---|---|---|
| 1 | Compressed 33,516-task cohort | Materialize canonical TSV | `DATA/RQ2/generated/canonical_matched_tasks.tsv` |
| 2 | Canonical TSV + primary manifests | Run seed-1996 offline experiment | A+P/A+P+C features, models, policies, task tables |
| 3 | Initial A+P+C result | Copy to global-base directory | Preserved route for later comparison |
| 4 | Primary result/models | Train final A+P+C process tails | Rewritten final A+P+C task table and policy |
| 5 | A+P and final A+P+C tables | Summarize paired outcomes | Overall/per-workflow/paired summaries |
| 6 | Primary task tables | Deterministically gzip and publish | Two 5,026-task result inputs |
| 7 | Five split manifests x five seeds | Repeat Steps 2–5 with explicit roots/counts | 25 complete summaries |
| 8 | All 25 summaries | Aggregate variance and route contracts | Three aggregate CSVs |
| 9 | Five committed RQ2 inputs | Run three plotters | Three RQ2 figures |

Do not run the aggregators on a partial matrix. Do not overwrite A+P+C before
copying its global-base form. Do not compare a mixed collection of global and
process-tail routes.

## Full RQ3 state sequence

| Step | Required input | Action | Required output before continuing |
|---:|---|---|---|
| 1 | Python 3.9 | Install pinned packages and deterministic thread variables | Functional environment |
| 2 | Network/Git | Clone and detach Sizey at the fixed commit | Exact revision check passes |
| 3 | Packaged CAMP cohort | Materialize source and prepare primary comparison cohort | 30,478 tasks and fixed manifests |
| 4 | Six Sizey inputs | Run and normalize Sizey per workflow | Six normalized prediction tables and run manifests |
| 5 | Harness outputs | Copy canonical cohort, split manifests, and Sizey outputs | CAMP comparison staging tree |
| 6 | Staged tree | Run preflight | All identity/population checks pass |
| 7 | Validated tree | Run CAMP A/A+P/A+P+C and process tails | Aligned CAMP task tables |
| 8 | All method tables | Summarize comparison | `global_metrics.csv` and diagnostics |
| 9 | Global summary | Publish and plot | RQ3 PNG/PDF |

The shared task IDs and assignments are the fairness boundary. Running the
methods on separately sampled populations invalidates the comparison even if
each individual program succeeds.

## Full RQ4 state sequence

| Step | Required input | Action | Required output before continuing |
|---:|---|---|---|
| 1 | Frozen compressed seed-1996 inputs | Expand with `prepare_inputs.py` | Generated aligned feature/prediction tree |
| 2 | Expanded inputs + scaler + policies | Fit/cross-fit gates and score tasks | Task gate-score table |
| 3 | Scores + fixed budgets | Run three equal-count policies for A+P/A+P+C | 36,000-row full repetition table |
| 4 | Full table | Filter A+P and five retained columns, deterministic gzip | 18,000-row committed table |
| 5 | Committed table | Plot and verify | RQ4 PNG/PDF |

At each budget and repetition, all three policies must select the same count.
At the 100% budget, every policy must have recall one. These are required
sanity checks, not optional interpretations.

## Full RQ5 state sequence

| Step | Required input | Action | Required output before continuing |
|---:|---|---|---|
| 1 | Packaged RQ2 cohort + RQ5 split | Prepare a fresh external workspace | 33,516-row canonical TSV and primary split |
| 2 | Workspace + pinned Python packages | Run APC-only unit tests | All policy, version, and learner-lifecycle tests pass |
| 3 | Canonical cohort + split | Rebuild seed-1996 models, policies, and history | Initial model, 28,490-row sidecar/SQLite, and workflow-cold model |
| 4 | Worker kernels and containers | Verify eBPF/BCC, `/usr/bin/time`, Apptainer, Bowtie2, and Samtools | All 12 task workers pass prerequisites |
| 5 | Initial model with zero Bowtie rows | Execute the cold run | 6,000 successful logical tasks and terminal 6,000-outcome model |
| 6 | Terminal cold model + post-run history | Execute the warm run in a new work directory | 6,000 successful logical tasks and warm task table |
| 7 | Cold/warm task tables | Deterministically gzip and plot | RQ5 PNG/PDF |

The warm run must use the exact terminal model and completed-history database
from its matching cold run. It must not use the cold Nextflow work directory,
`-resume`, a partially updated model, or a different cold repetition.

## Shared concepts versus shared files

| Connection | Shared concept | Practical file relationship |
|---|---|---|
| RQ1 to RQ2 | eBPF reads + mmap faults define consumed bytes | RQ2 uses the packaged matched cohort; it does not automatically consume a new `$RUN_ROOT` |
| RQ2 to RQ3 | Same A/P/C causal modeling and allocation logic | RQ3 has its own harness, staged cohort, scripts, and 21,337-task split |
| RQ2/RQ3 to RQ4 | Allocator predictions and causal features define audit risk/information | RQ4 uses frozen compressed seed-1996 feature and prediction tables |
| RQ2 to RQ5 | The six-workflow cohort initializes a model with no Bowtie2 leakage | RQ5 rebuilds the initial model/history from the packaged cohort and primary split |
| RQ5 cold to warm | Completed cold outcomes become strictly prior Bowtie2 evidence | The warm command reads the terminal cold model pointer and post-run history database |
| Every RQ to results | Generated summaries become compact figure inputs | Only selected CSV/TSV files and final figures live under `RESULTS` |

`SCRIPTS/RQ1/postprocessing/build_ml_ready_data.py` can construct a downstream
ML dataset from an expected completed-run layout, but it is not part of the
canonical RQ1 figure route and is not the documented replacement for the
packaged RQ2 cohort. Use it only when intentionally constructing a new cohort
and validate its layout, row population, and split contract separately.

## Generated versus committed state

Keep these outside version control or in ignored working paths:

- downloaded nf-core and Sizey repositories;
- raw sequencing files and Kraken2 databases;
- Nextflow work directories and container caches;
- expanded RQ2 and RQ4 inputs;
- fitted model directories;
- per-seed/per-split working results;
- full RQ4 diagnostic tables and temporary environments.
- RQ5 initial/updated model directories, raw Bowtie2 FASTQs, and live run roots.

Commit only the source/configuration/manifests already represented by the
artifact, the selected compact result tables, the final figures, and these
documents.

## Validation checklist

Before calling an RQ complete, verify all applicable points:

- commands were run from the artifact root unless a guide explicitly changes
  directory;
- fixed workflow/source releases match;
- expected task counts match before fitting or comparing;
- task IDs align between paired variants/methods;
- no current-task outcome appears in prelaunch features;
- Exp0/Exp1/Exp2 use separate work directories and matched resources;
- audit summary files exist before RQ1 normalization;
- the A+P+C global-base directory exists before process-tail replacement;
- all 25 RQ2 repeated summaries exist before aggregation;
- RQ3 preflight passes before CAMP execution;
- RQ4 equal-count and 100%-budget invariants hold;
- RQ5 cold history contains zero Bowtie2 rows before launch;
- RQ5 warm history contains exactly 6,000 Bowtie2 rows from its matching cold run;
- both RQ5 runs contain 6,000 successful logical tasks and use separate work directories;
- every final CSV/TSV is nonempty and has the documented row count;
- every expected PNG/PDF exists;
- packaged-result checksums are applied only to the packaged deterministic
  outputs they describe.

## Reading order for a new user

Read the theory-only connection guide first. Then read the data, workflows,
auditing, modeling, and gating component guides. Finally choose one RQ guide
and one of its two routes. This avoids requiring cluster knowledge for an
offline figure regeneration while still exposing the complete live and model
execution paths when needed.
