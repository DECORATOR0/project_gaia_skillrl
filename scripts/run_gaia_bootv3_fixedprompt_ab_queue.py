from __future__ import annotations

import csv
import json
import os
import shlex
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
CONFIG = Path(os.environ.get("GAIA_BOOTV3_CONFIG", str(ROOT / "configs/system.json")))
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")


CONCURRENCY = int(
    os.environ.get("GAIA_BOOTV3_CONCURRENCY", "").strip()
    or os.environ.get("NLRL_RUNTIME_TASK_CONCURRENCY", "").strip()
    or "20"
)
CONCURRENCY_LABEL = f"c{CONCURRENCY}"
QUEUE_NAME = f"gaia_bootv3_fixedprompt_ab_queue_{CONCURRENCY_LABEL}"
BOOT_DEV_RUN_NAME = f"gaia_bootv3_fixedprompt_boot_dev_iter1_{CONCURRENCY_LABEL}"
BOOT_TEST_RUN_NAME = f"gaia_bootv3_fixedprompt_boot_test_iter1_{CONCURRENCY_LABEL}"
A_OFFLINE_RUN_NAME = "gaia_bootv3_fixedprompt_A_full_offline_actor_iter1"
B_OFFLINE_RUN_NAME = "gaia_bootv3_fixedprompt_B_sharded_offline_actor_iter1"
A_DEV_EVAL_RUN_NAME = f"gaia_bootv3_fixedprompt_A_full_iter2_dev_eval_{CONCURRENCY_LABEL}"
B_DEV_EVAL_RUN_NAME = f"gaia_bootv3_fixedprompt_B_sharded_iter2_dev_eval_{CONCURRENCY_LABEL}"
A_TEST_EVAL_RUN_NAME = f"gaia_bootv3_fixedprompt_A_full_iter2_test_eval_{CONCURRENCY_LABEL}"
B_TEST_EVAL_RUN_NAME = f"gaia_bootv3_fixedprompt_B_sharded_iter2_test_eval_{CONCURRENCY_LABEL}"


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


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
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
        "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_CRITIC_STRATEGY",
        "NLRL_RUNTIME_CRITIC_SHARD_SIZE",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS",
        "GAIA_BOOTV3_CONFIG",
        "GAIA_BOOTV3_CONCURRENCY",
    ]
    return {key: env[key] for key in keys if key in env and env[key]}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    cmd_parts = [shlex.quote(part) for part in cmd]
    return "env " + " ".join([*env_parts, *cmd_parts])


def set_runtime_defaults() -> None:
    defaults = {
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
        "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "420",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS": "420",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


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


def skill_path_for_run(run_dir: Path) -> Path:
    candidates = sorted(
        run_dir.glob("iteration_*/skill_for_eval/gaia-general-skill/SKILL.md"),
        key=lambda path: path.as_posix(),
    )
    if candidates:
        return candidates[-1]
    bootstrap_skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    if bootstrap_skill.exists():
        return bootstrap_skill
    raise RuntimeError(f"No skill_for_eval found under {run_dir}")


def iteration_state_paths(run_dir: Path) -> list[Path]:
    iteration_dir = latest_iteration_dir(run_dir)
    if iteration_dir is None:
        return []
    return sorted(iteration_dir.glob("*/state.json"))


def load_boot_states(boot_run_dir: Path) -> list[EnvState]:
    state_paths = iteration_state_paths(boot_run_dir)
    if len(state_paths) != 83:
        raise RuntimeError(f"Expected 83 boot_dev states, found {len(state_paths)} in {boot_run_dir}")
    return [load_state(path) for path in state_paths]


def snapshot_skill(skill_dir: Path, target_root: Path) -> Path:
    target = target_root / "gaia-general-skill"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(skill_dir, target)
    return target


def train_env(
    *,
    iterations: int,
    skill_path: Path | None = None,
    bootstrap: bool = False,
    strategy: str = "full",
) -> dict[str, str]:
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
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "420",
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": str(iterations),
            "NLRL_RUNTIME_CRITIC_STRATEGY": strategy,
            "NLRL_RUNTIME_CRITIC_SHARD_SIZE": "12",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "420",
        }
    )
    env.pop("NLRL_RUNTIME_INITIAL_SKILL_PATH", None)
    env.pop("NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL", None)
    if skill_path is not None:
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(skill_path)
    if bootstrap:
        env["NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL"] = "1"
    return env


