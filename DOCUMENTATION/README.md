# CAMP documentation map

This directory reorganizes the artifact into three complementary views. It
does not replace the executable READMEs elsewhere in the repository and does
not introduce a second implementation.

## Start with the view you need

| Need | Document |
|---|---|
| Understand CAMP without commands | [`01_CONCEPTUAL_CONNECTIONS.md`](01_CONCEPTUAL_CONNECTIONS.md) |
| Understand inputs and task populations | [`02_COMPONENT_DATA_AND_COHORTS.md`](02_COMPONENT_DATA_AND_COHORTS.md) |
| Understand and execute the six workflows | [`03_COMPONENT_WORKFLOWS_AND_EXECUTION.md`](03_COMPONENT_WORKFLOWS_AND_EXECUTION.md) |
| Understand STRACE/eBPF signals and helpers | [`04_COMPONENT_AUDITING_AND_SIGNALS.md`](04_COMPONENT_AUDITING_AND_SIGNALS.md) |
| Understand A, P, C, models, calibration, and allocation | [`05_COMPONENT_MODELING_AND_ALLOCATION.md`](05_COMPONENT_MODELING_AND_ALLOCATION.md) |
| Understand selective-auditing gates and budgets | [`06_COMPONENT_SELECTIVE_GATING.md`](06_COMPONENT_SELECTIVE_GATING.md) |
| Reproduce RQ1 | [`07_EXPERIMENT_RQ1.md`](07_EXPERIMENT_RQ1.md) |
| Reproduce RQ2 and its variance tests | [`08_EXPERIMENT_RQ2.md`](08_EXPERIMENT_RQ2.md) |
| Reproduce RQ3 | [`09_EXPERIMENT_RQ3.md`](09_EXPERIMENT_RQ3.md) |
| Reproduce RQ4 | [`10_EXPERIMENT_RQ4.md`](10_EXPERIMENT_RQ4.md) |
| Reproduce RQ5 and interpret its live-feedback figure | [`13_EXPERIMENT_RQ5.md`](13_EXPERIMENT_RQ5.md) |
| See how every practical stage connects | [`11_END_TO_END_INTEGRATION.md`](11_END_TO_END_INTEGRATION.md) |
| View simple and detailed live architecture diagrams | [`12_LIVE_DEPLOYMENT_ARCHITECTURE.md`](12_LIVE_DEPLOYMENT_ARCHITECTURE.md) |
| Follow model training from the first development row through live prediction | [`14_MODEL_TRAINING_LIFECYCLE.md`](14_MODEL_TRAINING_LIFECYCLE.md) |
| Follow calibration from out-of-fold predictions through allocation and retry | [`15_ALLOCATION_CALIBRATION_LIFECYCLE.md`](15_ALLOCATION_CALIBRATION_LIFECYCLE.md) |

## Source-of-truth rule

The programs, configuration files, manifests, packaged tables, and existing
execution READMEs remain authoritative. These guides name those files and
explain how they connect. If a command is changed in the future, update its
canonical README and the corresponding guide together:

- workflow acquisition and launches: [`../WORKFLOWS/README.md`](../WORKFLOWS/README.md);
- audit installation and summaries: [`../AUDIT/README.md`](../AUDIT/README.md);
- RQ1 and RQ2 postprocessing/model commands: [`../SCRIPTS/README.md`](../SCRIPTS/README.md);
- RQ3 commands: [`../SCRIPTS/RQ3/README.md`](../SCRIPTS/RQ3/README.md);
- RQ4 commands: [`../SCRIPTS/RQ4/README.md`](../SCRIPTS/RQ4/README.md);
- RQ5 commands: [`../SCRIPTS/RQ5/README.md`](../SCRIPTS/RQ5/README.md);
- packaged outputs and checks: [`../RESULTS/README.md`](../RESULTS/README.md).

All paths in these documents are relative to the artifact root unless stated
otherwise.
