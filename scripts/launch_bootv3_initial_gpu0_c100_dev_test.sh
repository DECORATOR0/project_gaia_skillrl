#!/usr/bin/env bash
set -Eeuo pipefail

cd /data/xsy/project_gaia_skillrl

PY=/data/xsy/project_gaia_skillrl/.venv/bin/python
CONFIG=/data/xsy/project_gaia_skillrl/configs/system.json
DEV_DATASET=/data/xsy/project_gaia_skillrl/data/converted/gaia_2023_all_validation_dev_tasks.json
TEST_DATASET=/data/xsy/project_gaia_skillrl/data/converted/gaia_2023_all_validation_test_tasks.json
RUN_ROOT=/data/xsy/project_gaia_skillrl/runs
PROCESS_CSV=/data/xsy/活的进程.csv

RUN_PREFIX="${RUN_PREFIX:-$(date +%Y%m%d_%H%M%S)}"
DEV_RUN_NAME="${RUN_PREFIX}_gaia_bootv3_initial_gpu0_c100_dev83"
TEST_RUN_NAME="${RUN_PREFIX}_gaia_bootv3_initial_gpu0_c100_test82"

update_registry_status() {
  local status="$1"
  "${PY}" - "${PROCESS_CSV}" "$$" "${status}" <<'PY' || true
import csv
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

path = Path(sys.argv[1])
pid = sys.argv[2]
status = sys.argv[3]
now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
if not path.exists():
    raise SystemExit
with path.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.reader(handle))
if not rows:
    raise SystemExit
for row in rows[1:]:
    if len(row) < 8:
        continue
    if row[5].strip() != pid:
        continue
    row[1] = now
    row[2] = status
    if status != "running" and not row[7].strip():
        row[7] = now
with path.open("w", encoding="utf-8", newline="") as handle:
    csv.writer(handle).writerows(rows)
PY
}

on_exit() {
  local status=$?
  if [[ "${status}" -eq 0 ]]; then
    update_registry_status "finished"
    echo "[$(date -Iseconds)] launcher finished successfully"
  else
    update_registry_status "dead"
    echo "[$(date -Iseconds)] launcher failed: exit_status=${status} line=${BASH_LINENO[0]}" >&2
  fi
  exit "${status}"
}
trap on_exit EXIT

export PYTHONUNBUFFERED=1
export PYTHONFAULTHANDLER=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export BLIS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMBA_NUM_THREADS=1

export NLRL_RUNTIME_TASK_CONCURRENCY="${NLRL_RUNTIME_TASK_CONCURRENCY:-100}"
export NLRL_LLM_MAX_CONCURRENT_REQUESTS="${NLRL_LLM_MAX_CONCURRENT_REQUESTS:-100}"
export NLRL_RUNTIME_ITERATIONS_PER_BATCH=0
export NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS="${NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS:-720}"

export NLRL_EXECUTOR_MODEL="${NLRL_EXECUTOR_MODEL:-Qwen3-8B-local}"
export NLRL_EXECUTOR_BASE_URL="${NLRL_EXECUTOR_BASE_URL:-http://127.0.0.1:8100/v1}"
export NLRL_EXECUTOR_API_KEY="${NLRL_EXECUTOR_API_KEY:-EMPTY}"
export NLRL_EXECUTOR_API_MODE="${NLRL_EXECUTOR_API_MODE:-chat_completions}"
export NLRL_EXECUTOR_STREAM="${NLRL_EXECUTOR_STREAM:-1}"
export NLRL_EXECUTOR_TIMEOUT_SECONDS="${NLRL_EXECUTOR_TIMEOUT_SECONDS:-720}"
export NLRL_EXECUTOR_ENABLE_THINKING="${NLRL_EXECUTOR_ENABLE_THINKING:-1}"
export NLRL_EXECUTOR_TEMPERATURE="${NLRL_EXECUTOR_TEMPERATURE:-0.1}"

# Keep tool fallbacks on the prior OpenAI-compatible endpoint instead of the
# local text-only vLLM executor.
export NLRL_TOOL_BASE_URL="${NLRL_TOOL_BASE_URL:-http://35.220.164.252:3888/v1}"
export NLRL_TOOL_API_KEY="${NLRL_TOOL_API_KEY:-sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ}"
export NLRL_TOOL_TIMEOUT_SECONDS="${NLRL_TOOL_TIMEOUT_SECONDS:-720}"

latest_run_dir() {
  local run_name="$1"
  find "${RUN_ROOT}" -type d -name "${run_name}" | sort | tail -n 1
}

