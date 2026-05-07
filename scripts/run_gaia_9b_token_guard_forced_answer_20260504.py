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
from typing import Any
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
VLLM_PYTHON = Path("/data/xsy/miniconda3/envs/vllm_budget/bin/python")
CHILD_QUEUE = ROOT / "scripts/run_gaia_fresh_bootstrap_ab_tail_queue.py"
CONFIG = ROOT / "configs/system.json"
DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"

PREFIX = os.environ.get("GAIA_9B_TGUARD_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M_9b_tguard512_forced"
)
SUMMARY_PATH = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"

MODEL_PATH = "/data/xsy/codes/checkpoints/Qwen3.5-9B"
SERVED_MODEL = "Qwen3.5-9B-local"
MAX_MODEL_LEN = 49152
MAX_TOKENS = 12288
TOKEN_GUARD_MARGIN = 512
CONCURRENCY = int(os.environ.get("GAIA_9B_TGUARD_CONCURRENCY", "20"))


@dataclass(frozen=True)
class Lane:
    name: str
    gpu: int
    port: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


LANES = [
    Lane("gpu0_p8110", 0, 8110),
    Lane("gpu1_p8111", 1, 8111),
    Lane("gpu2_p8112", 2, 8112),
    Lane("gpu3_p8103", 3, 8103),
]


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def run_dir_for(run_name: str) -> Path:
    current = datetime.now()
    return RUN_ROOT / str(current.year) / str(current.month) / f"{current.year}-{current.month}-{current.day}" / run_name


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


def load_search_runtime_env() -> dict[str, str]:
    try:
        data = json.loads(SEARCH_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    env: dict[str, str] = {}
    for key, json_key in [
        ("HTTP_PROXY", "http_proxy"),
        ("HTTPS_PROXY", "https_proxy"),
        ("NO_PROXY", "no_proxy"),
    ]:
        value = str(data.get(json_key, "")).strip()
        if value:
            env[key] = value
    if "NO_PROXY" in env:
        parts = [part.strip() for part in env["NO_PROXY"].split(",") if part.strip()]
        for local_target in ("127.0.0.1", "localhost", "0.0.0.0"):
            if local_target not in parts:
                parts.append(local_target)
        env["NO_PROXY"] = ",".join(parts)
    return env


def endpoint_models(base_url: str, timeout_seconds: int = 5) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    request = Request(url, headers={"Authorization": "Bearer EMPTY"})
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]


def endpoint_ready_for_9b(base_url: str) -> bool:
    try:
        return SERVED_MODEL in endpoint_models(base_url)
    except Exception:
        return False


