# Reviewer feedback experiments

These are the W1, W2, W3, W5, and C-hat ablation reproduction packages, copied from
the reviewer workspaces. Each directory contains code, local inputs, recorded
outputs, expected results, and a README with commands.

| Directory | Reported comparison | Expected result |
|---|---|---|
| [W1](W1/README.md) | Equal requests and matched coverage | Sizey comparison: 74.4% fewer underallocations at equal requests; 16.6% less memory at matched coverage. Access: 2 versus CAMP's 31 underallocations; CAMP has 72.2% less recorded unused memory-time. |
| [W2](W2/README.md) | Selective versus random auditing, downstream allocation | At 20% audits: 265.8 versus 305.8 mean events; 13.1% fewer with 2.4% more requests. |
| [W3](W3/README.md) | Explicit-read versus read+mmap consumption | 32 versus 30 native underallocations in the primary seed-1996 comparison. |
| [W5](W5/README.md) | Allocation saving minus audit cost | Positive net GiB-hours at 5%, 10%, and 15%; approximately break-even at 20%. |
| [chat_ablation](chat_ablation/README.md) | Static/peak history, consumed history, and predicted consumption | A+P / A+P+CH / A+P+CHAT / A+P+CH+CHAT: **85 / 46 / 52 / 30** native underallocations on the same 5,026 tasks. |

## Reproduction

Use Linux and Python 3.9. In each directory:

```bash
bash setup.sh
bash reproduce.sh
```

Setup installs pinned Python dependencies; LightGBM packages need `libgomp`.
Once installed, result reproduction is local and does not need network access,
Slurm, a cloud worker, or the reviewer working directories. To use an existing
compatible environment, set `REVIEWER_PYTHON=/absolute/path/to/python`.

The standard commands recompute results from recorded measurements, selections,
or task predictions and compare them against preserved reference values.
W2 and W3 additionally include separate `fresh_training.py` commands for full
model-training experiments in a new directory. Those full training runs are
not implied by a successful standard reproduction.
C-hat ablation also includes `run_full_ablation.sh` for a fresh training run;
its standard reproduction uses the retained predictions.

## Verification

See [VERIFICATION.md](VERIFICATION.md) and each package's
`verification/reproduce.log` for the tested commands, expected values, and
what was actually executed. `COPY_PROVENANCE.json` identifies the source
workspaces, and `SHA256SUMS.txt` covers immutable code, inputs, and references.
Regenerated results and execution logs are excluded from that checksum list;
the reproduction validators check the generated numerical results.

From this directory, verify immutable files with:

```bash
sha256sum -c SHA256SUMS.txt
```

The existing paper artifact's original data, experiment code, and results were
not replaced. The original reviewer workspaces also remain in place.