print_score() {
  local summary_path="$1"
  if [[ -f "${summary_path}" ]]; then
    "${PY}" - "${summary_path}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
success = data.get("success_count", 0)
count = data.get("task_count", 0)
acc = success / count if count else 0.0
print(f"success_count={success}/{count} acc={acc:.6f}")
PY
  fi
}

echo "[$(date -Iseconds)] Starting GAIA boot-v3 initial GPU0 c100 dev/test queue"
echo "CWD=$(pwd)"
echo "PY=${PY}"
echo "CONFIG=${CONFIG}"
echo "DEV_DATASET=${DEV_DATASET}"
echo "TEST_DATASET=${TEST_DATASET}"
echo "RUN_PREFIX=${RUN_PREFIX}"
echo "DEV_RUN_NAME=${DEV_RUN_NAME}"
echo "TEST_RUN_NAME=${TEST_RUN_NAME}"
echo "NLRL_RUNTIME_TASK_CONCURRENCY=${NLRL_RUNTIME_TASK_CONCURRENCY}"
echo "NLRL_LLM_MAX_CONCURRENT_REQUESTS=${NLRL_LLM_MAX_CONCURRENT_REQUESTS}"
echo "NLRL_EXECUTOR_MODEL=${NLRL_EXECUTOR_MODEL}"
echo "NLRL_EXECUTOR_BASE_URL=${NLRL_EXECUTOR_BASE_URL}"
echo "NLRL_EXECUTOR_API_MODE=${NLRL_EXECUTOR_API_MODE}"
echo "NLRL_EXECUTOR_STREAM=${NLRL_EXECUTOR_STREAM}"
echo "NLRL_EXECUTOR_ENABLE_THINKING=${NLRL_EXECUTOR_ENABLE_THINKING}"
echo "NLRL_TOOL_BASE_URL=${NLRL_TOOL_BASE_URL}"

unset NLRL_RUNTIME_INITIAL_SKILL_PATH

echo "[$(date -Iseconds)] Running bootstrap + validation_dev eval"
echo "Command: ${PY} -m gaia_skillrl.cli --config ${CONFIG} train-local --dataset-path ${DEV_DATASET} --run-name ${DEV_RUN_NAME} --bootstrap-skill"
"${PY}" -m gaia_skillrl.cli \
  --config "${CONFIG}" \
  train-local \
  --dataset-path "${DEV_DATASET}" \
  --run-name "${DEV_RUN_NAME}" \
  --bootstrap-skill

DEV_RUN_DIR="$(latest_run_dir "${DEV_RUN_NAME}")"
if [[ -z "${DEV_RUN_DIR}" || ! -d "${DEV_RUN_DIR}" ]]; then
  echo "Could not locate dev run dir for ${DEV_RUN_NAME}" >&2
  exit 1
fi

SKILL_PATH="${DEV_RUN_DIR}/iteration_01/skill_for_eval/gaia-general-skill/SKILL.md"
if [[ ! -f "${SKILL_PATH}" ]]; then
  SKILL_PATH="${DEV_RUN_DIR}/bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
fi
if [[ ! -f "${SKILL_PATH}" ]]; then
  echo "Could not locate bootstrapped SKILL.md under ${DEV_RUN_DIR}" >&2
  exit 1
fi

echo "[$(date -Iseconds)] Finished validation_dev"
echo "DEV_RUN_DIR=${DEV_RUN_DIR}"
echo "BOOTSTRAP_SKILL_PATH=${SKILL_PATH}"
print_score "${DEV_RUN_DIR}/run_summary.json"

export NLRL_RUNTIME_INITIAL_SKILL_PATH="${SKILL_PATH}"

echo "[$(date -Iseconds)] Running validation_test eval with bootstrapped skill"
echo "Command: ${PY} -m gaia_skillrl.cli --config ${CONFIG} train-local --dataset-path ${TEST_DATASET} --run-name ${TEST_RUN_NAME}"
"${PY}" -m gaia_skillrl.cli \
  --config "${CONFIG}" \
  train-local \
  --dataset-path "${TEST_DATASET}" \
  --run-name "${TEST_RUN_NAME}"

TEST_RUN_DIR="$(latest_run_dir "${TEST_RUN_NAME}")"
if [[ -z "${TEST_RUN_DIR}" || ! -d "${TEST_RUN_DIR}" ]]; then
  echo "Could not locate test run dir for ${TEST_RUN_NAME}" >&2
  exit 1
fi

echo "[$(date -Iseconds)] Finished validation_test"
echo "TEST_RUN_DIR=${TEST_RUN_DIR}"
print_score "${TEST_RUN_DIR}/run_summary.json"
