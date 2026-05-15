from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system_bootv7_pseudocomplete.json"
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
DESIGN_DOC = ROOT / "实验设计与迭代/26.5.14_1547_GAIA_9B_BOOTV7_skill结构与AFull图锁核查.md"

PREFIX = os.environ.get(
    "GAIA_9B_BOOTV7_R1FIXED_MISSINGFILL_PREFIX",
    datetime.now().strftime("20260514_%H%M%S_9b_bootv7_r1fixed_missingfill"),
).strip()
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(os.environ.get("GAIA_9B_BOOTV7_R1FIXED_MISSINGFILL_MASTER_LOG", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"))
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"
CONCURRENCY = int(os.environ.get("GAIA_9B_BOOTV7_R1FIXED_MISSINGFILL_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_9B_BOOTV7_R1FIXED_MISSINGFILL_POLL_SECONDS", "30"))

BOOT_SKILL = (
    RUN_ROOT
    / "2026/5/2026-5-14/20260514_122107_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5_9b_bootv7_r1_boot_dev_c20"
    / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
)
AFULL_SKILL = (
    RUN_ROOT
    / "2026/5/2026-5-14/20260514_163538_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1fixed_best_A_full_offline_actor_iter1"
    / "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_baseline_254_gpu01_20260507 as base  # noqa: E402


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_gpu_ids() -> list[int]:
    raw = os.environ.get("GAIA_9B_BOOTV7_R1FIXED_MISSINGFILL_GPU_IDS", "0,1,2,3,4,5")
    gpu_ids = [int(item.strip()) for item in raw.split(",") if item.strip()]
    if not gpu_ids:
        raise RuntimeError("empty GPU id list")
    return gpu_ids


GPU_IDS = parse_gpu_ids()
LANES = [
    base.Lane(
        f"132_gpu{gpu}_9b",
        gpu,
        8128 + gpu,
        model_path="/data/xsy/codes/checkpoints/Qwen3.5-9B",
        served_name="Qwen3.5-9B-local",
        tokenizer_path="/data/xsy/codes/checkpoints/Qwen3.5-9B",
        max_model_len=49152,
    )
    for gpu in GPU_IDS
]


@dataclass(frozen=True)
class FillTarget:
    key: str
    method: str
    repeat: int
    split: str
    source_run: Path
    dataset: Path
    skill_path: Path


@dataclass
class FillJob:
    target: FillTarget
    task_ids: list[str]
    run_name: str
    lane: base.Lane | None = None
    pid: int | None = None
    log_path: Path | None = None
    started_at: str | None = None
    ended_at: str | None = None
    returncode: int | None = None


TARGETS = [
    FillTarget(
        "boot_r1_dev",
        "fixed_boot",
        1,
        "dev",
        RUN_ROOT / "2026/5/2026-5-14/20260514_122107_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5_9b_bootv7_r1_boot_dev_c20",
        DEV_DATASET,
        BOOT_SKILL,
    ),
    FillTarget(
        "boot_r1_test",
        "fixed_boot",
        1,
        "test",
        RUN_ROOT / "2026/5/2026-5-14/20260514_122127_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5_9b_bootv7_r1_boot_test_c20",
        TEST_DATASET,
        BOOT_SKILL,
    ),
    FillTarget(
        "boot_r2_dev",
        "fixed_boot",
        2,
        "dev",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163534_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_bootrep2_dev_c20",
        DEV_DATASET,
        BOOT_SKILL,
    ),
    FillTarget(
        "boot_r2_test",
        "fixed_boot",
        2,
        "test",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163536_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_bootrep2_test_c20",
        TEST_DATASET,
        BOOT_SKILL,
    ),
    FillTarget(
        "boot_r3_dev",
        "fixed_boot",
        3,
        "dev",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163540_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_bootrep3_dev_c20",
        DEV_DATASET,
        BOOT_SKILL,
    ),
    FillTarget(
        "boot_r3_test",
        "fixed_boot",
        3,
        "test",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163545_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_bootrep3_test_c20",
        TEST_DATASET,
        BOOT_SKILL,
    ),
    FillTarget(
        "afull_r1_dev",
        "fixed_A_full",
        1,
        "dev",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163526_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_A_full_rep1_dev_c20",
        DEV_DATASET,
        AFULL_SKILL,
    ),
    FillTarget(
        "afull_r1_test",
        "fixed_A_full",
        1,
        "test",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163527_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_A_full_rep1_test_c20",
        TEST_DATASET,
        AFULL_SKILL,
    ),
    FillTarget(
        "afull_r2_dev",
        "fixed_A_full",
        2,
        "dev",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163531_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_A_full_rep2_dev_c20",
        DEV_DATASET,
        AFULL_SKILL,
    ),
    FillTarget(
        "afull_r2_test",
        "fixed_A_full",
        2,
        "test",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163536_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_A_full_rep2_test_c20",
        TEST_DATASET,
        AFULL_SKILL,
    ),
    FillTarget(
        "afull_r3_dev",
        "fixed_A_full",
        3,
        "dev",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163544_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_A_full_rep3_dev_c20",
        DEV_DATASET,
        AFULL_SKILL,
    ),
    FillTarget(
        "afull_r3_test",
        "fixed_A_full",
        3,
        "test",
        RUN_ROOT / "2026/5/2026-5-14/20260514_163553_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_9b_bootv7_r1skill_A_full_rep3_test_c20",
        TEST_DATASET,
        AFULL_SKILL,
    ),
]


def configure_base() -> None:
    base.ROOT = ROOT
    base.PYTHON = PYTHON
    base.CONFIG = CONFIG
    base.SEARCH_RUNTIME_CONFIG = SEARCH_RUNTIME_CONFIG
    base.DEV_DATASET = DEV_DATASET
    base.TEST_DATASET = TEST_DATASET
    base.RUN_ROOT = RUN_ROOT
    base.LAUNCH_LOG_ROOT = LAUNCH_LOG_ROOT
    base.QUEUE_LOG_ROOT = QUEUE_LOG_ROOT
    base.HOST_TAG = "132"
    base.RUN_PREFIX = PREFIX
    base.MASTER_NAME = MASTER_NAME
    base.SUMMARY_PATH = SUMMARY_JSON
    base.CONCURRENCY = CONCURRENCY
    base.CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_9B_BOOTV7_R1FIXED_MISSINGFILL_TIMEOUT_SECONDS", "3600"))
    base.LANES = LANES
    base.CONFLICTING_VLLM_PORTS = [lane.port for lane in LANES]
    base.STOP_CONFLICTING_SERVICES = False
    base.VLLM_GPU_MEMORY_UTILIZATION = "0.90"
    base.VLLM_MAX_NUM_SEQS = "48"


def selected_task_ids(run_dir: Path) -> list[str]:
    path = run_dir / "selected_tasks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    task_ids = data.get("task_ids", [])
    if not isinstance(task_ids, list):
        raise RuntimeError(f"bad selected_tasks task_ids: {path}")
    return [str(item) for item in task_ids]


def landed_task_ids(run_dir: Path) -> set[str]:
    iteration = run_dir / "iteration_01"
    return {path.parent.name for path in iteration.glob("*/state.json")}


def missing_task_ids(run_dir: Path) -> list[str]:
    landed = landed_task_ids(run_dir)
    return [task_id for task_id in selected_task_ids(run_dir) if task_id not in landed]


def run_stats(run_dir: Path) -> dict[str, int]:
    total = len(selected_task_ids(run_dir))
    states = []
    for path in sorted((run_dir / "iteration_01").glob("*/state.json")):
        try:
            states.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    success = sum(
        1
        for state in states
        if state.get("env_result", {}).get("evaluation", {}).get("task_success")
    )
    return {"success": success, "landed": len(states), "total": total, "missing": max(0, total - len(states))}


def build_jobs() -> list[FillJob]:
    jobs = []
    for target in TARGETS:
        if not target.source_run.exists():
            raise FileNotFoundError(target.source_run)
        if not target.skill_path.exists():
            raise FileNotFoundError(target.skill_path)
        missing = missing_task_ids(target.source_run)
        if not missing:
            continue
        run_name = f"{PREFIX}_{target.key}_missing{len(missing)}_c{CONCURRENCY}"
        jobs.append(FillJob(target=target, task_ids=missing, run_name=run_name))
    return jobs


def write_manifest(status: str, jobs: list[FillJob], extra: dict | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "master_log": str(MASTER_LOG),
        "summary_json": str(SUMMARY_JSON),
        "design_doc": str(DESIGN_DOC),
        "lanes": [asdict(lane) for lane in LANES],
        "jobs": [
            {
                "target": asdict(job.target),
                "task_ids": job.task_ids,
                "run_name": job.run_name,
                "lane": asdict(job.lane) if job.lane else None,
                "pid": job.pid,
                "log_path": str(job.log_path) if job.log_path else "",
                "started_at": job.started_at,
                "ended_at": job.ended_at,
                "returncode": job.returncode,
            }
            for job in jobs
        ],
    }
    if extra:
        payload.update(extra)
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def build_env(lane: base.Lane, skill_path: Path) -> dict[str, str]:
    env = base.common_env(lane)
    env.update(
        {
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_RUNTIME_CONFIG),
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_INITIAL_SKILL_PATH": str(skill_path),
            "NLRL_ACTOR_MODEL": "gpt-5.2",
            "NLRL_ACTOR_API_MODE": "responses_sse",
            "NLRL_ACTOR_ENABLE_THINKING": "1",
            "NLRL_ACTOR_REASONING_EFFORT": "high",
            "NLRL_CRITIC_MODEL": "gpt-5.2",
            "NLRL_CRITIC_API_MODE": "responses_sse",
            "NLRL_CRITIC_ENABLE_THINKING": "1",
            "NLRL_CRITIC_REASONING_EFFORT": "high",
        }
    )
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    return env


def start_job(job: FillJob, lane: base.Lane) -> subprocess.Popen[str]:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{job.run_name}_{short_ts()}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "train-local",
        "--dataset-path",
        str(job.target.dataset),
        "--run-name",
        job.run_name,
    ]
    for task_id in job.task_ids:
        cmd.extend(["--task-id", task_id])
    env = build_env(lane, job.target.skill_path)
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
    job.lane = lane
    job.pid = proc.pid
    job.log_path = log_path
    job.started_at = now()
    base.append_process_row(
        kind="experiment_missingfill",
        name=job.run_name,
        pid=proc.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=" ".join(cmd),
        log_path=log_path,
        notes=(
            f"9B BOOTV7 r1fixed missing fill; target={job.target.key}; "
            f"source={job.target.source_run}; skill={job.target.skill_path}; lane={lane.name}; tasks={len(job.task_ids)}"
        ),
    )
    log(f"started {job.run_name} pid={proc.pid} lane={lane.name} tasks={len(job.task_ids)} log={log_path}")
    return proc


def latest_run_dir(run_name: str) -> Path | None:
    suffix = f"_{run_name}"
    matches = [
        path
        for pattern in (f"**/{run_name}", f"**/*{suffix}")
        for path in RUN_ROOT.glob(pattern)
        if path.is_dir()
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def summarize(jobs: list[FillJob], status: str, *, started_at: str, ended_at: str) -> dict:
    rows = []
    for job in jobs:
        fill_dir = latest_run_dir(job.run_name)
        rows.append(
            {
                "target": asdict(job.target),
                "missing_task_ids": job.task_ids,
                "missing_count": len(job.task_ids),
                "fill_run_name": job.run_name,
                "fill_run_dir": str(fill_dir) if fill_dir else "",
                "fill_stats": run_stats(fill_dir) if fill_dir else {},
                "source_stats_at_summary": run_stats(job.target.source_run),
                "pid": job.pid,
                "returncode": job.returncode,
                "log_path": str(job.log_path) if job.log_path else "",
            }
        )
    summary = {
        "prefix": PREFIX,
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "master_log": str(MASTER_LOG),
        "manifest_json": str(MANIFEST_JSON),
        "jobs": rows,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    started_at = now()
    status = "finished"
    configure_base()
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    base.ensure_process_csv_header()
    base.refresh_process_registry()
    base.append_process_row(
        kind="experiment_master",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir=str(QUEUE_LOG_ROOT),
        command=" ".join([str(PYTHON), "-u", str(Path(__file__).resolve())]),
        log_path=MASTER_LOG,
        notes=f"9B BOOTV7 r1fixed missing fill master; summary={SUMMARY_JSON}",
    )
    try:
        base.wait_endpoints()
        jobs = build_jobs()
        write_manifest("starting", jobs)
        if not jobs:
            log("no missing tasks found")
            summarize(jobs, "finished_no_missing", started_at=started_at, ended_at=now())
            write_manifest("finished_no_missing", jobs)
            return 0

        pending = list(jobs)
        running: dict[int, tuple[FillJob, subprocess.Popen[str]]] = {}
        completed: list[FillJob] = []
        while pending or running:
            used_lanes = {job.lane.name for job, proc in running.values() if proc.poll() is None and job.lane}
            free_lanes = [lane for lane in LANES if lane.name not in used_lanes]
            while pending and free_lanes:
                lane = free_lanes.pop(0)
                job = pending.pop(0)
                proc = start_job(job, lane)
                running[proc.pid] = (job, proc)
            write_manifest("running", jobs, {"pending_count": len(pending), "running_count": len(running)})
            time.sleep(POLL_SECONDS)

            for pid, (job, proc) in list(running.items()):
                code = proc.poll()
                if code is None:
                    continue
                job.returncode = code
                job.ended_at = now()
                base.update_process_row(pid, "finished" if code == 0 else "dead", exit_code=code)
                completed.append(job)
                running.pop(pid, None)
                log(f"exited {job.run_name} code={code}")

        if any(job.returncode for job in completed):
            status = "finished_with_failures"
        summary = summarize(jobs, status, started_at=started_at, ended_at=now())
        write_manifest(status, jobs, {"summary": summary})
        return 0 if status == "finished" else 1
    except KeyboardInterrupt:
        status = "stopped"
        summarize([], status, started_at=started_at, ended_at=now())
        write_manifest(status, [])
        raise
    except Exception:
        status = "dead"
        summarize([], status, started_at=started_at, ended_at=now())
        write_manifest(status, [])
        raise
    finally:
        base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
