#!/usr/bin/env bash
#SBATCH --job-name=camp-fresh-bowtie2
#SBATCH --partition=main
#SBATCH --constraint=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=12:00:00
#SBATCH --output=camp-rq5-bowtie2-%j.out
#SBATCH --error=camp-rq5-bowtie2-%j.err

set -euo pipefail

[[ "$#" -eq 7 ]] || {
  echo "Usage: sbatch $0 <fresh-experiment-root> <label> <model-root> <history-features> <history-db> <base-training-frame> <expected-bowtie2-history-rows>" >&2
  exit 2
}
experiment_root="$(realpath "$1")"
exec bash \
  "$experiment_root/camp/live_online_learning/slurm/run_fresh_bowtie2.sh" \
  "$@"
