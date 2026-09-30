# Paper-aligned C-hat ablation

All rows use the paper's seed-1996 85/15 chronological split: 28,490 development tasks and 5,026 held-out tasks. No model or allocation policy was reselected with test outcomes.

`paper_endpoint_reproduction.csv` preserves the paper routes: global routing for Access (A+P) and process-tail routing for full CAMP.

`route_stratified_metrics.csv` shows all four feature variants under each route. Comparisons within a route isolate feature changes; the paper endpoint comparison preserves the deployed paper configurations.
