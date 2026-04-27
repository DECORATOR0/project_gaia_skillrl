#!/usr/bin/env bash
set -Eeuo pipefail

trap 'status=$?; echo "[$(date -Iseconds)] launcher failed: exit_status=${status} line=${LINENO}" >&2; exit "${status}"' ERR

cd /data/xsy/project_gaia_skillrl

export PYTHONUNBUFFERED=1
export PYTHONFAULTHANDLER=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export BLIS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMBA_NUM_THREADS=1

export NLRL_RUNTIME_TASK_CONCURRENCY="${NLRL_RUNTIME_TASK_CONCURRENCY:-30}"
export NLRL_LLM_MAX_CONCURRENT_REQUESTS="${NLRL_LLM_MAX_CONCURRENT_REQUESTS:-30}"
export NLRL_RUNTIME_ITERATIONS_PER_BATCH=0
export NLRL_RUNTIME_INITIAL_SKILL_PATH="${NLRL_RUNTIME_INITIAL_SKILL_PATH:-/data/xsy/project_gaia_skillrl/runs/2026/4/2026-4-22/20260422_101853_gaia_bootv3_ab_B_sharded_offline_actor_iter2/offline_iter2/skill_after_actor/gaia-general-skill/SKILL.md}"

export NLRL_EXECUTOR_TIMEOUT_SECONDS="${NLRL_EXECUTOR_TIMEOUT_SECONDS:-720}"
export NLRL_EXECUTOR_MODEL="${NLRL_EXECUTOR_MODEL:-gpt-5.4}"
export NLRL_EXECUTOR_BASE_URL="${NLRL_EXECUTOR_BASE_URL:-http://127.0.0.1:8317/v1}"
export NLRL_EXECUTOR_API_KEY="${NLRL_EXECUTOR_API_KEY:-067c5abf8dcf32e618660683f8e4995e17fcdd14a5bc00d7fe8be6bd4336dd44176f476098df4c2f566113477ad11e87}"
export NLRL_EXECUTOR_API_MODE="${NLRL_EXECUTOR_API_MODE:-chat_completions}"
export NLRL_EXECUTOR_STREAM=0
export NLRL_EXECUTOR_ENABLE_THINKING="${NLRL_EXECUTOR_ENABLE_THINKING:-0}"
export NLRL_EXECUTOR_TEMPERATURE="${NLRL_EXECUTOR_TEMPERATURE:-0.1}"

PY=/data/xsy/project_gaia_skillrl/.venv/bin/python
CONFIG=/data/xsy/project_gaia_skillrl/configs/system.json
DATASET=/data/xsy/project_gaia_skillrl/data/converted/gaia_2023_all_validation_test_tasks.json
RUN_NAME="${RUN_NAME:-20260425_233408_gaia_gpt54_clipproxy_validation_test82_c30_nonstream_scripted}"

echo "[$(date -Iseconds)] Starting GAIA validation_test82 scripted eval"
echo "RUN_NAME=${RUN_NAME}"
echo "CWD=$(pwd)"
echo "PY=${PY}"
echo "CONFIG=${CONFIG}"
echo "DATASET=${DATASET}"
echo "NLRL_RUNTIME_TASK_CONCURRENCY=${NLRL_RUNTIME_TASK_CONCURRENCY}"
echo "NLRL_LLM_MAX_CONCURRENT_REQUESTS=${NLRL_LLM_MAX_CONCURRENT_REQUESTS}"
echo "NLRL_EXECUTOR_MODEL=${NLRL_EXECUTOR_MODEL}"
echo "NLRL_EXECUTOR_BASE_URL=${NLRL_EXECUTOR_BASE_URL}"
echo "NLRL_EXECUTOR_API_MODE=${NLRL_EXECUTOR_API_MODE}"
echo "NLRL_EXECUTOR_STREAM=${NLRL_EXECUTOR_STREAM}"
echo "NLRL_EXECUTOR_ENABLE_THINKING=${NLRL_EXECUTOR_ENABLE_THINKING}"
echo "NLRL_RUNTIME_INITIAL_SKILL_PATH=${NLRL_RUNTIME_INITIAL_SKILL_PATH}"
echo "Command: ${PY} -m gaia_skillrl.cli --config ${CONFIG} train-local --dataset-path ${DATASET} --run-name ${RUN_NAME}"

exec "${PY}" -m gaia_skillrl.cli \
  --config "${CONFIG}" \
  train-local \
  --dataset-path "${DATASET}" \
  --run-name "${RUN_NAME}"
