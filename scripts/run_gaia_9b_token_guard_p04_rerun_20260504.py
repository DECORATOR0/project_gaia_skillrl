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
VLLM_PYTHON = Path("/data/xsy/miniconda3/envs/vllm_budget/bin/python")
CHILD_QUEUE = ROOT / "scripts/run_gaia_fresh_bootstrap_ab_tail_queue.py"
CONFIG = ROOT / "configs/system.json"
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

RUN_PREFIX = os.environ.get("GAIA_9B_TOKEN_GUARD_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_9b_p04_token_guard"
)
MASTER_NAME = f"{RUN_PREFIX}_master"
SUMMARY_PATH = QUEUE_LOG_ROOT / f"{MASTER_NAME}_summary.json"
BASELINE_CONCURRENCY = int(os.environ.get("GAIA_9B_TOKEN_GUARD_BASELINE_CONCURRENCY", "20"))
QUEUE_CONCURRENCY = int(os.environ.get("GAIA_9B_TOKEN_GUARD_QUEUE_CONCURRENCY", "20"))


@dataclass(frozen=True)
class Lane:
    name: str
    gpu: int
    port: int
    model_path: str = "/data/xsy/codes/checkpoints/Qwen3.5-9B"
    served_name: str = "Qwen3.5-9B-local"
    max_model_len: int = 49152

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


LANES = [
    Lane("gpu0_9b", 0, 8110),
    Lane("gpu1_9b", 1, 8111),
    Lane("gpu2_9b", 2, 8112),
    Lane("gpu3_9b", 3, 8103),
]
OLD_8B_PORTS = [8101, 8102]


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
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
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


def update_process_row(pid: int, status: str) -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[5].strip() != str(pid):
            continue
        row[1] = now()
        row[2] = status
        if status != "running" and not row[7].strip():
            row[7] = now()
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
        log(f"stopped pid group {pid}: {status}")
    except ProcessLookupError:
        update_process_row(pid, "dead")


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
        "0.0.0.0",
        "--port",
        str(lane.port),
        "--tensor-parallel-size",
        "1",
        "--gpu-memory-utilization",
        "0.9",
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
        "128",
    ]


def stop_old_8b_services() -> None:
    for port in OLD_8B_PORTS:
        pid = find_vllm_pid(port)
        if pid is not None and pid_alive(pid):
            stop_pid_group(pid, f"stopped_before_{MASTER_NAME}_four_9b")


def start_or_reuse_vllm(lane: Lane) -> int | None:
    models = endpoint_models(lane.base_url)
    existing_pid = find_vllm_pid(lane.port)
    if lane.served_name in models:
        log(f"reuse {lane.name} endpoint={lane.base_url} pid={existing_pid or 'unknown'}")
        return existing_pid
    if existing_pid is not None and pid_alive(existing_pid):
        stop_pid_group(existing_pid, f"stopped_wrong_model_before_{MASTER_NAME}_{lane.name}")
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
        notes=f"9B token-guard p04 rerun vLLM; lane={lane.name}; max_model_len={lane.max_model_len}.",
    )
    log(f"started {lane.name} endpoint={lane.base_url} pid={proc.pid} log={log_path}")
    return proc.pid


