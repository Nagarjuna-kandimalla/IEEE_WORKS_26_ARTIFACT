# RQ4 data

This directory supplies the first inputs of the full RQ4 route. Execute the
ordered commands in [`../../SCRIPTS/RQ4/README.md`](../../SCRIPTS/RQ4/README.md);
do not expand these inputs as a separate preliminary procedure.

RQ4 compares CAMP three-gate selection with equal-count random baselines using
A+P and A+P+C. The packaged compressed inputs contain 9,141 initial tasks used
to fit the gates and 21,337 experiment tasks used for evaluation.

```text
inputs/seed_1996/a_plus_p/             A+P OOF and experiment predictions
inputs/seed_1996/a_plus_p_plus_c/      A+P+C OOF and experiment predictions
inputs/seed_1996/                       supporting inputs used by the scripts
inputs/seed_1996/initial_causal_features.tsv.gz
inputs/seed_1996/test_causal_features.tsv.gz
inputs/seed_1996/c_hat_initial_oof.tsv.gz
inputs/seed_1996/c_hat_test.tsv.gz
```

Run `SCRIPTS/RQ4/prepare_inputs.py` as documented in
`SCRIPTS/RQ4/README.md`; it expands these files into the generated working
directory used by the evaluation scripts.
