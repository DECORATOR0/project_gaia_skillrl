from __future__ import annotations

import csv
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

VLLM_PYTHON = Path(os.environ.get("GAIA_TOOLV3_VLLM_PYTHON", "/data/xsy/miniconda3/envs/vllm_env/bin/python"))
QWEN_MODEL = Path(os.environ.get("GAIA_TOOLV3_QWEN_MODEL", "/data/xsy/codes/checkpoints/Qwen3-8B"))
CONFIG = Path(os.environ.get("GAIA_TOOLV3_CONFIG", ROOT / "configs/system.json"))
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"

CONCURRENCY = int(os.environ.get("GAIA_TOOLV3_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_TOOLV3_POLL_SECONDS", "30"))
STAMP = os.environ.get("GAIA_TOOLV3_PREFIX", "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
QUEUE_NAME = f"{STAMP}_toolv3_six_tool_gpu012_queue_c{CONCURRENCY}"

LANES = {
    "gpu0": "http://127.0.0.1:8100/v1",
    "gpu1": "http://127.0.0.1:8101/v1",
    "gpu2": "http://127.0.0.1:8102/v1",
}

started_model_pids: dict[int, int] = {}


@dataclass
class TrainJob:
    key: str
    run_name: str
    dataset: Path
    lane: str
    command: str
    bootstrap: bool = False
    skill_path: Path | None = None
    process: subprocess.Popen[str] | None = None
    log_path: Path | None = None


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def refresh_process_csv() -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    checked_at = now()
    for row in rows[1:]:
        if len(row) < 8:
            continue
        pid_text = row[5].strip()
        if not pid_text.isdigit():
            continue
        row[1] = checked_at
        status = row[2].strip().lower()
        if status in {"running", "started", "in_progress"} and not pid_alive(int(pid_text)):
            row[2] = "dead"
            if not row[7].strip():
                row[7] = checked_at
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "CUDA_VISIBLE_DEVICES",
        "PYTHONUNBUFFERED",
        "PYTHONFAULTHANDLER",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMBA_NUM_THREADS",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_API_KEY",
        "NLRL_EXECUTOR_API_MODE",
        "NLRL_EXECUTOR_STREAM",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_EXECUTOR_TEMPERATURE",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS",
        "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS",
        "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_API_KEY",
        "NLRL_TOOL_TIMEOUT_SECONDS",
    ]
    return {key: env[key] for key in keys if key in env and env[key]}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    cmd_parts = [shlex.quote(part) for part in cmd]
    return "env " + " ".join([*env_parts, *cmd_parts])