def wait_endpoints() -> None:
    deadline = time.time() + int(os.environ.get("GAIA_9B_TOKEN_GUARD_ENDPOINT_WAIT_SECONDS", "1800"))
    while True:
        missing = [lane for lane in LANES if lane.served_name not in endpoint_models(lane.base_url)]
        if not missing:
            log("all 9B endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        if time.time() > deadline:
            raise RuntimeError("9B endpoints not ready: " + ", ".join(lane.base_url for lane in missing))
        log("waiting 9B endpoints: " + ", ".join(lane.base_url for lane in missing))
        time.sleep(30)


def common_env(base_url: str | None = None) -> dict[str, str]:
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
    env.update(load_search_runtime_env())
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": "24",
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(BASELINE_CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(BASELINE_CONCURRENCY),
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_EXECUTOR_MODEL": "Qwen3.5-9B-local",
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_EXECUTOR_MAX_TOKENS": "12288",
            "NLRL_EXECUTOR_TOKENIZER_PATH": "/data/xsy/codes/checkpoints/Qwen3.5-9B",
            "NLRL_EXECUTOR_MAX_MODEL_LEN": "49152",
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": "512",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
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
    if base_url is not None:
        env["NLRL_EXECUTOR_BASE_URL"] = base_url
    return env


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URLS",
        "GAIA_FRESH_QUEUE_PREFIX",
        "GAIA_FRESH_QUEUE_CONCURRENCY",
        "GAIA_FRESH_QUEUE_SERIALIZE_EVAL_WAVES",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_API_MODE",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_API_MODE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    return {key: env[key] for key in keys if env.get(key)}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def test_task_ids() -> list[str]:
    payload = json.loads(TEST_DATASET.read_text(encoding="utf-8"))
    return [str(item["task_id"]) for item in payload["tasks"]]


def task_shards(task_ids: list[str]) -> list[list[str]]:
    return [task_ids[idx:: len(LANES)] for idx in range(len(LANES))]


def start_baseline_shard(lane: Lane, shard_index: int, task_ids: list[str]) -> subprocess.Popen[str]:
    run_name = f"{RUN_PREFIX}_baseline_test_shard{shard_index}_{lane.name}_c{BASELINE_CONCURRENCY}"
    log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "direct-eval-local",
        "--dataset-path",
        str(TEST_DATASET),
        "--run-name",
        run_name,
    ]
    for task_id in task_ids:
        cmd.extend(["--task-id", task_id])
    env = common_env(lane.base_url)
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
        run_dir="(direct baseline shard)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes=f"9B token-guard p04 baseline test shard {shard_index}; lane={lane.name}; tasks={len(task_ids)}.",
    )
    log(f"started baseline shard{shard_index} pid={proc.pid} lane={lane.name} tasks={len(task_ids)} log={log_path}")
    return proc


def wait_processes(processes: list[subprocess.Popen[str]], label: str) -> None:
    failures: dict[int, int] = {}
    for proc in processes:
        code = proc.wait()
        update_process_row(proc.pid, "finished" if code == 0 else "dead")
        if code != 0:
            failures[proc.pid] = code
    if failures:
        raise RuntimeError(f"{label} failures: {failures}")


def run_baseline() -> list[dict[str, object]]:
    task_ids = test_task_ids()
    shards = task_shards(task_ids)
    processes = [start_baseline_shard(lane, idx, shard) for idx, (lane, shard) in enumerate(zip(LANES, shards))]
    wait_processes(processes, "baseline")
    return [
        {
            "run_name": f"{RUN_PREFIX}_baseline_test_shard{idx}_{lane.name}_c{BASELINE_CONCURRENCY}",
            "lane": lane.name,
            "base_url": lane.base_url,
            "task_count": len(shard),
        }
        for idx, (lane, shard) in enumerate(zip(LANES, shards))
    ]


