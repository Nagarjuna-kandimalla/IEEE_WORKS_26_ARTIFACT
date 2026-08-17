# Bowtie2 live workflow for RQ5

This custom Nextflow workflow is the RQ5 live cold/warm case. It downloads 38
paired public ENA runs, splits them according to the retained manifest, aligns
the resulting shards with Bowtie2 2.4.2, sorts and indexes BAM files with
Samtools 1.20, and runs flagstat. The emitted graph contains 6,000 logical
tasks.

The exact input list is:

```text
data/public_runs_prjeb11501_online_unseen_6000.tsv
```

`DOWNLOAD_RUN` streams every `fastq1_url` and `fastq2_url` over HTTPS. The
reference is streamed from Ensembl release 116 by the command recorded in
`SCRIPTS/RQ5/README.md`. Compute nodes therefore need outbound HTTPS access.

The complete sequence—including container download, initial-model build,
eBPF setup, cold execution, warm execution, result placement, and figure
generation—is in [`../../SCRIPTS/RQ5/README.md`](../../SCRIPTS/RQ5/README.md).
Do not invoke this workflow without that online runner: the runner supplies
the prediction service, causal history, learner, eBPF feedback, and A+P+C
memory closure required by RQ5.
