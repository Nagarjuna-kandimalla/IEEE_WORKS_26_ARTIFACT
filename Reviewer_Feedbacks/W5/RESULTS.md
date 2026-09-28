# Final R3-W5 audit net-benefit result

CAMP saves 23.596 GiB-hours relative to Access over the fixed 5,026-task test
horizon before audit cost. After charging positive paired eBPF runtime overhead
using each audited task's frozen CAMP request, the mean result across five
audit-selection seeds is:

| Audit budget | Audited tasks | Audit cost (GiB-h) | Net benefit (GiB-h) | 95% seed-mean interval |
|---:|---:|---:|---:|---:|
| 5% | 251 | 5.999 | **+17.597** | [17.355, 17.839] |
| 10% | 503 | 12.434 | **+11.161** | [10.646, 11.677] |
| 15% | 754 | 17.683 | **+5.913** | [5.603, 6.152] |
| 20% | 1,005 | 24.025 | **-0.429** | [-1.077, 0.346] |
| 25% | 1,257 | 29.682 | **-6.087** | [-6.753, -5.420] |
| 30% | 1,508 | 35.855 | **-12.259** | [-13.042, -11.477] |
| 50% | 2,513 | 66.534 | **-42.938** | [-43.884, -41.814] |
| 100% | 5,026 | 139.680 | **-116.084** | identical across seeds |

Net benefit is positive at 5%, 10%, and 15%. The 20% estimate is approximately
break-even because its interval crosses zero. Budgets of 25% and above are
clearly negative over this test horizon.

The allocation result is held fixed across budgets. This analysis establishes
whether the submitted CAMP saving covers measured audit cost; it does not claim
that the selected audits caused the frozen allocation saving.
