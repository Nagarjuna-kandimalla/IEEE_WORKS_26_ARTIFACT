#!/usr/bin/env bash
#SBATCH --job-name=camp-rq5-build
#SBATCH --partition=main
#SBATCH --constraint=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=12:00:00
#SBATCH --exclusive

set -euo pipefail

[[ "$#" -eq 2 ]] || {
  echo "Usage: sbatch $0 <artifact-root> <fresh-experiment-root>" >&2
  exit 2
}
artifact_root="$(realpath "$1")"
experiment_root="$(realpath "$2")"
exec bash "$artifact_root/SCRIPTS/RQ5/build_initial_state.sh" \
  "$experiment_root" "${SLURM_CPUS_PER_TASK:-32}"