def run_boot_ab_queue() -> dict[str, object]:
    queue_prefix = f"{RUN_PREFIX}_bootab"
    log_path = QUEUE_LOG_ROOT / f"{queue_prefix}_fresh_bootstrap_ab_tail_queue_c{QUEUE_CONCURRENCY}.log"
    env = common_env()
    env.update(
        {
            "GAIA_FRESH_QUEUE_PREFIX": queue_prefix,
            "GAIA_FRESH_QUEUE_CONCURRENCY": str(QUEUE_CONCURRENCY),
            "GAIA_FRESH_QUEUE_TAIL_THRESHOLD": os.environ.get("GAIA_9B_TOKEN_GUARD_TAIL_THRESHOLD", "3"),
            "GAIA_FRESH_QUEUE_ALLOW_PARTIAL_TAIL": os.environ.get("GAIA_9B_TOKEN_GUARD_ALLOW_PARTIAL_TAIL", "1"),
            "GAIA_FRESH_QUEUE_TAIL_GRACE_POLLS": os.environ.get("GAIA_9B_TOKEN_GUARD_TAIL_GRACE_POLLS", "3"),
            "GAIA_FRESH_QUEUE_SERIALIZE_EVAL_WAVES": "1",
            "GAIA_FRESH_QUEUE_POLL_SECONDS": os.environ.get("GAIA_9B_TOKEN_GUARD_POLL_SECONDS", "60"),
            "GAIA_FRESH_OFFLINE_AB_WORKERS": "2",
            "GAIA_FRESH_QUEUE_SKIP_BOOTSTRAP_PREFLIGHT": "1",
            "GAIA_FRESH_REQUIRE_CODEX_ACTOR": "1",
            "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URLS": ",".join(lane.base_url for lane in LANES),
            "GAIA_ARCHV5_REMOTE_EXECUTOR_LANES": str(len(LANES)),
            "GAIA_ARCHV5_EXECUTOR_MODEL": "Qwen3.5-9B-local",
            "NLRL_ACTOR_MODEL": os.environ.get("GAIA_9B_TOKEN_GUARD_ACTOR_MODEL", "gpt-5.4"),
            "NLRL_ACTOR_BASE_URL": os.environ.get("GAIA_9B_TOKEN_GUARD_ACTOR_BASE_URL", "codex-cli"),
            "NLRL_ACTOR_API_KEY": os.environ.get("GAIA_9B_TOKEN_GUARD_ACTOR_API_KEY", "EMPTY"),
            "NLRL_ACTOR_API_MODE": os.environ.get("GAIA_9B_TOKEN_GUARD_ACTOR_API_MODE", "codex_cli"),
            "NLRL_ACTOR_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_TOKEN_GUARD_ACTOR_TIMEOUT_SECONDS", "1800"),
            "NLRL_CRITIC_MODEL": os.environ.get("GAIA_9B_TOKEN_GUARD_CRITIC_MODEL", "gpt-5.4"),
            "NLRL_CRITIC_BASE_URL": os.environ.get("GAIA_9B_TOKEN_GUARD_CRITIC_BASE_URL", "codex-cli"),
            "NLRL_CRITIC_API_KEY": os.environ.get("GAIA_9B_TOKEN_GUARD_CRITIC_API_KEY", "EMPTY"),
            "NLRL_CRITIC_API_MODE": os.environ.get("GAIA_9B_TOKEN_GUARD_CRITIC_API_MODE", "codex_cli"),
            "NLRL_CRITIC_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_TOKEN_GUARD_CRITIC_TIMEOUT_SECONDS", "1800"),
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_TOKEN_GUARD_CODEX_TIMEOUT_SECONDS", "1800"),
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(QUEUE_CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(QUEUE_CONCURRENCY),
        }
    )
    cmd = [str(PYTHON), "-u", str(CHILD_QUEUE)]
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
        kind="experiment_queue",
        name=f"{queue_prefix}_fresh_bootstrap_ab_tail_queue_c{QUEUE_CONCURRENCY}",
        pid=proc.pid,
        cwd=ROOT,
        run_dir="(fresh boot+AB queue)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes="9B p04 token-guard/nocap rerun: boot_dev, boot_test, A/B offline actors, A/B dev/test evals.",
    )
    log(f"started boot+AB queue pid={proc.pid} log={log_path}")
    code = proc.wait()
    update_process_row(proc.pid, "finished" if code == 0 else "dead")
    if code != 0:
        raise RuntimeError(f"boot+AB queue failed with returncode={code}; log={log_path}")
    return {
        "queue_prefix": queue_prefix,
        "log_path": str(log_path),
        "returncode": code,
    }


def write_summary(status: str, **extra: object) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": MASTER_NAME,
        "run_prefix": RUN_PREFIX,
        "updated_at": now(),
        "status": status,
        "lanes": [asdict(lane) for lane in LANES],
        "config": {
            "model": "Qwen3.5-9B-local",
            "tokenizer_path": "/data/xsy/codes/checkpoints/Qwen3.5-9B",
            "max_model_len": 49152,
            "max_tokens": 12288,
            "token_guard_safety_margin": 512,
            "max_context_chars": 0,
            "thinking_token_budget": None,
            "max_executor_steps": 24,
            "forced_answer_after_max_steps": True,
        },
        **extra,
    }
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    master_log = QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"
    append_process_row(
        kind="experiment_queue",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(9B token guard p04 rerun master)",
        command=command_display(os.environ, [str(PYTHON), *sys.argv]),
        log_path=master_log,
        notes=f"9B token-guard p04 rerun master; summary={SUMMARY_PATH}",
    )
    status = "finished"
    write_summary("starting")
    try:
        stop_old_8b_services()
        pids = [start_or_reuse_vllm(lane) for lane in LANES]
        wait_endpoints()
        baseline = run_baseline()
        write_summary("baseline_finished", baseline=baseline, vllm_pids=pids)
        queue = run_boot_ab_queue()
        write_summary("finished", baseline=baseline, boot_ab_queue=queue, vllm_pids=pids)
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
