from __future__ import annotations

import csv
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
VLLM_PYTHON = Path("/data/xsy/miniconda3/envs/vllm_budget/bin/python")
DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
CONFIG = ROOT / "configs/system.json"
SEARCH_CONFIG = ROOT / "configs/search_runtime.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

ORIGINAL_MANIFEST = QUEUE_LOG_ROOT / "20260503_203208_gaia_notrunc_budget_matrix_c20.json"
ORIGINAL_QUEUE_PID = int(os.environ.get("GAIA_ORIGINAL_QUEUE_PID", "1017777"))
ORIGINAL_STAMP = "20260503_203208"
TAKEOVER_MODE = os.environ.get("GAIA_TAKEOVER_MODE", "after_8b")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
QUEUE_NAME = f"{STAMP}_gaia_9b_takeover_after_8b_c20"
QUEUE_LOG = QUEUE_LOG_ROOT / f"{QUEUE_NAME}.log"
MANIFEST_PATH = QUEUE_LOG_ROOT / f"{QUEUE_NAME}.json"
CONCURRENCY = 20
MAX_MODEL_LEN = 49152
MONITOR_INTERVAL_SECONDS = 300

RUN_RE = re.compile(
    r"notrunc_(8b|9b)_(cap49152|nocap)_(think4k|think_unlimited)_"
    r"(out8k|out12k|out_unlimited)_c20_(gpu\d+_(?:8b|9b))"
)


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


@dataclass(frozen=True)
class Job:
    input_cap: str
    thinking_budget: str
    max_tokens_label: str
    priority: int

    @property
    def key(self) -> tuple[str, str, str, str]:
        return ("9b", self.input_cap, self.thinking_budget, self.max_tokens_label)

    @property
    def max_context_chars(self) -> int:
        return MAX_MODEL_LEN if self.input_cap == "cap49152" else 0

    @property
    def thinking_token_budget(self) -> int | None:
        return 4096 if self.thinking_budget == "think4k" else None

    @property
    def max_tokens(self) -> int | None:
        if self.max_tokens_label == "out8k":
            return 8192
        if self.max_tokens_label == "out12k":
            return 12288
        return None


LANES = [
    Lane("gpu0_9b", 0, 8110),
    Lane("gpu2_9b", 2, 8112),
    Lane("gpu1_9b", 1, 8101),
    Lane("gpu3_9b", 3, 8103),
]

csv_lock = threading.Lock()
log_lock = threading.Lock()
manifest_lock = threading.Lock()
job_lock = threading.Lock()
jobs: list[Job] = []
manifest: dict[str, object] = {
    "queue_name": QUEUE_NAME,
    "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "status": "starting",
    "original_manifest": str(ORIGINAL_MANIFEST),
    "original_queue_pid": ORIGINAL_QUEUE_PID,
    "lanes": [asdict(lane) for lane in LANES],
    "jobs": [],
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    line = f"[{now()}] {message}"
    with log_lock:
        print(line, flush=True)
        QUEUE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with QUEUE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def shell_join(parts: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def csv_fieldnames() -> list[str]:
    if not PROCESS_CSV.exists():
        return [
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
            "notes",
            "log_path",
            "exit_code",
        ]
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle))


def read_process_rows() -> tuple[list[str], list[dict[str, str]]]:
    fields = csv_fieldnames()
    if not PROCESS_CSV.exists():
        return fields, []
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        return fields, list(csv.DictReader(handle))


def write_process_rows(fields: list[str], rows: list[dict[str, str]]) -> None:
    with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def append_process_row(
    *,
    kind: str,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    notes: str,
    log_path: Path,
) -> None:
    with csv_lock:
        fields, rows = read_process_rows()
        row = {field: "" for field in fields}
        row.update(
            {
                "recorded_at": now(),
                "last_checked_at": now(),
                "status": "running",
                "kind": kind,
                "name": name,
                "pid": str(pid),
                "start_time": now(),
                "cwd": str(cwd),
                "run_dir": run_dir,
                "command": command,
                "notes": notes,
                "log_path": str(log_path),
            }
        )
        rows.append(row)
        write_process_rows(fields, rows)


