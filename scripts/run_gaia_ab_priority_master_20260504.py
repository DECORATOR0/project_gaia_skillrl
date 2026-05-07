from __future__ import annotations

import csv
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CHILD_QUEUE = ROOT / "scripts/run_gaia_fresh_bootstrap_ab_tail_queue.py"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")
QUEUE_LOG_ROOT = ROOT / "runs/_queue_logs"
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
MASTER_PREFIX = os.environ.get("GAIA_AB_PRIORITY_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_abprio"
)
MASTER_NAME = f"{MASTER_PREFIX}_8b9b_priority_master"
SUMMARY_PATH = QUEUE_LOG_ROOT / f"{MASTER_NAME}_summary.json"


@dataclass(frozen=True)
class PriorityConfig:
    priority: int
    input_cap: str
    thinking: str
    output_cap: str
    score_8b: str
    missing_8b: int
    score_9b: str
    missing_9b: int
    note: str = ""

    @property
    def slug(self) -> str:
        return f"p{self.priority:02d}_{self.input_cap}_{self.thinking}_{self.output_cap}"


PRIORITY_CONFIGS = [
    PriorityConfig(1, "cap49152", "think4k", "out_unlimited", "14/82", 0, "32/82", 0, "main first pick"),
    PriorityConfig(2, "cap49152", "think4k", "out8k", "12/82", 0, "35/82", 1, "best 9B direct score"),
    PriorityConfig(3, "cap49152", "think4k", "out12k", "12/82", 0, "29/82", 0, "bounded output baseline"),
    PriorityConfig(4, "cap49152", "think_unlimited", "out12k", "15/82", 1, "32/82", 1, "best capped-input 8B score"),
    PriorityConfig(5, "cap49152", "think_unlimited", "out8k", "14/82", 2, "28/82", 2),
    PriorityConfig(6, "cap49152", "think_unlimited", "out_unlimited", "12/82", 3, "32/82", 1),
    PriorityConfig(7, "nocap", "think4k", "out_unlimited", "15/82", 2, "34/82", 5, "nocap backup"),
    PriorityConfig(8, "nocap", "think_unlimited", "out8k", "13/82", 3, "36/82", 5, "high 9B score but more missing"),
    PriorityConfig(9, "nocap", "think4k", "out8k", "16/82", 1, "32/82", 4),
    PriorityConfig(10, "nocap", "think4k", "out12k", "14/82", 0, "32/82", 8, "9B direct tail wedged once"),
    PriorityConfig(11, "nocap", "think_unlimited", "out12k", "15/82", 2, "23/82", 5),
    PriorityConfig(12, "nocap", "think_unlimited", "out_unlimited", "8/82", 4, "29/82", 4, "last resort"),
]


MODEL_SPECS = {
    "8b": {
        "model": "Qwen3-8B-local",
        "tokenizer_path": "/data/xsy/codes/checkpoints/Qwen3-8B",
        "max_model_len": "40960",
        "endpoints_env": "GAIA_AB_PRIORITY_8B_ENDPOINTS",
        "endpoints_default": "http://127.0.0.1:8102/v1,http://127.0.0.1:8101/v1",
    },
    "9b": {
        "model": "Qwen3.5-9B-local",
        "tokenizer_path": "/data/xsy/codes/checkpoints/Qwen3.5-9B",
        "max_model_len": "49152",
        "endpoints_env": "GAIA_AB_PRIORITY_9B_ENDPOINTS",
        "endpoints_default": "http://127.0.0.1:8110/v1,http://127.0.0.1:8103/v1",
    },
}


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def load_search_runtime_env() -> dict[str, str]:
    try:
        data = json.loads(SEARCH_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}

    env: dict[str, str] = {}
    http_proxy = str(data.get("http_proxy", "")).strip()
    https_proxy = str(data.get("https_proxy", "")).strip()
    no_proxy = str(data.get("no_proxy", "")).strip()
    if http_proxy:
        env["HTTP_PROXY"] = http_proxy
    if https_proxy:
        env["HTTPS_PROXY"] = https_proxy
    if no_proxy:
        parts = [part.strip() for part in no_proxy.split(",") if part.strip()]
        for local_target in ["127.0.0.1", "localhost", "0.0.0.0"]:
            if local_target not in parts:
                parts.append(local_target)
        env["NO_PROXY"] = ",".join(parts)
    return env


