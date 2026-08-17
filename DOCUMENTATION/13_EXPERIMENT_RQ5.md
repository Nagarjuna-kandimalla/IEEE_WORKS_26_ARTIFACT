# Experiment RQ5: online feedback in a live Bowtie2 workflow

RQ5 asks whether CAMP can use outcomes collected during a cold Bowtie2 run to
make the first memory requests in a subsequent warm run substantially tighter
while retaining first-attempt safety.

## Artifact locations

```text
SCRIPTS/RQ5/README.md
SCRIPTS/RQ5/experiment_template/
SCRIPTS/RQ5/figures/generate_rq5_bowtie_feedback.py
DATA/RQ5/inputs/split_manifest_primary.tsv.gz
WORKFLOWS/bowtie2/
RESULTS/RQ5/csv/
RESULTS/RQ5/figures/fig_rq5_bowtie_feedback.{png,pdf}
```

Use the packaged-data or full Slurm command sequence in
[`../SCRIPTS/RQ5/README.md`](../SCRIPTS/RQ5/README.md).

## Experiment design

The retained experiment is the final APC-only Bowtie2 cold/warm `v4` pair:

```text
bowtie2_fresh_apc_online_cold_20260804_v4
bowtie2_fresh_apc_online_warm_20260804_v4
```

Each execution contains 6,000 logical tasks. The workflow aligns 6,000
public-data shards and includes the supporting download, reference, BAM
indexing, and flagstat processes. Both runs use the same workflow structure,
inputs, worker pool, allocation policy, and retry policy. They use separate
Nextflow work directories, and neither run uses Nextflow `-resume`.

The allocation policy is direct A+P+C:

- A and A+P predictions are computed only as shadow comparisons;
- only A+P+C selects the submitted memory request;
- a standalone A prediction is not used as a request or safety floor; and
- a failed first attempt remains part of the evidence even if an independent
  retry later succeeds.

The initial model and history contain 28,490 completed tasks from the six
non-Bowtie2 workflows. Bowtie2 is excluded from that initial training and
history, so the cold run starts with zero prior Bowtie2 outcomes. The learner
first updates after 128 completed tasks and then after each additional 128
completions. A newly published model affects only decisions made after its
publication; earlier task decisions are not changed.

The warm run starts from the terminal model produced after all 6,000 cold-run
outcomes have been incorporated. Thus, `cold` means no prior Bowtie2 history
at launch, whereas `warm` means one complete preceding Bowtie2 execution is
available. The two labels do not refer to Nextflow cache reuse.

## Completed-run evidence

| Quantity | Cold | Warm |
|---|---:|---:|
| Logical task instances | 6,000 | 6,000 |
| Successful logical tasks | 6,000 | 6,000 |
| Recorded attempts | 6,016 | 6,015 |
| Retried logical tasks | 16 | 15 |
| Recorded first-attempt OOMs | 0 | 0 |
| Aggregate observed peak memory | 238.4989 GiB | 238.8190 GiB |
| Aggregate first requested memory | 898.3857 GiB | 251.9268 GiB |
| Unused memory-time | 23.954478 GiB-h | 0.017716 GiB-h |

The nearly unchanged observed peak totals show that the cold and warm runs
execute comparable work. The large change is in CAMP's requested capacity,
not in the workflow's actual aggregate memory demand.

## Figure contents

The retained RQ5 figure contains three panels:

1. **Requested capacity:** aggregate observed peak memory beside aggregate
   first-requested memory for the cold and warm runs.
2. **Reservation wastage:** unused memory-time, plotted on a logarithmic
   scale.
3. **First-attempt margin:** the fraction of tasks whose initial memory
   request falls into each margin range relative to observed peak RSS.

The earlier end-to-end runtime panel is intentionally not part of this
version. The third panel instead shows how the distribution of individual
first requests changes after online feedback.

## First-attempt allocation margin

For a task with first request \(R\) and observed peak RSS \(P\), its margin is:

