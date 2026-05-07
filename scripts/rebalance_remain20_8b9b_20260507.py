from __future__ import annotations

import csv
import json
import os
import shlex
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

import pexpect


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
RUN_BASE = ROOT / "runs/2026/5/2026-5-7"
QUEUE_LOG_ROOT = ROOT / "runs/_queue_logs"
LAUNCH_LOG_ROOT = ROOT / "runs/_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

REMOTE_HOST = "124.115.123.132"
REMOTE_PORT = "22219"
REMOTE_USER = "xsy"
REMOTE_PASSWORD = "xsy945@j8ca0"

PREFIX = "20260507_1038_rebalance_8b9b_remain20"
STATUS_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_launch_manifest.json"

DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"

SEARCH_ENV = {
    "HTTP_PROXY": "http://127.0.0.1:17890",
    "HTTPS_PROXY": "http://127.0.0.1:17890",
    "NO_PROXY": "127.0.0.1,localhost,0.0.0.0",
}

EIGHT_B_RUNS = [
    {
        "label": "8b_bootv4_B1_dev",
        "run_name": "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_8b_bootv4_B1_dev_eval_c20",
        "dataset": DEV_DATASET,
        "stop_for_rebalance": False,
    },
    {
        "label": "8b_bootv4_B2_dev",
        "run_name": "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_8b_bootv4_B2_dev_eval_c20",
        "dataset": DEV_DATASET,
        "stop_for_rebalance": True,
    },
    {
        "label": "8b_bootv3_B1_dev",
        "run_name": "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_8b_bootv3_B1_dev_eval_c20",
        "dataset": DEV_DATASET,
        "stop_for_rebalance": True,
    },
    {
        "label": "8b_bootv3_B1_test",
        "run_name": "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_8b_bootv3_B1_test_eval_c20",
        "dataset": TEST_DATASET,
        "stop_for_rebalance": True,
    },
    {
        "label": "8b_bootv3_B2_dev",
        "run_name": "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_8b_bootv3_B2_dev_eval_c20",
        "dataset": DEV_DATASET,
        "stop_for_rebalance": True,
    },
]

