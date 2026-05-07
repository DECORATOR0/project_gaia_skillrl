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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
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
    os.environ.get("GAIA_FRESH_QUEUE_CONCURRENCY", "").strip()
    or os.environ.get("NLRL_RUNTIME_TASK_CONCURRENCY", "").strip()
    or "20"
)
TAIL_THRESHOLD = int(os.environ.get("GAIA_FRESH_QUEUE_TAIL_THRESHOLD", "3"))
POLL_SECONDS = int(os.environ.get("GAIA_FRESH_QUEUE_POLL_SECONDS", "60"))
ALLOW_PARTIAL_TAIL = os.environ.get("GAIA_FRESH_QUEUE_ALLOW_PARTIAL_TAIL", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
TAIL_GRACE_POLLS = int(os.environ.get("GAIA_FRESH_QUEUE_TAIL_GRACE_POLLS", "3"))
SERIALIZE_EVAL_WAVES = os.environ.get(
    "GAIA_FRESH_QUEUE_SERIALIZE_EVAL_WAVES", "0"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RUN_PREFIX = os.environ.get("GAIA_FRESH_QUEUE_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)
RESUME_BOOT_DEV_RUN_NAME = os.environ.get("GAIA_FRESH_QUEUE_RESUME_BOOT_DEV_RUN_NAME", "").strip()
RESUME_BOOT_SKILL_PATH = os.environ.get("GAIA_FRESH_QUEUE_RESUME_BOOT_SKILL_PATH", "").strip()
CONCURRENCY_LABEL = f"c{CONCURRENCY}"

BOOT_DEV_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_boot_dev_{CONCURRENCY_LABEL}"
BOOT_TEST_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_boot_test_{CONCURRENCY_LABEL}"
A_OFFLINE_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_A_full_offline_actor_iter1"
B_OFFLINE_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_B_sharded_offline_actor_iter1"
A_DEV_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_A_full_dev_eval_{CONCURRENCY_LABEL}"
A_TEST_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_A_full_test_eval_{CONCURRENCY_LABEL}"
B_DEV_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_B_sharded_dev_eval_{CONCURRENCY_LABEL}"
B_TEST_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_B_sharded_test_eval_{CONCURRENCY_LABEL}"
QUEUE_NAME = f"{RUN_PREFIX}_fresh_bootstrap_ab_tail_queue_{CONCURRENCY_LABEL}"

REMOTE_EXECUTOR_BASE_URL = os.environ.get("GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URL", "").strip()
REMOTE_EXECUTOR_BASE_URLS = [
    item.strip()
    for item in os.environ.get("GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URLS", "").split(",")
    if item.strip()
]
REMOTE_EXECUTOR_LANES = max(1, int(os.environ.get("GAIA_ARCHV5_REMOTE_EXECUTOR_LANES", "4")))
EXECUTOR_MODEL = os.environ.get(
    "GAIA_ARCHV5_EXECUTOR_MODEL",
    "qwen3.5-9b" if (REMOTE_EXECUTOR_BASE_URL or REMOTE_EXECUTOR_BASE_URLS) else "Qwen3-8B-local",
).strip()
if REMOTE_EXECUTOR_BASE_URLS:
    LANES = {f"remote{idx}": url for idx, url in enumerate(REMOTE_EXECUTOR_BASE_URLS)}
elif REMOTE_EXECUTOR_BASE_URL:
    LANES = {f"remote{idx}": REMOTE_EXECUTOR_BASE_URL for idx in range(REMOTE_EXECUTOR_LANES)}
else:
    LANES = {
        "gpu0": "http://127.0.0.1:8100/v1",
        "gpu1": "http://127.0.0.1:8101/v1",
        "gpu2": "http://127.0.0.1:8102/v1",
        "gpu3": "http://127.0.0.1:8103/v1",
    }
LANE_NAMES = list(LANES)


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


@dataclass
class TrainJob:
    key: str
    run_name: str
    dataset: Path
    lane: str
    iterations: int = 0
    skill_path: Path | None = None
    bootstrap: bool = False
    strategy: str = "full"
    process: subprocess.Popen[str] | None = None
    log_path: Path | None = None
    terminal_status: str | None = None


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
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_CRITIC_STRATEGY",
        "NLRL_RUNTIME_CRITIC_SHARD_SIZE",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_BASE_URL",
        "NLRL_ACTOR_API_KEY",
        "NLRL_ACTOR_API_MODE",
        "NLRL_ACTOR_TIMEOUT_SECONDS",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_BASE_URL",
        "NLRL_CRITIC_API_KEY",
        "NLRL_CRITIC_API_MODE",
        "NLRL_CRITIC_TIMEOUT_SECONDS",
        "NLRL_CODEX_CLI_PATH",
        "NLRL_CODEX_CLI_WORKDIR",
        "NLRL_CODEX_CLI_TIMEOUT_SECONDS",
        "NLRL_CODEX_CLI_EXTRA_ARGS",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_API_KEY",
        "NLRL_EXECUTOR_API_MODE",
        "NLRL_EXECUTOR_STREAM",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_TEMPERATURE",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS",
        "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS",
        "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_API_KEY",
        "NLRL_TOOL_TIMEOUT_SECONDS",
        "NLRL_TOOL_MODEL",
        "NLRL_TOOL_AUDIO_MODEL",
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URL",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URLS",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_LANES",
        "GAIA_ARCHV5_EXECUTOR_MODEL",
        "GAIA_FRESH_QUEUE_SKIP_BOOTSTRAP_PREFLIGHT",
        "GAIA_FRESH_QUEUE_PREFIX",
        "GAIA_FRESH_QUEUE_CONCURRENCY",
        "GAIA_FRESH_QUEUE_TAIL_THRESHOLD",
        "GAIA_FRESH_QUEUE_ALLOW_PARTIAL_TAIL",
        "GAIA_FRESH_QUEUE_TAIL_GRACE_POLLS",
        "GAIA_FRESH_QUEUE_SERIALIZE_EVAL_WAVES",
        "GAIA_FRESH_QUEUE_RESUME_BOOT_DEV_RUN_NAME",
        "GAIA_FRESH_QUEUE_RESUME_BOOT_SKILL_PATH",
        "GAIA_FRESH_REQUIRE_CODEX_ACTOR",
        "GAIA_QUEUE_CLEANUP_SERVER_PIDS",
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


def latest_run_dir(run_name: str) -> Path | None:
    matches: list[Path] = []
    patterns = [f"**/{run_name}", f"**/*_{run_name}"]
    run_name_parts = run_name.split("_", 2)
    if (
        len(run_name_parts) == 3
        and run_name_parts[0].isdigit()
        and len(run_name_parts[0]) == 8
        and run_name_parts[1].isdigit()
        and len(run_name_parts[1]) in {4, 6}
    ):
        patterns.append(f"**/*_{run_name_parts[2]}")
    for pattern in patterns:
        matches.extend(path for path in RUN_ROOT.glob(pattern) if path.is_dir())
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


def iteration_state_paths(run_dir: Path) -> list[Path]:
    iteration_dir = latest_iteration_dir(run_dir)
    if iteration_dir is None:
        return []
    return sorted(iteration_dir.glob("*/state.json"))


def progress_for(run_name: str) -> tuple[int, int]:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return 0, 0
    total = selected_task_count(run_dir)
    return len(iteration_state_paths(run_dir)), total


def min_required_states(total: int) -> int:
    if total <= 0:
        return 0
    if not ALLOW_PARTIAL_TAIL:
        return total
    return max(1, total - TAIL_THRESHOLD)


def run_has_required_states(run_name: str) -> bool:
    done, total = progress_for(run_name)
    return total > 0 and done >= min_required_states(total)


def tail_ready(job: TrainJob) -> bool:
    done, total = progress_for(job.run_name)
    if total <= 0:
        return False
    remaining = max(0, total - done)
    ready = remaining <= TAIL_THRESHOLD
    log(
        f"tail check {job.key}: {done}/{total}, "
        f"remaining={remaining}, threshold={TAIL_THRESHOLD}, ready={ready}"
    )
    return ready


def stop_job(job: TrainJob, status: str) -> None:
    process = job.process
    if process is None or process.poll() is not None:
        return
    try:
        job.terminal_status = status
        os.killpg(process.pid, signal.SIGTERM)
        update_process_row(process.pid, status)
        log(f"stopped {job.key} pid={process.pid} status={status}")
    except ProcessLookupError:
        update_process_row(process.pid, "dead")
    except Exception as exc:
        log(f"failed to stop {job.key} pid={process.pid}: {exc}")


def wait_for_run_required_states(job: TrainJob, label: str) -> None:
    while True:
        if run_has_required_states(job.run_name):
            done, total = progress_for(job.run_name)
            log(
                f"{label} ready: {done}/{total} states "
                f"(required={min_required_states(total)}, partial_tail={ALLOW_PARTIAL_TAIL})"
            )
            return
        code = check_job(job)
        if code is not None:
            done, total = progress_for(job.run_name)
            raise RuntimeError(
                f"{job.key} ended before enough states were available: "
                f"returncode={code}, states={done}/{total}, required={min_required_states(total)}"
            )
        done, total = progress_for(job.run_name)
        log(
            f"waiting for {label}: {done}/{total}, "
            f"required={min_required_states(total) if total else 'unknown'}"
        )
        time.sleep(POLL_SECONDS)


def bootstrap_skill_path(run_name: str) -> Path | None:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def snapshot_skill(skill_dir: Path, target_root: Path) -> Path:
    target = target_root / "gaia-general-skill"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(skill_dir, target)
    return target


def build_env(job: TrainJob) -> dict[str, str]:
    if job.lane not in LANES:
        raise RuntimeError(f"Unknown lane {job.lane!r}")
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
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": str(job.iterations),
            "NLRL_RUNTIME_CRITIC_STRATEGY": job.strategy,
            "NLRL_RUNTIME_CRITIC_SHARD_SIZE": "12",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": os.environ.get(
                "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY", "any_phase"
            ),
            "NLRL_EXECUTOR_MODEL": EXECUTOR_MODEL,
            "NLRL_EXECUTOR_BASE_URL": LANES[job.lane],
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": os.environ.get("NLRL_EXECUTOR_TIMEOUT_SECONDS", "1200"),
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": os.environ.get(
                "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS", "1200"
            ),
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_TOOL_BASE_URL": os.environ.get("NLRL_TOOL_BASE_URL", "http://35.220.164.252:3888/v1"),
            "NLRL_TOOL_API_KEY": os.environ.get(
                "NLRL_TOOL_API_KEY",
                "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            ),
            "NLRL_TOOL_TIMEOUT_SECONDS": os.environ.get("NLRL_TOOL_TIMEOUT_SECONDS", "1200"),
            "NLRL_TOOL_MODEL": os.environ.get("NLRL_TOOL_MODEL", "gpt-4o-mini"),
            "NLRL_TOOL_AUDIO_MODEL": os.environ.get("NLRL_TOOL_AUDIO_MODEL", "gpt-4o-mini-transcribe"),
        }
    )
    if job.skill_path is not None:
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(job.skill_path)
    if job.bootstrap:
        env["NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL"] = "1"
    enforce_codex_actor_critic_if_required(env)
    return env


