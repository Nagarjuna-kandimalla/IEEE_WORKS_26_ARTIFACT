#!/usr/bin/env bash
set -euo pipefail

[[ "$#" -eq 7 ]] || {
  echo "Usage: $0 <fresh-experiment-root> <label> <model-root> <history-features> <history-db> <base-training-frame> <expected-bowtie2-rows>" >&2
  exit 2
}

experiment_root="$(realpath "$1")"
shift
artifact_root="$(realpath "${RQ5_ARTIFACT_ROOT:?RQ5_ARTIFACT_ROOT is required}")"
workflow_runtime_root="$(realpath -m "${RQ5_RUN_ROOT:?RQ5_RUN_ROOT is required}")"
python_command="${RQ5_PYTHON:-python3}"
nextflow_command="${RQ5_NEXTFLOW:-nextflow}"
python() {
  "$python_command" "$@"
}
nextflow() {
  "$nextflow_command" "$@"
}
online_root="$experiment_root/camp/live_online_learning"
camp_source_root="$experiment_root/camp"
live_root="$camp_source_root/live_deployment"
legacy_root="$experiment_root/WORKS_ML"
workflow_root="$artifact_root/WORKFLOWS/bowtie2"
source_root="$workflow_root"
cold_source="$experiment_root/artifacts/workflow_cold"
policy_source="$experiment_root/artifacts/policies"
camp_policy_source="$experiment_root/artifacts/camp_policy.json"
experiment_config="$camp_source_root/original_offline_replay/config/experiment.json"
workflow_config="$online_root/configs/bowtie2_online.config"
input_source="$workflow_root/data/public_runs_prjeb11501_online_unseen_6000.tsv"
label="$1"
model_source="$(realpath "$2")"
history_features_source="$(realpath "$3")"
history_db_source="$(realpath "$4")"
base_training_source="$(realpath "$5")"
expected_bowtie_rows="$6"
run_root="$workflow_runtime_root/runs/$label"

for path in \
  "$model_source/online_manifest.json" \
  "$model_source/a/preprocessor.joblib" \
  "$model_source/a_plus_p/preprocessor.joblib" \
  "$model_source/a_plus_p_plus_c/preprocessor.joblib" \
  "$model_source/signature_scaler.joblib" \
  "$model_source/version_calibration.tsv" \
  "$history_features_source" \
  "$history_db_source" \
  "$base_training_source" \
  "$cold_source/policy.json" \
  "$cold_source/models/consumed_c_hat/preprocessor.joblib" \
  "$policy_source/a/process_tail_selected_policy.json" \
  "$camp_policy_source" \
  "$experiment_config" \
  "$workflow_config" \
  "$input_source"; do
  [[ -e "$path" ]] || {
    echo "ERROR: required input is missing: $path" >&2
    exit 2
  }
done
[[ ! -e "$run_root" ]] || {
  echo "ERROR: fresh run directory already exists: $run_root" >&2
  exit 2
}

for command in "$python_command" "$nextflow_command" sbatch squeue scancel rg; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "ERROR: required command is unavailable: $command" >&2
    exit 2
  }
done
[[ -f "${CAMP_EBPF_TRACER:?CAMP_EBPF_TRACER is required}" ]] || {
  echo "ERROR: CAMP_EBPF_TRACER is not a file: $CAMP_EBPF_TRACER" >&2
  exit 2
}
mkdir -p "$workflow_runtime_root/runs"
python "$online_root/scripts/preflight_run_inputs.py" \
  --workflow bowtie2 \
  --model-root "$model_source" \
  --history-features "$history_features_source" \
  --history-db "$history_db_source" \
  --base-training-frame "$base_training_source" \
  --expected-workflow-rows "$expected_bowtie_rows"

mkdir -p "$run_root"
export PATH="$workflow_root/tool_shims:$PATH"
for command in apptainer wget gzip; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "ERROR: required workflow command is unavailable: $command" >&2
    exit 2
  }
done

camp_root="$run_root/camp"
snapshot_root="$camp_root/frozen_inputs"
mkdir -p \
  "$camp_root/decisions" \
  "$camp_root/history_updates" \
  "$camp_root/task_outcomes" \
  "$camp_root/models/versions" \
  "$snapshot_root/live_code/camp_online" \
  "$snapshot_root/live_code/camp_live" \
  "$snapshot_root/live_code/camp_support" \
  "$snapshot_root/live_code/camp_ml" \
  "$snapshot_root/policies/a" \
  "$snapshot_root/policies/a_plus_p_plus_c" \
  "$snapshot_root/workflow_configs"
