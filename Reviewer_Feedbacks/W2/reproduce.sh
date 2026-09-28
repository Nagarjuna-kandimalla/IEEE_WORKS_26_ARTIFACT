#!/usr/bin/env bash
set -euo pipefail
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
reviewer_python="${REVIEWER_PYTHON:-$package_root/.venv/bin/python}"
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$package_root"
"$reviewer_python" workspace.py verify
"$reviewer_python" -m unittest discover -s tests -v
"$reviewer_python" workspace.py summarize
"$reviewer_python" validate_results.py
