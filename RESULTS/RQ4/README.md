# RQ4 results

The packaged RQ4 result is the A+P risk-target selective-auditing figure in
`figures/`. Its 1,000-repetition source table is in `csv/` so the figure can be
regenerated without rerunning the gate evaluation.

Use [`../../SCRIPTS/RQ4/README.md`](../../SCRIPTS/RQ4/README.md) and choose its
packaged-CSV route or full frozen-input route. Both routes finish at the same
verification commands below.

Verify the packaged inputs and outputs from the artifact root:

```bash
echo 'fb48f6c5bf0e7541be11e38b4350e02b7a7d5a4ed709700be3dc394a4546c396  RESULTS/RQ4/csv/budget_repetitions.csv.gz' | sha256sum -c -
echo '6aff8bf1b5030a78c115715d66e0f85b4e808f2ccc57445563dfa4b0b49794a8  RESULTS/RQ4/figures/fig_rq4_selective_audit.png' | sha256sum -c -
```