cp -a "$model_source" "$snapshot_root/initial_model"
cp -a "$cold_source/models" "$snapshot_root/workflow_cold_models"
cp -p \
  "$history_features_source" \
  "$snapshot_root/initial_history_features.tsv"
cp -p \
  "$base_training_source" \
  "$snapshot_root/base_training_frame.tsv"
cp -p \
  "$cold_source/policy.json" \
  "$experiment_config" \
  "$workflow_config" \
  "$input_source" \
  "$snapshot_root/"
cp -p "$online_root/configs/"*.config \
  "$snapshot_root/workflow_configs/"
cp -p \
  "$policy_source/a/process_tail_selected_policy.json" \
  "$snapshot_root/policies/a/"
cp -p \
  "$camp_policy_source" \
  "$snapshot_root/policies/a_plus_p_plus_c/process_tail_selected_policy.json"
cp -p \
  "$online_root/scripts/allocate_task.py" \
  "$legacy_root/scripts/record_online_task.py" \
  "$online_root/scripts/serve_predictions.py" \
  "$online_root/scripts/run_online_learner.py" \
  "$snapshot_root/live_code/"
cp -a "$legacy_root/camp_ml/." "$snapshot_root/live_code/camp_ml/"
cp -p \
  "$online_root/camp_online/__init__.py" \
  "$online_root/camp_online/engine.py" \
  "$online_root/camp_online/policy.py" \
  "$snapshot_root/live_code/camp_online/"
cp -p \
  "$live_root/camp_live/__init__.py" \
  "$live_root/camp_live/cold_start.py" \
  "$snapshot_root/live_code/camp_live/"
cp -p \
  "$camp_source_root/scripts/common.py" \
  "$camp_source_root/scripts/calibration.py" \
  "$camp_source_root/scripts/history.py" \
  "$camp_source_root/scripts/modeling.py" \
  "$snapshot_root/live_code/camp_support/"

python "$legacy_root/scripts/prepare_run_history.py" \
  --mode snapshot \
  --source "$history_db_source" \
  --destination "$camp_root/inference_history.sqlite3"

python - \
  "$camp_root/inference_history.sqlite3" \
  "$snapshot_root/initial_history_features.tsv" \
  "$expected_bowtie_rows" <<'PY'
import sqlite3
import sys

import pandas as pd