def start_train(
    *,
    run_name: str,
    dataset: Path,
    iterations: int,
    skill_path: Path | None = None,
    bootstrap: bool = False,
    strategy: str = "full",
    notes: str,
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
    if bootstrap:
        cmd.append("--bootstrap-skill")
    env = train_env(
        iterations=iterations,
        skill_path=skill_path,
        bootstrap=bootstrap,
        strategy=strategy,
    )
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
        command=command_display(env, cmd),
        log_path=log_path,
        notes=notes,
    )
    log(f"started {run_name} pid={process.pid} log={log_path}")
    return process


def wait_for_process(process: subprocess.Popen[str], run_name: str) -> int:
    while True:
        code = process.poll()
        done, total = progress_for(run_name)
        if total:
            log(f"wait {run_name}: {done}/{total}")
        if code is not None:
            status = "finished" if code == 0 else "dead"
            update_process_row(process.pid, status)
            log(f"{run_name} exited returncode={code}")
            return code
        time.sleep(60)


def stop_children(children: list[subprocess.Popen[str]]) -> None:
    for process in children:
        if process.poll() is not None:
            continue
        try:
            os.killpg(process.pid, signal.SIGTERM)
            update_process_row(process.pid, "stopped")
        except Exception:
            pass


def run_offline_variant(
    *,
    strategy: str,
    run_name: str,
    boot_run_dir: Path,
    boot_skill: Path,
) -> Path:
    log(f"offline {strategy}: preparing run")
    config = clone_system_config(
        load_system_config(CONFIG),
        runtime={"critic_strategy": strategy, "critic_shard_size": 12},
    )
    trainer = GaiaSkillTrainer(config)
    run_dir = trainer.prepare_run_dir(run_name, mode="gaia-offline-critic-actor")
    state_config = trainer._batch_state_config(run_dir)
    reset_experience_buffer(state_config.experience_buffer_path)
    reset_skill_library(state_config.skill_library_root)
    write_skill_bundle(
        state_config.skill_library_root,
        "gaia-general-skill",
        {"SKILL.md": boot_skill.read_text(encoding="utf-8")},
    )
    headers = discover_skills(state_config.skill_library_root)
    if not headers:
        raise RuntimeError(f"No skill loaded for offline actor run {run_name}.")
    active_skill = load_skill_detail(headers[0])
    iteration_dir = ensure_dir(run_dir / "offline_iter1")
    snapshot_skill(Path(active_skill.header.skill_dir), iteration_dir / "skill_before_actor")
    states = load_boot_states(boot_run_dir)
    critic = SkillCritic(state_config)
    actor = SkillActor(state_config)
    history_poll: list[dict[str, Any]] = [
        {
            "iteration_index": 0,
            "summary": (
                "Fresh boot-v3 dev states after executor_system dynamic-phase fix "
                "and bootstrap CONCLUDE-gate prompt fix."
            ),
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
            "strategy": strategy,
            "source_run": str(boot_run_dir),
            "source_iteration": str(latest_iteration_dir(boot_run_dir) or ""),
            "source_state_count": len(states),
            "input_skill": str(boot_skill),
            "reward": to_dict(reward),
            "actor_decision": to_dict(decision),
            "skill_after_actor": str(skill_after / "SKILL.md"),
        },
    )
    log(f"offline {strategy}: produced {skill_after / 'SKILL.md'}")
    return skill_after / "SKILL.md"


