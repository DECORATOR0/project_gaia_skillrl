from __future__ import annotations

import csv
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

A_SKILL = (
    ROOT
    / "runs/2026/4/2026-4-21/20260421_225900_gaia_bootv3_ab_A_full_offline_actor_iter1"
    / "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)
B_SKILL = (
    ROOT
    / "runs/2026/4/2026-4-21/20260421_230059_gaia_bootv3_ab_B_sharded_offline_actor_iter1"
    / "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)
A_ITER2_DEV_RUN = (
    ROOT
    / "runs/2026/4/2026-4-21/20260421_231006_gaia_bootv3_ab_A_full_iter2_dev_eval_c20"
)
A_ITER2_DEV_ITERATION = A_ITER2_DEV_RUN / "iteration_01"

A_MISSING_TEST_IDS = ["5a0c1adf-205e-4841-a666-7c3ef95def9d"]
B_MISSING_TEST_IDS = [
    "e142056d-56ab-4352-b091-b56054bd1359",
    "9e1fc53b-46ff-49a1-9d05-9e6faac34cc5",
    "50ec8903-b81f-4257-9450-1085afd2c319",
]


sys.path.insert(0, str(ROOT))

from gaia_skillrl.actor import SkillActor  # noqa: E402
from gaia_skillrl.config import clone_system_config, load_system_config  # noqa: E402
from gaia_skillrl.critic import SkillCritic  # noqa: E402
from gaia_skillrl.schemas import (  # noqa: E402
    EnvRunResult,
    EnvState,
    EvaluationResult,
    ExecutorStepRecord,
    PhaseTransition,
    ToolCallRecord,
    to_dict,
)
from gaia_skillrl.skills import (  # noqa: E402
    discover_skills,
    load_skill_detail,
    reset_experience_buffer,
    reset_skill_library,
    write_skill_bundle,
)
from gaia_skillrl.trainer import GaiaSkillTrainer  # noqa: E402
from gaia_skillrl.utils import ensure_dir, write_json  # noqa: E402


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def dataclass_from_dict(cls: type, data: dict[str, Any]):
    allowed = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in allowed})


def load_state(path: Path) -> EnvState:
    data = json.loads(path.read_text(encoding="utf-8"))
    env_data = data["env_result"]
    env_result = EnvRunResult(
        final_answer=env_data.get("final_answer", ""),
        final_choice_label=env_data.get("final_choice_label", ""),
        tool_trajectory=[
            dataclass_from_dict(ToolCallRecord, item)
            for item in env_data.get("tool_trajectory", [])
        ],
        phase_transitions=[
            dataclass_from_dict(PhaseTransition, item)
            for item in env_data.get("phase_transitions", [])
        ],
        action_trace=[
            dataclass_from_dict(ExecutorStepRecord, item)
            for item in env_data.get("action_trace", [])
        ],
        executor_summary=env_data.get("executor_summary", ""),
        raw_executor_output=env_data.get("raw_executor_output", ""),
        evaluation=dataclass_from_dict(EvaluationResult, env_data.get("evaluation", {})),
    )
    return EnvState(
        task_id=data["task_id"],
        task_prompt=data["task_prompt"],
        env_result=env_result,
        gold_trajectory=data.get("gold_trajectory", []),
        gold_tool_names=data.get("gold_tool_names", []),
        active_skill_name=data.get("active_skill_name", "gaia-general-skill"),
        task_context=data.get("task_context", {}),
    )


def append_process_row(
    *,
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


def update_process_row(pid: int, status: str) -> None:
    if not PROCESS_CSV.exists():
        return
    rows: list[list[str]]
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


def snapshot_skill(skill_dir: Path, target_root: Path) -> Path:
    target = target_root / "gaia-general-skill"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(skill_dir, target)
    return target


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
        path
        for path in run_dir.glob("iteration_*")
        if path.is_dir() and any(child.is_dir() for child in path.iterdir())
    ]
    if not iterations:
        return None
    return max(iterations, key=lambda path: path.name)


def progress_for(run_name: str) -> tuple[int, int]:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return 0, 0
    total = selected_task_count(run_dir)
    iteration_dir = latest_iteration_dir(run_dir)
    if total <= 0 or iteration_dir is None:
        return 0, total
    return len(list(iteration_dir.glob("*/state.json"))), total