def append_process_row(
    *,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
    kind: str = "experiment_queue",
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


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    keys = [
        "GAIA_AB_PRIORITY_PREFIX",
        "GAIA_AB_PRIORITY_START_PRIORITY",
        "GAIA_AB_PRIORITY_MAX_CONFIGS",
        "GAIA_AB_PRIORITY_CHILD_CONCURRENCY",
        "GAIA_AB_PRIORITY_8B_ENDPOINTS",
        "GAIA_AB_PRIORITY_9B_ENDPOINTS",
        "GAIA_AB_PRIORITY_HTTP_PROXY",
        "GAIA_AB_PRIORITY_HTTPS_PROXY",
        "GAIA_AB_PRIORITY_NO_PROXY",
        "GAIA_AB_PRIORITY_SERIALIZE_EVAL_WAVES",
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "GAIA_FRESH_REQUIRE_CODEX_ACTOR",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_BASE_URL",
        "NLRL_ACTOR_API_MODE",
        "NLRL_ACTOR_TIMEOUT_SECONDS",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_BASE_URL",
        "NLRL_CRITIC_API_MODE",
        "NLRL_CRITIC_TIMEOUT_SECONDS",
        "NLRL_CODEX_CLI_TIMEOUT_SECONDS",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    env_parts = [f"{key}={shlex.quote(env[key])}" for key in keys if env.get(key)]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def parse_endpoints(model_key: str) -> list[str]:
    spec = MODEL_SPECS[model_key]
    raw = os.environ.get(spec["endpoints_env"], spec["endpoints_default"])
    endpoints = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    if not endpoints:
        raise RuntimeError(f"empty endpoint list for {model_key}")
    return endpoints


def lane_url_list(endpoints: list[str]) -> list[str]:
    if len(endpoints) == 1:
        return [endpoints[0]] * 4
    return [endpoints[0], endpoints[1], endpoints[1], endpoints[0]]


def endpoint_ready(base_url: str, timeout_seconds: int = 10) -> bool:
    url = base_url.rstrip("/") + "/models"
    try:
        request = Request(url, headers={"Authorization": "Bearer EMPTY"})
        with urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def wait_for_endpoints(model_key: str, endpoints: list[str]) -> None:
    deadline = time.time() + int(os.environ.get("GAIA_AB_PRIORITY_ENDPOINT_WAIT_SECONDS", "1800"))
    while True:
        ready = [endpoint for endpoint in endpoints if endpoint_ready(endpoint)]
        if len(ready) == len(endpoints):
            log(f"{model_key} endpoints ready: {', '.join(endpoints)}")
            return
        if time.time() >= deadline:
            missing = sorted(set(endpoints) - set(ready))
            raise RuntimeError(f"{model_key} endpoints not ready before deadline: {missing}")
        missing = sorted(set(endpoints) - set(ready))
        log(f"{model_key} waiting endpoints: {', '.join(missing)}")
        time.sleep(30)


def limited_priority_configs() -> list[PriorityConfig]:
    max_configs = int(os.environ.get("GAIA_AB_PRIORITY_MAX_CONFIGS", "12"))
    start_priority = int(os.environ.get("GAIA_AB_PRIORITY_START_PRIORITY", "1"))
    configs = [item for item in PRIORITY_CONFIGS if item.priority >= start_priority]
    if max_configs > 0:
        configs = configs[:max_configs]
    return configs


def apply_config_env(env: dict[str, str], config: PriorityConfig) -> None:
    if config.input_cap not in {"cap49152", "nocap"}:
        raise RuntimeError(f"unknown input cap {config.input_cap}")
    env["NLRL_RUNTIME_MAX_CONTEXT_CHARS"] = "0"

    for key in [
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_THINKING_BUDGET",
        "NLRL_LLM_THINKING_TOKEN_BUDGET",
        "NLRL_LLM_THINKING_BUDGET",
    ]:
        env.pop(key, None)
    if config.thinking == "think4k":
        env["NLRL_EXECUTOR_THINKING_TOKEN_BUDGET"] = "4096"
    elif config.thinking != "think_unlimited":
        raise RuntimeError(f"unknown thinking cap {config.thinking}")

    env.pop("NLRL_EXECUTOR_MAX_TOKENS", None)
    if config.output_cap == "out8k":
        env["NLRL_EXECUTOR_MAX_TOKENS"] = "8192"
    elif config.output_cap == "out12k":
        env["NLRL_EXECUTOR_MAX_TOKENS"] = "12288"
    elif config.output_cap != "out_unlimited":
        raise RuntimeError(f"unknown output cap {config.output_cap}")


def child_env(model_key: str, config: PriorityConfig) -> dict[str, str]:
    spec = MODEL_SPECS[model_key]
    endpoints = parse_endpoints(model_key)
    search_env = load_search_runtime_env()
    env = os.environ.copy()
    for key in ["ALL_PROXY", "all_proxy", "GAIA_QUEUE_CLEANUP_SERVER_PIDS"]:
        env.pop(key, None)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_RUNTIME_CONFIG),
            "GAIA_FRESH_QUEUE_PREFIX": f"{MASTER_PREFIX}_{model_key}_{config.slug}",
            "GAIA_FRESH_QUEUE_CONCURRENCY": os.environ.get("GAIA_AB_PRIORITY_CHILD_CONCURRENCY", "20"),
            "GAIA_FRESH_QUEUE_TAIL_THRESHOLD": os.environ.get("GAIA_AB_PRIORITY_TAIL_THRESHOLD", "3"),
            "GAIA_FRESH_QUEUE_ALLOW_PARTIAL_TAIL": os.environ.get(
                "GAIA_AB_PRIORITY_ALLOW_PARTIAL_TAIL", "1"
            ),
            "GAIA_FRESH_QUEUE_TAIL_GRACE_POLLS": os.environ.get("GAIA_AB_PRIORITY_TAIL_GRACE_POLLS", "3"),
            "GAIA_FRESH_QUEUE_SERIALIZE_EVAL_WAVES": os.environ.get(
                "GAIA_AB_PRIORITY_SERIALIZE_EVAL_WAVES", "1"
            ),
            "GAIA_FRESH_QUEUE_POLL_SECONDS": os.environ.get("GAIA_AB_PRIORITY_POLL_SECONDS", "60"),
            "GAIA_FRESH_OFFLINE_AB_WORKERS": os.environ.get("GAIA_AB_PRIORITY_OFFLINE_WORKERS", "2"),
            "GAIA_FRESH_QUEUE_SKIP_BOOTSTRAP_PREFLIGHT": "1",
            "GAIA_FRESH_REQUIRE_CODEX_ACTOR": "1",
            "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URLS": ",".join(lane_url_list(endpoints)),
            "GAIA_ARCHV5_REMOTE_EXECUTOR_LANES": "4",
            "GAIA_ARCHV5_EXECUTOR_MODEL": spec["model"],
            "NLRL_ACTOR_MODEL": os.environ.get("GAIA_AB_PRIORITY_ACTOR_MODEL", "gpt-5.4"),
            "NLRL_ACTOR_BASE_URL": os.environ.get("GAIA_AB_PRIORITY_ACTOR_BASE_URL", "codex-cli"),
            "NLRL_ACTOR_API_KEY": os.environ.get("GAIA_AB_PRIORITY_ACTOR_API_KEY", "EMPTY"),
            "NLRL_ACTOR_API_MODE": os.environ.get("GAIA_AB_PRIORITY_ACTOR_API_MODE", "codex_cli"),
            "NLRL_ACTOR_TIMEOUT_SECONDS": os.environ.get("GAIA_AB_PRIORITY_ACTOR_TIMEOUT_SECONDS", "1800"),
            "NLRL_CRITIC_MODEL": os.environ.get("GAIA_AB_PRIORITY_CRITIC_MODEL", "gpt-5.4"),
            "NLRL_CRITIC_BASE_URL": os.environ.get("GAIA_AB_PRIORITY_CRITIC_BASE_URL", "codex-cli"),
            "NLRL_CRITIC_API_KEY": os.environ.get("GAIA_AB_PRIORITY_CRITIC_API_KEY", "EMPTY"),
            "NLRL_CRITIC_API_MODE": os.environ.get("GAIA_AB_PRIORITY_CRITIC_API_MODE", "codex_cli"),
            "NLRL_CRITIC_TIMEOUT_SECONDS": os.environ.get("GAIA_AB_PRIORITY_CRITIC_TIMEOUT_SECONDS", "1800"),
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": os.environ.get("GAIA_AB_PRIORITY_CODEX_TIMEOUT_SECONDS", "1800"),
            "NLRL_RUNTIME_TASK_CONCURRENCY": os.environ.get("GAIA_AB_PRIORITY_CHILD_CONCURRENCY", "20"),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": os.environ.get("GAIA_AB_PRIORITY_CHILD_CONCURRENCY", "20"),
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_EXECUTOR_MODEL": spec["model"],
            "NLRL_EXECUTOR_TOKENIZER_PATH": spec["tokenizer_path"],
            "NLRL_EXECUTOR_MAX_MODEL_LEN": spec["max_model_len"],
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": "512",
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_TOOL_BASE_URL": os.environ.get("NLRL_TOOL_BASE_URL", "http://35.220.164.252:3888/v1"),
            "NLRL_TOOL_API_KEY": os.environ.get(
                "NLRL_TOOL_API_KEY",
                "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            ),
            "NLRL_TOOL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_MODEL": os.environ.get("NLRL_TOOL_MODEL", "gpt-4o-mini"),
            "NLRL_TOOL_AUDIO_MODEL": os.environ.get("NLRL_TOOL_AUDIO_MODEL", "gpt-4o-mini-transcribe"),
            "HTTP_PROXY": os.environ.get("GAIA_AB_PRIORITY_HTTP_PROXY")
            or search_env.get("HTTP_PROXY")
            or os.environ.get("HTTP_PROXY", "http://127.0.0.1:17890"),
            "HTTPS_PROXY": os.environ.get("GAIA_AB_PRIORITY_HTTPS_PROXY")
            or search_env.get("HTTPS_PROXY")
            or os.environ.get("HTTPS_PROXY", "http://127.0.0.1:17890"),
            "NO_PROXY": os.environ.get("GAIA_AB_PRIORITY_NO_PROXY")
            or search_env.get("NO_PROXY")
            or os.environ.get("NO_PROXY", "127.0.0.1,localhost,0.0.0.0"),
        }
    )
    apply_config_env(env, config)
    return env