def find_vllm_pid_by_port(port: int) -> int | None:
    result = subprocess.run(
        ["ps", "-eo", "pid,args"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    marker = f"--port {port}"
    for line in result.stdout.splitlines():
        if "vllm.entrypoints.openai.api_server" not in line or marker not in line:
            continue
        parts = line.strip().split(maxsplit=1)
        if parts and parts[0].isdigit():
            return int(parts[0])
    return None


def stop_port_if_not_9b(port: int) -> None:
    base_url = f"http://127.0.0.1:{port}/v1"
    try:
        models = endpoint_models(base_url)
    except Exception:
        models = []
    if SERVED_MODEL in models:
        return
    pid = find_vllm_pid_by_port(port)
    if pid is None:
        return
    log(f"stopping non-9B vLLM on port {port}: pid={pid}, models={models}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 90
    while time.time() < deadline:
        if find_vllm_pid_by_port(port) is None:
            return
        time.sleep(3)
    log(f"SIGKILL non-9B vLLM on port {port}: pid={pid}")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def vllm_cmd(lane: Lane) -> list[str]:
    return [
        str(VLLM_PYTHON),
        "-u",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        MODEL_PATH,
        "--served-model-name",
        SERVED_MODEL,
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
        str(MAX_MODEL_LEN),
        "--max-num-seqs",
        "128",
    ]


def start_or_reuse_vllm(lane: Lane) -> int | None:
    if endpoint_ready_for_9b(lane.base_url):
        pid = find_vllm_pid_by_port(lane.port)
        log(f"reuse 9B vLLM {lane.name}: pid={pid} endpoint={lane.base_url}")
        return pid
    pid = find_vllm_pid_by_port(lane.port)
    if pid is not None:
        stop_port_if_not_9b(lane.port)
    log_path = LAUNCH_LOG_ROOT / f"{PREFIX}_{lane.name}_vllm.log"
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(lane.gpu)
    env["PYTHONUNBUFFERED"] = "1"
    cmd = vllm_cmd(lane)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
    append_process_row(
        kind="model_server",
        name=f"{PREFIX}_{lane.name}_vllm",
        pid=process.pid,
        cwd=ROOT,
        run_dir=MODEL_PATH,
        command=f"CUDA_VISIBLE_DEVICES={lane.gpu} {shlex.join(cmd)} > {shlex.quote(str(log_path))} 2>&1 < /dev/null",
        log_path=log_path,
        notes=f"9B vLLM for token-guard rerun; lane={lane.name}; max_model_len={MAX_MODEL_LEN}.",
    )
    log(f"started 9B vLLM {lane.name}: pid={process.pid} endpoint={lane.base_url} log={log_path}")
    return process.pid


def wait_vllm_ready() -> None:
    deadline = time.time() + int(os.environ.get("GAIA_9B_TGUARD_ENDPOINT_WAIT_SECONDS", "1800"))
    while True:
        missing = [lane for lane in LANES if not endpoint_ready_for_9b(lane.base_url)]
        if not missing:
            log("all 9B endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        if time.time() >= deadline:
            raise RuntimeError("9B endpoints not ready: " + ", ".join(lane.base_url for lane in missing))
        log("waiting 9B endpoints: " + ", ".join(lane.base_url for lane in missing))
        time.sleep(20)


def common_env(base_url: str | None = None) -> dict[str, str]:
    search_env = load_search_runtime_env()
    env = os.environ.copy()
    for key in ("ALL_PROXY", "all_proxy"):
        env.pop(key, None)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_RUNTIME_CONFIG),
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_EXECUTOR_MODEL": SERVED_MODEL,
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_EXECUTOR_MAX_TOKENS": str(MAX_TOKENS),
            "NLRL_EXECUTOR_TOKENIZER_PATH": MODEL_PATH,
            "NLRL_EXECUTOR_MAX_MODEL_LEN": str(MAX_MODEL_LEN),
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": str(TOKEN_GUARD_MARGIN),
            "NLRL_TOOL_BASE_URL": os.environ.get("NLRL_TOOL_BASE_URL", "http://35.220.164.252:3888/v1"),
            "NLRL_TOOL_API_KEY": os.environ.get(
                "NLRL_TOOL_API_KEY",
                "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            ),
            "NLRL_TOOL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_MODEL": os.environ.get("NLRL_TOOL_MODEL", "gpt-4o-mini"),
            "NLRL_TOOL_AUDIO_MODEL": os.environ.get("NLRL_TOOL_AUDIO_MODEL", "gpt-4o-mini-transcribe"),
            "HTTP_PROXY": os.environ.get("GAIA_9B_TGUARD_HTTP_PROXY")
            or search_env.get("HTTP_PROXY")
            or os.environ.get("HTTP_PROXY", "http://127.0.0.1:17890"),
            "HTTPS_PROXY": os.environ.get("GAIA_9B_TGUARD_HTTPS_PROXY")
            or search_env.get("HTTPS_PROXY")
            or os.environ.get("HTTPS_PROXY", "http://127.0.0.1:17890"),
            "NO_PROXY": os.environ.get("GAIA_9B_TGUARD_NO_PROXY")
            or search_env.get("NO_PROXY")
            or os.environ.get("NO_PROXY", "127.0.0.1,localhost,0.0.0.0"),
        }
    )
    if base_url:
        env["NLRL_EXECUTOR_BASE_URL"] = base_url
    return env


def env_display(env: dict[str, str]) -> str:
    keys = [
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
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
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    return " ".join(f"{key}={shlex.quote(env[key])}" for key in keys if env.get(key))


def load_test_task_ids() -> list[str]:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    return [str(task["task_id"]) for task in payload["tasks"]]


def split_evenly(items: list[str], count: int) -> list[list[str]]:
    shards = [[] for _ in range(count)]
    for index, item in enumerate(items):
        shards[index % count].append(item)
    return shards


def launch_baseline_shards() -> list[dict[str, Any]]:
    task_shards = split_evenly(load_test_task_ids(), len(LANES))
    processes: list[tuple[subprocess.Popen[str], Path, str, Lane, int]] = []
    for shard_index, (lane, task_ids) in enumerate(zip(LANES, task_shards), start=1):
        run_name = f"{PREFIX}_9b_baseline_test_shard{shard_index:02d}_of04_c{CONCURRENCY}"
        run_dir = run_dir_for(run_name)
        log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
        cmd = [
            str(PYTHON),
            "-m",
            "gaia_skillrl.cli",
            "--config",
            str(CONFIG),
            "direct-eval-local",
            "--dataset-path",
            str(DATASET),
            "--run-name",
            run_name,
        ]
        for task_id in task_ids:
            cmd.extend(["--task-id", task_id])
        env = common_env(lane.base_url)
        LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
        append_process_row(
            kind="experiment",
            name=run_name,
            pid=process.pid,
            cwd=ROOT,
            run_dir=str(run_dir),
            command=f"env {env_display(env)} {shlex.join(cmd)} > {shlex.quote(str(log_path))} 2>&1 < /dev/null",
            log_path=log_path,
            notes=(
                f"9B direct baseline shard after token_guard/forced_answer runtime patch; "
                f"lane={lane.name}; shard_tasks={len(task_ids)}."
            ),
        )
        log(f"baseline shard {shard_index}/4 started pid={process.pid} lane={lane.name} tasks={len(task_ids)}")
        processes.append((process, log_path, run_name, lane, len(task_ids)))

    results: list[dict[str, Any]] = []
    for process, log_path, run_name, lane, task_count in processes:
        returncode = process.wait()
        status = "finished" if returncode == 0 else "dead"
        update_process_row(process.pid, status)
        log(f"baseline shard done {run_name}: returncode={returncode} log={log_path}")
        results.append(
            {
                "kind": "baseline_shard",
                "run_name": run_name,
                "lane": asdict(lane),
                "task_count": task_count,
                "status": status,
                "returncode": returncode,
                "log_path": str(log_path),
            }
        )
    return results


def launch_boot_ab_queue() -> dict[str, Any]:
    run_prefix = f"{PREFIX}_9b_p04_nocap_token_guard512_think_unlimited_out12k"
    log_path = QUEUE_LOG_ROOT / f"{run_prefix}.log"
    env = common_env()
    env.update(
        {
            "GAIA_FRESH_QUEUE_PREFIX": run_prefix,
            "GAIA_FRESH_QUEUE_CONCURRENCY": str(CONCURRENCY),
            "GAIA_FRESH_QUEUE_TAIL_THRESHOLD": os.environ.get("GAIA_9B_TGUARD_TAIL_THRESHOLD", "3"),
            "GAIA_FRESH_QUEUE_ALLOW_PARTIAL_TAIL": os.environ.get("GAIA_9B_TGUARD_ALLOW_PARTIAL_TAIL", "1"),
            "GAIA_FRESH_QUEUE_TAIL_GRACE_POLLS": os.environ.get("GAIA_9B_TGUARD_TAIL_GRACE_POLLS", "3"),
            "GAIA_FRESH_QUEUE_SERIALIZE_EVAL_WAVES": "1",
            "GAIA_FRESH_QUEUE_POLL_SECONDS": os.environ.get("GAIA_9B_TGUARD_POLL_SECONDS", "60"),
            "GAIA_FRESH_OFFLINE_AB_WORKERS": os.environ.get("GAIA_9B_TGUARD_OFFLINE_WORKERS", "2"),
            "GAIA_FRESH_QUEUE_SKIP_BOOTSTRAP_PREFLIGHT": "1",
            "GAIA_FRESH_REQUIRE_CODEX_ACTOR": "1",
            "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URLS": ",".join(lane.base_url for lane in LANES),
            "GAIA_ARCHV5_REMOTE_EXECUTOR_LANES": str(len(LANES)),
            "GAIA_ARCHV5_EXECUTOR_MODEL": SERVED_MODEL,
            "NLRL_ACTOR_MODEL": os.environ.get("GAIA_9B_TGUARD_ACTOR_MODEL", "gpt-5.4"),
            "NLRL_ACTOR_BASE_URL": os.environ.get("GAIA_9B_TGUARD_ACTOR_BASE_URL", "codex-cli"),
            "NLRL_ACTOR_API_KEY": os.environ.get("GAIA_9B_TGUARD_ACTOR_API_KEY", "EMPTY"),
            "NLRL_ACTOR_API_MODE": os.environ.get("GAIA_9B_TGUARD_ACTOR_API_MODE", "codex_cli"),
            "NLRL_ACTOR_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_TGUARD_ACTOR_TIMEOUT_SECONDS", "1800"),
            "NLRL_CRITIC_MODEL": os.environ.get("GAIA_9B_TGUARD_CRITIC_MODEL", "gpt-5.4"),
            "NLRL_CRITIC_BASE_URL": os.environ.get("GAIA_9B_TGUARD_CRITIC_BASE_URL", "codex-cli"),
            "NLRL_CRITIC_API_KEY": os.environ.get("GAIA_9B_TGUARD_CRITIC_API_KEY", "EMPTY"),
            "NLRL_CRITIC_API_MODE": os.environ.get("GAIA_9B_TGUARD_CRITIC_API_MODE", "codex_cli"),
            "NLRL_CRITIC_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_TGUARD_CRITIC_TIMEOUT_SECONDS", "1800"),
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_TGUARD_CODEX_TIMEOUT_SECONDS", "1800"),
        }
    )
    cmd = [str(PYTHON), "-u", str(CHILD_QUEUE)]
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
    append_process_row(
        kind="experiment_queue",
        name=run_prefix,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(fresh boot_dev/boot_test + offline A/B + four evals)",
        command=f"env {env_display(env)} {shlex.join(cmd)} > {shlex.quote(str(log_path))} 2>&1 < /dev/null",
        log_path=log_path,
        notes="9B clean p04 rerun with chars cap disabled, tokenizer token guard 512, forced answer after max steps.",
    )
    log(f"boot/AB queue started pid={process.pid} prefix={run_prefix} log={log_path}")
    returncode = process.wait()
    status = "finished" if returncode == 0 else "dead"
    update_process_row(process.pid, status)
    log(f"boot/AB queue done returncode={returncode} log={log_path}")
    return {
        "kind": "boot_ab_queue",
        "run_prefix": run_prefix,
        "status": status,
        "returncode": returncode,
        "log_path": str(log_path),
    }


def write_summary(results: list[dict[str, Any]], status: str) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(
            {
                "prefix": PREFIX,
                "updated_at": now(),
                "status": status,
                "model": SERVED_MODEL,
                "model_path": MODEL_PATH,
                "max_model_len": MAX_MODEL_LEN,
                "max_tokens": MAX_TOKENS,
                "token_guard_safety_margin": TOKEN_GUARD_MARGIN,
                "max_context_chars": 0,
                "lanes": [asdict(lane) | {"base_url": lane.base_url} for lane in LANES],
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def command_display() -> str:
    return f"env GAIA_9B_TGUARD_PREFIX={shlex.quote(PREFIX)} {shlex.join([str(PYTHON), '-u', __file__])}"


def main() -> int:
    for path in (PYTHON, VLLM_PYTHON, CHILD_QUEUE, CONFIG, DATASET):
        if not Path(path).exists():
            raise RuntimeError(f"missing required path: {path}")
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    master_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", QUEUE_LOG_ROOT / f"{PREFIX}_master.log"))
    append_process_row(
        kind="experiment_queue",
        name=f"{PREFIX}_master",
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(9B token guard forced answer rerun)",
        command=command_display(),
        log_path=master_log,
        notes=f"summary={SUMMARY_PATH}",
    )
    status = "running"
    results: list[dict[str, Any]] = []
    write_summary(results, status)
    try:
        stop_port_if_not_9b(8101)
        stop_port_if_not_9b(8102)
        for lane in LANES:
            start_or_reuse_vllm(lane)
        wait_vllm_ready()
        baseline_results = launch_baseline_shards()
        results.extend(baseline_results)
        write_summary(results, status)
        if any(item["returncode"] != 0 for item in baseline_results):
            log("baseline shards include nonzero returncode; continuing to boot/AB queue")
        boot_ab_result = launch_boot_ab_queue()
        results.append(boot_ab_result)
        status = "finished" if all(item.get("returncode") == 0 for item in results) else "finished_with_child_failures"
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception:
        status = "dead"
        raise
    finally:
        write_summary(results, status)
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
