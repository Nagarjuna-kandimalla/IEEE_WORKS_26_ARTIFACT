# Audit setup

This artifact uses two full-audit modes:

- **Experiment 1 (STRACE):** records selected read, write, and memory-mapping
  system calls for every task.
- **Experiment 2 (eBPF):** records successful read bytes and file-backed mmap
  page-fault bytes for every task.

The two signals are not interchangeable. STRACE records `mmap` and `mmap2`
as invocation counts; it does not infer bytes consumed through a mapping.
Experiment 2 obtains mmap consumption in bytes from `filemap_fault` events.

## Position in the RQ1 end-to-end route

Use this README after preparing the workflows and datasets in
[`WORKFLOWS/README.md`](../WORKFLOWS/README.md):

1. install and smoke-test STRACE and BCC/eBPF on every Slurm worker;
2. return to `WORKFLOWS/README.md` and run all six Exp0/Exp1/Exp2 triplets;
3. return here and run **Summarize the completed audits** below; and
4. continue with the numbered RQ1 postprocessing steps in
   [`SCRIPTS/README.md`](../SCRIPTS/README.md).

Keep the same `RUN_ROOT` for workflow execution, audit summarization, and
postprocessing.

## Install STRACE

Install STRACE on every worker node that may execute an audited task.

Debian or Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y strace
```

RHEL, Rocky Linux, AlmaLinux, Fedora, or recent Amazon Linux:

```bash
sudo dnf install -y strace
```

Older RHEL-compatible systems:

```bash
sudo yum install -y strace
```

Verify the installation on a worker node:

```bash
command -v strace
strace --version
strace -f -e trace=read,write,mmap -o /tmp/strace-smoke.log \
  sh -c 'printf test >/tmp/strace-smoke-data; cat /tmp/strace-smoke-data >/dev/null'
test -s /tmp/strace-smoke.log
```

The preserved experiments do not pin a particular STRACE release. Record the
output of `strace --version` in the run manifest when reproducing them.

## Permissions and runtime requirements

The Exp 1 wrapper starts the task as a child of STRACE under the same user.
This normally does not require root or `CAP_SYS_PTRACE`. On a restricted
cluster, confirm that:

- `strace` is installed on the compute nodes, not only the login node;
- the task user may trace child processes;
- the container runtime or site security policy does not block `ptrace`;
- `${SLURM_TMPDIR}` is writable, or `/tmp` is available as a fallback; and
- the final results directory is writable from the compute node.

Run the smoke command above inside the same Slurm/container environment used
for workflow tasks. A login-node success does not validate compute nodes.

## Experiment 1 syscall contract

The final process-targeted Exp 1 overlay uses the following exact set:

```text
read, write,
pread64, pwrite64,
readv, writev,
preadv, pwritev,
mmap, mmap2
```

The executed command is equivalent to:

```bash
strace -ff -o "$TRACE_DIR/strace.log" \
  -e trace=read,write,pread64,pwrite64,readv,writev,preadv,pwritev,mmap,mmap2 \
  -e raw=read,write,pread64,pwrite64,readv,writev,preadv,pwritev,mmap,mmap2 \
  bash -c nxf_launch_orig
```

`-ff` follows forked/created processes and writes one `strace.log.<pid>` file
per traced process. The workflow wrapper preserves the task's exit status,
counts the trace files, combines them deterministically, and removes the
node-local temporary trace directory.

### Parser semantics

The Exp 1 parser classifies calls as follows:

| Output | Included calls | Calculation |
|---|---|---|
| `strace_read_calls` | `read`, `pread64`, `readv`, `preadv` | Number of completed calls |
| `strace_read_bytes` | same read family | Sum of positive syscall return values |
| `strace_write_calls` | `write`, `pwrite64`, `writev`, `pwritev` | Number of completed calls |
| `strace_write_bytes` | same write family | Sum of positive syscall return values |
| `strace_mmap_calls` | `mmap`, `mmap2` | Invocation count only |

Failed read/write calls contribute zero bytes. An unfinished call and its
`<... resumed>` line are counted once, using the return value from the resumed
line. For Exp 1, the consumed-data field is:

```text
consumed bytes = strace_read_bytes
```

Do not add `strace_mmap_calls` to this byte value.

## Nextflow Exp 1 setup

The working experiments enable STRACE through an additive Nextflow overlay:

```groovy
includeConfig 'helper_scripts/exp1/targeted_strace.config'