def update_process_row(pid: int, status: str, *, exit_code: int | None = None) -> None:
    with csv_lock:
        fields, rows = read_process_rows()
        for row in rows:
            if str(row.get("pid", "")).strip() != str(pid):
                continue
            row["status"] = status
            row["last_checked_at"] = now()
            if status != "running" and not row.get("ended_at"):
                row["ended_at"] = now()
            if exit_code is not None:
                row["exit_code"] = str(exit_code)
            break
        write_process_rows(fields, rows)


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def original_jobs() -> list[dict[str, object]]:
    if not ORIGINAL_MANIFEST.exists():
        return []
    payload = read_json(ORIGINAL_MANIFEST)
    raw = payload.get("jobs", [])
    return raw if isinstance(raw, list) else []


def job_key_from_name(run_name: str) -> tuple[str, str, str, str] | None:
    match = RUN_RE.search(run_name)
    if not match:
        return None
    model, input_cap, thinking, output, _lane = match.groups()
    return (model, input_cap, thinking, output)


def all_8b_keys() -> set[tuple[str, str, str, str]]:
    return {
        ("8b", input_cap, thinking, output)
        for input_cap in ("cap49152", "nocap")
        for thinking in ("think4k", "think_unlimited")
        for output in ("out8k", "out12k", "out_unlimited")
    }


def make_9b_jobs() -> list[Job]:
    out: list[Job] = []
    for input_cap in ("cap49152", "nocap"):
        for thinking in ("think4k", "think_unlimited"):
            for output in ("out8k", "out12k", "out_unlimited"):
                priority = 50
                if thinking == "think4k" and input_cap == "cap49152":
                    priority = 0
                elif thinking == "think4k":
                    priority = 10
                elif input_cap == "cap49152":
                    priority = 20
                out.append(Job(input_cap, thinking, output, priority))
    return sorted(out, key=lambda item: (item.priority, item.input_cap, item.max_tokens_label))


def wait_8b_drained() -> None:
    required = all_8b_keys()
    while True:
        entries = original_jobs()
        seen: set[tuple[str, str, str, str]] = set()
        running: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            run_name = str(item.get("run_name", ""))
            key = job_key_from_name(run_name)
            if not key or key[0] != "8b":
                continue
            seen.add(key)
            if item.get("status") == "running":
                running.append(run_name)
        missing = sorted(required - seen)
        log(f"wait 8B drain: seen={len(seen)}/12 running={len(running)} missing={len(missing)}")
        if not missing and not running:
            return
        time.sleep(60)


def stop_pid_group(pid: int, status: str, *, wait_seconds: int = 8) -> None:
    if not pid_alive(pid):
        update_process_row(pid, "exited")
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        update_process_row(pid, "exited")
        return
    update_process_row(pid, status)
    time.sleep(wait_seconds)
    if pid_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def find_vllm_pid(port: int) -> int | None:
    result = subprocess.run(
        ["ps", "-eo", "pid,cmd"],
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
        parts = line.strip().split(maxsplit=1)
        if parts and parts[0].isdigit():
            return int(parts[0])
    return None


def endpoint_models(base_url: str) -> list[str]:
    try:
        with urlopen(base_url.rstrip("/") + "/models", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError):
        return []
    return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]


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