def preflight() -> None:
    required = [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required paths: {missing}")

    executor_prompt = (ROOT / "prompts/executor_system.md").read_text(encoding="utf-8")
    if "GATHER and ANALYZE" in executor_prompt or "do not output the final answer" in executor_prompt:
        raise RuntimeError("executor_system.md still contains stale GATHER/ANALYZE wording.")

    bootstrap_prompt = (ROOT / "prompts/bootstrap_skill_system.md").read_text(encoding="utf-8")
    if "CONCLUDE` as a gated finalization phase" not in bootstrap_prompt:
        raise RuntimeError("bootstrap_skill_system.md does not contain the CONCLUDE gate constraint.")


def require_completed_run(run_name: str, expected_states: int) -> Path:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        raise RuntimeError(f"Run directory was not created for {run_name}")
    state_count = len(iteration_state_paths(run_dir))
    if state_count != expected_states:
        raise RuntimeError(f"{run_name} state count is {state_count}, expected {expected_states}")
    return run_dir


def run_eval_sequence(
    *,
    eval_specs: list[tuple[str, Path, Path, str]],
    children: list[subprocess.Popen[str]],
) -> int:
    for run_name, dataset, skill_path, notes in eval_specs:
        process = start_train(
            run_name=run_name,
            dataset=dataset,
            iterations=0,
            skill_path=skill_path,
            notes=notes,
        )
        children.append(process)
        code = wait_for_process(process, run_name)
        if code != 0:
            return code or 1
    return 0


def main() -> int:
    set_runtime_defaults()
    queue_log_raw = os.environ.get("NLRL_QUEUE_LOG_PATH", "").strip()
    queue_log = Path(queue_log_raw) if queue_log_raw else LAUNCH_LOG_ROOT / f"{QUEUE_NAME}.log"
    append_process_row(
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(queue manager)",
        command=command_display(os.environ, [str(PYTHON), *sys.argv]),
        log_path=queue_log,
        notes=(
            "fresh boot-v3 after executor and bootstrap CONCLUDE fixes; skip direct; "
            f"sequential queue; train/eval children use {CONCURRENCY_LABEL}"
        ),
    )
    children: list[subprocess.Popen[str]] = []
    queue_status = "finished"
    try:
        preflight()

        boot_dev = start_train(
            run_name=BOOT_DEV_RUN_NAME,
            dataset=DEV_DATASET,
            iterations=0,
            bootstrap=True,
            notes=(
                f"fixed-prompt boot-v3 dev; bootstrap fresh skill; {CONCURRENCY_LABEL}; "
                "executor prompt dynamic phase graph; bootstrap CONCLUDE gate"
            ),
        )
        children.append(boot_dev)
        code = wait_for_process(boot_dev, BOOT_DEV_RUN_NAME)
        if code != 0:
            queue_status = "dead"
            return code or 1

        boot_dev_run = require_completed_run(BOOT_DEV_RUN_NAME, expected_states=83)
        boot_skill = skill_path_for_run(boot_dev_run)
        log(f"boot_dev skill={boot_skill}")

        boot_test = start_train(
            run_name=BOOT_TEST_RUN_NAME,
            dataset=TEST_DATASET,
            iterations=0,
            skill_path=boot_skill,
            notes=f"fixed-prompt boot-v3 validation_test eval; {CONCURRENCY_LABEL}; skill={boot_skill}",
        )
        children.append(boot_test)
        code = wait_for_process(boot_test, BOOT_TEST_RUN_NAME)
        if code != 0:
            queue_status = "dead"
            return code or 1
        require_completed_run(BOOT_TEST_RUN_NAME, expected_states=82)

        a_skill = run_offline_variant(
            strategy="full",
            run_name=A_OFFLINE_RUN_NAME,
            boot_run_dir=boot_dev_run,
            boot_skill=boot_skill,
        )
        b_skill = run_offline_variant(
            strategy="sharded",
            run_name=B_OFFLINE_RUN_NAME,
            boot_run_dir=boot_dev_run,
            boot_skill=boot_skill,
        )

        code = run_eval_sequence(
            eval_specs=[
                (
                    A_DEV_EVAL_RUN_NAME,
                    DEV_DATASET,
                    a_skill,
                    f"fixed-prompt A full offline actor iter1 dev eval; {CONCURRENCY_LABEL}; skill={a_skill}",
                ),
                (
                    B_DEV_EVAL_RUN_NAME,
                    DEV_DATASET,
                    b_skill,
                    f"fixed-prompt B sharded offline actor iter1 dev eval; {CONCURRENCY_LABEL}; skill={b_skill}",
                ),
                (
                    A_TEST_EVAL_RUN_NAME,
                    TEST_DATASET,
                    a_skill,
                    f"fixed-prompt A full offline actor iter1 validation_test eval; {CONCURRENCY_LABEL}; skill={a_skill}",
                ),
                (
                    B_TEST_EVAL_RUN_NAME,
                    TEST_DATASET,
                    b_skill,
                    f"fixed-prompt B sharded offline actor iter1 validation_test eval; {CONCURRENCY_LABEL}; skill={b_skill}",
                ),
            ],
            children=children,
        )
        if code != 0:
            queue_status = "dead"
            return code or 1

        log("fixed-prompt A/B queue completed")
        return 0
    except KeyboardInterrupt:
        queue_status = "stopped"
        stop_children(children)
        return 130
    except Exception:
        queue_status = "dead"
        stop_children(children)
        raise
    finally:
        update_process_row(os.getpid(), queue_status)


if __name__ == "__main__":
    raise SystemExit(main())
