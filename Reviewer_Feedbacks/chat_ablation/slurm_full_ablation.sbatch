#!/usr/bin/env bash
#SBATCH --job-name=camp-chat-ablation
#SBATCH --partition=elastic
#SBATCH --cpus-per-task=16
#SBATCH --mem=28G
#SBATCH --output=full-ablation-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit from Reviewer_Feedbacks/chat_ablation}"
export N_JOBS="${N_JOBS:-8}"
bash ./run_full_ablation.sh
