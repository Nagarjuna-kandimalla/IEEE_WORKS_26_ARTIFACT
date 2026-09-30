# Artifact package verification — 2026-09-30

The following command completed successfully from this artifact directory,
using an existing Python 3.9 environment with all seven pinned dependency
versions from `requirements.txt`:

```bash
REVIEWER_PYTHON=/absolute/path/to/python bash reproduce.sh
```

## Checks executed

- Verified the copied source package's original checksums and the artifact
  package's immutable-file checksums.
- Checked 33,516 unique tasks, 28,490 development tasks, 5,026 test tasks,
  disjoint development/test identities, and six workflows.
- Verified the four feature views, paired task order, recorded peaks and
  runtimes, and native underallocation counts **85 / 46 / 52 / 30**.
- Recomputed every native metric and all 5,000 grouped-bootstrap repetitions.
- Recomputed both route analyses and the separately labeled original paper
  endpoints (87/31) versus latest ablation endpoints (85/30).
- Compared four regenerated CSV tables and the analysis manifest against the
  archived references, using the original validator (`rtol=1e-9`, `atol=1e-7`).
- Checked shell syntax, Python syntax, and the two model-training entry points'
  `--help` commands.

See [reproduce.log](reproduce.log), [validation.json](validation.json), and
[`../results/contract_validation.json`](../results/contract_validation.json).

Model fitting, live workflows, cloud jobs, and fresh dependency installation
were not repeated. These checks establish reproduction from the retained
task-level predictions; they do not establish exact fresh-training numerical
agreement on another machine. The full-training entry point is included for
that separate experiment.
