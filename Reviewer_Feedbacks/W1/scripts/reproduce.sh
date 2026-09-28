#!/usr/bin/env bash
set -euo pipefail
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1
paper_python="${W1_PYTHON:-$package_root/.venv/bin/python}"
if [[ ! -x "$paper_python" ]]; then
    printf '%s\n' 'Create .venv and install requirements.lock.txt, or set W1_PYTHON to a Python executable.' >&2
    exit 1
fi
exec "$paper_python" "$package_root/scripts/reproduce_paper.py" "$@"
