#!/usr/bin/env bash
#SBATCH --job-name=camp-chat-ablation
#SBATCH --partition=elastic
#SBATCH --cpus-per-task=16
#SBATCH --mem=28G
#SBATCH --chdir=/work/Reviewer_Feedbacks_WORKS/ablation_test
#SBATCH --output=/work/Reviewer_Feedbacks_WORKS/ablation_test/logs/full-ablation-%j.out

set -euo pipefail
export N_JOBS="${SLURM_CPUS_PER_TASK:-16}"
./run_full_ablation.sh --fresh
