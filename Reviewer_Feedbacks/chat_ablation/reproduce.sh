#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHON="${REVIEWER_PYTHON:-${PYTHON:-${ROOT}/.venv/bin/python}}"
cd "${ROOT}"
sha256sum --quiet -c MANIFEST.sha256
"${PYTHON}" "${ROOT}/validate_contract.py"
bash "${ROOT}/run_reference_analysis.sh"