def write_summary(results: list[dict[str, Any]]) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(
            {
                "master": MASTER_NAME,
                "updated_at": now(),
                "configs": [asdict(item) for item in limited_priority_configs()],
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_one_child(model_key: str, config: PriorityConfig) -> dict[str, Any]:
    log_path = QUEUE_LOG_ROOT / f"{MASTER_PREFIX}_{model_key}_{config.slug}.log"
    env = child_env(model_key, config)
    env["NLRL_QUEUE_LOG_PATH"] = str(log_path)
    cmd = [str(PYTHON), "-u", str(CHILD_QUEUE)]
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log(
        f"{model_key} start {config.slug}: endpoints={env['GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URLS']} "
        f"scores 8B={config.score_8b}/miss{config.missing_8b}, "
        f"9B={config.score_9b}/miss{config.missing_9b}"
    )
    started_at = now()
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
    returncode = process.wait()
    ended_at = now()
    status = "finished" if returncode == 0 else "dead"
    log(f"{model_key} done {config.slug}: returncode={returncode} log={log_path}")
    return {
        "model_key": model_key,
        "model": MODEL_SPECS[model_key]["model"],
        "config": asdict(config),
        "status": status,
        "returncode": returncode,
        "started_at": started_at,
        "ended_at": ended_at,
        "log_path": str(log_path),
    }


def model_worker(model_key: str, shared_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    endpoints = parse_endpoints(model_key)
    wait_for_endpoints(model_key, endpoints)
    local_results: list[dict[str, Any]] = []
    continue_on_failure = os.environ.get("GAIA_AB_PRIORITY_CONTINUE_ON_FAILURE", "1").lower() not in {
        "0",
        "false",
        "no",
    }
    for config in limited_priority_configs():
        result = run_one_child(model_key, config)
        local_results.append(result)
        shared_results.append(result)
        write_summary(shared_results)
        if result["returncode"] == 130:
            raise RuntimeError(f"{model_key} {config.slug} was stopped manually")
        if result["returncode"] != 0 and not continue_on_failure:
            raise RuntimeError(f"{model_key} {config.slug} failed with {result['returncode']}")
    return local_results


def main() -> int:
    if not PYTHON.exists() or not CHILD_QUEUE.exists():
        raise RuntimeError(f"missing PYTHON or CHILD_QUEUE: {PYTHON}, {CHILD_QUEUE}")
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    master_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"))
    append_process_row(
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(8B/9B priority master)",
        command=command_display(os.environ, [str(PYTHON), *sys.argv]),
        log_path=master_log,
        notes=(
            "GAIA unattended AB priority queue; two independent workers for 8B and 9B; "
            "each config runs boot_dev, boot_test, offline A/B skill generation, A_dev, A_test, B_dev, B_test; "
            f"summary={SUMMARY_PATH}"
        ),
    )
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    results: list[dict[str, Any]] = []
    write_summary(results)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="model-worker") as pool:
            futures = [pool.submit(model_worker, model_key, results) for model_key in ("8b", "9b")]
            for future in as_completed(futures):
                future.result()
        if any(result["returncode"] != 0 for result in results):
            status = "finished_with_child_failures"
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception:
        status = "dead"
        raise
    finally:
        write_summary(results)
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