NINE_B_RUNS = [
    ("9b_bootv4_B1_dev", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv4_B1_dev_eval_c20", DEV_DATASET),
    ("9b_bootv4_B1_test", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv4_B1_test_eval_c20", TEST_DATASET),
    ("9b_bootv3_B1_dev", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv3_B1_dev_eval_c20_oversub_refill_c20", DEV_DATASET),
    ("9b_bootv3_B1_test", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv3_B1_test_eval_c20_oversub_refill_c20", TEST_DATASET),
    ("9b_bootv3_B2_dev", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv3_B2_dev_eval_c20_oversub_refill_c20", DEV_DATASET),
    ("9b_bootv3_B2_test", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv3_B2_test_eval_c20_oversub_refill_c20", TEST_DATASET),
    ("9b_bootv3_B3_dev", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv3_B3_dev_eval_c20_oversub_refill_c20", DEV_DATASET),
    ("9b_bootv3_B3_test", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv3_B3_test_eval_c20_oversub_refill_c20", TEST_DATASET),
    ("9b_bootv4_B2_dev", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv4_B2_dev_eval_c20_oversub_refill_c20", DEV_DATASET),
    ("9b_bootv4_B2_test", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv4_B2_test_eval_c20_oversub_refill_c20", TEST_DATASET),
    ("9b_bootv4_B3_dev", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv4_B3_dev_eval_c20_oversub_refill_c20", DEV_DATASET),
    ("9b_bootv4_B3_test", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv4_B3_test_eval_c20_oversub_refill_c20", TEST_DATASET),
]

NEW_9B_ENDPOINTS = [
    ("gpu3_9b_rebalanced", "http://127.0.0.1:18123/v1"),
    ("gpu4_9b_rebalanced", "http://127.0.0.1:18124/v1"),
    ("gpu5_9b_rebalanced", "http://127.0.0.1:18125/v1"),
    ("gpu6_9b_rebalanced", "http://127.0.0.1:18126/v1"),
    ("gpu7_9b_rebalanced", "http://127.0.0.1:18127/v1"),
]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def run_remote(command: str, timeout: int = 180) -> str:
    child = pexpect.spawn(
        "ssh",
        [
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "UserKnownHostsFile=/data/xsy/.ssh/known_hosts",
            "-p",
            REMOTE_PORT,
            f"{REMOTE_USER}@{REMOTE_HOST}",
            command,
        ],
        timeout=timeout,
        encoding="utf-8",
    )
    output: list[str] = []
    while True:
        index = child.expect(["password:", "yes/no", pexpect.EOF, pexpect.TIMEOUT])
        output.append(child.before)
        if index == 0:
            child.sendline(REMOTE_PASSWORD)
        elif index == 1:
            child.sendline("yes")
        elif index == 2:
            break
        else:
            raise TimeoutError("remote ssh command timed out")
    return "".join(output)


def deploy_remote_9b_services() -> str:
    remote_script = r"""
set -u
mkdir -p /data/xsy/project_gaia_skillrl/runs/_launch_logs
for port in 8123 8124 8125 8126 8127; do
  pids=$(ps -eo pid,args | awk -v p="$port" '$0 ~ /vllm.entrypoints.openai.api_server/ && index($0, "--port " p) {print $1}')
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
done
sleep 8
for port in 8123 8124 8125 8126 8127; do
  pids=$(ps -eo pid,args | awk -v p="$port" '$0 ~ /vllm.entrypoints.openai.api_server/ && index($0, "--port " p) {print $1}')
  for pid in $pids; do kill -9 "$pid" 2>/dev/null || true; done
done
for spec in 3:8123 4:8124 5:8125 6:8126 7:8127; do
  gpu=${spec%%:*}
  port=${spec##*:}
  log=/data/xsy/project_gaia_skillrl/runs/_launch_logs/20260507_1038_remote132_qwen35_9b_gpu${gpu}_p${port}_rebalance.log
  CUDA_VISIBLE_DEVICES=$gpu nohup /data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python -u -m vllm.entrypoints.openai.api_server \
    --model /data/xsy/codes/checkpoints/Qwen3.5-9B \
    --served-model-name Qwen3.5-9B-local \
    --host 127.0.0.1 \
    --port $port \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.90 \
    --dtype bfloat16 \
    --trust-remote-code \
    --enable-prefix-caching \
    --reasoning-parser qwen3 \
    --reasoning-config '{"reasoning_start_str":"<think>","reasoning_end_str":"</think>"}' \
    --max-model-len 49152 \
    --max-num-seqs 128 > "$log" 2>&1 < /dev/null &
  echo "started gpu=$gpu port=$port pid=$! log=$log"
done
"""
    return run_remote(f"bash -lc {shlex.quote(remote_script)}", timeout=120)


def endpoint_models(base_url: str) -> list[str]:
    try:
        req = Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urlopen(req, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    except Exception:
        return []


def wait_new_9b_endpoints() -> None:
    deadline = time.time() + 420
    while time.time() < deadline:
        missing = [url for _, url in NEW_9B_ENDPOINTS if "Qwen3.5-9B-local" not in endpoint_models(url)]
        if not missing:
            log("new 9B endpoints ready: " + ", ".join(url for _, url in NEW_9B_ENDPOINTS))
            return
        log("waiting new 9B endpoints: " + ", ".join(missing))
        time.sleep(15)
    raise RuntimeError("new 9B endpoints did not become ready")


def latest_run_dir(run_name: str) -> Path:
    tail = "_".join(run_name.split("_")[2:])
    hits = sorted(RUN_BASE.glob("*_" + tail))
    if not hits:
        raise FileNotFoundError(run_name)
    return hits[-1]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def selected_task_ids(run_dir: Path) -> list[str]:
    return [str(item) for item in load_json(run_dir / "selected_tasks.json").get("task_ids", [])]


def state_task_ids(run_dir: Path) -> set[str]:
    ids: set[str] = set()
    for state_path in run_dir.glob("iteration_01/*/state.json"):
        try:
            data = load_json(state_path)
            ids.add(str(data.get("task_id") or state_path.parent.name))
        except Exception:
            ids.add(state_path.parent.name)
    return ids


def initial_skill_path(run_dir: Path) -> str:
    cfg = load_json(run_dir / "config_snapshot.json")
    base = cfg.get("base_config", cfg)
    return str(base["runtime"]["initial_skill_path"])


def missing_for(run_name: str, supplemental_tails: list[str] | None = None) -> tuple[Path, list[str], str]:
    run_dir = latest_run_dir(run_name)
    done = state_task_ids(run_dir)
    for tail_name in supplemental_tails or []:
        for tail_dir in RUN_BASE.glob("*_" + tail_name):
            done |= state_task_ids(tail_dir)
    missing = [task_id for task_id in selected_task_ids(run_dir) if task_id not in done]
    return run_dir, missing, initial_skill_path(run_dir)


def kill_local_runs(patterns: list[str]) -> list[int]:
    killed: list[int] = []
    ps = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, errors="replace")
    for line in ps.splitlines():
        if not any(pattern in line for pattern in patterns):
            continue
        if "rg " in line or "rebalance_remain20_8b9b_20260507.py" in line:
            continue
        try:
            pid = int(line.strip().split(maxsplit=1)[0])
        except Exception:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
    return killed


def base_env(model: str, base_url: str, tokenizer: str, max_model_len: int, concurrency: int, skill_path: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "ALL_PROXY",
        "all_proxy",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_THINKING_BUDGET",
        "NLRL_LLM_THINKING_TOKEN_BUDGET",
        "NLRL_LLM_THINKING_BUDGET",
    ]:
        env.pop(key, None)
    env.update(SEARCH_ENV)
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": "",
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "BLIS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "NUMBA_NUM_THREADS": "1",
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": "24",
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(concurrency),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(concurrency),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
            "NLRL_RUNTIME_INITIAL_SKILL_PATH": skill_path,
            "NLRL_EXECUTOR_MODEL": model,
            "NLRL_EXECUTOR_BASE_URL": base_url,
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_EXECUTOR_MAX_TOKENS": "12288",
            "NLRL_EXECUTOR_TOKENIZER_PATH": tokenizer,
            "NLRL_EXECUTOR_MAX_MODEL_LEN": str(max_model_len),
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": "512",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_ACTOR_MODEL": "gpt-5.4",
            "NLRL_ACTOR_BASE_URL": "codex-cli",
            "NLRL_ACTOR_API_KEY": "EMPTY",
            "NLRL_ACTOR_API_MODE": "codex_cli",
            "NLRL_ACTOR_TIMEOUT_SECONDS": "3600",
            "NLRL_CRITIC_MODEL": "gpt-5.4",
            "NLRL_CRITIC_BASE_URL": "codex-cli",
            "NLRL_CRITIC_API_KEY": "EMPTY",
            "NLRL_CRITIC_API_MODE": "codex_cli",
            "NLRL_CRITIC_TIMEOUT_SECONDS": "3600",
            "NLRL_TOOL_BASE_URL": os.environ.get("NLRL_TOOL_BASE_URL", "http://35.220.164.252:3888/v1"),
            "NLRL_TOOL_API_KEY": os.environ.get("NLRL_TOOL_API_KEY", "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ"),
            "NLRL_TOOL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_MODEL": os.environ.get("NLRL_TOOL_MODEL", "gpt-4o-mini"),
            "NLRL_TOOL_AUDIO_MODEL": os.environ.get("NLRL_TOOL_AUDIO_MODEL", "gpt-4o-mini-transcribe"),
        }
    )
    return env


def ensure_process_csv_header() -> None:
    if PROCESS_CSV.exists() and PROCESS_CSV.stat().st_size > 0:
        return
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
                "recorded_at",
                "last_checked_at",
                "status",
                "kind",
                "name",
                "pid",
                "start_time",
                "ended_at",
                "cwd",
                "run_dir",
                "command",
                "exit_code",
                "notes",
                "log_path",
            ]
        )


def append_process_row(kind: str, name: str, pid: int, command: list[str], log_path: Path, notes: str) -> None:
    ensure_process_csv_header()
    row = [
        now(),
        now(),
        "running",
        kind,
        name,
        str(pid),
        now(),
        "",
        str(ROOT),
        "",
        " ".join(command),
        "",
        notes,
        str(log_path),
    ]
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(row)


def launch_eval(
    *,
    run_name: str,
    dataset: Path,
    task_ids: list[str],
    env: dict[str, str],
    notes: str,
) -> dict[str, object]:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "train-local",
        "--dataset-path",
        str(dataset),
        "--run-name",
        run_name,
    ]
    for task_id in task_ids:
        cmd.extend(["--task-id", task_id])
    with log_path.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    append_process_row("gaia_eval_rebalance", run_name, process.pid, cmd, log_path, notes)
    log(f"started {run_name} pid={process.pid} tasks={len(task_ids)} log={log_path}")
    return {"run_name": run_name, "pid": process.pid, "tasks": len(task_ids), "log_path": str(log_path), "notes": notes}


def main() -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"started_at": now(), "prefix": PREFIX, "events": []}

    log("stopping local 8B evals whose endpoints will be converted")
    killed = kill_local_runs([item["run_name"] for item in EIGHT_B_RUNS if item["stop_for_rebalance"]])
    manifest["stopped_local_8b_eval_pids"] = killed
    log(f"stopped local 8B eval pids={killed}")

    log("redeploying remote132 ports 8123-8127 from 8B to 9B")
    remote_output = deploy_remote_9b_services()
    manifest["remote_redeploy_output"] = remote_output
    log(remote_output.strip())

    wait_new_9b_endpoints()

    launched: list[dict[str, object]] = []

    log("launching 8B tail refill on retained endpoint 18122")
    for item in EIGHT_B_RUNS:
        run_dir, missing, skill = missing_for(
            str(item["run_name"]),
            ["20260507_0959_remain20_retry_8b_bootv3_B2_test_missing1_c1"] if item["label"] == "8b_bootv3_B2_test" else None,
        )
        if not missing:
            continue
        run_name = f"{PREFIX}_tail_{item['label']}_c2"
        env = base_env(
            "Qwen3-8B-local",
            "http://127.0.0.1:18122/v1",
            "/data/xsy/codes/checkpoints/Qwen3-8B",
            40960,
            2,
            skill,
        )
        launched.append(
            launch_eval(
                run_name=run_name,
                dataset=Path(str(item["dataset"])),
                task_ids=missing,
                env=env,
                notes=f"8B tail refill after 9B rebalance; source={run_dir}; endpoint=18122",
            )
        )

    log("launching 9B refill on newly converted endpoints")
    endpoint_index = 0
    for label, source_run_name, dataset in NINE_B_RUNS:
        run_dir, missing, skill = missing_for(source_run_name)
        if not missing:
            continue
        endpoint_name, endpoint_url = NEW_9B_ENDPOINTS[endpoint_index % len(NEW_9B_ENDPOINTS)]
        endpoint_index += 1
        run_name = f"{PREFIX}_refill_{label}_{endpoint_name}_c20"
        env = base_env(
            "Qwen3.5-9B-local",
            endpoint_url,
            "/data/xsy/codes/checkpoints/Qwen3.5-9B",
            49152,
            20,
            skill,
        )
        launched.append(
            launch_eval(
                run_name=run_name,
                dataset=dataset,
                task_ids=missing,
                env=env,
                notes=f"9B dynamic refill after converting idle 8B lane; source={run_dir}; endpoint={endpoint_url}",
            )
        )

    manifest["launched"] = launched
    manifest["ended_at"] = now()
    STATUS_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"wrote manifest {STATUS_JSON}")


if __name__ == "__main__":
    main()
