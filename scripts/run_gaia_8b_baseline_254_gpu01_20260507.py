from __future__ import annotations

import csv
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
VLLM_PYTHON_CANDIDATES = [
    Path("/data/xsy/miniconda3/envs/vllm_budget/bin/python"),
    Path("/data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python"),
]
VLLM_PYTHON = Path(
    os.environ.get("GAIA_8B_BASELINE_VLLM_PYTHON", "").strip()
    or next((str(path) for path in VLLM_PYTHON_CANDIDATES if path.exists()), str(VLLM_PYTHON_CANDIDATES[0]))
)
CONFIG = ROOT / "configs/system.json"
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

HOST_TAG = os.environ.get("GAIA_8B_BASELINE_HOST_TAG", "254").strip() or "254"
RUN_PREFIX = os.environ.get("GAIA_8B_BASELINE_PREFIX", "").strip() or datetime.now().strftime(
    f"%Y%m%d_%H%M%S_8b_baseline_{HOST_TAG}_gpu01"
)
MASTER_NAME = f"{RUN_PREFIX}_master"
SUMMARY_PATH = QUEUE_LOG_ROOT / f"{MASTER_NAME}_summary.json"
CONCURRENCY = int(os.environ.get("GAIA_8B_BASELINE_CONCURRENCY", "20"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_8B_BASELINE_CODEX_TIMEOUT_SECONDS", "3600"))
VLLM_GPU_MEMORY_UTILIZATION = os.environ.get("GAIA_8B_BASELINE_VLLM_GPU_MEMORY_UTILIZATION", "0.72")
VLLM_MAX_NUM_SEQS = os.environ.get("GAIA_8B_BASELINE_VLLM_MAX_NUM_SEQS", "32")
STOP_CONFLICTING_SERVICES = os.environ.get("GAIA_8B_BASELINE_STOP_CONFLICTING_SERVICES", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CONFLICTING_VLLM_PORTS = [8110, 8111]


@dataclass(frozen=True)
class Lane:
    name: str
    gpu: int
    port: int
    model_path: str = "/data/xsy/codes/checkpoints/Qwen3-8B"
    served_name: str = "Qwen3-8B-local"
    tokenizer_path: str = "/data/xsy/codes/checkpoints/Qwen3-8B"
    max_model_len: int = 40960

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


LANES = [
    Lane(f"{HOST_TAG}_gpu0_8b", 0, 8128),
    Lane(f"{HOST_TAG}_gpu1_8b", 1, 8129),
]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def load_search_runtime_env() -> dict[str, str]:
    try:
        data = json.loads(SEARCH_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    env: dict[str, str] = {}
    if data.get("http_proxy"):
        env["HTTP_PROXY"] = str(data["http_proxy"])
    if data.get("https_proxy"):
        env["HTTPS_PROXY"] = str(data["https_proxy"])
    no_proxy = str(data.get("no_proxy", "127.0.0.1,localhost,0.0.0.0"))
    parts = [part.strip() for part in no_proxy.split(",") if part.strip()]
    for item in ["127.0.0.1", "localhost", "0.0.0.0"]:
        if item not in parts:
            parts.append(item)
    env["NO_PROXY"] = ",".join(parts)
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


def append_process_row(
    *,
    kind: str,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
) -> None:
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
        str(cwd),
        run_dir,
        command,
        "",
        notes,
        str(log_path),
    ]
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(row)


def update_process_row(pid: int, status: str, *, exit_code: int | None = None, notes_suffix: str = "") -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[5].strip() != str(pid):
            continue
        while len(row) < 14:
            row.append("")
        row[1] = now()
        row[2] = status
        if status != "running" and not row[7].strip():
            row[7] = now()
        if exit_code is not None:
            row[11] = str(exit_code)
        if notes_suffix:
            row[12] = (row[12] + "; " if row[12].strip() else "") + notes_suffix
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def refresh_process_registry() -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[2].strip() != "running":
            continue
        pid_text = row[5].strip()
        if not pid_text.isdigit():
            continue
        row[1] = now()
        if not pid_alive(int(pid_text)):
            row[2] = "dead"
            if not row[7].strip():
                row[7] = now()
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def find_vllm_pid(port: int) -> int | None:
    result = subprocess.run(
        ["ps", "-eo", "pid,args"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    marker = f"--port {port}"
    for line in result.stdout.splitlines():
        if "vllm.entrypoints.openai.api_server" not in line:
            continue
        if marker not in line:
            continue
        pid_text = line.strip().split(maxsplit=1)[0]
        if pid_text.isdigit():
            return int(pid_text)
    return None


def endpoint_models(base_url: str) -> list[str]:
    try:
        request = Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    except Exception:
        return []


def stop_pid_group(pid: int, status: str) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
        update_process_row(pid, status)
        log(f"sent SIGTERM to pid group {pid}: {status}")
    except ProcessLookupError:
        update_process_row(pid, "dead")
        return
    deadline = time.time() + 45
    while time.time() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(1)
    try:
        os.killpg(pid, signal.SIGKILL)
        update_process_row(pid, f"{status}_sigkill")
        log(f"sent SIGKILL to pid group {pid}: {status}")
    except ProcessLookupError:
        update_process_row(pid, status)


def stop_conflicting_services() -> list[int]:
    stopped: list[int] = []
    if not STOP_CONFLICTING_SERVICES:
        return stopped
    for port in CONFLICTING_VLLM_PORTS:
        pid = find_vllm_pid(port)
        if pid is None or not pid_alive(pid):
            continue
        models = endpoint_models(f"http://127.0.0.1:{port}/v1")
        if "Qwen3-8B-local" in models:
            continue
        stop_pid_group(pid, f"stopped_for_{RUN_PREFIX}_8b_baseline")
        stopped.append(pid)
    return stopped


def vllm_cmd(lane: Lane) -> list[str]:
    return [
        str(VLLM_PYTHON),
        "-u",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        lane.model_path,
        "--served-model-name",
        lane.served_name,
        "--host",
        "127.0.0.1",
        "--port",
        str(lane.port),
        "--tensor-parallel-size",
        "1",
        "--gpu-memory-utilization",
        VLLM_GPU_MEMORY_UTILIZATION,
        "--dtype",
        "bfloat16",
        "--trust-remote-code",
        "--enable-prefix-caching",
        "--reasoning-parser",
        "qwen3",
        "--reasoning-config",
        '{"reasoning_start_str":"<think>","reasoning_end_str":"</think>"}',
        "--max-model-len",
        str(lane.max_model_len),
        "--max-num-seqs",
        VLLM_MAX_NUM_SEQS,
    ]


def start_or_reuse_vllm(lane: Lane) -> int | None:
    models = endpoint_models(lane.base_url)
    existing_pid = find_vllm_pid(lane.port)
    if lane.served_name in models:
        log(f"reuse {lane.name} endpoint={lane.base_url} pid={existing_pid or 'unknown'}")
        return existing_pid
    if existing_pid is not None and pid_alive(existing_pid):
        stop_pid_group(existing_pid, f"stopped_wrong_model_before_{RUN_PREFIX}_{lane.name}")
        time.sleep(5)

    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{RUN_PREFIX}_{lane.name}_vllm_p{lane.port}.log"
    cmd = vllm_cmd(lane)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(lane.gpu)
    env["PYTHONUNBUFFERED"] = "1"
    with log_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    append_process_row(
        kind="model_server",
        name=f"{RUN_PREFIX}_{lane.name}_vllm_p{lane.port}",
        pid=proc.pid,
        cwd=ROOT,
        run_dir=lane.model_path,
        command=shell_join(["env", f"CUDA_VISIBLE_DEVICES={lane.gpu}", "PYTHONUNBUFFERED=1", *cmd]),
        log_path=log_path,
        notes=(
            f"local Qwen3-8B baseline vLLM; lane={lane.name}; "
            f"max_model_len={lane.max_model_len}; "
            f"gpu_memory_utilization={VLLM_GPU_MEMORY_UTILIZATION}; max_num_seqs={VLLM_MAX_NUM_SEQS}."
        ),
    )
    log(f"started {lane.name} endpoint={lane.base_url} pid={proc.pid} log={log_path}")
    return proc.pid


def wait_endpoints() -> None:
    deadline = time.time() + int(os.environ.get("GAIA_8B_BASELINE_ENDPOINT_WAIT_SECONDS", "1800"))
    while True:
        missing = [lane for lane in LANES if lane.served_name not in endpoint_models(lane.base_url)]
        if not missing:
            log("selected 8B endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        if time.time() >= deadline:
            raise RuntimeError("8B endpoints not ready: " + ", ".join(lane.base_url for lane in missing))
        log("waiting 8B endpoints: " + ", ".join(f"{lane.name}:{lane.base_url}" for lane in missing))
        time.sleep(30)


def common_env(lane: Lane) -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "ALL_PROXY",
        "all_proxy",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_THINKING_BUDGET",
        "NLRL_LLM_THINKING_TOKEN_BUDGET",
        "NLRL_LLM_THINKING_BUDGET",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS",
    ]:
        env.pop(key, None)
    env.update(load_search_runtime_env())
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
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_RUNTIME_CONFIG),
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": "24",
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
            "NLRL_CRITIC_SHARD_CONCURRENCY": "1",
            "NLRL_EXECUTOR_MODEL": lane.served_name,
            "NLRL_EXECUTOR_BASE_URL": lane.base_url,
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_EXECUTOR_MAX_TOKENS": "12288",
            "NLRL_EXECUTOR_TOKENIZER_PATH": lane.tokenizer_path,
            "NLRL_EXECUTOR_MAX_MODEL_LEN": str(lane.max_model_len),
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": "512",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_ACTOR_MODEL": os.environ.get("GAIA_8B_BASELINE_ACTOR_MODEL", "gpt-5.4"),
            "NLRL_ACTOR_BASE_URL": os.environ.get("GAIA_8B_BASELINE_ACTOR_BASE_URL", "codex-cli"),
            "NLRL_ACTOR_API_KEY": os.environ.get("GAIA_8B_BASELINE_ACTOR_API_KEY", "EMPTY"),
            "NLRL_ACTOR_API_MODE": os.environ.get("GAIA_8B_BASELINE_ACTOR_API_MODE", "codex_cli"),
            "NLRL_ACTOR_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
            "NLRL_CRITIC_MODEL": os.environ.get("GAIA_8B_BASELINE_CRITIC_MODEL", "gpt-5.4"),
            "NLRL_CRITIC_BASE_URL": os.environ.get("GAIA_8B_BASELINE_CRITIC_BASE_URL", "codex-cli"),
            "NLRL_CRITIC_API_KEY": os.environ.get("GAIA_8B_BASELINE_CRITIC_API_KEY", "EMPTY"),
            "NLRL_CRITIC_API_MODE": os.environ.get("GAIA_8B_BASELINE_CRITIC_API_MODE", "codex_cli"),
            "NLRL_CRITIC_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
            "NLRL_TOOL_BASE_URL": os.environ.get("NLRL_TOOL_BASE_URL", "http://35.220.164.252:3888/v1"),
            "NLRL_TOOL_API_KEY": os.environ.get(
                "NLRL_TOOL_API_KEY",
                "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            ),
            "NLRL_TOOL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_MODEL": os.environ.get("NLRL_TOOL_MODEL", "gpt-4o-mini"),
            "NLRL_TOOL_AUDIO_MODEL": os.environ.get("NLRL_TOOL_AUDIO_MODEL", "gpt-4o-mini-transcribe"),
        }
    )
    return env


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "GAIA_8B_BASELINE_PREFIX",
        "GAIA_8B_BASELINE_HOST_TAG",
        "GAIA_8B_BASELINE_CONCURRENCY",
        "GAIA_8B_BASELINE_CODEX_TIMEOUT_SECONDS",
        "GAIA_8B_BASELINE_VLLM_PYTHON",
        "GAIA_8B_BASELINE_STOP_CONFLICTING_SERVICES",
        "GAIA_8B_BASELINE_VLLM_GPU_MEMORY_UTILIZATION",
        "GAIA_8B_BASELINE_VLLM_MAX_NUM_SEQS",
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "CUDA_VISIBLE_DEVICES",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_API_MODE",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_BASE_URL",
        "NLRL_ACTOR_API_MODE",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_BASE_URL",
        "NLRL_CRITIC_API_MODE",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_MODEL",
    ]
    return {key: env[key] for key in keys if env.get(key)}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def start_eval(label: str, dataset_path: Path, lane: Lane) -> subprocess.Popen[str]:
    run_name = f"{RUN_PREFIX}_8b_baseline_{label}_{lane.name}_c{CONCURRENCY}"
    log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "direct-eval-local",
        "--dataset-path",
        str(dataset_path),
        "--run-name",
        run_name,
    ]
    env = common_env(lane)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    append_process_row(
        kind="experiment",
        name=run_name,
        pid=proc.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes=(
            f"8B direct baseline {label}; no bootstrap; no initial skill; "
            f"lane={lane.name}; endpoint={lane.base_url}; c{CONCURRENCY}."
        ),
    )
    log(f"started baseline {label} pid={proc.pid} lane={lane.name} log={log_path}")
    return proc


def write_summary(status: str, **extra: object) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": MASTER_NAME,
        "run_prefix": RUN_PREFIX,
        "updated_at": now(),
        "status": status,
        "lanes": [asdict(lane) for lane in LANES],
        "config": {
            "model": "Qwen3-8B-local",
            "tokenizer_path": "/data/xsy/codes/checkpoints/Qwen3-8B",
            "max_model_len": 40960,
            "max_tokens": 12288,
            "token_guard_safety_margin": 512,
            "max_context_chars": 0,
            "thinking_token_budget": None,
            "max_executor_steps": 24,
            "task_concurrency": CONCURRENCY,
            "vllm_gpu_memory_utilization": VLLM_GPU_MEMORY_UTILIZATION,
            "vllm_max_num_seqs": VLLM_MAX_NUM_SEQS,
            "tool_profile": "atomic_v2",
            "answer_acceptance_policy": "any_phase",
            "runner": "direct-eval-local",
            "bootstrap": "disabled",
            "initial_skill": None,
        },
        **extra,
    }
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait_processes(processes: dict[str, subprocess.Popen[str]]) -> None:
    failures: dict[str, int] = {}
    for label, proc in processes.items():
        code = proc.wait()
        update_process_row(proc.pid, "finished" if code == 0 else "dead", exit_code=code)
        if code != 0:
            failures[label] = code
    if failures:
        raise RuntimeError(f"baseline failures: {failures}")