def start_or_reuse_vllm(lane: Lane) -> int | None:
    models = endpoint_models(lane.base_url)
    existing_pid = find_vllm_pid(lane.port)
    if lane.served_name in models:
        log(f"reuse 9B vLLM {lane.name} pid={existing_pid or 'unknown'} endpoint={lane.base_url}")
        return existing_pid
    if existing_pid and pid_alive(existing_pid):
        log(f"stop wrong vLLM on {lane.base_url} pid={existing_pid} models={models}")
        stop_pid_group(existing_pid, f"stopped_before_{QUEUE_NAME}_{lane.name}")

    log_path = LAUNCH_LOG_ROOT / f"{QUEUE_NAME}_{lane.name}_vllm_p{lane.port}.log"
    cmd = vllm_cmd(lane)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(lane.gpu)
    env["PYTHONUNBUFFERED"] = "1"
    handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    handle.close()
    append_process_row(
        kind="model_server",
        name=f"{QUEUE_NAME}_{lane.name}_vllm_p{lane.port}",
        pid=proc.pid,
        cwd=ROOT,
        run_dir=lane.model_path,
        command=shell_join(["env", f"CUDA_VISIBLE_DEVICES={lane.gpu}", "PYTHONUNBUFFERED=1", *cmd])
        + f" > {shlex.quote(str(log_path))} 2>&1 < /dev/null",
        notes=f"9B takeover vLLM service; lane={lane.name}; max_model_len={lane.max_model_len}; gpu_memory_utilization=0.9.",
        log_path=log_path,
    )
    log(f"started 9B vLLM {lane.name} pid={proc.pid} endpoint={lane.base_url} log={log_path}")
    return proc.pid


def wait_services_ready(lanes: list[Lane], pids: dict[str, int | None]) -> None:
    pending = {lane.name: lane for lane in lanes}
    deadline = time.monotonic() + 900
    while pending:
        for name, lane in list(pending.items()):
            models = endpoint_models(lane.base_url)
            if lane.served_name in models:
                log(f"service ready {name} endpoint={lane.base_url} models={models}")
                pending.pop(name)
        for name, pid in pids.items():
            if name in pending and pid and not pid_alive(pid):
                update_process_row(pid, "dead_before_ready")
                raise RuntimeError(f"vLLM service exited before readiness: {name} pid={pid}")
        if not pending:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"vLLM readiness timeout: {sorted(pending)}")
        log(f"waiting 9B services ready: {sorted(pending)}")
        time.sleep(15)


def current_started_9b_keys() -> set[tuple[str, str, str, str]]:
    keys: set[tuple[str, str, str, str]] = set()
    for item in original_jobs():
        if not isinstance(item, dict):
            continue
        key = job_key_from_name(str(item.get("run_name", "")))
        if key and key[0] == "9b":
            keys.add(key)
    for path in LAUNCH_LOG_ROOT.glob(f"{ORIGINAL_STAMP}_notrunc_9b_*_c20_*.log"):
        key = job_key_from_name(path.name)
        if key and key[0] == "9b":
            keys.add(key)
    return keys


def running_8b_experiment_pids() -> list[int]:
    result = subprocess.run(
        ["ps", "-eo", "pid,cmd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        if "gaia_skillrl.cli" not in line:
            continue
        if "_notrunc_8b_" not in line:
            continue
        parts = line.strip().split(maxsplit=1)
        if parts and parts[0].isdigit():
            pids.append(int(parts[0]))
    return pids


def wait_existing_8b_processes_done() -> None:
    while True:
        pids = running_8b_experiment_pids()
        log(f"wait current 8B processes done: running_pids={pids}")
        if not pids:
            return
        time.sleep(60)


def run_name_for(job: Job, lane: Lane) -> str:
    return (
        f"{ORIGINAL_STAMP}_notrunc_9b_{job.input_cap}_{job.thinking_budget}_"
        f"{job.max_tokens_label}_c{CONCURRENCY}_{lane.name}"
    )


def run_dir_for(run_name: str) -> Path:
    current = datetime.now()
    return RUN_ROOT / str(current.year) / str(current.month) / f"{current.year}-{current.month}-{current.day}" / run_name


def experiment_env(job: Job, lane: Lane) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_CONFIG),
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": str(job.max_context_chars),
            "NLRL_EXECUTOR_MODEL": lane.served_name,
            "NLRL_EXECUTOR_BASE_URL": lane.base_url,
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_TOOL_BASE_URL": "http://35.220.164.252:3888/v1",
            "NLRL_TOOL_API_KEY": "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            "NLRL_TOOL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_MODEL": "gpt-4o-mini",
            "NLRL_TOOL_AUDIO_MODEL": "gpt-4o-mini-transcribe",
        }
    )
    if job.thinking_token_budget is not None:
        env["NLRL_EXECUTOR_THINKING_TOKEN_BUDGET"] = str(job.thinking_token_budget)
    else:
        env.pop("NLRL_EXECUTOR_THINKING_TOKEN_BUDGET", None)
        env.pop("NLRL_EXECUTOR_THINKING_BUDGET", None)
    if job.max_tokens is not None:
        env["NLRL_EXECUTOR_MAX_TOKENS"] = str(job.max_tokens)
    else:
        env.pop("NLRL_EXECUTOR_MAX_TOKENS", None)
    return env


