# Data

This README describes inputs; it is not an additional execution stage. Start
the packaged-results route or the full experiment route in
[`../README.md`](../README.md). The RQ2 commands are ordered in
[`../SCRIPTS/README.md`](../SCRIPTS/README.md), and the RQ4 commands are ordered
in [`../SCRIPTS/RQ4/README.md`](../SCRIPTS/RQ4/README.md). RQ5 workspace,
model-build, and live-run commands are ordered in
[`../SCRIPTS/RQ5/README.md`](../SCRIPTS/RQ5/README.md).

## RQ2 matched cohort

`RQ2/camp_ml_cohort_33516.csv.gz` is the compressed six-workflow cohort used
by the offline A+P and A+P+C experiment. It contains 33,516 logical tasks:

```text
GATK               500
Minimap2         6,001
Sarek            6,012
Seqinspector     6,001
Taxprofiler      5,999
Viralmetagenome  9,003
```

The experiment uses static task fields, peak RSS, runtime, causal timestamps, and
the eBPF consumed-data signal. The consumed-data field is the sum of successful
read-family return bytes and mmap page-fault bytes.

The file remains compressed in the artifact. Materialize the tab-separated
input expected by the experiment with:

```bash
.venv-rq2/bin/python SCRIPTS/RQ2/materialize_offline_data.py \
  --source DATA/RQ2/camp_ml_cohort_33516.csv.gz \
  --output DATA/RQ2/generated/canonical_matched_tasks.tsv
```

The resulting TSV has 33,516 rows. The packaged split manifests assign 28,490
rows to development and 5,026 to the chronological test set. The generated
TSV is an intermediate file and does not need to be committed.

## RQ4 selective-auditing inputs

`RQ4/inputs/seed_1996` contains compressed A+P and A+P+C prediction tables and
causal features used by the selective-auditing evaluation: 9,141 initial tasks
and 21,337 experiment tasks. See `RQ4/README.md` and
`SCRIPTS/RQ4/README.md` for the expansion and evaluation commands.

## RQ5 online-feedback inputs

`RQ5/inputs/split_manifest_primary.tsv.gz` retains the exact seed-1996
train/calibration/test assignment used to build the initial online model. The
model build combines it with the existing 33,516-task RQ2 cohort. The 6,000
Bowtie2 task graph is constructed from the HTTPS manifest under
`WORKFLOWS/bowtie2/data`.
