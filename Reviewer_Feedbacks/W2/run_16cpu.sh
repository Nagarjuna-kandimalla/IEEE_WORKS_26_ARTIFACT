#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
R3W2_PYTHON_BIN="${R3W2_PYTHON:-.venv/bin/python}"
mkdir -p logs
"$R3W2_PYTHON_BIN" workspace.py run --workers "${R3W2_WORKERS:-2}" --threads "${R3W2_THREADS:-8}" "$@" 2>&1 | tee -a logs/cohort_run.log
"$R3W2_PYTHON_BIN" workspace.py summarize 2>&1 | tee -a logs/cohort_summary.log
