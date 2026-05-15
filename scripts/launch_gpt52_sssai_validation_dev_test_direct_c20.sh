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

export NLRL_RUNTIME_TASK_CONCURRENCY="${NLRL_RUNTIME_TASK_CONCURRENCY:-20}"
export NLRL_LLM_MAX_CONCURRENT_REQUESTS="${NLRL_LLM_MAX_CONCURRENT_REQUESTS:-20}"
export NLRL_EXECUTOR_MODEL="${NLRL_EXECUTOR_MODEL:-gpt-5.2}"
export NLRL_EXECUTOR_BASE_URL="${NLRL_EXECUTOR_BASE_URL:-https://node-hk.sssaicode.com/api/v1/responses}"
export NLRL_EXECUTOR_API_KEY="${NLRL_EXECUTOR_API_KEY:-sk-sssaicode-9d340d3064511a95fb7f428f2356dfc7a08c724271a66aa1bdc72a3b2af85cb4}"
export NLRL_EXECUTOR_API_MODE="${NLRL_EXECUTOR_API_MODE:-responses_sse}"
export NLRL_EXECUTOR_STREAM="${NLRL_EXECUTOR_STREAM:-1}"
export NLRL_EXECUTOR_TIMEOUT_SECONDS="${NLRL_EXECUTOR_TIMEOUT_SECONDS:-1200}"
export NLRL_EXECUTOR_TEMPERATURE="${NLRL_EXECUTOR_TEMPERATURE:-0.1}"
GAIA_GPT52_SSSAI_REASONING_EFFORT="${GAIA_GPT52_SSSAI_REASONING_EFFORT-}"
GAIA_GPT52_SSSAI_MAX_OUTPUT_TOKENS="${GAIA_GPT52_SSSAI_MAX_OUTPUT_TOKENS-4096}"
if [[ -n "${GAIA_GPT52_SSSAI_MAX_OUTPUT_TOKENS}" && ! "${GAIA_GPT52_SSSAI_MAX_OUTPUT_TOKENS}" =~ ^[0-9]+$ ]]; then
  echo "GAIA_GPT52_SSSAI_MAX_OUTPUT_TOKENS must be a non-negative integer" >&2
  exit 2
fi
unset NLRL_EXECUTOR_REASONING
unset NLRL_EXECUTOR_ENABLE_THINKING
unset NLRL_LLM_REASONING_EFFORT
unset NLRL_LLM_REASONING
unset NLRL_LLM_MAX_TOKENS
if [[ -n "${GAIA_GPT52_SSSAI_REASONING_EFFORT}" ]]; then
  export NLRL_EXECUTOR_REASONING_EFFORT="${GAIA_GPT52_SSSAI_REASONING_EFFORT}"
else
  unset NLRL_EXECUTOR_REASONING_EFFORT
fi
if [[ -n "${GAIA_GPT52_SSSAI_MAX_OUTPUT_TOKENS}" && "${GAIA_GPT52_SSSAI_MAX_OUTPUT_TOKENS}" != "0" ]]; then
  export NLRL_EXECUTOR_MAX_TOKENS="${GAIA_GPT52_SSSAI_MAX_OUTPUT_TOKENS}"
else
  unset NLRL_EXECUTOR_MAX_TOKENS
fi

unset NLRL_RUNTIME_INITIAL_SKILL_PATH
unset NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL

PY=/data/xsy/project_gaia_skillrl/.venv/bin/python
CONFIG=/data/xsy/project_gaia_skillrl/configs/system.json
RUN_PREFIX="${RUN_PREFIX:-$(date +%Y%m%d_%H%M%S)}"

echo "[$(date -Iseconds)] Starting GAIA direct gpt-5.2 validation_dev + validation_test eval"
echo "CWD=$(pwd)"
echo "PY=${PY}"
echo "CONFIG=${CONFIG}"
echo "NLRL_RUNTIME_TASK_CONCURRENCY=${NLRL_RUNTIME_TASK_CONCURRENCY}"
echo "NLRL_LLM_MAX_CONCURRENT_REQUESTS=${NLRL_LLM_MAX_CONCURRENT_REQUESTS}"
echo "NLRL_EXECUTOR_MODEL=${NLRL_EXECUTOR_MODEL}"
echo "NLRL_EXECUTOR_BASE_URL=${NLRL_EXECUTOR_BASE_URL}"
echo "NLRL_EXECUTOR_API_MODE=${NLRL_EXECUTOR_API_MODE}"
echo "NLRL_EXECUTOR_STREAM=${NLRL_EXECUTOR_STREAM}"
echo "NLRL_EXECUTOR_REASONING_EFFORT=${NLRL_EXECUTOR_REASONING_EFFORT:-<omitted>}"
echo "NLRL_EXECUTOR_MAX_TOKENS=${NLRL_EXECUTOR_MAX_TOKENS:-<omitted>}"

run_split() {
  local split_label="$1"
  local dataset="$2"
  local count_label="$3"
  local run_name="${RUN_PREFIX}_gaia_${split_label}${count_label}_direct_gpt52_sssai_c20_noreasoning_noskill"

  echo "[$(date -Iseconds)] Starting ${split_label} ${count_label}"
  echo "DATASET=${dataset}"
  echo "RUN_NAME=${run_name}"
  echo "Command: ${PY} -m gaia_skillrl.cli --config ${CONFIG} direct-eval-local --dataset-path ${dataset} --run-name ${run_name}"

  "${PY}" -m gaia_skillrl.cli \
    --config "${CONFIG}" \
    direct-eval-local \
    --dataset-path "${dataset}" \
    --run-name "${run_name}"

  local run_dir
  run_dir="$(find /data/xsy/project_gaia_skillrl/runs -type d -name "${run_name}" | sort | tail -n 1)"
  echo "[$(date -Iseconds)] Finished ${split_label}"
  echo "RUN_DIR=${run_dir}"
  if [[ -f "${run_dir}/run_summary.json" ]]; then
    "${PY}" - "${run_dir}/run_summary.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
success = data.get("success_count")
count = data.get("task_count")
acc = success / count if count else 0.0
print(f"success_count={success}/{count} acc={acc:.6f}")
PY
  fi
}

run_split \
  "validation_dev" \
  "/data/xsy/project_gaia_skillrl/data/converted/gaia_2023_all_validation_dev_tasks.json" \
  "83"

run_split \
  "validation_test" \
  "/data/xsy/project_gaia_skillrl/data/converted/gaia_2023_all_validation_test_tasks.json" \
  "82"

echo "[$(date -Iseconds)] All requested splits finished"