def append_process_row(
    *,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
    kind: str = "experiment",
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


def vllm_cmd(gpu: int) -> list[str]:
    port = 8100 + gpu
    return [
        str(VLLM_PYTHON),
        "-u",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(QWEN_MODEL),
        "--served-model-name",
        "Qwen3-8B-local",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
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
        "--max-model-len",
        "40960",
        "--max-num-seqs",
        "128",
    ]


def endpoint_ready(base_url: str) -> bool:
    try:
        with urlopen(base_url.rstrip("/") + "/models", timeout=5) as response:
            return 200 <= response.status < 500
    except (URLError, TimeoutError, OSError):
        return False


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


def start_vllm(gpu: int) -> None:
    lane = f"gpu{gpu}"
    base_url = LANES[lane]
    port = 8100 + gpu
    existing_pid = find_vllm_pid(port)
    if endpoint_ready(base_url):
        log(f"reuse healthy vLLM gpu={gpu} port={port} pid={existing_pid or 'unknown'}")
        if existing_pid is not None:
            started_model_pids[gpu] = existing_pid
        return
    if existing_pid is not None and pid_alive(existing_pid):
        log(f"existing vLLM on port {port} is unhealthy; stopping pid={existing_pid}")
        try:
            os.killpg(os.getpgid(existing_pid), signal.SIGTERM)
            update_process_row(existing_pid, f"stopped_unhealthy_before_{QUEUE_NAME}_gpu{gpu}")
            time.sleep(5)
        except ProcessLookupError:
            update_process_row(existing_pid, "dead")

    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"qwen3_8b_local_vllm_gpu{gpu}_p{port}_{STAMP}_toolv3.log"
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    cmd = vllm_cmd(gpu)
    handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            cmd,
            cwd="/data/xsy",
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    finally:
        handle.close()
    started_model_pids[gpu] = process.pid
    append_process_row(
        name=f"qwen3_8b_local_vllm_gpu{gpu}_p{port}_{STAMP}_toolv3",
        pid=process.pid,
        cwd=Path("/data/xsy"),
        run_dir=str(LAUNCH_LOG_ROOT),
        command=command_display(env, cmd),
        log_path=log_path,
        notes=f"Local Qwen3-8B vLLM service for GAIA TOOL-GAIA-V3 queue; GPU={gpu}; port={port}.",
        kind="model_server",
    )
    log(f"started vLLM gpu={gpu} pid={process.pid} port={port} log={log_path}")


def wait_vllm_ready() -> None:
    deadline = time.monotonic() + int(os.environ.get("GAIA_TOOLV3_VLLM_READY_TIMEOUT", "900"))
    pending = set(LANES)
    while pending:
        for lane in list(pending):
            if endpoint_ready(LANES[lane]):
                pending.remove(lane)
                log(f"vLLM ready lane={lane} endpoint={LANES[lane]}")
        if not pending:
            return
        for gpu, pid in list(started_model_pids.items()):
            lane = f"gpu{gpu}"
            if lane in pending and not pid_alive(pid):
                update_process_row(pid, "dead")
                raise RuntimeError(f"vLLM {lane} pid={pid} exited before readiness")
        if time.monotonic() > deadline:
            raise TimeoutError(f"vLLM readiness timeout, pending={sorted(pending)}")
        log(f"waiting for vLLM readiness: pending={sorted(pending)}")
        time.sleep(15)


def build_env(job: TrainJob) -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_EXECUTOR_BASE_URL",
    ]:
        env.pop(key, None)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "BLIS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "NUMBA_NUM_THREADS": "1",
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_TOOL_PROFILE": "reagent_facade_v3",
            "NLRL_EXECUTOR_MODEL": "Qwen3-8B-local",
            "NLRL_EXECUTOR_BASE_URL": LANES[job.lane],
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "720",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "720",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_TOOL_BASE_URL": "http://35.220.164.252:3888/v1",
            "NLRL_TOOL_API_KEY": "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            "NLRL_TOOL_TIMEOUT_SECONDS": "720",
        }
    )
    if job.skill_path is not None:
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(job.skill_path)
    if job.bootstrap:
        env["NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL"] = "1"
    return env


def start_job(job: TrainJob, notes: str) -> TrainJob:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    job.log_path = LAUNCH_LOG_ROOT / f"{job.run_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        job.command,
        "--dataset-path",
        str(job.dataset),
        "--run-name",
        job.run_name,
    ]
    if job.bootstrap:
        cmd.append("--bootstrap-skill")
    env = build_env(job)
    handle = job.log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    finally:
        handle.close()
    job.process = process
    append_process_row(
        name=job.run_name,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=job.log_path,
        notes=notes,
    )
    log(f"started {job.key} pid={process.pid} lane={job.lane} log={job.log_path}")
    return job


def check_job(job: TrainJob) -> int | None:
    if job.process is None:
        return None
    code = job.process.poll()
    if code is None:
        return None
    update_process_row(job.process.pid, "finished" if code == 0 else "dead")
    return code


def ensure_running_or_ok(job: TrainJob) -> None:
    code = check_job(job)
    if code is not None and code != 0:
        raise RuntimeError(f"{job.key} failed with returncode={code}")