def wait_for_process(process: subprocess.Popen[str], name: str) -> int:
    while True:
        code = process.poll()
        done, total = progress_for(name)
        if total:
            log(f"wait {name}: {done}/{total}")
        if code is not None:
            status = "finished" if code == 0 else "dead"
            update_process_row(process.pid, status)
            log(f"{name} exited returncode={code}")
            return code
        time.sleep(60)


def tail_ready(process: subprocess.Popen[str], run_name: str) -> bool:
    if process.poll() is not None:
        return True
    done, total = progress_for(run_name)
    if total <= 0:
        return False
    remaining = max(0, total - done)
    threshold = max(1, total // 20)
    ready = remaining <= threshold
    log(f"tail check {run_name}: {done}/{total}, remaining={remaining}, threshold={threshold}, ready={ready}")
    return ready


def common_eval_env(skill_path: Path, iterations: int = 0) -> dict[str, str]:
    env = os.environ.copy()
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
            "NLRL_RUNTIME_TASK_CONCURRENCY": "20",
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": "20",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "420",
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": str(iterations),
            "NLRL_RUNTIME_INITIAL_SKILL_PATH": str(skill_path),
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "420",
        }
    )
    return env


def start_eval(
    *,
    run_name: str,
    dataset: Path,
    skill_path: Path,
    task_ids: list[str] | None = None,
) -> subprocess.Popen[str]:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{stamp}.log"
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
    for task_id in task_ids or []:
        cmd.extend(["--task-id", task_id])
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=common_eval_env(skill_path),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    append_process_row(
        name=run_name,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=" ".join(cmd),
        log_path=log_path,
        notes=f"boot-v3 A-next queue child; c20; stream=1; skill={skill_path}",
    )
    log(f"started {run_name} pid={process.pid} log={log_path}")
    return process


def load_a_iter2_dev_states() -> list[EnvState]:
    state_paths = sorted(A_ITER2_DEV_ITERATION.glob("*/state.json"))
    if len(state_paths) != 83:
        raise RuntimeError(f"Expected 83 A iter2 dev states, found {len(state_paths)}")
    return [load_state(path) for path in state_paths]


def run_a_next_offline_actor() -> Path:
    run_name = "gaia_bootv3_ab_A_full_offline_actor_iter2"
    log("A full offline actor iter2: preparing run")
    config = clone_system_config(
        load_system_config(CONFIG),
        runtime={"critic_strategy": "full", "critic_shard_size": 12},
    )
    trainer = GaiaSkillTrainer(config)
    run_dir = trainer.prepare_run_dir(run_name, mode="gaia-offline-critic-actor")
    state_config = trainer._batch_state_config(run_dir)
    reset_experience_buffer(state_config.experience_buffer_path)
    reset_skill_library(state_config.skill_library_root)
    write_skill_bundle(
        state_config.skill_library_root,
        "gaia-general-skill",
        {"SKILL.md": A_SKILL.read_text(encoding="utf-8")},
    )
    headers = discover_skills(state_config.skill_library_root)
    if not headers:
        raise RuntimeError("No skill loaded for A offline actor iter2.")
    active_skill = load_skill_detail(headers[0])
    iteration_dir = ensure_dir(run_dir / "offline_iter2")
    snapshot_skill(Path(active_skill.header.skill_dir), iteration_dir / "skill_before_actor")
    states = load_a_iter2_dev_states()
    critic = SkillCritic(state_config)
    actor = SkillActor(state_config)
    history_poll: list[dict[str, Any]] = [
        {
            "iteration_index": 1,
            "summary": "A_full offline actor iter1 was trained from boot_v3 dev states.",
        }
    ]
    reward = critic.evaluate_batch(states, active_skill, history_poll, iteration_dir)
    decision = actor.act(reward, active_skill, history_poll, iteration_dir)
    actor.apply(decision)
    updated_skill = load_skill_detail(discover_skills(state_config.skill_library_root)[0])
    skill_after = snapshot_skill(Path(updated_skill.header.skill_dir), iteration_dir / "skill_after_actor")
    write_json(
        run_dir / "offline_actor_summary.json",
        {
            "run_name": run_dir.name,
            "strategy": "full",
            "source_run": str(A_ITER2_DEV_RUN),
            "source_iteration": str(A_ITER2_DEV_ITERATION),
            "source_state_count": len(states),
            "input_skill": str(A_SKILL),
            "reward": to_dict(reward),
            "actor_decision": to_dict(decision),
            "skill_after_actor": str(skill_after / "SKILL.md"),
        },
    )
    log(f"A full offline actor iter2: produced {skill_after / 'SKILL.md'}")
    return skill_after / "SKILL.md"