```text
margin_percent = ((R - P) / P) * 100
```

The calculation uses the request made before the task outcome was known. A
later retry or model update cannot revise this first decision. The figure
places every logical task into exactly one bucket:

| Bucket | Definition | Interpretation |
|---|---|---|
| Below peak | `R / P < 1.00` | Initial request was below the observed peak |
| 0–5% | `1.00 <= R / P <= 1.05` | Safe request with a very small margin |
| 5–10% | `1.05 < R / P <= 1.10` | Safe request with a modest margin |
| 10–25% | `1.10 < R / P <= 1.25` | Safe but more conservative request |
| Above 25% | `R / P > 1.25` | Substantial overallocation |

The exact distributions behind the third panel are:

| First-attempt margin | Cold tasks | Cold share | Warm tasks | Warm share |
|---|---:|---:|---:|---:|
| Below peak | 4 | 0.0667% | 13 | 0.2167% |
| 0–5% | 3,349 | 55.8167% | 3,961 | 66.0167% |
| 5–10% | 1,166 | 19.4333% | 1,603 | 26.7167% |
| 10–25% | 477 | 7.9500% | 407 | 6.7833% |
| Above 25% | 1,004 | 16.7333% | 16 | 0.2667% |
| **Total** | **6,000** | **100%** | **6,000** | **100%** |

`Below peak` is an allocation diagnostic, not automatically an observed OOM.
It compares the initial request with the task's recorded peak RSS. On this
cluster, Slurm memory requests act as reservations rather than enforced
memory limits. Consequently, the 4 cold and 13 warm below-peak cases coexist
with zero recorded first-attempt OOMs. The retry counts also must not be
interpreted as OOM counts because retries can result from independent
execution failures.

## Figure analysis

Panel (a) shows that aggregate observed peak memory stays essentially stable:
238.50 GiB in the cold run and 238.82 GiB in the warm run. Aggregate requested
memory falls from 898.39 GiB to 251.93 GiB, a reduction of 646.46 GiB or about
72.0%. The warm request is only about 5.5% above the aggregate observed peak,
whereas the cold request is approximately 3.77 times the aggregate peak.

Panel (b) shows an even larger reduction after accounting for task duration.
Unused memory-time falls from 23.954478 GiB-h to 0.017716 GiB-h, a reduction
of about 99.93%, or approximately 1,352 times less wastage. The logarithmic
axis is necessary because the cold and warm values differ by more than three
orders of magnitude.

Panel (c) explains how the reduction is achieved. In the cold run, 75.25% of
tasks are requested within 10% of their observed peak. In the warm run this
share rises to 92.73%, an improvement of 17.48 percentage points. The share
above a 25% margin collapses from 16.73% (1,004 tasks) to 0.27% (16 tasks).
This is direct evidence that completed Bowtie2 outcomes make subsequent
requests much more concentrated around actual task demand.

The tighter warm distribution has a small safety trade-off: below-peak cases
increase from 4 to 13 tasks, or from 0.07% to 0.22%. First-request coverage is
therefore 99.93% for cold and 99.78% for warm. Nevertheless, all 6,000 tasks
complete successfully in both runs and neither run records a first-attempt
OOM. RQ5 therefore demonstrates a large reduction in reservation waste while
retaining very high first-request coverage.

## Interpretation

The cold-to-warm comparison isolates online feedback from cache reuse and
from a change in workload size. The warm allocator has access to completed
Bowtie2 behavior learned from the cold execution, while the cold allocator
does not. The stable observed demand, 72.0% reduction in requested capacity,
99.93% reduction in unused memory-time, and movement of tasks into the 0–10%
margin buckets collectively show that CAMP's live updates improve allocation
precision for the next execution.

These results establish improvement for the retained Bowtie2 cold/warm pair.
They should not be interpreted as a confidence interval over repeated workflow
runs or as proof that every future input distribution will behave identically.
