#!/usr/bin/env bash
set -euo pipefail
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
reviewer_python="${REVIEWER_PYTHON:-$package_root/.venv/bin/python}"
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$package_root"
export W1_PYTHON="$reviewer_python"
bash "$package_root/scripts/reproduce.sh" "$@"
