from __future__ import annotations

import csv
import json
import os
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
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
QUEUE_NAME = f"{STAMP}_gaia_notrunc_budget_matrix_c20"
QUEUE_LOG = QUEUE_LOG_ROOT / f"{QUEUE_NAME}.log"
MANIFEST_PATH = QUEUE_LOG_ROOT / f"{QUEUE_NAME}.json"
CONCURRENCY = 20
MAX_MODEL_LEN = 49152
MONITOR_INTERVAL_SECONDS = 300


@dataclass(frozen=True)
class Lane:
    name: str
    gpu: int
    port: int
    model_label: str
    model_path: str
    served_name: str
    max_model_len: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


@dataclass(frozen=True)
class Job:
    model_label: str
    input_cap: str
    thinking_budget: str
    max_tokens_label: str
    priority: int

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
    Lane("gpu0_8b", 0, 8100, "8b", "/data/xsy/codes/checkpoints/Qwen3-8B", "Qwen3-8B-local", 40960),
    Lane("gpu2_8b", 2, 8102, "8b", "/data/xsy/codes/checkpoints/Qwen3-8B", "Qwen3-8B-local", 40960),
    Lane("gpu1_9b", 1, 8101, "9b", "/data/xsy/codes/checkpoints/Qwen3.5-9B", "Qwen3.5-9B-local", 49152),
    Lane("gpu3_9b", 3, 8103, "9b", "/data/xsy/codes/checkpoints/Qwen3.5-9B", "Qwen3.5-9B-local", 49152),
]

csv_lock = threading.Lock()
log_lock = threading.Lock()
manifest_lock = threading.Lock()
jobs_by_model: dict[str, list[Job]] = {"8b": [], "9b": []}
manifest: dict[str, object] = {
    "queue_name": QUEUE_NAME,
    "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "status": "starting",
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
        reader = csv.reader(handle)
        return next(reader)


def read_process_rows() -> tuple[list[str], list[dict[str, str]]]:
    fields = csv_fieldnames()
    if not PROCESS_CSV.exists():
        return fields, []
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return fields, rows


def write_process_rows(fields: list[str], rows: list[dict[str, str]]) -> None:
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
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


def refresh_process_csv() -> None:
    with csv_lock:
        fields, rows = read_process_rows()
        for row in rows:
            if row.get("status") != "running":
                continue
            raw_pid = str(row.get("pid", "")).strip()
            if not raw_pid.isdigit():
                continue
            pid = int(raw_pid)
            if pid_alive(pid):
                row["last_checked_at"] = now()
            else:
                row["status"] = "exited"
                row["last_checked_at"] = now()
                if not row.get("ended_at"):
                    row["ended_at"] = now()
        write_process_rows(fields, rows)


def endpoint_models(base_url: str) -> list[str]:
    try:
        with urlopen(base_url.rstrip("/") + "/models", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError):
        return []
    return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]


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


def stop_pid(pid: int, status: str) -> None:
    if not pid_alive(pid):
        update_process_row(pid, "exited")
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        update_process_row(pid, "exited")
        return
    update_process_row(pid, status)
    time.sleep(8)
    if pid_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            return


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
    existing_models = endpoint_models(lane.base_url)
    existing_pid = find_vllm_pid(lane.port)
    if lane.served_name in existing_models:
        log(f"reuse vLLM {lane.name} pid={existing_pid or 'unknown'} models={existing_models}")
        return existing_pid
    if existing_pid and pid_alive(existing_pid):
        log(f"stop unhealthy/wrong vLLM on port {lane.port} pid={existing_pid} models={existing_models}")
        stop_pid(existing_pid, f"stopped_before_{QUEUE_NAME}_{lane.name}")

    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{QUEUE_NAME}_{lane.name}_vllm_p{lane.port}.log"
    cmd = vllm_cmd(lane)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(lane.gpu)
    env["PYTHONUNBUFFERED"] = "1"
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
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
        pid=process.pid,
        cwd=ROOT,
        run_dir=lane.model_path,
        command=shell_join(["env", f"CUDA_VISIBLE_DEVICES={lane.gpu}", "PYTHONUNBUFFERED=1", *cmd]) + f" > {shlex.quote(str(log_path))} 2>&1 < /dev/null",
        notes=f"Matrix vLLM service; lane={lane.name}; model={lane.served_name}; max_model_len={lane.max_model_len}; gpu_memory_utilization=0.9.",
        log_path=log_path,
    )
    log(f"started vLLM {lane.name} pid={process.pid} log={log_path}")
    return process.pid


def wait_services_ready(lanes: list[Lane], service_pids: dict[str, int | None]) -> None:
    deadline = time.monotonic() + 900
    pending = {lane.name: lane for lane in lanes}
    while pending:
        for name, lane in list(pending.items()):
            models = endpoint_models(lane.base_url)
            if lane.served_name in models:
                log(f"service ready {lane.name} endpoint={lane.base_url} models={models}")
                pending.pop(name)
        for name, pid in service_pids.items():
            if name in pending and pid and not pid_alive(pid):
                update_process_row(pid, "dead_before_ready")
                raise RuntimeError(f"vLLM service exited before readiness: {name} pid={pid}")
        if not pending:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"vLLM readiness timeout: {sorted(pending)}")
        log(f"waiting services ready: {sorted(pending)}")
        time.sleep(15)