def preflight() -> None:
    required = [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET, A_SKILL, B_SKILL, A_ITER2_DEV_RUN]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required paths: {missing}")
    state_count = len(list(A_ITER2_DEV_ITERATION.glob("*/state.json")))
    if state_count != 83:
        raise RuntimeError(f"A iter2 dev state count is {state_count}, expected 83")


def main() -> int:
    queue_log_raw = os.environ.get("NLRL_QUEUE_LOG_PATH", "").strip()
    queue_log = Path(queue_log_raw) if queue_log_raw else LAUNCH_LOG_ROOT / "gaia_bootv3_A_next_and_missing_queue.log"
    start_key = os.environ.get("NLRL_QUEUE_START_KEY", "missing_then_a").strip() or "missing_then_a"
    append_process_row(
        name="gaia_bootv3_A_next_and_missing_queue",
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(queue manager)",
        command=" ".join([str(PYTHON), *sys.argv]),
        log_path=queue_log,
        notes=(
            "fill A/B test missing tasks, then continue A_full to offline actor iter2, "
            f"dev iter3 eval, validation_test eval; c20; executor stream=1; start_key={start_key}"
        ),
    )
    queue_status = "finished"
    try:
        preflight()
        jobs: list[subprocess.Popen[str]] = []

        if start_key == "missing_then_a":
            a_missing = start_eval(
                run_name="gaia_bootv3_ab_A_full_iter2_test_missing1_c20_r2",
                dataset=TEST_DATASET,
                skill_path=A_SKILL,
                task_ids=A_MISSING_TEST_IDS,
            )
            jobs.append(a_missing)
            if wait_for_process(a_missing, "gaia_bootv3_ab_A_full_iter2_test_missing1_c20_r2") != 0:
                queue_status = "dead"
                return a_missing.returncode or 1

            b_missing = start_eval(
                run_name="gaia_bootv3_ab_B_sharded_iter2_test_missing3_c20_r2",
                dataset=TEST_DATASET,
                skill_path=B_SKILL,
                task_ids=B_MISSING_TEST_IDS,
            )
            jobs.append(b_missing)
            if wait_for_process(b_missing, "gaia_bootv3_ab_B_sharded_iter2_test_missing3_c20_r2") != 0:
                queue_status = "dead"
                return b_missing.returncode or 1
        elif start_key == "a_next":
            log("start_key=a_next: skipping missing-task fills")
        else:
            raise RuntimeError(f"Unsupported NLRL_QUEUE_START_KEY={start_key!r}")

        a_next_skill = run_a_next_offline_actor()
        dev_eval = start_eval(
            run_name="gaia_bootv3_ab_A_full_iter3_dev_eval_c20",
            dataset=DEV_DATASET,
            skill_path=a_next_skill,
        )
        jobs.append(dev_eval)

        test_eval: subprocess.Popen[str] | None = None
        test_name = "gaia_bootv3_ab_A_full_iter3_test_eval_c20"
        while True:
            for process in jobs:
                code = process.poll()
                if code is not None and code != 0:
                    log(f"child failed pid={process.pid} returncode={code}")
                    update_process_row(process.pid, "dead")
                    for child in jobs:
                        if child.poll() is None:
                            try:
                                os.killpg(child.pid, signal.SIGTERM)
                                update_process_row(child.pid, "stopped")
                            except Exception:
                                pass
                    queue_status = "dead"
                    return code or 1
            if test_eval is None and tail_ready(dev_eval, "gaia_bootv3_ab_A_full_iter3_dev_eval_c20"):
                test_eval = start_eval(
                    run_name=test_name,
                    dataset=TEST_DATASET,
                    skill_path=a_next_skill,
                )
                jobs.append(test_eval)
            if test_eval is not None and dev_eval.poll() is not None and test_eval.poll() is not None:
                update_process_row(dev_eval.pid, "finished" if dev_eval.returncode == 0 else "dead")
                update_process_row(test_eval.pid, "finished" if test_eval.returncode == 0 else "dead")
                log("A-next queue completed")
                return 0
            time.sleep(60)
    except Exception:
        queue_status = "dead"
        raise
    finally:
        update_process_row(os.getpid(), queue_status)


if __name__ == "__main__":
    raise SystemExit(main())