def env_truthy(env: dict[str, str], key: str) -> bool:
    return env.get(key, "").strip().lower() in {"1", "true", "yes", "on"}


def enforce_codex_actor_critic_if_required(env: dict[str, str]) -> None:
    if not env_truthy(env, "GAIA_FRESH_REQUIRE_CODEX_ACTOR"):
        return
    defaults = {
        "NLRL_ACTOR_MODEL": "gpt-5.4",
        "NLRL_ACTOR_BASE_URL": "codex-cli",
        "NLRL_ACTOR_API_KEY": "EMPTY",
        "NLRL_ACTOR_API_MODE": "codex_cli",
        "NLRL_ACTOR_TIMEOUT_SECONDS": "1800",
        "NLRL_CRITIC_MODEL": "gpt-5.4",
        "NLRL_CRITIC_BASE_URL": "codex-cli",
        "NLRL_CRITIC_API_KEY": "EMPTY",
        "NLRL_CRITIC_API_MODE": "codex_cli",
        "NLRL_CRITIC_TIMEOUT_SECONDS": "1800",
        "NLRL_CODEX_CLI_TIMEOUT_SECONDS": "1800",
    }
    for key, value in defaults.items():
        env.setdefault(key, value)
    for role in ("ACTOR", "CRITIC"):
        mode = env.get(f"NLRL_{role}_API_MODE", "").strip().lower()
        base_url = env.get(f"NLRL_{role}_BASE_URL", "").strip()
        model = env.get(f"NLRL_{role}_MODEL", "").strip()
        if mode != "codex_cli" or base_url != "codex-cli" or not model:
            raise RuntimeError(
                f"GAIA_FRESH_REQUIRE_CODEX_ACTOR=1 but {role.lower()} resolved to "
                f"model={model!r}, base_url={base_url!r}, api_mode={mode!r}"
            )


