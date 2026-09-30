#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${REVIEWER_PYTHON:-${PYTHON:-${ROOT}/.venv/bin/python}}"
N_JOBS="${N_JOBS:-8}"
RESULT_ROOT="${ROOT}/pipeline/results/seed_1996"
MODEL_ROOT="${ROOT}/pipeline/models/seed_1996"

if [[ "${1:-}" == "--fresh" ]]; then
  rm -rf "${RESULT_ROOT}" "${MODEL_ROOT}" "${ROOT}/results/full_rebuild"
elif [[ -e "${RESULT_ROOT}" || -e "${MODEL_ROOT}" ]]; then
  echo "A full-run output already exists. Rerun with --fresh to replace package-owned outputs." >&2
  exit 2
fi

mkdir -p "${ROOT}/results/full_rebuild"

"${PYTHON}" "${ROOT}/pipeline/prepare_ablation_inputs.py"

variants=("A+P" "A+P+CH" "A+P+CHAT" "A+P+C")
stems=("a_plus_p" "a_plus_p_plus_ch" "a_plus_p_plus_chat" "a_plus_p_plus_c")

for variant in "${variants[@]}"; do
  "${PYTHON}" "${ROOT}/pipeline/run_global_variant.py" \
    --variant "${variant}" --n-jobs "${N_JOBS}"
done

# Keep the global predictions because process-tail training updates each variant
# directory in place.
for stem in "${stems[@]}"; do
  cp -a "${RESULT_ROOT}/${stem}" "${RESULT_ROOT}/${stem}_final_global_base"
done

for variant in "${variants[@]}"; do
  "${PYTHON}" "${ROOT}/pipeline/train_process_tail.py" \
    --seed 1996 --variant "${variant}" --n-jobs "${N_JOBS}" \
    --result-root "${RESULT_ROOT}" --model-root "${MODEL_ROOT}"
done

"${PYTHON}" "${ROOT}/analyze_paper_configuration_ablation.py" \
  --run-root "${RESULT_ROOT}" \
  --output-dir "${ROOT}/results/full_rebuild/paper_configuration"
"${PYTHON}" "${ROOT}/analyze_paper_routes.py" \
  --run-root "${RESULT_ROOT}" \
  --output-dir "${ROOT}/results/full_rebuild/route_analysis"
"${PYTHON}" "${ROOT}/validate_results.py" \
  --actual-root "${ROOT}/results/full_rebuild" \
  --expected-root "${ROOT}/expected_results"

echo "Full ablation rebuilt and validated in ${ROOT}/results/full_rebuild"
