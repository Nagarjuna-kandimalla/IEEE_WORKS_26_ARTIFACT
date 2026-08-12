#!/usr/bin/env python3
"""Run one GATK scatter task and write static plus runtime metrics."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def size(path: Path) -> int:
    return path.stat().st_size if path.exists() else -1


def parse_time_peak_kb(path: Path) -> int:
    if not path.exists():
        return -1
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        normalized = line.strip()
        if normalized.startswith("Maximum resident set size (kbytes):"):
            return int(normalized.rsplit(":", 1)[1].strip())
    return -1


def strace_counts(prefix: Path) -> tuple[int, int, int, int]:
    mmap_calls = 0
    read_calls = 0
    read_bytes = 0
    write_calls = 0
    for path in prefix.parent.glob(prefix.name + "*"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.search(r"(?:^|\s)mmap2?\(", line):
                mmap_calls += 1
            if re.search(r"(?:^|\s)(?:read|pread64|readv|preadv)\(|<\.\.\. (?:read|pread64|readv|preadv) resumed>", line):
                read_calls += 1
                result = re.search(r"\)\s+=\s+(-?\d+)(?:\s|$)", line)
                if result:
                    read_bytes += max(0, int(result.group(1)))
            if re.search(r"(?:^|\s)(?:write|pwrite64|writev|pwritev)\(", line):
                write_calls += 1
    return mmap_calls, read_calls, read_bytes, write_calls


def cgroup_peak_bytes() -> int:
    """Read a Slurm task cgroup-v2 peak; never use a shared local session peak."""
    if not os.environ.get("SLURM_JOB_ID"):
        return -1
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            hierarchy, controllers, relative_path = line.split(":", 2)
            if hierarchy == "0" and controllers == "":
                cgroup_relative = relative_path.lstrip("/")
                if not cgroup_relative:
                    return -1
                peak = Path("/sys/fs/cgroup") / cgroup_relative / "memory.peak"
                return int(peak.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    return -1


def stable_task_hash(args: argparse.Namespace, reference_size: int) -> str:
    identity = "|".join([
        "gatk_synthetic_nextflow", "HaplotypeCaller", str(args.sample_id), str(args.coverage), str(args.seed),
        str(args.scatter_id), str(args.chrom), str(args.start), str(args.end), str(reference_size),
        str(args.java_heap_mb), str(args.threads),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def load_ebpf_metrics(root: Path) -> dict[str, str]:
    defaults = {
        "ebpf_audit_plan_flag": "NoAudit",
        "ebpf_audit_requested": "false",
        "ebpf_audit_applied": "false",
        "ebpf_file_read_bytes": "0",
        "ebpf_file_mmap_bytes": "0",
        "ebpf_file_total_bytes": "0",
        "ebpf_audit_status": "not_requested",
        "ebpf_attribution_tsv_path": "",
        "ebpf_audit_error": "",
    }
    metric_files = sorted((root / "metrics" / "tasks").rglob("*.json")) if (root / "metrics" / "tasks").exists() else []
    if not metric_files:
        return defaults
    payload = json.loads(metric_files[0].read_text(encoding="utf-8"))
    defaults.update({
        "ebpf_audit_plan_flag": str(payload.get("audit_flag", "")),
        "ebpf_audit_requested": str(payload.get("audit_requested", False)).lower(),
        "ebpf_audit_applied": str(payload.get("audit_applied", False)).lower(),
        "ebpf_file_read_bytes": str(payload.get("ebpf_read_bytes", 0)),
        "ebpf_file_mmap_bytes": str(payload.get("ebpf_mmap_bytes", 0)),
        "ebpf_file_total_bytes": str(payload.get("ebpf_total_bytes", 0)),
        "ebpf_audit_status": str(payload.get("status", "")),
        "ebpf_attribution_tsv_path": str(payload.get("ebpf_tsv_path", "")),
        "ebpf_audit_error": str(payload.get("error", "")),
    })
    return defaults


def write_metrics(path: Path, values: dict[str, str | int | float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["field", "value"])
        for key in sorted(values):
            writer.writerow([key, values[key]])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--scatter-id", required=True)
    parser.add_argument("--chrom", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--contig-length", required=True)
    parser.add_argument("--has-str-region", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--bam", required=True)
    parser.add_argument("--bai", required=True)
    parser.add_argument("--gatk-jar", required=True)
    parser.add_argument("--java-heap-mb", type=int, required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--instrumentation", choices=["none", "strace", "ebpf", "selective"], required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--task-plan", default="")
    parser.add_argument("--ebpf-python", default="")
    parser.add_argument("--ebpf-tracer-python", default="")
    parser.add_argument("--ebpf-runner", default="")
    parser.add_argument("--ebpf-script", default="")
    parser.add_argument("--online-logical-task-id", default="")
    parser.add_argument("--online-decision-time", default="")
    parser.add_argument("--online-static-prediction-mb", type=float, default=0.0)
    parser.add_argument("--online-history-prediction-mb", type=float, default=0.0)
    parser.add_argument("--online-allocation-mb", type=float, default=0.0)
    parser.add_argument("--online-model-version", default="")
    parser.add_argument("--online-system-config-id", default="")
    parser.add_argument(
        "--workflow-version",
        default="gatk-4.5.0.0+custom-nextflow",
    )
    parser.add_argument("--result-dir", required=True)
    args = parser.parse_args()

    result_dir = Path(args.result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    interval = result_dir / "interval.interval_list"
    interval.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        f"@SQ\tSN:{args.chrom}\tLN:{args.contig_length}\n"
        f"{args.chrom}\t{args.start}\t{args.end}\t+\tinterval_{args.scatter_id}\n",
        encoding="utf-8",
    )

    task_id = f"{args.sample_id}.scatter_{args.scatter_id}"
    output_vcf = result_dir / f"{task_id}.g.vcf.gz"
    stdout_path = result_dir / "gatk.stdout.log"
    stderr_path = result_dir / "gatk.stderr.log"
    time_path = result_dir / "resource.time.txt"
    command_path = result_dir / "command.json"
    strace_prefix = result_dir / "strace.log"
    audit_root = result_dir / "audit"
    tmp_dir = result_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    gatk_command = [
        "java", f"-Xmx{args.java_heap_mb}m", "-jar", args.gatk_jar,
        "HaplotypeCaller", "-R", args.reference, "-I", args.bam, "-L", str(interval),
        "-O", str(output_vcf), "-ERC", "GVCF", "--native-pair-hmm-threads", str(args.threads),
        "--tmp-dir", str(tmp_dir), "--verbosity", "ERROR", "--QUIET", "true",
    ]
    timed_command = ["/usr/bin/time", "-v", "-o", str(time_path)] + gatk_command
    command = timed_command
    if args.instrumentation == "strace":
        command = [
            "strace", "-ff", "-o", str(strace_prefix),
            "-e", "trace=read,write,pread64,pwrite64,readv,writev,preadv,pwritev,mmap,mmap2",
        ] + timed_command
    elif args.instrumentation in ("ebpf", "selective"):
        if not args.task_plan:
            plan = result_dir / "all_audit_plan.csv"
            plan.write_text(
                "task_type,task_instance,predicted_memory,audit_flag\n"
                f"HaplotypeCaller,{task_id},{args.java_heap_mb},Audit\n",
                encoding="utf-8",
            )
        else:
            plan = Path(args.task_plan)
        if not (args.ebpf_python and args.ebpf_runner and args.ebpf_script and args.ebpf_tracer_python):
            raise ValueError("eBPF paths are required for eBPF and selective instrumentation")
        command = [
            args.ebpf_python, args.ebpf_runner,
            "--task-plan", str(plan), "--task-type", "HaplotypeCaller", "--task-instance", task_id,
            "--metrics-dir", str(audit_root / "metrics"), "--ebpf-outdir", str(audit_root / "ebpf"),
            "--ebpf-script", args.ebpf_script,
            "--ebpf-tracer-python", args.ebpf_tracer_python,
            "--",
        ] + timed_command

    command_path.write_text(json.dumps(command, indent=2) + "\n", encoding="utf-8")
    started = time.time()
    decision_time = (
        args.online_decision_time
        or datetime.fromtimestamp(started, timezone.utc).isoformat()
    )
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
    runtime_seconds = time.time() - started
    completion_time = datetime.now(timezone.utc).isoformat()

    mmap_calls, read_calls, read_bytes, write_calls = strace_counts(strace_prefix)
    ebpf = load_ebpf_metrics(audit_root)
    reference_size = size(Path(args.reference))
    bam_size = size(Path(args.bam))
    bai_size = size(Path(args.bai))
    interval_size = size(interval)
    input_bytes_total = sum(max(0, value) for value in (reference_size, bam_size, bai_size, interval_size))
    if args.instrumentation == "strace":
        observed_c_bytes: str | int = read_bytes
        observed_c_source = "strace_read_syscalls"
    elif args.instrumentation in ("ebpf", "selective") and ebpf["ebpf_audit_applied"] == "true":
        observed_c_bytes = ebpf["ebpf_file_total_bytes"]
        observed_c_source = "ebpf_file_access"
    else:
        # Baseline and non-audited selective tasks have no observed c signal.
        observed_c_bytes = ""
        observed_c_source = "not_observed"
    peak_rss_kb = parse_time_peak_kb(time_path)
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace").lower()
    metrics: dict[str, str | int | float] = {
        "workflow": "gatk_synthetic_nextflow",
        "dataset_id": args.dataset_id,
        "static_manifest_path": "results/manifests/static_task_manifest.tsv",
        "process": "HaplotypeCaller",
        "bucket": "GATK::HaplotypeCaller",
        "task_type": "HaplotypeCaller",
        "task_instance": task_id,
        "task_hash": stable_task_hash(args, reference_size),
        "sample_id": args.sample_id,
        "coverage": args.coverage,
        "seed": args.seed,
        "scatter_id": args.scatter_id,
        "chrom": args.chrom,
        "start": args.start,
        "end": args.end,
        "interval_length_bp": int(args.end) - int(args.start) + 1,
        "contig_length_bp": args.contig_length,
        "has_str_region": args.has_str_region,
        "reference_size_bytes": reference_size,
        "bam_size_bytes": bam_size,
        "bai_size_bytes": bai_size,
        "interval_file_size_bytes": interval_size,
        "input_bytes_total": input_bytes_total,
        "a_bytes": input_bytes_total,
        "requested_java_heap_mb": args.java_heap_mb,
        "requested_threads": args.threads,
        "workflow_version": args.workflow_version,
        "instrumentation_mode": args.instrumentation,
        "online_logical_task_id": args.online_logical_task_id,
        "online_decision_time": decision_time,
        "completion_time": completion_time,
        "online_static_prediction_mb": args.online_static_prediction_mb,
        "online_history_prediction_mb": args.online_history_prediction_mb,
        "online_allocated_memory_mb": args.online_allocation_mb,
        "online_model_version": args.online_model_version,
        "online_system_config_id": args.online_system_config_id,
        "runtime_seconds": round(runtime_seconds, 6),
        "exit_code": completed.returncode,
        "oom_suspected": str(completed.returncode in (9, 137) or "outofmemory" in stderr_text or "out of memory" in stderr_text).lower(),
        "peak_rss_kb": peak_rss_kb,
        "M_peak_rss_bytes": peak_rss_kb * 1024 if peak_rss_kb >= 0 else -1,
        "M_cgroup_peak_bytes": cgroup_peak_bytes(),
        "strace_mmap_syscall_count": mmap_calls,
        "strace_read_syscall_count": read_calls,
        "strace_read_syscall_bytes": read_bytes,
        "strace_write_syscall_count": write_calls,
        "observed_c_bytes": observed_c_bytes,
        "observed_c_source": observed_c_source,
        "output_vcf_size_bytes": size(output_vcf),
        "gatk_jar": args.gatk_jar,
        "python_version": sys.version.split()[0],
    }
    metrics.update(ebpf)
    write_metrics(result_dir / "task_metrics.tsv", metrics)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