def start_train(job: TrainJob, notes: str) -> TrainJob:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job.log_path = LAUNCH_LOG_ROOT / f"{job.run_name}_{stamp}.log"
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
    log(
        f"started {job.key} pid={process.pid} lane={job.lane} "
        f"base_url={LANES[job.lane]} log={job.log_path}"
    )
    return job


def check_job(job: TrainJob) -> int | None:
    if job.process is None:
        return None
    code = job.process.poll()
    if code is None:
        return None
    if code == 0:
        update_process_row(job.process.pid, "finished")
    elif job.terminal_status and job.terminal_status.startswith("stopped_tail_partial") and run_has_required_states(job.run_name):
        update_process_row(job.process.pid, "partial_tail_accepted")
    else:
        update_process_row(job.process.pid, "dead")
    return code


def ensure_job_ok(job: TrainJob) -> None:
    code = check_job(job)
    if code is not None and code != 0:
        if job.terminal_status and job.terminal_status.startswith("stopped_tail_partial") and run_has_required_states(job.run_name):
            done, total = progress_for(job.run_name)
            log(f"accepting partial {job.key} after {job.terminal_status}: returncode={code}, states={done}/{total}")
            return
        raise RuntimeError(f"{job.key} failed with returncode={code}")


def wait_until(label: str, predicate, jobs: list[TrainJob]) -> None:
    while True:
        for job in jobs:
            ensure_job_ok(job)
        if predicate():
            return
        log(f"waiting for {label}")
        time.sleep(POLL_SECONDS)


