#!/usr/bin/env bash
set -Eeuo pipefail

REGISTRY=${REGISTRY:-/data/xsy/活的进程.csv}
RUN_NAME=${RUN_NAME:?RUN_NAME is required}
SKILL=${SKILL:-/data/xsy/project_gaia_skillrl/runs/2026/4/2026-4-21/20260421_214829_gaia_bootv3_ab_boot_dev_iter1_c20/iteration_01/skill_for_eval/gaia-general-skill/SKILL.md}

update_registry_status() {
  local status="$1"
  python3 - "$REGISTRY" "$$" "$status" <<'PY' || true
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

with path.open("r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.reader(f))

for row in rows[1:]:
    if len(row) >= 8 and row[5].strip() == pid:
        row[1] = now
        row[2] = status
        if status != "running" and not row[7].strip():
            row[7] = now

with path.open("w", encoding="utf-8", newline="") as f:
    csv.writer(f).writerows(rows)
PY
}

trap 's=$?; if [[ "$s" -eq 0 ]]; then update_registry_status finished; else update_registry_status dead; fi; exit "$s"' EXIT

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

export NLRL_RUNTIME_TASK_CONCURRENCY=20
export NLRL_LLM_MAX_CONCURRENT_REQUESTS=20
export NLRL_RUNTIME_ITERATIONS_PER_BATCH=0
export NLRL_RUNTIME_INITIAL_SKILL_PATH="$SKILL"

export NLRL_EXECUTOR_MODEL=Qwen3-8B-local
export NLRL_EXECUTOR_BASE_URL=http://127.0.0.1:8100/v1
export NLRL_EXECUTOR_API_KEY=EMPTY
export NLRL_EXECUTOR_API_MODE=chat_completions
export NLRL_EXECUTOR_STREAM=1
export NLRL_EXECUTOR_ENABLE_THINKING=1
export NLRL_EXECUTOR_TEMPERATURE=0.1
export NLRL_EXECUTOR_TIMEOUT_SECONDS=720
export NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS=720
export NLRL_EXECUTOR_STREAM_INCLUDE_USAGE=1

export NLRL_TOOL_BASE_URL=http://35.220.164.252:3888/v1
export NLRL_TOOL_API_KEY=sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ
export NLRL_TOOL_TIMEOUT_SECONDS=720

echo "[$(date -Iseconds)] start bootstrap original skill on GPU0/8100"
echo "RUN_NAME=${RUN_NAME}"
echo "SKILL=${SKILL}"

/data/xsy/project_gaia_skillrl/.venv/bin/python -m gaia_skillrl.cli \
  --config /data/xsy/project_gaia_skillrl/configs/system.json \
  train-local \
  --dataset-path /data/xsy/project_gaia_skillrl/data/converted/gaia_2023_all_validation_test_tasks.json \
  --run-name "$RUN_NAME"

echo "[$(date -Iseconds)] finished bootstrap original skill on GPU0/8100"
