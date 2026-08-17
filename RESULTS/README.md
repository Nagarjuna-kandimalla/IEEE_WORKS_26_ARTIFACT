# Results

This README identifies and verifies the final files. To regenerate every
figure sequentially from these packaged CSV/TSV files, start at **Path A** in
[`../README.md`](../README.md). To recreate the CSV/TSV files from experiment
execution, follow **Path B** there instead.

The RQ1 result files are organized as follows:

```text
RQ1/csv/rq1_workflow_overhead.csv
RQ1/csv/sarek_reconstructed_active_runtime.csv
RQ1/csv/strace_vs_ebpf_workflow_summary.csv
RQ1/figures/fig_rq1_signal_overhead.png
```

`strace_vs_ebpf_workflow_summary.csv` supplies the eBPF explicit-read and
mmap-fault measurements. `rq1_workflow_overhead.csv` supplies the STRACE and
eBPF runtime increases used by the second panel.
`sarek_reconstructed_active_runtime.csv` is the retained resumed-session
timing input used by the post-run combiner. The combiner and its exact
verification command are documented in `SCRIPTS/README.md`.

The RQ1 plotting script and the package versions used by it are included under
`SCRIPTS/RQ1/figures`. Run the following commands from the artifact root:

```bash
rq1_environment="${TMPDIR:-/tmp}/camp-rq1-environment"
python3 -m venv "$rq1_environment"
"$rq1_environment/bin/python" -m pip install \
  -r SCRIPTS/RQ1/figures/requirements.txt
"$rq1_environment/bin/python" \
  SCRIPTS/RQ1/figures/make_rq1_signal_overhead.py
```

The script reads the two figure-input files in `RESULTS/RQ1/csv` and writes
both PDF and PNG outputs into `RESULTS/RQ1/figures`. Confirm the packaged PNG
with:

```bash
echo 'ddbb37e2d3a47a503fdc7435be89c98649113875a833768f9dc10938db2cab00  RESULTS/RQ1/figures/fig_rq1_signal_overhead.png' |
  sha256sum -c -
```

## RQ2 offline experiment

The packaged RQ2 inputs and outputs are:

```text
RQ2/csv/a_plus_p_task_predictions.tsv.gz
RQ2/csv/a_plus_p_plus_c_task_predictions.tsv.gz
RQ2/csv/repetition_allocator_metrics.csv
RQ2/csv/camp_only_seed_variance_summary.csv
RQ2/csv/route_contract_repetition_effects.csv
RQ2/figures/fig_camp_only_seed_variance_underallocations.{png,pdf}
RQ2/figures/fig_offline_seed_variance_underallocations.{png,pdf}
RQ2/figures/fig_rq2_allocation_tradeoff.png
```

The two compressed prediction tables each contain the same 5,026
chronological test tasks. `a_plus_p_task_predictions.tsv.gz` uses static and
prior peak-RSS information. `a_plus_p_plus_c_task_predictions.tsv.gz` adds
prior consumed-data information and predicted current consumption.

An underallocation is a task whose first memory request is below its recorded
peak RSS. The frozen A+P table contains 87 underallocations and the A+P+C table
contains 31; both contain zero unresolved tasks after the retry policy. These
are offline allocation diagnostics, not observed scheduler OOM exits.

`repetition_allocator_metrics.csv` contains Access and CAMP metrics for five
seeds over five development/holdout splits. The CAMP-only summary supplies
the mean, sample variation, and 95% intervals. The route-contract table
supplies the Access-versus-CAMP split-testing figure.

Confirm the three packaged PNG files with:

```bash
echo '31dfe2052c4ba2a8ba0bd1828db0173806e7566b32ed9193473e8b1c2e1dc3cf  RESULTS/RQ2/figures/fig_camp_only_seed_variance_underallocations.png' |
  sha256sum -c -
echo '41f7e941b33ea68b1c4decf33bbaf8e8cebc0f5cd7b1750688e8feb29a2f9bb6  RESULTS/RQ2/figures/fig_offline_seed_variance_underallocations.png' |
  sha256sum -c -
echo '4ddb9e460f9f321ce73435435fe831e59447a123802bfc6d44eb1317b934faf7  RESULTS/RQ2/figures/fig_rq2_allocation_tradeoff.png' |
  sha256sum -c -
```

The corresponding commands and pinned dependencies are in
`SCRIPTS/RQ2/figures`. The complete 25-run generation and aggregation commands
are documented in `SCRIPTS/README.md`.

## RQ3 Sizey versus CAMP

```text
RQ3/csv/global_metrics.csv
RQ3/figures/fig_sizey_vs_camp.pdf
RQ3/figures/fig_sizey_vs_camp.png
```

`global_metrics.csv` is the table read by the Sizey-versus-CAMP figure script.
The complete download, execution, summarization, and figure commands are in
`SCRIPTS/RQ3/README.md`.

Confirm the packaged PNG with:

```bash
echo 'ce723e0b25f5eefe5a5c89e2ec719d8e3b4014318c140f9e86db8ed4b739f55f  RESULTS/RQ3/figures/fig_sizey_vs_camp.png' |
  sha256sum -c -
```

## RQ4 selective auditing

RQ4 evaluates CAMP three-gate selective auditing against uniform random and
process-stratified random selection. The retained figure input and outputs are:

```text
RQ4/csv/budget_repetitions.csv.gz
RQ4/figures/fig_rq4_selective_audit.{png,pdf}
```

The repetition table contains 1,000 A+P repetitions for each of three policies
at six budgets. It regenerates the packaged PNG/PDF figure directly.

Confirm the packaged table and PNG with:

```bash
echo 'fb48f6c5bf0e7541be11e38b4350e02b7a7d5a4ed709700be3dc394a4546c396  RESULTS/RQ4/csv/budget_repetitions.csv.gz' |
  sha256sum -c -
echo '6aff8bf1b5030a78c115715d66e0f85b4e808f2ccc57445563dfa4b0b49794a8  RESULTS/RQ4/figures/fig_rq4_selective_audit.png' |
  sha256sum -c -
```

See `SCRIPTS/RQ4/README.md` for both the short figure command and the complete
input-to-figure experiment command.

## RQ5 live Bowtie2 online feedback

```text
RQ5/csv/bowtie2_cold_attempts.csv.gz
RQ5/csv/bowtie2_warm_attempts.csv.gz
RQ5/csv/bowtie2_cold_task_instances.tsv.gz
RQ5/csv/bowtie2_warm_task_instances.tsv.gz
RQ5/figures/fig_rq5_bowtie_feedback.{png,pdf}
```

The two task-instance tables contain 6,000 logical tasks each and directly
regenerate the revised requested-capacity, reservation-wastage, and
first-attempt-margin figure. The attempt tables retain the 6,016 cold and
6,015 warm attempts, including retries.

```bash
echo '0fc92d92db85fbc244fc5b6b3fc1bdd71605df8922641271a2e09c1b87cef84b  RESULTS/RQ5/csv/bowtie2_cold_task_instances.tsv.gz' | sha256sum -c -
echo 'c7458fcda6bde477d9fe3ef70fd5abb5e30499e0dde95e486a073c835ad3b897  RESULTS/RQ5/csv/bowtie2_warm_task_instances.tsv.gz' | sha256sum -c -
echo '83975dd337287ca6985bf85067dba30d040b7fbf4c9e2f21244cb24cab6b85f5  RESULTS/RQ5/figures/fig_rq5_bowtie_feedback.png' | sha256sum -c -
```

See `SCRIPTS/RQ5/README.md` for the packaged-data and full Slurm routes.