def stop_children(jobs: list[TrainJob]) -> None:
    for job in jobs:
        stop_job(job, "stopped")


def cleanup_model_servers() -> None:
    raw = os.environ.get("GAIA_QUEUE_CLEANUP_SERVER_PIDS", "").strip()
    if not raw:
        return
    for pid_text in [item.strip() for item in raw.split(",") if item.strip()]:
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        try:
            os.killpg(pid, signal.SIGTERM)
            update_process_row(pid, "stopped_after_gaia_bootstrap_ab_queue")
            log(f"stopped model server pid={pid}")
        except ProcessLookupError:
            update_process_row(pid, "dead")
        except Exception as exc:
            log(f"failed to stop model server pid={pid}: {exc}")


def load_boot_states(boot_run_dir: Path) -> list[EnvState]:
    state_paths = iteration_state_paths(boot_run_dir)
    total = selected_task_count(boot_run_dir)
    expected_states = total or 83
    required_states = min_required_states(expected_states)
    if len(state_paths) < required_states:
        raise RuntimeError(
            f"Need at least {required_states} boot states for AB, "
            f"found {len(state_paths)} in {boot_run_dir}"
        )
    if len(state_paths) < expected_states:
        log(
            f"using partial boot states for AB: {len(state_paths)}/{expected_states} "
            f"(tail_threshold={TAIL_THRESHOLD})"
        )
    return [load_state(path) for path in state_paths]


def run_offline_variant(
    *,
    strategy: str,
    run_name: str,
    boot_run_dir: Path,
    boot_skill: Path,
) -> Path:
    log(f"offline {strategy}: preparing run from {boot_run_dir}")
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
                f"Fresh boot-v3 dev states after {EXECUTOR_MODEL} executor alignment and "
                f"tail-threshold={TAIL_THRESHOLD} scheduling."
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
            "source_selected_count": selected_task_count(boot_run_dir),
            "tail_threshold": TAIL_THRESHOLD,
            "input_skill": str(boot_skill),
            "reward": to_dict(reward),
            "actor_decision": to_dict(decision),
            "skill_after_actor": str(skill_after / "SKILL.md"),
        },
    )
    log(f"offline {strategy}: produced {skill_after / 'SKILL.md'}")
    return skill_after / "SKILL.md"