def experiment_env_display(env: dict[str, str]) -> list[str]:
    keys = [
        "PYTHONUNBUFFERED=1",
        "PYTHONFAULTHANDLER",
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_API_KEY",
        "NLRL_EXECUTOR_API_MODE",
        "NLRL_EXECUTOR_STREAM",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_TEMPERATURE",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS",
        "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS",
        "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_API_KEY",
        "NLRL_TOOL_TIMEOUT_SECONDS",
        "NLRL_TOOL_MODEL",
        "NLRL_TOOL_AUDIO_MODEL",
    ]
    parts: list[str] = []
    for key in keys:
        if "=" in key:
            parts.append(key)
        elif key in env:
            parts.append(f"{key}={env[key]}")
    return parts


def experiment_cmd(run_name: str) -> list[str]:
    return [
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


def score_snapshot(run_dir: Path) -> str:
    states = list(run_dir.glob("iteration_01/*/state.json"))
    correct = false = unknown = 0
    for path in states:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        score = payload.get("score")
        if score is True:
            correct += 1
        elif score is False:
            false += 1
        else:
            unknown += 1
    return f"states={len(states)} correct={correct} false={false} unknown={unknown}"


def lane_busy(lane: Lane) -> bool:
    result = subprocess.run(
        ["ps", "-eo", "pid,cmd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    marker = f"_c{CONCURRENCY}_{lane.name}"
    for line in result.stdout.splitlines():
        if "gaia_skillrl.cli" in line and marker in line:
            return True
    return False


def update_manifest_job(run_name: str, data: dict[str, object]) -> None:
    with manifest_lock:
        rows = manifest.setdefault("jobs", [])
        assert isinstance(rows, list)
        for item in rows:
            if isinstance(item, dict) and item.get("run_name") == run_name:
                item.update(data)
                break
        else:
            rows.append({"run_name": run_name, **data})
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def launch_experiment(job: Job, lane: Lane) -> tuple[subprocess.Popen[str], Path, Path, str]:
    run_name = run_name_for(job, lane)
    run_dir = run_dir_for(run_name)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
    cmd = experiment_cmd(run_name)
    env = experiment_env(job, lane)
    handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    handle.close()
    append_process_row(
        kind="experiment",
        name=run_name,
        pid=proc.pid,
        cwd=ROOT,
        run_dir=str(run_dir),
        command=shell_join(["env", "-u", "ALL_PROXY", "-u", "all_proxy", *experiment_env_display(env), *cmd])
        + f" > {shlex.quote(str(log_path))} 2>&1 < /dev/null",
        notes=(
            f"GAIA 9B takeover matrix job; lane={lane.name}; input_cap={job.input_cap}; "
            f"thinking_budget={job.thinking_budget}; max_tokens={job.max_tokens}; c{CONCURRENCY}."
        ),
        log_path=log_path,
    )
    log(f"launched {run_name} pid={proc.pid} lane={lane.name} log={log_path}")
    return proc, run_dir, log_path, run_name


def worker(lane: Lane) -> None:
    while True:
        with job_lock:
            job = jobs.pop(0) if jobs else None
        if job is None:
            log(f"{lane.name} drained")
            return
        while lane_busy(lane):
            log(f"{lane.name} busy with existing run; waiting")
            time.sleep(60)
        proc, run_dir, log_path, run_name = launch_experiment(job, lane)
        update_manifest_job(
            run_name,
            {
                "status": "running",
                "lane": lane.name,
                "pid": proc.pid,
                "run_dir": str(run_dir),
                "log_path": str(log_path),
                "job": asdict(job),
                "started_at": now(),
            },
        )
        last_monitor = 0.0
        while True:
            code = proc.poll()
            if code is not None:
                status = "finished" if code == 0 else "dead"
                update_process_row(proc.pid, status, exit_code=code)
                update_manifest_job(
                    run_name,
                    {
                        "status": status,
                        "exit_code": code,
                        "ended_at": now(),
                        "score_snapshot": score_snapshot(run_dir),
                    },
                )
                log(f"{run_name} exited code={code} {score_snapshot(run_dir)}")
                break
            if time.monotonic() - last_monitor >= MONITOR_INTERVAL_SECONDS:
                last_monitor = time.monotonic()
                update_process_row(proc.pid, "running")
                snapshot = score_snapshot(run_dir)
                update_manifest_job(run_name, {"last_checked_at": now(), "score_snapshot": snapshot})
                log(f"monitor {run_name}: {snapshot}")
            time.sleep(30)


def main() -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    append_process_row(
        kind="experiment_queue",
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir=str(MANIFEST_PATH),
        command=shell_join(["nohup", "setsid", str(PYTHON), "-u", __file__]) + f" > {shlex.quote(str(QUEUE_LOG))} 2>&1 < /dev/null",
        notes="Waits for original 8B matrix jobs to drain, then takes GPU0/2 over as 9B lanes and runs remaining 9B matrix jobs.",
        log_path=QUEUE_LOG,
    )
    log(f"9B takeover watcher started manifest={MANIFEST_PATH} mode={TAKEOVER_MODE}")
    if TAKEOVER_MODE == "now":
        log("immediate takeover requested; stopping original queue scheduler now")
        if pid_alive(ORIGINAL_QUEUE_PID):
            stop_pid_group(ORIGINAL_QUEUE_PID, f"stopped_for_{QUEUE_NAME}")
        wait_existing_8b_processes_done()
    else:
        wait_8b_drained()
        log("8B drained; stopping original queue scheduler")
        if pid_alive(ORIGINAL_QUEUE_PID):
            stop_pid_group(ORIGINAL_QUEUE_PID, f"stopped_for_{QUEUE_NAME}")
    log("stopping old 8B vLLM services on GPU0/GPU2")
    for port in (8100, 8102):
        pid = find_vllm_pid(port)
        if pid:
            log(f"stop old 8B vLLM port={port} pid={pid}")
            stop_pid_group(pid, f"stopped_for_{QUEUE_NAME}_replace_9b")

    started = current_started_9b_keys()
    global jobs
    jobs = [job for job in make_9b_jobs() if job.key not in started]
    log(f"remaining 9B jobs for takeover: {len(jobs)}; already_started={len(started)}")
    with manifest_lock:
        manifest["status"] = "running"
        manifest["takeover_at"] = now()
        manifest["already_started_9b"] = [list(item) for item in sorted(started)]
        manifest["planned_jobs"] = [asdict(job) for job in jobs]
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    pids = {lane.name: start_or_reuse_vllm(lane) for lane in LANES}
    wait_services_ready(LANES, pids)
    threads = [threading.Thread(target=worker, args=(lane,), daemon=False) for lane in LANES]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with manifest_lock:
        manifest["status"] = "finished"
        manifest["ended_at"] = now()
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    update_process_row(os.getpid(), "finished", exit_code=0)
    log("9B takeover queue finished")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        update_process_row(os.getpid(), "stopped")
        raise
    except Exception as exc:
        log(f"9B takeover queue failed: {type(exc).__name__}: {exc}")
        update_process_row(os.getpid(), "dead")
        raise
