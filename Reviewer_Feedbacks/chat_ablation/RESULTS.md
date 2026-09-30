# C-hat ablation results

These results use the fixed 5,026-task held-out set. Under the paper
configuration, A+P uses the global route and each CAMP feature variant uses the
process-tail route with global fallback.

| Variant | MdAPE (%) | Spearman | q99 pinball loss (MiB) | Underallocations | Coverage (%) | Total request (GiB) |
|---|---:|---:|---:|---:|---:|---:|
| A+P | 21.75 | 0.8430 | 28.94 | 85 | 98.31 | 26,075 |
| A+P+CH | 21.32 | 0.8791 | 230.94 | 46 | 99.08 | 80,853 |
| A+P+CHAT | 24.41 | 0.8357 | 163.25 | 52 | 98.97 | 123,958 |
| A+P+CH+CHAT | 26.51 | 0.8773 | 198.97 | 30 | 99.40 | 34,271 |

The full model has 55 fewer underallocations than A+P on the same test
population. The grouped bootstrap difference is -55 underallocations with a
95% interval of [-73, -38]. It also requests 2.318 times the observed total
peak memory, compared with 1.764 for A+P.

The paper-configuration table combines a feature change with a route change.
Use `expected_results/route_analysis/route_stratified_metrics.csv` to isolate
feature effects under a common route. Under the common global route, CH improves
Spearman correlation from 0.8430 to 0.8791; C-hat alone reaches 0.8357; and the
full feature set reaches 0.8773. Allocation outcomes depend on both the feature
view and the selected tail policy, so C-hat's value should not be inferred from
underallocation counts alone.

Machine-readable results and confidence intervals are in `expected_results/`.
