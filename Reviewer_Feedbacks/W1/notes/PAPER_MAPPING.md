# W1 paper-to-code mapping

Paper source: `/work/Final_Camera_Ready_IEEE_WORKS_26/source/WORKS_Camera_Ready/`.
Scope: eight-page editorial version, as inspected on 2026-09-28.

| Paper passage | Reproduction | Output |
|---|---|---|
| Section IV, `R3-W1-ACCESS-METHOD` | Uniform Access request multiplier; fixed original cohort and runtime convention | `native_and_equal_budget.csv`, RQ2 rows |
| Section V/RQ2, `R3-W1-ACCESS-RESULT` | Equal-budget underallocations and recorded positive unused memory-time | `paper_values.json`, Access fields |
| Section IV, `R3-W1-SIZEY-METHOD` | Equal budget and smallest multiplier reaching 95% observed coverage | `native_and_equal_budget.csv`, RQ3 rows; `rq3_operating_points.csv` |
| Section V/RQ3, `R3-W1-SIZEY-RESULT` | Counts and percentage reductions at those two operating points | `paper_values.json`, Sizey and matched-coverage fields |

`inputs/paper_claims.json` preserves the exact four LaTeX passages and expected
displayed numbers. The runner checks every number against fresh calculations
from the original predictions. Historical result tables are comparison targets,
not inputs to those calculations.

The paper includes neither W1 bootstrap intervals nor W1 operating-curve plots.
Those remain in the sibling archive. Other paper figures and the five-seed RQ2
robustness experiment belong to the main artifact, outside this W1 package.
