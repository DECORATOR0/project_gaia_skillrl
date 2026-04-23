from __future__ import annotations

import csv
import json
import math
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
BOOT_DEV_RUN = ROOT / "runs/2026/4/2026-4-21/20260421_214829_gaia_bootv3_ab_boot_dev_iter1_c20"
BOOT_DEV_ITERATION = BOOT_DEV_RUN / "iteration_01"
BOOT_SKILL = BOOT_DEV_RUN / "iteration_01/skill_for_eval/gaia-general-skill/SKILL.md"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")


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


def load_boot_states() -> list[EnvState]:
    state_paths = sorted(BOOT_DEV_ITERATION.glob("*/state.json"))
    if len(state_paths) != 83:
        raise RuntimeError(f"Expected 83 boot_dev states, found {len(state_paths)}")
    return [load_state(path) for path in state_paths]


def snapshot_skill(skill_dir: Path, target_root: Path) -> Path:
    target = target_root / "gaia-general-skill"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(skill_dir, target)
    return target


def run_offline_variant(*, strategy: str, run_name: str) -> Path:
    log(f"offline {strategy}: preparing run")
    config = load_system_config(CONFIG)
    config = clone_system_config(config, runtime={
        "critic_strategy": strategy,
        "critic_shard_size": 12,
    })
    trainer = GaiaSkillTrainer(config)
    run_dir = trainer.prepare_run_dir(run_name, mode="gaia-offline-critic-actor")
    state_config = trainer._batch_state_config(run_dir)
    reset_experience_buffer(state_config.experience_buffer_path)
    reset_skill_library(state_config.skill_library_root)
    write_skill_bundle(
        state_config.skill_library_root,
        "gaia-general-skill",
        {"SKILL.md": BOOT_SKILL.read_text(encoding="utf-8")},
    )
    headers = discover_skills(state_config.skill_library_root)
    if not headers:
        raise RuntimeError("No skill loaded for offline actor run.")
    active_skill = load_skill_detail(headers[0])
    iteration_dir = ensure_dir(run_dir / "offline_iter1")
    snapshot_skill(Path(active_skill.header.skill_dir), iteration_dir / "skill_before_actor")
    states = load_boot_states()
    critic = SkillCritic(state_config)
    actor = SkillActor(state_config)
    history_poll: list[dict[str, Any]] = []
    reward = critic.evaluate_batch(states, active_skill, history_poll, iteration_dir)
    decision = actor.act(reward, active_skill, history_poll, iteration_dir)
    actor.apply(decision)
    updated_skill = load_skill_detail(discover_skills(state_config.skill_library_root)[0])
    skill_after = snapshot_skill(Path(updated_skill.header.skill_dir), iteration_dir / "skill_after_actor")
    write_json(
        run_dir / "offline_actor_summary.json",
        {
            "run_name": run_dir.name,
            "strategy": strategy,
            "source_run": str(BOOT_DEV_RUN),
            "source_iteration": str(BOOT_DEV_ITERATION),
            "source_state_count": len(states),
            "reward": to_dict(reward),
            "actor_decision": to_dict(decision),
            "skill_after_actor": str(skill_after / "SKILL.md"),
        },
    )
    log(f"offline {strategy}: produced {skill_after / 'SKILL.md'}")
    return skill_after / "SKILL.md"


def append_process_row(*, name: str, pid: int, cwd: Path, run_dir: str, command: str, log_path: Path, notes: str) -> None:
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
    selected = run_dir / "selected_tasks.json"
    if not selected.exists():
        return 0
    try:
        return len(json.loads(selected.read_text(encoding="utf-8")).get("task_ids", []))
    except Exception:
        return 0


def latest_iteration_dir(run_dir: Path) -> Path | None:
    iterations = [
        path for path in run_dir.glob("iteration_*")
        if path.is_dir() and any(child.is_dir() for child in path.iterdir())
    ]
    if not iterations:
        return None
    return max(iterations, key=lambda path: path.name)


def tail_ready(process: subprocess.Popen[str], run_name: str) -> bool:
    if process.poll() is not None:
        return True
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return False
    total = selected_task_count(run_dir)
    iteration = latest_iteration_dir(run_dir)
    if total <= 0 or iteration is None:
        return False
    done = len(list(iteration.glob("*/state.json")))
    remaining = max(0, total - done)
    threshold = max(1, math.floor(total * 0.05))
    ready = remaining <= threshold
    log(f"tail check {run_name}: {done}/{total}, remaining={remaining}, threshold={threshold}, ready={ready}")
    return ready


def start_eval(*, run_name: str, dataset: Path, skill_path: Path) -> subprocess.Popen[str]:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{stamp}.log"
    env = os.environ.copy()
    env.update({
        "NLRL_RUNTIME_TASK_CONCURRENCY": "20",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS": "20",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH": str(skill_path),
    })
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
    append_process_row(
        name=run_name,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=" ".join(cmd),
        log_path=log_path,
        notes=f"offline A/B eval child; skill={skill_path}",
    )
    log(f"started eval {run_name} pid={process.pid} log={log_path}")
    return process


def stop_children(children: list[subprocess.Popen[str]]) -> None:
    for process in children:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                pass


def main() -> int:
    if not BOOT_SKILL.exists():
        raise RuntimeError(f"Missing boot skill: {BOOT_SKILL}")
    a_skill = run_offline_variant(strategy="full", run_name="gaia_bootv3_ab_A_full_offline_actor_iter1")
    b_skill = run_offline_variant(strategy="sharded", run_name="gaia_bootv3_ab_B_sharded_offline_actor_iter1")

    eval_specs = [
        ("gaia_bootv3_ab_A_full_iter2_dev_eval_c20", DEV_DATASET, a_skill),
        ("gaia_bootv3_ab_B_sharded_iter2_dev_eval_c20", DEV_DATASET, b_skill),
        ("gaia_bootv3_ab_A_full_iter2_test_eval_c20", TEST_DATASET, a_skill),
        ("gaia_bootv3_ab_B_sharded_iter2_test_eval_c20", TEST_DATASET, b_skill),
    ]
    children: list[subprocess.Popen[str]] = []
    try:
        current = start_eval(run_name=eval_specs[0][0], dataset=eval_specs[0][1], skill_path=eval_specs[0][2])
        children.append(current)
        next_index = 1
        while True:
            if next_index < len(eval_specs) and tail_ready(current, eval_specs[next_index - 1][0]):
                spec = eval_specs[next_index]
                current = start_eval(run_name=spec[0], dataset=spec[1], skill_path=spec[2])
                children.append(current)
                next_index += 1
            if next_index == len(eval_specs) and all(child.poll() is not None for child in children):
                log("all offline A/B eval jobs completed")
                return 0
            for child in children:
                if child.poll() not in {None, 0}:
                    log(f"child failed pid={child.pid} returncode={child.returncode}")
                    stop_children(children)
                    return child.returncode or 1
            time.sleep(60)
    except KeyboardInterrupt:
        stop_children(children)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
