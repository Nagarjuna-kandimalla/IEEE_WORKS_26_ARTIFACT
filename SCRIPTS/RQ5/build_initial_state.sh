#!/usr/bin/env bash
set -euo pipefail

[[ "$#" -ge 1 && "$#" -le 2 ]] || {
  echo "Usage: $0 <fresh-experiment-root> [n-jobs]" >&2
  exit 2
}

experiment_root="$(realpath "$1")"
n_jobs="${2:-32}"
offline_root="$experiment_root/camp/original_offline_replay"
live_root="$experiment_root/camp/live_deployment"
online_root="$experiment_root/camp/live_online_learning"
artifacts="$experiment_root/artifacts"
seed=1996
model_version="fresh-paper-initial-20260804-v1"
python_command="${RQ5_PYTHON:-python3}"

for path in \
  "$offline_root/scripts/prepare_split.py" \
  "$offline_root/scripts/run_camp.py" \
  "$offline_root/scripts/train_process_tail.py" \
  "$offline_root/config/experiment.json" \
  "$live_root/scripts/train_cold_start_model.py" \
  "$online_root/scripts/prepare_initial_bundle.py" \
  "$online_root/scripts/build_fresh_seed_history.py" \
  "$online_root/scripts/select_camp_policy.py" \
  "$experiment_root/inputs/canonical_matched_tasks.tsv" \
  "$experiment_root/inputs/split_manifest_primary.tsv"; do
  [[ -f "$path" ]] || {
    echo "ERROR: required build input is missing: $path" >&2
    exit 2
  }
done
for path in "$offline_root/results" "$offline_root/models" "$artifacts"; do
  [[ ! -e "$path" ]] || {
    echo "ERROR: refusing non-fresh build path: $path" >&2
    exit 2
  }
done

export PYTHONHASHSEED=0
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

cd "$offline_root"
"$python_command" scripts/prepare_split.py
"$python_command" scripts/run_camp.py --seed "$seed" --n-jobs "$n_jobs"
"$python_command" scripts/train_process_tail.py \
  --seed "$seed" --variant A --n-jobs "$n_jobs"
"$python_command" scripts/train_process_tail.py \
  --seed "$seed" --variant A+P+C --n-jobs "$n_jobs"

result_root="$offline_root/results/seed_$seed"
source_model_root="$offline_root/models/seed_$seed"
mkdir -p "$artifacts/policies/a"
cp -p \
  "$result_root/a/process_tail_selected_policy.json" \
  "$artifacts/policies/a/"

"$python_command" "$online_root/scripts/select_camp_policy.py" \
  --candidates "$result_root/a_plus_p_plus_c/calibration_candidates.csv" \
  --output "$artifacts/camp_policy.json" \
  --minimum-global-coverage 0.99 \
  --minimum-workflow-coverage 0.975

"$python_command" "$online_root/scripts/prepare_initial_bundle.py" \
  --source-model-root "$source_model_root" \
  --initial-features "$result_root/initial_causal_features.tsv" \
  --c-hat-oof "$result_root/c_hat_initial_oof.tsv" \
  --config "$offline_root/config/experiment.json" \
  --output-root "$artifacts/initial_model" \
  --model-version "$model_version" \
  --n-jobs "$n_jobs"

"$python_command" "$online_root/scripts/build_fresh_seed_history.py" \
  --initial-features "$result_root/initial_causal_features.tsv" \
  --history-tsv "$artifacts/initial_history_features.tsv" \
  --history-db "$artifacts/initial_history.sqlite3" \
  --manifest "$artifacts/initial_history_manifest.json" \
  --excluded-workflow bowtie2

"$python_command" "$live_root/scripts/train_cold_start_model.py" \
  --history-features "$artifacts/initial_history_features.tsv" \
  --model-root "$artifacts/initial_model" \
  --output-root "$artifacts/workflow_cold" \
  --n-jobs "$n_jobs"

echo "initial_state_status=complete"
echo "initial_state_root=$artifacts"