database, features, expected = sys.argv[1:]
connection = sqlite3.connect(database)
try:
    total, bowtie = connection.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN lower(workflow) = 'bowtie2' THEN 1 ELSE 0 END)
        FROM task_outcomes
        """
    ).fetchone()
finally:
    connection.close()
feature_rows = len(pd.read_csv(features, sep="\t", usecols=["logical_task_id"]))
bowtie = int(bowtie or 0)
if total != feature_rows:
    raise SystemExit(
        f"history/sidecar mismatch: sqlite={total}, sidecar={feature_rows}"
    )
if bowtie != int(expected):
    raise SystemExit(
        f"expected {expected} Bowtie2 history rows; found {bowtie}"
    )
print(
    f"camp_online_preflight=ok history_rows={total} "
    f"bowtie2_rows={bowtie}"
)
PY

python - \
  "$camp_root/current_model.json" \
  "$snapshot_root/initial_model" \
  "$snapshot_root/initial_history_features.tsv" <<'PY'
import json
import pathlib
import sys

pointer, model_root, history = map(pathlib.Path, sys.argv[1:])
manifest = json.loads(
    (model_root / "online_manifest.json").read_text(encoding="utf-8")
)
pointer.write_text(
    json.dumps(
        {
            "model_version": manifest["model_version"],
            "model_root": str(model_root.resolve()),
            "history_features": str(history.resolve()),
            "eligible_count": 0,
            "history_sequence": 0,
            "published_at": manifest["created_at"],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY

(
  cd "$snapshot_root"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    > SHA256SUMS
)

export CAMP_RUN_ID="$label"
export CAMP_HISTORY_DB="$camp_root/inference_history.sqlite3"
export CAMP_OUTCOME_DIR="$camp_root/task_outcomes"
export CAMP_DECISION_DIR="$camp_root/decisions"
export CAMP_PREDICTION_SOCKET="/tmp/camp-online-${label}.sock"
export CAMP_PREDICTION_READY="$camp_root/predictor.ready"
export CAMP_ALLOCATE_SCRIPT="$snapshot_root/live_code/allocate_task.py"
export CAMP_RECORD_SCRIPT="$snapshot_root/live_code/record_online_task.py"

learner_stop="$camp_root/learner.stop"
learner_ready="$camp_root/learner.ready"
learner_job_id="$(
  sbatch --parsable \
    --partition="${CAMP_SLURM_QUEUE:-main}" \
    --nodelist="${CAMP_LEARNER_NODELIST:-mempred-worker-13}" \
    --constraint="${CAMP_WORKER_CONSTRAINT:-standard}" \
    --output="$camp_root/learner-%j.out" \
    --error="$camp_root/learner-%j.err" \
    "$online_root/slurm/run_fresh_learner.slurm" \
    --learner-script "$online_root/scripts/run_online_learner.py" \
    --workflow bowtie2 \
    --version-prefix "$label" \
    --history-db "$CAMP_HISTORY_DB" \
    --state-db "$camp_root/online_learner_state.sqlite3" \
    --history-update-dir "$camp_root/history_updates" \
    --outcome-dir "$CAMP_OUTCOME_DIR" \
    --decision-dir "$CAMP_DECISION_DIR" \
    --base-training-frame "$snapshot_root/base_training_frame.tsv" \
    --base-history-features "$snapshot_root/initial_history_features.tsv" \
    --initial-model-root "$snapshot_root/initial_model" \
    --canonical-model-root "$snapshot_root/initial_model" \
    --experiment-config "$snapshot_root/experiment.json" \
    --current-pointer "$camp_root/current_model.json" \
    --version-dir "$camp_root/models/versions" \
    --update-log "$camp_root/online_learning_events.jsonl" \
    --stop-file "$learner_stop" \
    --ready-file "$learner_ready" \
    --first-update 128 \
    --update-every 128 \
    --process-min-support 16 \
    --calibration-window 64 \
    --n-jobs 16
)"
echo "$learner_job_id" > "$camp_root/learner_job_id.txt"

prediction_pid=
nextflow_pid=
cleanup() {
  local status=$?
  if [[ -n "${nextflow_pid:-}" ]] \
      && kill -0 "$nextflow_pid" 2>/dev/null; then
    kill -TERM "$nextflow_pid" 2>/dev/null || true
    wait "$nextflow_pid" 2>/dev/null || true
  fi
  if [[ -n "${prediction_pid:-}" ]] \
      && kill -0 "$prediction_pid" 2>/dev/null; then
    kill "$prediction_pid" 2>/dev/null || true
    wait "$prediction_pid" 2>/dev/null || true
  fi
  if [[ -n "${learner_job_id:-}" ]] \
      && squeue -h -j "$learner_job_id" | grep -q .; then
    touch "$learner_stop"
    scancel "$learner_job_id" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT TERM INT

for _ in $(seq 1 2400); do
  [[ -f "$learner_ready" ]] && break
  learner_state="$(squeue -h -j "$learner_job_id" -o '%T' | xargs)"
  case "$learner_state" in
    FAILED*|CANCELLED*|TIMEOUT*|OUT_OF_MEMORY*|NODE_FAIL*)
      echo "ERROR: learner failed during startup: $learner_state" >&2
      exit 2
      ;;
  esac
  if [[ -z "$learner_state" ]]; then
    echo "ERROR: learner left the queue before becoming ready." >&2
    exit 2
  fi
  sleep 0.5
done
[[ -f "$learner_ready" ]] || {
  echo "ERROR: learner did not become ready." >&2
  exit 2
}

python "$snapshot_root/live_code/serve_predictions.py" \
  --socket "$CAMP_PREDICTION_SOCKET" \
  --ready-file "$CAMP_PREDICTION_READY" \
  --current-pointer "$camp_root/current_model.json" \
  --history-update-dir "$camp_root/history_updates" \
  --cold-start-root "$snapshot_root/workflow_cold_models" \
  --cold-start-policy "$snapshot_root/policy.json" \
  --policy-root "$snapshot_root/policies" \
  --experiment-config "$snapshot_root/experiment.json" \
  --event-log "$camp_root/predictor_events.jsonl" \
  >"$camp_root/predictor.log" 2>&1 &
prediction_pid=$!

for _ in $(seq 1 800); do
  [[ -S "$CAMP_PREDICTION_SOCKET" && -f "$CAMP_PREDICTION_READY" ]] \
    && break
  if ! kill -0 "$prediction_pid" 2>/dev/null; then
    echo "ERROR: prediction service exited during startup." >&2
    sed -n '1,240p' "$camp_root/predictor.log" >&2 || true
    exit 2
  fi
  sleep 0.25
done
[[ -S "$CAMP_PREDICTION_SOCKET" && -f "$CAMP_PREDICTION_READY" ]] || {
  echo "ERROR: prediction service did not become ready." >&2
  exit 2
}

run_manifest="$snapshot_root/public_runs_prjeb11501_online_unseen_6000.tsv"
cd "$run_root"
set +e
nextflow \
  -log "$run_root/.nextflow.log" \
  run "$source_root/main.nf" \
  -profile slurm \
  -c "$workflow_config" \
  --outdir "$run_root/results" \
  --metrics_dir "$run_root/metrics" \
  --work_dir "$run_root/work" \
  --trace_file "$run_root/results/pipeline_info/execution_trace.txt" \
  --merged_csv "$run_root/results/pipeline_info/merged_audit_trace.csv" \
  --merged_tsv "$run_root/results/pipeline_info/merged_audit_trace.tsv" \
  --run_manifest "$run_manifest" \
  --reference_url \
  https://ftp.ensembl.org/pub/release-116/fasta/saccharomyces_cerevisiae/dna/Saccharomyces_cerevisiae.R64-1-1.dna.toplevel.fa.gz &
nextflow_pid=$!
learner_runtime_failed=0
while kill -0 "$nextflow_pid" 2>/dev/null; do
  if ! squeue -h -j "$learner_job_id" | grep -q .; then
    echo "ERROR: learner exited while Nextflow was active." >&2
    learner_runtime_failed=1
    kill -TERM "$nextflow_pid" 2>/dev/null || true
    break
  fi
  sleep 2
done
wait "$nextflow_pid"
nextflow_status=$?
nextflow_pid=
if [[ "$learner_runtime_failed" -ne 0 ]]; then
  nextflow_status=2
fi
set -e

touch "$learner_stop"
for _ in $(seq 1 7200); do
  squeue -h -j "$learner_job_id" | grep -q . || break
  sleep 1
done
if rg -q '"event": "learner_stopped"' \
    "$camp_root/online_learning_events.jsonl"; then
  learner_state=COMPLETED
else
  learner_state=FAILED_OR_INCOMPLETE
fi
echo "learner_final_state=$learner_state"
if [[ "$learner_state" != COMPLETED ]]; then
  nextflow_status=2
fi

if [[ -n "${prediction_pid:-}" ]] \
    && kill -0 "$prediction_pid" 2>/dev/null; then
  kill "$prediction_pid" 2>/dev/null || true
  wait "$prediction_pid" 2>/dev/null || true
fi
prediction_pid=

if compgen -G "$CAMP_OUTCOME_DIR/*.outcome.json" >/dev/null; then
  python "$legacy_root/scripts/build_run_outcome_db.py" \
    --outcome-dir "$CAMP_OUTCOME_DIR" \
    --db "$camp_root/postrun_outcomes.sqlite3" \
    --expected-workflow bowtie2
  python "$legacy_root/scripts/promote_run_history.py" \
    --base-history "$CAMP_HISTORY_DB" \
    --run-outcomes "$camp_root/postrun_outcomes.sqlite3" \
    --destination "$camp_root/postrun_history.sqlite3" \
    --expected-workflow bowtie2
fi

mkdir -p \
  "$run_root/results/manifests" \
  "$run_root/results/metrics"
python "$artifact_root/WORKFLOWS/common/build_static_manifest.py" \
  bowtie2 \
  --runs "$run_manifest" \
  --dataset-id bowtie2_prjeb11501_online_unseen_6000 \
  --out "$run_root/results/manifests/static_task_manifest.tsv"

if [[ -f "$run_root/results/pipeline_info/execution_trace.txt" ]]; then
  set +e
  python "$artifact_root/WORKFLOWS/common/normalize_nextflow_baseline_trace.py" \
    --workflow bowtie2 \
    --dataset-id bowtie2_prjeb11501_online_unseen_6000 \
    --static-manifest \
    "$run_root/results/manifests/static_task_manifest.tsv" \
    --instrumentation ebpf \
    --trace "$run_root/results/pipeline_info/execution_trace.txt" \
    --ebpf-merged \
    "$run_root/results/pipeline_info/merged_audit_trace.tsv" \
    --out "$run_root/results/metrics/training_task_metrics.tsv"
  normalize_status=$?
  set -e
  echo "normalization_status=$normalize_status"
fi

if compgen -G "$CAMP_DECISION_DIR/*.json" >/dev/null; then
  python "$live_root/scripts/summarize_live_run.py" \
    --run-root "$run_root"
fi
trap - EXIT TERM INT
exit "$nextflow_status"