def boot_test_lane_ready(boot_test: TrainJob | None) -> bool:
    if boot_test is None:
        return True
    if run_has_required_states(boot_test.run_name):
        if boot_test.process is not None and boot_test.process.poll() is None and tail_ready(boot_test):
            stop_job(boot_test, "stopped_tail_partial_for_b_dev")
        return True
    code = check_job(boot_test)
    if code is None:
        return False
    done, total = progress_for(boot_test.run_name)
    raise RuntimeError(
        f"boot_test ended before enough states were available for B_dev lane release: "
        f"returncode={code}, states={done}/{total}, required={min_required_states(total)}"
    )


def start_a_eval_jobs(a_skill: Path) -> list[TrainJob]:
    a_dev = start_train(
        TrainJob(
            key="a_dev",
            run_name=A_DEV_RUN_NAME,
            dataset=DEV_DATASET,
            lane=LANE_NAMES[0],
            skill_path=a_skill,
        ),
        notes=f"fresh A_full offline actor skill validation_dev83 eval; lane={LANE_NAMES[0]}; skill={a_skill}",
    )
    a_test = start_train(
        TrainJob(
            key="a_test",
            run_name=A_TEST_RUN_NAME,
            dataset=TEST_DATASET,
            lane=LANE_NAMES[min(1, len(LANE_NAMES) - 1)],
            skill_path=a_skill,
        ),
        notes=f"fresh A_full offline actor skill validation_test82 eval; lane={LANE_NAMES[min(1, len(LANE_NAMES) - 1)]}; skill={a_skill}",
    )
    return [a_dev, a_test]


def start_b_test_eval(b_skill: Path) -> TrainJob:
    return start_train(
        TrainJob(
            key="b_test",
            run_name=B_TEST_RUN_NAME,
            dataset=TEST_DATASET,
            lane=LANE_NAMES[min(3, len(LANE_NAMES) - 1)],
            skill_path=b_skill,
        ),
        notes=f"fresh B_sharded offline actor skill validation_test82 eval; lane={LANE_NAMES[min(3, len(LANE_NAMES) - 1)]}; skill={b_skill}",
    )


def start_b_dev_eval(b_skill: Path) -> TrainJob:
    return start_train(
        TrainJob(
            key="b_dev",
            run_name=B_DEV_RUN_NAME,
            dataset=DEV_DATASET,
            lane=LANE_NAMES[min(2, len(LANE_NAMES) - 1)],
            skill_path=b_skill,
        ),
        notes=f"fresh B_sharded offline actor skill validation_dev83 eval; lane={LANE_NAMES[min(2, len(LANE_NAMES) - 1)]}; skill={b_skill}",
    )


