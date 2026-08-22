#!/usr/bin/env bash
#SBATCH --job-name=camp-fresh-learner
#SBATCH --partition=main
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=12:00:00

set -euo pipefail

[[ "$#" -ge 2 && "$1" == "--learner-script" ]] || {
  echo "Usage: sbatch $0 --learner-script <path> [learner arguments...]" >&2
  exit 2
}
learner_script="$(realpath "$2")"
shift 2
[[ -f "$learner_script" ]] || {
  echo "ERROR: learner script is missing: $learner_script" >&2
  exit 2
}

python_command="${RQ5_PYTHON:-python3}"
command -v "$python_command" >/dev/null 2>&1 || {
  echo "ERROR: RQ5 Python is unavailable: $python_command" >&2
  exit 2
}

learner_args=("$@")
shared_state_db=
shared_history_db=
local_state_db="/tmp/camp-fresh-learner-${SLURM_JOB_ID}.sqlite3"
local_history_db="/tmp/camp-fresh-learner-${SLURM_JOB_ID}-history.sqlite3"
for ((index = 0; index < ${#learner_args[@]}; index++)); do
  value_index=$((index + 1))
  case "${learner_args[$index]}" in
    --state-db)
      [[ "$value_index" -lt "${#learner_args[@]}" ]] || {
        echo "ERROR: --state-db has no value." >&2
        exit 2
      }
      shared_state_db="$(realpath -m "${learner_args[$value_index]}")"
      learner_args[$value_index]="$local_state_db"
      ;;
    --history-db)
      [[ "$value_index" -lt "${#learner_args[@]}" ]] || {
        echo "ERROR: --history-db has no value." >&2
        exit 2
      }
      shared_history_db="$(realpath -m "${learner_args[$value_index]}")"
      learner_args[$value_index]="$local_history_db"
      ;;
  esac
done
[[ -n "$shared_state_db" ]] || {
  echo "ERROR: learner arguments do not contain --state-db." >&2
  exit 2
}
[[ -n "$shared_history_db" ]] || {
  echo "ERROR: learner arguments do not contain --history-db." >&2
  exit 2
}
[[ -f "$shared_history_db" ]] || {
  echo "ERROR: seeded learner history is missing: $shared_history_db" >&2
  exit 2
}
for local_db in "$local_state_db" "$local_history_db"; do
  [[ ! -e "$local_db" ]] || {
    echo "ERROR: local learner database already exists: $local_db" >&2
    exit 2
  }
done
cp -p "$shared_history_db" "$local_history_db"

snapshot_one() {
  local local_db=$1
  local shared_db=$2
  local snapshot
  [[ -f "$local_db" ]] || {
    echo "ERROR: local learner database was not created: $local_db" >&2
    return 1
  }
  mkdir -p "$(dirname "$shared_db")"
  snapshot="${shared_db}.tmp-${SLURM_JOB_ID}"
  cp -p "$local_db" "$snapshot" &&
    mv -f "$snapshot" "$shared_db"
}

snapshot_databases() {
  local command_status=$?
  local snapshot_status=0
  trap - EXIT
  set +e
  snapshot_one "$local_state_db" "$shared_state_db" || snapshot_status=1
  snapshot_one "$local_history_db" "$shared_history_db" || snapshot_status=1
  rm -f \
    "$local_state_db" "$local_state_db-journal" \
    "$local_state_db-wal" "$local_state_db-shm" \
    "$local_history_db" "$local_history_db-journal" \
    "$local_history_db-wal" "$local_history_db-shm"
  if ((command_status != 0)); then
    exit "$command_status"
  fi
  exit "$snapshot_status"
}
trap snapshot_databases EXIT

"$python_command" "$learner_script" "${learner_args[@]}"
