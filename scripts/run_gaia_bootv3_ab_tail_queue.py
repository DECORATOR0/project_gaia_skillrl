from __future__ import annotations

import csv
import json
import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = ROOT / "runs/_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")


WaitMode = Literal["complete", "tail"]


@dataclass
class Job:
    key: str
    run_name: str
    dataset: Path
    iterations: int
    strategy: str = "full"
    bootstrap: bool = False
    skill_from: str | None = None
    wait_for_previous: WaitMode = "tail"
    process: subprocess.Popen[str] | None = None
    log_path: Path | None = None
    run_dir: Path | None = None


JOBS = [
    Job(
        key="boot_dev",
        run_name="gaia_bootv3_ab_boot_dev_iter1_c20",
        dataset=DEV_DATASET,
        iterations=0,
        bootstrap=True,
        wait_for_previous="complete",
    ),
    Job(
        key="boot_test",
        run_name="gaia_bootv3_ab_boot_test_iter1_c20",
        dataset=TEST_DATASET,
        iterations=0,
        skill_from="boot_dev",
        wait_for_previous="complete",
    ),
    Job(
        key="a_iter1",
        run_name="gaia_bootv3_ab_A_full_iter1_dev_c20",
        dataset=DEV_DATASET,
        iterations=1,
        strategy="full",
        skill_from="boot_dev",
    ),
    Job(
        key="b_iter1",
        run_name="gaia_bootv3_ab_B_sharded_iter1_dev_c20",
        dataset=DEV_DATASET,
        iterations=1,
        strategy="sharded",
        skill_from="boot_dev",
    ),
    Job(
        key="a_iter2",
        run_name="gaia_bootv3_ab_A_full_iter2_dev_c20",
        dataset=DEV_DATASET,
        iterations=1,
        strategy="full",
        skill_from="a_iter1",
    ),
    Job(
        key="b_iter2",
        run_name="gaia_bootv3_ab_B_sharded_iter2_dev_c20",
        dataset=DEV_DATASET,
        iterations=1,
        strategy="sharded",
        skill_from="b_iter1",
    ),
    Job(
        key="a_test",
        run_name="gaia_bootv3_ab_A_full_iter2_test_c20",
        dataset=TEST_DATASET,
        iterations=0,
        skill_from="a_iter2",
    ),
    Job(
        key="b_test",
        run_name="gaia_bootv3_ab_B_sharded_iter2_test_c20",
        dataset=TEST_DATASET,
        iterations=0,
        skill_from="b_iter2",
    ),
]


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def append_process_row(*, name: str, pid: int, cwd: Path, run_dir: str, command: str, log_path: Path, notes: str) -> None:
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    row = [
        now(),
        now(),
        "running",
        "experiment",
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


def latest_run_dir(run_name: str) -> Path | None:
    matches = [path for path in RUN_ROOT.glob(f"**/*_{run_name}") if path.is_dir()]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def selected_task_count(run_dir: Path) -> int:
    selected_path = run_dir / "selected_tasks.json"
    if not selected_path.exists():
        return 0
    try:
        data = json.loads(selected_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    task_ids = data.get("task_ids", [])
    return len(task_ids) if isinstance(task_ids, list) else 0


def latest_iteration_dir(run_dir: Path) -> Path | None:
    iterations = [
        path for path in run_dir.glob("iteration_*")
        if path.is_dir() and any(child.is_dir() for child in path.iterdir())
    ]
    if not iterations:
        return None
    return max(iterations, key=lambda path: path.name)


def progress(run_dir: Path) -> tuple[int, int, str]:
    total = selected_task_count(run_dir)
    iteration_dir = latest_iteration_dir(run_dir)
    if total <= 0 or iteration_dir is None:
        return 0, total, ""
    completed = len(list(iteration_dir.glob("*/state.json")))
    return completed, total, iteration_dir.name


def tail_ready(job: Job) -> bool:
    if job.process is not None and job.process.poll() is not None:
        return True
    run_dir = job.run_dir or latest_run_dir(job.run_name)
    if run_dir is None:
        return False
    job.run_dir = run_dir
    completed, total, iteration_name = progress(run_dir)
    if total <= 0:
        return False
    remaining = max(0, total - completed)
    threshold = max(1, math.floor(total * 0.05))
    ready = remaining <= threshold
    log(
        f"tail check {job.key}: {completed}/{total} in {iteration_name}, "
        f"remaining={remaining}, threshold={threshold}, ready={ready}"
    )
    return ready


def complete_ready(job: Job) -> bool:
    return job.process is not None and job.process.poll() is not None


def skill_path_for(job: Job) -> Path | None:
    run_dir = job.run_dir or latest_run_dir(job.run_name)
    if run_dir is None:
        return None
    job.run_dir = run_dir
    candidates = sorted(
        run_dir.glob("iteration_*/skill_for_eval/gaia-general-skill/SKILL.md"),
        key=lambda path: path.as_posix(),
    )
    if candidates:
        return candidates[-1]
    bootstrap_skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return bootstrap_skill if bootstrap_skill.exists() else None


def command_for(job: Job, jobs_by_key: dict[str, Job]) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env.update({
        "NLRL_RUNTIME_TASK_CONCURRENCY": "20",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS": "20",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH": str(job.iterations),
        "NLRL_RUNTIME_CRITIC_STRATEGY": job.strategy,
        "NLRL_RUNTIME_CRITIC_SHARD_SIZE": "12",
    })
    env.pop("NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL", None)
    env.pop("NLRL_RUNTIME_INITIAL_SKILL_PATH", None)
    if job.skill_from:
        source = jobs_by_key[job.skill_from]
        skill_path = skill_path_for(source)
        if skill_path is None:
            raise RuntimeError(f"Skill dependency is not ready for {job.key}: {job.skill_from}")
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(skill_path)
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "train-local",
        "--dataset-path",
        str(job.dataset),
        "--run-name",
        job.run_name,
    ]
    if job.bootstrap:
        cmd.append("--bootstrap-skill")
    return cmd, env


def start_job(job: Job, jobs_by_key: dict[str, Job]) -> None:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job.log_path = LAUNCH_LOG_ROOT / f"{job.run_name}_{stamp}.log"
    cmd, env = command_for(job, jobs_by_key)
    log(f"starting {job.key}: {' '.join(cmd)}")
    handle = job.log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    job.process = process
    append_process_row(
        name=job.run_name,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=" ".join(cmd),
        log_path=job.log_path,
        notes=f"boot-v3 A/B queue child; key={job.key}; strategy={job.strategy}; iterations={job.iterations}",
    )
    log(f"started {job.key} pid={process.pid} log={job.log_path}")


def stop_children(jobs: list[Job]) -> None:
    for job in jobs:
        if job.process is not None and job.process.poll() is None:
            try:
                os.killpg(job.process.pid, signal.SIGTERM)
            except Exception:
                pass


def main() -> int:
    jobs_by_key = {job.key: job for job in JOBS}
    start_key = os.environ.get("NLRL_QUEUE_START_KEY", "").strip()
    start_index = 0
    if start_key:
        key_to_index = {job.key: index for index, job in enumerate(JOBS)}
        if start_key not in key_to_index:
            raise RuntimeError(f"Unknown NLRL_QUEUE_START_KEY={start_key!r}")
        start_index = key_to_index[start_key]
    started = start_index
    start_job(JOBS[start_index], jobs_by_key)
    started = start_index + 1
    try:
        while True:
            for job in JOBS[start_index:started]:
                if job.process is not None and job.process.poll() is not None:
                    code = job.process.returncode
                    if code != 0:
                        log(f"job failed: {job.key} returncode={code}")
                        stop_children(JOBS)
                        return code or 1
            if started < len(JOBS):
                previous = JOBS[started - 1]
                next_job = JOBS[started]
                ready = complete_ready(previous) if next_job.wait_for_previous == "complete" else tail_ready(previous)
                dependency_ready = True
                if next_job.skill_from:
                    dependency_ready = skill_path_for(jobs_by_key[next_job.skill_from]) is not None
                if ready and dependency_ready:
                    start_job(next_job, jobs_by_key)
                    started += 1
            if started == len(JOBS) and all(
                job.process is not None and job.process.poll() is not None
                for job in JOBS[start_index:]
            ):
                log("all jobs completed")
                return 0
            time.sleep(60)
    except KeyboardInterrupt:
        log("received KeyboardInterrupt; stopping children")
        stop_children(JOBS)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
