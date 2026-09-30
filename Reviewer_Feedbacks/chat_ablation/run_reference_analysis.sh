#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-${ROOT}/.venv/bin/python}"
RUN_ROOT="${ROOT}/reference_predictions/seed_1996"

"${PYTHON}" "${ROOT}/analyze_paper_configuration_ablation.py" \
  --run-root "${RUN_ROOT}" \
  --output-dir "${ROOT}/results/paper_configuration"
"${PYTHON}" "${ROOT}/analyze_paper_routes.py" \
  --run-root "${RUN_ROOT}" \
  --output-dir "${ROOT}/results/route_analysis"
"${PYTHON}" "${ROOT}/validate_results.py" \
  --actual-root "${ROOT}/results" \
  --expected-root "${ROOT}/expected_results"

echo "Reference analysis reproduced and validated in ${ROOT}/results"
