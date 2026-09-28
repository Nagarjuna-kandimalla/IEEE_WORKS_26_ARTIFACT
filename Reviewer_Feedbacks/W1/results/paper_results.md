# W1 paper results — reproduced from original frozen predictions

| Comparison | Reported result |
|---|---|
| Access, equal budget | 34,277.55 GiB each; Access 2 versus CAMP 31 underallocations. |
| Access, unused memory-time | CAMP 39.87 versus Access 143.19 GiB-hours; 72.2% less. |
| Sizey, equal budget | 79,049.42 GiB each; CAMP 287 versus Sizey 1,119 underallocations; 74.4% fewer. |
| Sizey, matched coverage | 95.004% and 1,066 underallocations each; CAMP 77,818.52 versus Sizey 93,357.81 GiB; 16.6% less. |

All original-result checks, pre-organization numerical comparisons, and manuscript rounding checks passed.

RQ2 retains all 5,026 tasks and the original zero weighting for 449 missing runtimes in memory-time only. RQ3 retains 21,337 tasks with positive runtimes.
Coverage matching is retrospective on recorded peaks; models and histories are fixed. These are aggregate request sums, not concurrent RAM. No training or workload execution occurs.