def main() -> int:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    master_log = QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"
    append_process_row(
        kind="experiment_queue",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir=f"(8B direct baseline {HOST_TAG} GPU0/GPU1 master)",
        command=command_display(os.environ, [str(PYTHON), *sys.argv]),
        log_path=master_log,
        notes=f"8B direct baseline master on {HOST_TAG}; dev on GPU0, test on GPU1; summary={SUMMARY_PATH}.",
    )
    status = "finished"
    write_summary("starting")
    try:
        refresh_process_registry()
        stopped = stop_conflicting_services()
        vllm_pids = [start_or_reuse_vllm(lane) for lane in LANES]
        wait_endpoints()
        write_summary("vllm_ready", stopped_conflicting_pids=stopped, vllm_pids=vllm_pids)
        processes = {
            "dev": start_eval("dev", DEV_DATASET, LANES[0]),
            "test": start_eval("test", TEST_DATASET, LANES[1]),
        }
        write_summary(
            "evals_running",
            stopped_conflicting_pids=stopped,
            vllm_pids=vllm_pids,
            eval_pids={label: proc.pid for label, proc in processes.items()},
        )
        wait_processes(processes)
        write_summary(
            "finished",
            stopped_conflicting_pids=stopped,
            vllm_pids=vllm_pids,
            eval_pids={label: proc.pid for label, proc in processes.items()},
        )
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        write_summary(status)
        raise
    except Exception as exc:
        status = "dead"
        write_summary(status, error=str(exc))
        raise
    finally:
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