def make_jobs() -> list[Job]:
    jobs: list[Job] = []
    for model_label in ("8b", "9b"):
        for input_cap in ("cap49152", "nocap"):
            for thinking_budget in ("think4k", "think_unlimited"):
                for max_tokens_label in ("out8k", "out12k", "out_unlimited"):
                    priority = 50
                    if input_cap == "cap49152" and thinking_budget == "think_unlimited" and max_tokens_label == "out_unlimited":
                        priority = 0
                    elif input_cap == "nocap" and thinking_budget == "think4k" and max_tokens_label in {"out8k", "out12k"}:
                        priority = 1
                    elif thinking_budget == "think4k":
                        priority = 10
                    jobs.append(Job(model_label, input_cap, thinking_budget, max_tokens_label, priority))
    return sorted(jobs, key=lambda item: (item.priority, item.model_label, item.input_cap, item.max_tokens_label))


def run_name_for(job: Job, lane: Lane) -> str:
    return (
        f"{STAMP}_notrunc_{job.model_label}_{job.input_cap}_{job.thinking_budget}_"
        f"{job.max_tokens_label}_c{CONCURRENCY}_{lane.name}"
    )


def run_dir_for(run_name: str) -> Path:
    current = datetime.now()
    return RUN_ROOT / str(current.year) / str(current.month) / f"{current.year}-{current.month}-{current.day}" / run_name


def experiment_env(job: Job, lane: Lane) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    env.update({
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
    })
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


def launch_experiment(job: Job, lane: Lane) -> tuple[subprocess.Popen[str], Path, Path, str]:
    run_name = run_name_for(job, lane)
    run_dir = run_dir_for(run_name)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
    cmd = experiment_cmd(run_name)
    env = experiment_env(job, lane)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
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
        pid=process.pid,
        cwd=ROOT,
        run_dir=str(run_dir),
        command=shell_join(["env", "-u", "ALL_PROXY", "-u", "all_proxy", *experiment_env_display(env), *cmd]) + f" > {shlex.quote(str(log_path))} 2>&1 < /dev/null",
        notes=(
            f"GAIA no-old-message-pretruncate matrix job; lane={lane.name}; model={lane.served_name}; "
            f"input_cap={job.input_cap}; max_context_chars={job.max_context_chars}; "
            f"thinking_budget={job.thinking_budget}; max_tokens={job.max_tokens}; c{CONCURRENCY}."
        ),
        log_path=log_path,
    )
    log(f"launched {run_name} pid={process.pid} lane={lane.name} log={log_path}")
    return process, run_dir, log_path, run_name


def update_manifest_job(run_name: str, data: dict[str, object]) -> None:
    with manifest_lock:
        jobs = manifest.setdefault("jobs", [])
        assert isinstance(jobs, list)
        for item in jobs:
            if isinstance(item, dict) and item.get("run_name") == run_name:
                item.update(data)
                break
        else:
            jobs.append({"run_name": run_name, **data})
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def gpu_snapshot() -> str:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return " | ".join(line.strip() for line in result.stdout.splitlines() if line.strip())


def worker(lane: Lane) -> None:
    queue = jobs_by_model[lane.model_label]
    while True:
        with manifest_lock:
            job = queue.pop(0) if queue else None
        if job is None:
            log(f"{lane.name} drained")
            return
        process, run_dir, log_path, run_name = launch_experiment(job, lane)
        update_manifest_job(
            run_name,
            {
                "status": "running",
                "lane": lane.name,
                "pid": process.pid,
                "run_dir": str(run_dir),
                "log_path": str(log_path),
                "job": asdict(job),
                "started_at": now(),
            },
        )
        last_monitor = 0.0
        while True:
            code = process.poll()
            if code is not None:
                status = "finished" if code == 0 else "dead"
                update_process_row(process.pid, status, exit_code=code)
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
                snapshot = score_snapshot(run_dir)
                update_process_row(process.pid, "running")
                update_manifest_job(run_name, {"last_checked_at": now(), "score_snapshot": snapshot})
                log(f"monitor {run_name}: {snapshot}; gpu={gpu_snapshot()}")
            time.sleep(30)


def main() -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    refresh_process_csv()
    append_process_row(
        kind="experiment_queue",
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir=str(MANIFEST_PATH),
        command=shell_join(["nohup", "setsid", str(PYTHON), "-u", __file__]) + f" > {shlex.quote(str(QUEUE_LOG))} 2>&1 < /dev/null",
        notes="GAIA 8B/9B no-old-message-pretruncate 24-job matrix; 2x input cap, 2x thinking budget, 3x max_tokens, c20.",
        log_path=QUEUE_LOG,
    )
    log(f"queue started manifest={MANIFEST_PATH}")
    service_pids = {lane.name: start_or_reuse_vllm(lane) for lane in LANES}
    wait_services_ready(LANES, service_pids)

    for job in make_jobs():
        jobs_by_model[job.model_label].append(job)
    with manifest_lock:
        manifest["status"] = "running"
        manifest["planned_jobs"] = [asdict(job) for job in make_jobs()]
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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
    log("queue finished")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        update_process_row(os.getpid(), "stopped")
        raise
    except Exception as exc:
        log(f"queue failed: {type(exc).__name__}: {exc}")
        update_process_row(os.getpid(), "dead")
        raise
