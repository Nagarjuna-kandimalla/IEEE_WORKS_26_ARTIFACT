# Component: selective auditing

RQ4 treats auditing as a budgeted selection problem. Given pre-outcome task
information, it selects the tasks whose outcomes should be measured and
compares that selection with equal-count random baselines.

## Inputs and scenarios

The evaluator uses frozen initial and experiment features, `C-hat`, and
prediction tables for two scenarios:

```text
a_plus_p
a_plus_p_plus_c
```

There are 9,141 initial tasks used to fit/cross-fit the gates and 21,337 later
experiment tasks used for evaluation. All tables are aligned by logical task
ID. The packaged paper figure retains A+P underallocation-target results; the
full evaluator also produces A+P+C diagnostics.

## Three gates

The selector combines three distinct reasons to audit a task:

### Risk lane

Prioritizes tasks likely to be underallocated or to have a large memory
shortfall under the active allocation view. It targets immediate safety
failures.

### Information lane

Prioritizes high-information or surprising tasks whose observation can add
useful knowledge. It targets learning value rather than only predicted
failure.

### Discovery lane

Preserves exploration of poorly represented or uncertain regions. Half of the
discovery allocation is randomized in the preserved design so the system does
not become completely self-confirming.

The default lane fractions are 80% risk, 10% information, and 10% discovery.
The evaluator also contains alternate lane mixes for sensitivity analysis.

## Leakage boundary

`selective_gates.py` defines post-outcome fields that may not be used as gate
features. Gate fitting uses cross-fitted initial predictions and pre-outcome
features. The experiment task's actual peak, consumption, and derived target
labels are revealed only for retrospective evaluation after a selection plan
has been constructed.

## Gate models and scoring

The gate implementation uses LightGBM classifiers/regressors with numeric and
categorical preprocessing. It creates task-level gate targets from initial
out-of-fold allocator behavior, builds scenario-appropriate A/P/C feature
sets, cross-fits scores for validation, fits final gate models, and scores the
experiment population. Workflow-relative ranks prevent large workflows from
automatically dominating solely through scale.

## Equal-budget policies

Three policies receive exactly the same number of task selections:

| Policy | Selection rule |
|---|---|
| `three_gate` | CAMP risk/information/discovery plan |
| `uniform_random` | Uniform sample over all experiment tasks |
| `process_stratified_random` | Random sample distributed proportionally across workflow/process groups |

The process-stratified baseline is important because it tests whether CAMP's
gain comes merely from spreading audits across processes. Quota allocation
handles integer budgets while preserving total selected count.

## Budgets and repetitions

The complete evaluation runs budgets 5%, 10%, 20%, 30%, 50%, and 100%.
Random baselines and randomized discovery are repeated 1,000 times. Sensitivity
analyses use 200 repetitions by default. The 100% budget is a sanity point at
which every policy must select every task and recall must equal one.

The full A+P/A+P+C repetition table contains:

```text
2 scenarios x 3 policies x 6 budgets x 1,000 repetitions = 36,000 rows
```

The committed paper-figure table filters to A+P and therefore has 18,000 rows.

## Evaluation targets

The evaluator calculates:

- underallocation recall;
- share of total memory shortfall captured;
- heavy-information recall;
- recall of top-5% peak tasks;
- consumed-surprise recall.

It also writes validation discrimination/calibration metrics, workflow
summaries, lane diagnostics, sensitivity results, comparison statistics, and
task-level inclusion information. Holm adjustment is used where multiple
policy comparisons are evaluated.

The retained RQ4 figure uses underallocation recall for the A+P risk target at
5% through 50%. Its comparison is CAMP three-gate versus uniform random versus
process-stratified random at equal budgets.

## Working and committed outputs

The evaluator writes working results below:

```text
SCRIPTS/RQ4/results/seed_1996/selective_gating/
```

This includes the full repetition table, `selected_tasks_20_percent.tsv`,
`task_gate_scores.tsv`, diagnostic tables, and figures. These outputs can be
regenerated and are ignored by Git.

The retained result is:

```text
RESULTS/RQ4/csv/budget_repetitions.csv.gz
RESULTS/RQ4/figures/fig_rq4_selective_audit.png
RESULTS/RQ4/figures/fig_rq4_selective_audit.pdf
```

At the retained budgets, the mean three-gate A+P underallocation recall is
approximately 0.075, 0.129, 0.275, 0.399, and 0.579 for 5%, 10%, 20%, 30%,
and 50%, respectively. The random baselines remain close to their nominal
budget fractions.