def preflight() -> None:
    required = [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required paths: {missing}")
    if os.environ.get("GAIA_FRESH_QUEUE_SKIP_BOOTSTRAP_PREFLIGHT", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        bootstrap_prompt = (ROOT / "prompts/bootstrap_skill_system.md").read_text(encoding="utf-8")
        if "Make `CONCLUDE` reachable quickly" not in bootstrap_prompt:
            raise RuntimeError("bootstrap_skill_system.md is not the 4/21 quick-CONCLUDE prompt.")


def wait_all(jobs: list[TrainJob], *, allow_tail_stop: bool = True) -> None:
    failures: dict[str, int] = {}
    tail_seen: dict[str, int] = {}
    while True:
        unfinished = []
        for job in jobs:
            code = check_job(job)
            if code is None:
                if allow_tail_stop and ALLOW_PARTIAL_TAIL and tail_ready(job):
                    tail_seen[job.key] = tail_seen.get(job.key, 0) + 1
                    if tail_seen[job.key] >= TAIL_GRACE_POLLS:
                        stop_job(job, "stopped_tail_partial")
                        continue
                else:
                    tail_seen.pop(job.key, None)
                unfinished.append(job)
            elif code != 0:
                if run_has_required_states(job.run_name):
                    done, total = progress_for(job.run_name)
                    log(f"accepting partial {job.key} after returncode={code}: {done}/{total}")
                    update_process_row(job.process.pid, "partial_tail_accepted")
                else:
                    failures[job.key] = code
        if not unfinished:
            if failures:
                raise RuntimeError(f"Jobs failed: {failures}")
            return
        for job in unfinished:
            done, total = progress_for(job.run_name)
            if total:
                log(f"running {job.key}: {done}/{total}")
        time.sleep(POLL_SECONDS)


def main() -> int:
    queue_log_raw = os.environ.get("NLRL_QUEUE_LOG_PATH", "").strip()
    queue_log = Path(queue_log_raw) if queue_log_raw else LAUNCH_LOG_ROOT / f"{QUEUE_NAME}.log"
    mode_note = (
        f"resume boot_dev={RESUME_BOOT_DEV_RUN_NAME}"
        if RESUME_BOOT_DEV_RUN_NAME
        else "fresh bootstrap"
    )
    append_process_row(
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(queue manager)",
        command=command_display(os.environ, [str(PYTHON), *sys.argv]),
        log_path=queue_log,
        notes=(
            "bootstrap prompt rolled back to 4/21 quick-CONCLUDE version; direct skipped; "
            f"{mode_note}; partial tail allowed={ALLOW_PARTIAL_TAIL}; "
            "generate A/B from dev states once tail threshold is reached; dependency-ready scheduling; "
            f"run A_dev gpu0, A_test gpu1, B_dev gpu2, B_test gpu3; {CONCURRENCY_LABEL}; "
            f"serialize_eval_waves={SERIALIZE_EVAL_WAVES}."
        ),
        kind="experiment_queue",
    )
    jobs: list[TrainJob] = []
    queue_status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        preflight()
        boot_test: TrainJob | None = None
        if RESUME_BOOT_DEV_RUN_NAME:
            boot_run_dir = latest_run_dir(RESUME_BOOT_DEV_RUN_NAME)
            if boot_run_dir is None:
                raise RuntimeError(f"resume boot_dev run directory not found: {RESUME_BOOT_DEV_RUN_NAME}")
            boot_skill = Path(RESUME_BOOT_SKILL_PATH) if RESUME_BOOT_SKILL_PATH else bootstrap_skill_path(RESUME_BOOT_DEV_RUN_NAME)
            if boot_skill is None or not boot_skill.exists():
                raise RuntimeError(f"resume bootstrap skill not found: {boot_skill}")
            if not run_has_required_states(RESUME_BOOT_DEV_RUN_NAME):
                done, total = progress_for(RESUME_BOOT_DEV_RUN_NAME)
                raise RuntimeError(
                    f"resume boot_dev lacks enough states: {done}/{total}, required={min_required_states(total)}"
                )
            log(f"resuming from boot_dev={RESUME_BOOT_DEV_RUN_NAME}, skill={boot_skill}")
        else:
            boot_dev = start_train(
                TrainJob(
                    key="boot_dev",
                    run_name=BOOT_DEV_RUN_NAME,
                    dataset=DEV_DATASET,
                    lane=LANE_NAMES[0],
                    bootstrap=True,
                ),
                notes=(
                    f"fresh boot-v3 bootstrap skill + validation_dev83 eval; lane={LANE_NAMES[0]}; "
                    f"{CONCURRENCY_LABEL}; executor={EXECUTOR_MODEL}."
                ),
            )
            jobs.append(boot_dev)

            wait_until(
                "bootstrap skill from boot_dev",
                lambda: bootstrap_skill_path(BOOT_DEV_RUN_NAME) is not None,
                jobs,
            )
            boot_skill = bootstrap_skill_path(BOOT_DEV_RUN_NAME)
            if boot_skill is None:
                raise RuntimeError("Bootstrap skill disappeared after readiness check.")
            log(f"bootstrap skill ready: {boot_skill}")

            boot_test = start_train(
                TrainJob(
                    key="boot_test",
                    run_name=BOOT_TEST_RUN_NAME,
                    dataset=TEST_DATASET,
                    lane=LANE_NAMES[min(2, len(LANE_NAMES) - 1)],
                    skill_path=boot_skill,
                ),
                notes=(
                    f"fresh bootstrap skill validation_test82 eval; lane={LANE_NAMES[min(2, len(LANE_NAMES) - 1)]}; {CONCURRENCY_LABEL}; "
                    f"skill={boot_skill}"
                ),
            )
            jobs.append(boot_test)

            wait_for_run_required_states(boot_dev, "boot_dev states for AB")
            if boot_dev.process is not None and boot_dev.process.poll() is None and tail_ready(boot_dev):
                stop_job(boot_dev, "stopped_tail_partial_for_ab")
            boot_run_dir = latest_run_dir(BOOT_DEV_RUN_NAME)
            if boot_run_dir is None:
                raise RuntimeError("boot_dev run directory was not created.")

        eval_jobs: list[TrainJob] = []
        b_skill: Path | None = None
        b_dev_started = False
        b_test_started = False
        a_eval_done = not SERIALIZE_EVAL_WAVES
        offline_workers = max(1, int(os.environ.get("GAIA_FRESH_OFFLINE_AB_WORKERS", "1")))
        with ThreadPoolExecutor(max_workers=offline_workers, thread_name_prefix="offline-ab") as pool:
            offline_futures = {
                pool.submit(
                    run_offline_variant,
                    strategy="full",
                    run_name=A_OFFLINE_RUN_NAME,
                    boot_run_dir=boot_run_dir,
                    boot_skill=boot_skill,
                ): "a",
                pool.submit(
                    run_offline_variant,
                    strategy="sharded",
                    run_name=B_OFFLINE_RUN_NAME,
                    boot_run_dir=boot_run_dir,
                    boot_skill=boot_skill,
                ): "b",
            }
            pending = set(offline_futures)
            while pending or (
                b_skill is not None and (not b_test_started or not b_dev_started)
            ):
                completed_now = [future for future in pending if future.done()]
                for future in completed_now:
                    pending.remove(future)
                    variant = offline_futures[future]
                    skill = future.result()
                    if variant == "a":
                        log(f"A skill ready; starting A dev/test immediately: {skill}")
                        new_jobs = start_a_eval_jobs(skill)
                        jobs.extend(new_jobs)
                        eval_jobs.extend(new_jobs)
                        if SERIALIZE_EVAL_WAVES:
                            log(
                                "serialize eval waves enabled; waiting for A dev/test "
                                "before starting B eval jobs"
                            )
                            wait_all(new_jobs)
                            a_eval_done = True
                    else:
                        b_skill = skill
                        log(f"B skill ready: {skill}")

                if b_skill is not None and not b_test_started and a_eval_done:
                    log(f"starting B test: {b_skill}")
                    b_test = start_b_test_eval(b_skill)
                    jobs.append(b_test)
                    eval_jobs.append(b_test)
                    b_test_started = True

                if b_skill is not None and b_test_started and not b_dev_started and a_eval_done:
                    if boot_test_lane_ready(boot_test):
                        log(f"B dev lane ready; starting B dev: {b_skill}")
                        b_dev = start_b_dev_eval(b_skill)
                        jobs.append(b_dev)
                        eval_jobs.append(b_dev)
                        b_dev_started = True
                    else:
                        done, total = progress_for(boot_test.run_name) if boot_test is not None else (0, 0)
                        log(
                            f"B dev waiting for boot_test lane: {done}/{total}, "
                            f"required={min_required_states(total) if total else 'unknown'}"
                        )

                if pending or (
                    b_skill is not None and (not b_test_started or not b_dev_started)
                ):
                    for job in eval_jobs:
                        ensure_job_ok(job)
                    time.sleep(POLL_SECONDS)

        wait_all(eval_jobs)
        log("fresh bootstrap AB tail queue completed")
        return 0
    except KeyboardInterrupt:
        queue_status = "stopped"
        stop_children(jobs)
        return 130
    except Exception:
        queue_status = "dead"
        stop_children(jobs)
        raise
    finally:
        cleanup_model_servers()
        update_process_row(os.getpid(), queue_status)


if __name__ == "__main__":
    raise SystemExit(main())
