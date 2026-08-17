# Component: auditing and consumption signals

RQ1 compares two full-audit mechanisms with an unaudited baseline. This
component also defines the consumed-data label used later by CAMP.

## Signal definitions

### STRACE

The process-targeted STRACE wrapper follows descendants and traces:

```text
read, write,
pread64, pwrite64,
readv, writev,
preadv, pwritev,
mmap, mmap2
```

Read bytes are the sum of positive return values from `read`, `pread64`,
`readv`, and `preadv`. Write bytes are calculated analogously. Failed calls
add zero bytes. An unfinished syscall and its resumed line count once. STRACE
records `mmap`/`mmap2` invocation counts, not the bytes later touched through
the mapping.

The STRACE consumption value is therefore:

```text
STRACE consumed bytes = successful read-family return bytes
```

The wrapper uses `-ff`, retains one file per traced PID, combines those files
deterministically, preserves the task exit code, records the trace-file count,
and removes node-local temporary trace data.

### eBPF

The retained tracer follows descendants with `sched_process_fork`, tracks
`openat`/`openat2` descriptor mappings, sums positive `read` and `pread64`
returns, retires descriptors at `close`, and attributes file-backed mappings
through `filemap_fault`. Each attributable fault adds 4,096 bytes in the
preserved implementation.

The eBPF/CAMP consumption value is:

```text
eBPF consumed bytes = read return bytes + file-backed mmap page-fault bytes
```

The two audit signals are not interchangeable: only eBPF includes mmap
consumption in bytes.

## Installation and permission boundary

STRACE must be installed on every possible worker. Tracing a child process as
the same user normally needs neither root nor `CAP_SYS_PTRACE`, but site
security and the container runtime must permit `ptrace`.

eBPF requires BCC, Python BCC bindings, matching kernel headers, supported
kernel events, and elevated or delegated tracing privileges. Validate the
actual compute-node environment, not only the login node. The canonical
installation and smoke tests are in [`../AUDIT/README.md`](../AUDIT/README.md).

Do not run the whole scheduler client as root merely to gain BPF access. Use
the site's approved privileged helper, capabilities, or job configuration.

## Helper composition

The audit directory contains:

```text
AUDIT/helper_scripts/exp0/
  baseline.config
  gatk.config
  minimap2.config

AUDIT/helper_scripts/exp1/
  strace.config
  targeted_strace.config
  gatk.config
  minimap2.config
  parse_strace_logs.py

AUDIT/helper_scripts/exp2/
  ebpf.config
  targeted_ebpf.config
  gatk.config
  minimap2.config
  ebpf_audit_py39.py
  run_with_optional_audit.py
  summarize_ebpf.py
  task_plan_loader.py
```

Generic nf-core workflows use `baseline.config`, `strace.config`, or
`ebpf.config`. The custom workflows use the workflow-named configuration. A
single matching overlay is placed after the workflow configuration in the
Nextflow command.

## STRACE task output

For a selected task, Exp1 writes:

```text
results/strace/<process>/task_<task-hash>/
  process_name.txt
  strace_file_count.txt
  strace.log.combined
```

After the run, the parser writes a tabular task summary. When a Nextflow trace
is supplied with `--successful-only`, it restricts the result to completed or
cached tasks and removes failed retry attempts:

```bash
python3 AUDIT/helper_scripts/exp1/parse_strace_logs.py \
  --root "$run_dir/results/strace" \
  --out "$run_dir/results/metrics/strace_task_summary.tsv" \
  --trace "$run_dir/nextflow_trace.tsv" \
  --successful-only
```

## eBPF task output

Exp2 writes task-level tracer output under `results/ebpf`. The summarizer
combines it into:

```bash
python3 AUDIT/helper_scripts/exp2/summarize_ebpf.py \
  --root "$run_dir/results/ebpf" \
  --out "$run_dir/results/metrics/ebpf_task_summary.tsv"
```

The summary distinguishes read-return bytes, mmap page-fault bytes, and their
sum. `ebpf_total_consumed_bytes` is the downstream `C` label.

## Six-workflow summarization contract

Minimap2, Sarek, Seqinspector, Taxprofiler, and Viralmetagenome use the common
STRACE and eBPF summarizers. GATK writes audit measurements directly into
`results/metrics/haplotypecaller_task_metrics.tsv` and is read later by the
RQ1 combiner.

After all modes finish, the required common outputs are:

```text
$RUN_ROOT/<workflow>/exp1/results/metrics/strace_task_summary.tsv
$RUN_ROOT/<workflow>/exp2/results/metrics/ebpf_task_summary.tsv
```

These files are inputs to `normalize_nextflow_baseline_trace.py`. The
normalizer combines trace timing/resource fields, static-manifest identity,
and audit summaries; the enrichment step adds staged input sizes.

## What RQ1 evaluates

The final signal table reports STRACE read GiB, eBPF read GiB, eBPF mmap-fault
GiB, total eBPF GiB, mmap byte share, and coverage. The overhead table reports
baseline, STRACE, and eBPF lifecycle times and percentage overhead.

The retained Sarek run crossed resumed sessions and lacks a valid direct
STRACE makespan in its common summary. Its packaged overhead input uses a
separately reconstructed active runtime. Fresh non-resumed runs use direct
timings for all three modes.

## Connection to later CAMP stages

RQ1 demonstrates the measurement difference and overhead. RQ2 does not rerun
the tracer: it consumes the frozen matched cohort containing the eBPF fields.
Historical eBPF consumption feeds `C` and trains the current-task `C-hat`
model. RQ4 then asks how to choose a subset of future tasks for auditing so
that useful consumption history can be acquired under a fixed budget.
