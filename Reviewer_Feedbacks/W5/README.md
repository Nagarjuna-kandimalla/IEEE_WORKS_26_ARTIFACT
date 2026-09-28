# R3-W5 paper-aligned audit net-benefit reproduction

This is the retained reproducibility package for the result used in the paper.
It puts CAMP's memory-time saving and eBPF auditing cost in the same unit,
GiB-hours, over the same 5,026 held-out tasks.

## Fixed experiment contract

- Test population: 5,026 tasks.
- Audit budgets: 5%, 10%, 15%, 20%, 25%, 30%, 50%, and 100%.
- Audit-selection seeds: 1996 through 2000.
- CAMP allocation saving: frozen submitted-paper CAMP allocation relative to
  Access, 23.59556 GiB-hours.
- Per-task audit overhead: `max(eBPF runtime - no-audit runtime, 0)`.
- Audit cost: overhead seconds multiplied by the frozen CAMP request and
  converted to GiB-hours.
- Net benefit: CAMP allocation saving minus audit cost.

No model training or workflow execution is required. The 40 frozen audit
selection sets and paired runtime/allocation measurements are retained as
compact inputs.

## Files

- `inputs/audit_selections.tsv.gz`: selected tasks for 8 budgets x 5 seeds.
- `inputs/task_level_full_ebpf_net_benefit.csv`: paired runtimes and frozen
  paper allocation measurements for all 5,026 tasks.
- `analyze.py`: complete paper-aligned accounting and grouped bootstrap.
- `expected_results/`: frozen primary outputs used by validation.
- `results/`: regenerated outputs.
- `validate.py`: checks all primary values and the positive-budget conclusion.
- `RESULTS.md`: final numbers and interpretation.
- `MANIFEST.sha256`: integrity hashes for code, inputs, and expected outputs.

## Reproduce without a full run

```bash
cd Reviewer_Feedbacks/W5
bash setup.sh
bash reproduce.sh
```

`reproduce.sh` performs only local CSV analysis. It does not submit a Slurm
job, start an elastic worker, retrain CAMP, or access the restored S3 mount.

To use an existing compatible Python environment:

```bash
REVIEWER_PYTHON=/path/to/python bash reproduce.sh
```

Verify package integrity with:

```bash
sha256sum -c MANIFEST.sha256
```

Earlier closed-loop replay diagnostics, worker logs, and budget-generation
material are preserved separately under
`/work/Reviewer_Feedbacks_WORKS/R3_W5_not_needed/`.

## Verification

The standard command was executed after copying this package into the artifact.
All eight budgets and five seeds were recomputed and matched the preserved
summary and per-seed tables within `rtol=1e-12`, `atol=1e-10`. See
`verification/reproduce.log`. Inputs retain measured paired runtimes and
frozen audit selections; this is an accounting reproduction, not a fresh
workflow or audit-selection experiment.