def run_name_search_terms(run_name: str) -> list[str]:
    terms = [run_name]
    parts = run_name.split("_", 2)
    if len(parts) == 3 and parts[0].isdigit() and len(parts[0]) == 8 and parts[1].isdigit():
        terms.append(parts[2])
    return terms


def latest_run_dir(run_name: str) -> Path | None:
    matches_by_path: dict[Path, float] = {}
    for term in run_name_search_terms(run_name):
        for item in RUN_ROOT.glob(f"**/*{term}"):
            if item.is_dir():
                matches_by_path[item] = item.stat().st_mtime
    matches = sorted(matches_by_path, key=lambda item: matches_by_path[item])
    return matches[-1] if matches else None


def bootstrap_skill_path(run_name: str) -> Path | None:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def wait_for_boot_skill(boot_job: TrainJob, also_watch: list[TrainJob]) -> Path:
    while True:
        for job in [boot_job, *also_watch]:
            ensure_running_or_ok(job)
        skill = bootstrap_skill_path(boot_job.run_name)
        if skill is not None:
            log(f"bootstrap skill ready: {skill}")
            return skill
        log("waiting for bootstrap skill")
        time.sleep(POLL_SECONDS)


def wait_all(jobs: list[TrainJob]) -> None:
    pending = set(job.key for job in jobs)
    while pending:
        for job in jobs:
            if job.key not in pending:
                continue
            code = check_job(job)
            if code is None:
                continue
            if code != 0:
                raise RuntimeError(f"{job.key} failed with returncode={code}")
            pending.remove(job.key)
            log(f"completed {job.key}")
        if pending:
            log(f"waiting for jobs: {sorted(pending)}")
            time.sleep(POLL_SECONDS)


def queue_command_display() -> str:
    env = os.environ.copy()
    env["GAIA_TOOLV3_CONCURRENCY"] = str(CONCURRENCY)
    env["GAIA_TOOLV3_PREFIX"] = STAMP
    return command_display(env, [str(PYTHON), str(Path(__file__).resolve())])


def main() -> None:
    refresh_process_csv()
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    queue_log = Path(os.environ.get("GAIA_TOOLV3_QUEUE_LOG_PATH", LAUNCH_LOG_ROOT / f"{QUEUE_NAME}.log"))
    append_process_row(
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(queue manager)",
        command=queue_command_display(),
        log_path=queue_log,
        notes="TOOL-GAIA-V3 six-tool facade GPU012 queue: direct test, bootstrap dev, bootstrap test; c20.",
    )
    log(f"queue start name={QUEUE_NAME}")
    try:
        for gpu in [0, 1, 2]:
            start_vllm(gpu)
        wait_vllm_ready()

        direct = TrainJob(
            key="direct_test",
            run_name=f"{STAMP}_toolv3_direct_test_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane="gpu0",
            command="direct-eval-local",
        )
        boot_dev = TrainJob(
            key="boot_dev",
            run_name=f"{STAMP}_toolv3_boot_dev_c{CONCURRENCY}",
            dataset=DEV_DATASET,
            lane="gpu1",
            command="train-local",
            bootstrap=True,
        )
        start_job(direct, "TOOL-GAIA-V3 no-skill direct executor validation_test82; lane=gpu0; c20.")
        start_job(boot_dev, "TOOL-GAIA-V3 fresh bootstrap skill validation_dev83; lane=gpu1; c20.")

        boot_skill = wait_for_boot_skill(boot_dev, [direct])
        boot_test = TrainJob(
            key="boot_test",
            run_name=f"{STAMP}_toolv3_boot_test_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane="gpu2",
            command="train-local",
            skill_path=boot_skill,
        )
        start_job(
            boot_test,
            f"TOOL-GAIA-V3 bootstrap skill validation_test82 eval; lane=gpu2; c20; skill={boot_skill}",
        )

        wait_all([direct, boot_dev, boot_test])
        update_process_row(os.getpid(), "finished")
        log("queue completed")
    except Exception:
        update_process_row(os.getpid(), "dead")
        raise


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