params {
    instrumentation      = 'strace'
    strace_enabled       = true
    strace_process_regex = '.*'
}
```

Use a narrower Java regular expression for `strace_process_regex` when only
selected Nextflow processes should be traced. The overlay checks for `strace`
before executing the task and exits with an explicit error when it is absent.

Expected task-level layout:

```text
results/strace/<process>/task_<task-hash>/
  process_name.txt
  strace_file_count.txt
  strace.log.combined
```

Parse a completed run with the preserved parser:

```bash
python3 AUDIT/helper_scripts/exp1/parse_strace_logs.py \
  --root results/strace \
  --out results/metrics/strace_task_summary.tsv
```

When a Nextflow trace is available, restrict the output to successful or
cached tasks:

```bash
python3 AUDIT/helper_scripts/exp1/parse_strace_logs.py \
  --root results/strace \
  --out results/metrics/strace_task_summary.tsv \
  --trace nextflow_trace.tsv \
  --successful-only
```

## Experiment 2 eBPF event contract

The retained Exp 2 tracer uses these kernel events:

- `sched_process_fork` to follow descendants of the selected task;
- entry/exit tracepoints for `openat` and `openat2` to map descriptors to
  filenames;
- entry/exit tracepoints for `read` and `pread64` to sum positive return
  values;
- the `close` entry tracepoint to retire descriptor mappings; and
- a kprobe on `filemap_fault`, adding one page (4,096 bytes in the preserved
  implementation) for each attributable file-backed page fault.

Its consumed-data contract is:

```text
eBPF consumed bytes = read return bytes + file-backed mmap page-fault bytes
```

Unlike STRACE, the Exp 2 tracer requires BCC/eBPF support, matching kernel
headers, and elevated tracing privileges. Its preserved Debian/Ubuntu setup is:

```bash
sudo apt-get install -y bpfcc-tools python3-bpfcc "linux-headers-$(uname -r)"
```

Verify the tracer environment on every worker:

```bash
export EBPF_PYTHON=/usr/bin/python3
sudo -n true
sudo -n "$EBPF_PYTHON" -c 'from bcc import BPF'
```

## How the helpers connect to workflow execution

The canonical Nextflow commands are in **Run all nf-core workflows with the
audit helpers** and **Run the custom workflows with the audit helpers** in
[`WORKFLOWS/README.md`](../WORKFLOWS/README.md). Those commands add exactly one
matching overlay after the workflow configuration: `exp0/baseline.config`,
`exp1/strace.config`, or `exp2/ebpf.config` for nf-core; and the corresponding
workflow-named configuration for GATK and Minimap2.

Do not reuse a work directory between modes and do not add `-resume` when
measuring Exp0/Exp1/Exp2 overhead.

## Summarize the completed audits

Run the following only after all six workflow triplets have completed. Start
from the artifact root and reuse the `RUN_ROOT` from workflow execution:

```bash
artifact_root="$PWD"
RUN_ROOT="${RUN_ROOT:-$artifact_root/RQ1_RUNS}"
```

After all Exp1 runs complete, create the successful-task summaries with:

```bash
for workflow in minimap2 sarek seqinspector taxprofiler viralmetagenome; do
  run_dir="$RUN_ROOT/$workflow/exp1"
  python3 AUDIT/helper_scripts/exp1/parse_strace_logs.py \
    --root "$run_dir/results/strace" \
    --out "$run_dir/results/metrics/strace_task_summary.tsv" \
    --trace "$run_dir/nextflow_trace.tsv" \
    --successful-only
done
```

After all Exp2 runs complete, summarize the eBPF output with:

```bash
for workflow in minimap2 sarek seqinspector taxprofiler viralmetagenome; do
  run_dir="$RUN_ROOT/$workflow/exp2"
  python3 AUDIT/helper_scripts/exp2/summarize_ebpf.py \
    --root "$run_dir/results/ebpf" \
    --out "$run_dir/results/metrics/ebpf_task_summary.tsv"
done
```

GATK writes its task-level STRACE or eBPF values directly into
`results/metrics/haplotypecaller_task_metrics.tsv`; the RQ1 combiner reads
that file without the common normalizer.

Confirm that both summaries exist for the five commonly normalized workflows,
then continue directly with **RQ1 Step 1: normalize and enrich** in
[`SCRIPTS/README.md`](../SCRIPTS/README.md):

```bash
for workflow in minimap2 sarek seqinspector taxprofiler viralmetagenome; do
  test -s "$RUN_ROOT/$workflow/exp1/results/metrics/strace_task_summary.tsv"
  test -s "$RUN_ROOT/$workflow/exp2/results/metrics/ebpf_task_summary.tsv"
done
```
