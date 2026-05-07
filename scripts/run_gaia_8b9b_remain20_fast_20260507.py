from __future__ import annotations

import csv
import json
import math
import os
import re
import shlex
import signal
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

PREFIX = os.environ.get("GAIA_24EXP_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_8b9b_archv53_bootv3v4_remain20_fast"
)
QUEUE_NAME = f"{PREFIX}_master_queue"
QUEUE_LOG = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", QUEUE_LOG_ROOT / f"{QUEUE_NAME}.log"))
REPORT_DOC = Path(
    os.environ.get(
        "GAIA_24EXP_REPORT_DOC",
        f"/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.07_{PREFIX}_GAIA_8B9B_24实验结果.md",
    )
)
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"

TASK_CONCURRENCY = int(os.environ.get("GAIA_24EXP_TASK_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_24EXP_POLL_SECONDS", "20"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_24EXP_CODEX_TIMEOUT_SECONDS", "3600"))
DO_REMOTE_CLEANUP = os.environ.get("GAIA_24EXP_CLEANUP_REMOTE132", "0").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
OFFLINE_WORKERS = int(os.environ.get("GAIA_REMAIN20_OFFLINE_WORKERS", "10"))
CRITIC_SHARD_CONCURRENCY = os.environ.get("GAIA_REMAIN20_CRITIC_SHARD_CONCURRENCY", "99").strip() or "99"
ALLOW_PARTIAL_SOURCE_STATES = os.environ.get("GAIA_REMAIN20_ALLOW_PARTIAL_SOURCE_STATES", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
SKIP_FINAL_CODEX_ANALYSIS = os.environ.get("GAIA_REMAIN20_SKIP_FINAL_CODEX_ANALYSIS", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

REMOTE_HOST = "124.115.123.132"
REMOTE_PORT = "22219"
REMOTE_USER = "xsy"
REMOTE_PASSWORD = "xsy945@j8ca0"

EXISTING_9B_BOOTV3_DEV = Path(
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/"
    "20260505_004012_9b_bootv3_archv53_fresh_bootv3_boot_dev_c20"
)
EXISTING_9B_BOOTV4_DEV = Path(
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/"
    "20260505_005556_9b_bootv4_archv53_fresh_bootv4_boot_dev_c20"
)
EXISTING_8B_BOOTV3_DEV = Path(
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-7/"
    "20260507_012303_8b9b_archv53_bootv3v4_24exp_8b_bootv3_boot_dev_c20"
)
EXISTING_8B_BOOTV4_DEV = Path(
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-7/"
    "20260507_012303_8b9b_archv53_bootv3v4_24exp_8b_bootv4_boot_dev_c20"
)

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
from gaia_skillrl.search_config import apply_search_runtime_env, build_search_runtime_env, load_search_runtime_config  # noqa: E402
from gaia_skillrl.skills import (  # noqa: E402
    discover_skills,
    load_skill_detail,
    reset_experience_buffer,
    reset_skill_library,
    write_skill_bundle,
)
from gaia_skillrl.trainer import GaiaSkillTrainer  # noqa: E402
from gaia_skillrl.utils import ensure_dir, write_json  # noqa: E402


@dataclass(frozen=True)
class Lane:
    name: str
    model_key: str
    base_url: str
    served_name: str
    tokenizer_path: str
    max_model_len: int
    remote_gpu: int
    remote_port: int


@dataclass
class EvalSpec:
    key: str
    label: str
    model_key: str
    dataset: Path
    run_name: str
    counted: bool = True
    bootstrap: bool = False
    skill_path: Path | None = None
    task_ids: list[str] = field(default_factory=list)
    bootstrap_preprocess: bool = False
    preprocess_note_char_cap: int = 700
    preferred_lanes: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class RunningEval:
    spec: EvalSpec
    lane: Lane
    process: subprocess.Popen[str]
    log_path: Path
    started_at: str


@dataclass
class BootSource:
    model_key: str
    boot_key: str
    label: str
    boot_dev_run: Path
    boot_skill: Path
    supplemental_runs: list[Path] = field(default_factory=list)
    source_notes: str = ""


LANES: list[Lane] = [
    Lane(
        "remote132_gpu0_9b",
        "9b",
        "http://127.0.0.1:18120/v1",
        "Qwen3.5-9B-local",
        "/data/xsy/codes/checkpoints/Qwen3.5-9B",
        49152,
        0,
        8120,
    ),
    Lane(
        "remote132_gpu1_9b",
        "9b",
        "http://127.0.0.1:18121/v1",
        "Qwen3.5-9B-local",
        "/data/xsy/codes/checkpoints/Qwen3.5-9B",
        49152,
        1,
        8121,
    ),
    Lane("remote132_gpu2_8b", "8b", "http://127.0.0.1:18122/v1", "Qwen3-8B-local", "/data/xsy/codes/checkpoints/Qwen3-8B", 40960, 2, 8122),
    Lane("remote132_gpu3_8b", "8b", "http://127.0.0.1:18123/v1", "Qwen3-8B-local", "/data/xsy/codes/checkpoints/Qwen3-8B", 40960, 3, 8123),
    Lane("remote132_gpu4_8b", "8b", "http://127.0.0.1:18124/v1", "Qwen3-8B-local", "/data/xsy/codes/checkpoints/Qwen3-8B", 40960, 4, 8124),
    Lane("remote132_gpu5_8b", "8b", "http://127.0.0.1:18125/v1", "Qwen3-8B-local", "/data/xsy/codes/checkpoints/Qwen3-8B", 40960, 5, 8125),
    Lane("remote132_gpu6_8b", "8b", "http://127.0.0.1:18126/v1", "Qwen3-8B-local", "/data/xsy/codes/checkpoints/Qwen3-8B", 40960, 6, 8126),
    Lane("remote132_gpu7_8b", "8b", "http://127.0.0.1:18127/v1", "Qwen3-8B-local", "/data/xsy/codes/checkpoints/Qwen3-8B", 40960, 7, 8127),
]

LANE_BY_NAME = {lane.name: lane for lane in LANES}

pending_evals: list[EvalSpec] = []
running_evals: list[RunningEval] = []
completed_evals: dict[str, dict[str, Any]] = {}
eval_failures: dict[str, dict[str, Any]] = {}
all_eval_specs: list[EvalSpec] = []
dependency_specs: list[EvalSpec] = []
offline_records: list[dict[str, Any]] = []
offline_failures: list[dict[str, Any]] = []
strong_source_roots: list[Path] = []
status_lock = threading.RLock()
scheduler_stop = threading.Event()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def dataclass_from_dict(cls: type, data: dict[str, Any]):
    allowed = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in allowed})


def load_state(path: Path) -> EnvState:
    data = json.loads(path.read_text(encoding="utf-8"))
    env_data = data["env_result"]
    env_result = EnvRunResult(
        final_answer=env_data.get("final_answer", ""),
        final_choice_label=env_data.get("final_choice_label", ""),
        tool_trajectory=[dataclass_from_dict(ToolCallRecord, item) for item in env_data.get("tool_trajectory", [])],
        phase_transitions=[dataclass_from_dict(PhaseTransition, item) for item in env_data.get("phase_transitions", [])],
        action_trace=[dataclass_from_dict(ExecutorStepRecord, item) for item in env_data.get("action_trace", [])],
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


def ensure_process_csv_header() -> None:
    if PROCESS_CSV.exists() and PROCESS_CSV.stat().st_size > 0:
        return
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
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
                "exit_code",
                "notes",
                "log_path",
            ]
        )


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def refresh_process_registry() -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8:
            continue
        if row[2].strip() != "running":
            continue
        kind = row[3].strip()
        if kind == "remote_vllm":
            continue
        pid_text = row[5].strip()
        if not pid_text.isdigit():
            continue
        row[1] = now()
        if not pid_alive(int(pid_text)):
            row[2] = "dead"
            if not row[7].strip():
                row[7] = now()
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def append_process_row(
    *,
    kind: str,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
) -> None:
    ensure_process_csv_header()
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


def update_process_row(pid: int, status: str, exit_code: int | None = None) -> None:
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
        if exit_code is not None:
            while len(row) < 12:
                row.append("")
            row[11] = str(exit_code)
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def mark_process_rows_by_name(patterns: list[str], status: str) -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[2].strip() not in {"running", "running_remote"}:
            continue
        name = row[4]
        command = row[10] if len(row) > 10 else ""
        if not any(pattern in name or pattern in command for pattern in patterns):
            continue
        row[1] = now()
        row[2] = status
        if not row[7].strip():
            row[7] = now()
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
        "NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS",
        "NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_CRITIC_SHARD_CONCURRENCY",
        "CUDA_VISIBLE_DEVICES",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_BASE_URL",
        "NLRL_ACTOR_API_MODE",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_BASE_URL",
        "NLRL_CRITIC_API_MODE",
        "NLRL_WEB_SEARCH_PROVIDER",
        "NLRL_SERPER_API_KEY",
        "NLRL_SERPER_API_KEYS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    return {key: env[key] for key in keys if env.get(key)}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def common_env(lane: Lane) -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "ALL_PROXY",
        "all_proxy",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_THINKING_BUDGET",
        "NLRL_LLM_THINKING_TOKEN_BUDGET",
        "NLRL_LLM_THINKING_BUDGET",
    ]:
        env.pop(key, None)
    apply_search_runtime_env(env)
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": "",
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "BLIS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "NUMBA_NUM_THREADS": "1",
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": "24",
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(TASK_CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(TASK_CONCURRENCY),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
            "NLRL_CRITIC_SHARD_CONCURRENCY": CRITIC_SHARD_CONCURRENCY,
            "NLRL_EXECUTOR_MODEL": lane.served_name,
            "NLRL_EXECUTOR_BASE_URL": lane.base_url,
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_EXECUTOR_MAX_TOKENS": "12288",
            "NLRL_EXECUTOR_TOKENIZER_PATH": lane.tokenizer_path,
            "NLRL_EXECUTOR_MAX_MODEL_LEN": str(lane.max_model_len),
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": "512",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_ACTOR_MODEL": os.environ.get("GAIA_24EXP_ACTOR_MODEL", "gpt-5.4"),
            "NLRL_ACTOR_BASE_URL": "codex-cli",
            "NLRL_ACTOR_API_KEY": "EMPTY",
            "NLRL_ACTOR_API_MODE": "codex_cli",
            "NLRL_ACTOR_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
            "NLRL_CRITIC_MODEL": os.environ.get("GAIA_24EXP_CRITIC_MODEL", "gpt-5.4"),
            "NLRL_CRITIC_BASE_URL": "codex-cli",
            "NLRL_CRITIC_API_KEY": "EMPTY",
            "NLRL_CRITIC_API_MODE": "codex_cli",
            "NLRL_CRITIC_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
            "NLRL_TOOL_BASE_URL": os.environ.get("NLRL_TOOL_BASE_URL", "http://35.220.164.252:3888/v1"),
            "NLRL_TOOL_API_KEY": os.environ.get(
                "NLRL_TOOL_API_KEY",
                "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            ),
            "NLRL_TOOL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_MODEL": os.environ.get("NLRL_TOOL_MODEL", "gpt-4o-mini"),
            "NLRL_TOOL_AUDIO_MODEL": os.environ.get("NLRL_TOOL_AUDIO_MODEL", "gpt-4o-mini-transcribe"),
        }
    )
    return env


def endpoint_models(base_url: str) -> list[str]:
    try:
        request = Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    except Exception:
        return []


def wait_endpoints() -> None:
    deadline = time.time() + int(os.environ.get("GAIA_24EXP_ENDPOINT_WAIT_SECONDS", "180"))
    while True:
        missing = [lane for lane in LANES if lane.served_name not in endpoint_models(lane.base_url)]
        if not missing:
            log("all remote132 endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        if time.time() >= deadline:
            raise RuntimeError("remote132 endpoints not ready: " + ", ".join(f"{lane.name}:{lane.base_url}" for lane in missing))
        log("waiting endpoints: " + ", ".join(f"{lane.name}:{lane.base_url}" for lane in missing))
        time.sleep(10)


def run_name_suffix_for_match(run_name: str) -> str:
    match = re.match(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_(.+)$", run_name)
    return "_" + match.group(1) if match else run_name


def latest_run_dir(run_name: str) -> Path | None:
    suffix = run_name_suffix_for_match(run_name)
    matches = [
        path
        for pattern in (f"**/{run_name}", f"**/*{suffix}")
        for path in RUN_ROOT.glob(pattern)
        if path.is_dir()
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def latest_iteration_dir(run_dir: Path) -> Path | None:
    iterations = [path for path in run_dir.glob("iteration_*") if path.is_dir()]
    return max(iterations, key=lambda path: path.name) if iterations else None


def selected_task_ids(run_dir: Path) -> list[str]:
    path = run_dir / "selected_tasks.json"
    if not path.exists():
        return []
    try:
        return [str(item) for item in json.loads(path.read_text(encoding="utf-8")).get("task_ids", [])]
    except Exception:
        return []


def bootstrap_skill_path(run_name: str) -> Path | None:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def enqueue_eval(spec: EvalSpec) -> None:
    with status_lock:
        pending_evals.append(spec)
        if spec.counted:
            all_eval_specs.append(spec)
        else:
            dependency_specs.append(spec)
        write_manifest_locked()
    log(f"queued eval {spec.run_name} counted={spec.counted} model={spec.model_key} notes={spec.notes}")


def free_lane_for(spec: EvalSpec) -> Lane | None:
    busy = {item.lane.name for item in running_evals if item.process.poll() is None}
    candidates = [LANE_BY_NAME[name] for name in spec.preferred_lanes if name in LANE_BY_NAME]
    candidates.extend([lane for lane in LANES if lane.model_key == spec.model_key and lane.name not in {item.name for item in candidates}])
    for lane in candidates:
        if lane.model_key == spec.model_key and lane.name not in busy:
            return lane
    return None


def start_eval_locked(spec: EvalSpec, lane: Lane) -> RunningEval:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{spec.run_name}_{short_ts()}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "train-local",
        "--dataset-path",
        str(spec.dataset),
        "--run-name",
        spec.run_name,
    ]
    for task_id in spec.task_ids:
        cmd.extend(["--task-id", task_id])
    if spec.bootstrap:
        cmd.append("--bootstrap-skill")
    env = common_env(lane)
    if spec.skill_path is not None:
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(spec.skill_path)
    if spec.bootstrap_preprocess:
        env["NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS"] = "1"
        env["NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP"] = str(spec.preprocess_note_char_cap)
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
    append_process_row(
        kind="experiment" if spec.counted else "dependency_eval",
        name=spec.run_name,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes=f"{spec.label}; lane={lane.name}; endpoint={lane.base_url}; counted={spec.counted}; {spec.notes}",
    )
    started = RunningEval(spec=spec, lane=lane, process=process, log_path=log_path, started_at=now())
    running_evals.append(started)
    log(f"started {spec.run_name} pid={process.pid} lane={lane.name} endpoint={lane.base_url} log={log_path}")
    return started


def write_manifest_locked() -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "prefix": PREFIX,
        "updated_at": now(),
        "pending": [asdict(spec) | {"dataset": str(spec.dataset), "skill_path": str(spec.skill_path) if spec.skill_path else None} for spec in pending_evals],
        "running": [
            {
                "run_name": item.spec.run_name,
                "label": item.spec.label,
                "lane": asdict(item.lane),
                "pid": item.process.pid,
                "log_path": str(item.log_path),
                "started_at": item.started_at,
            }
            for item in running_evals
            if item.process.poll() is None
        ],
        "completed": completed_evals,
        "failures": eval_failures,
        "offline_records": offline_records,
        "offline_failures": offline_failures,
    }
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scheduler_loop() -> None:
    while not scheduler_stop.is_set() or pending_evals or running_evals:
        with status_lock:
            still_running: list[RunningEval] = []
            for item in running_evals:
                code = item.process.poll()
                if code is None:
                    still_running.append(item)
                    continue
                status = "finished" if code == 0 else "dead"
                update_process_row(item.process.pid, status, code)
                record = {
                    "run_name": item.spec.run_name,
                    "label": item.spec.label,
                    "counted": item.spec.counted,
                    "model_key": item.spec.model_key,
                    "lane": asdict(item.lane),
                    "returncode": code,
                    "log_path": str(item.log_path),
                    "started_at": item.started_at,
                    "ended_at": now(),
                }
                completed_evals[item.spec.run_name] = record
                if code != 0:
                    eval_failures[item.spec.run_name] = record
                log(f"eval exited {item.spec.run_name} code={code}")
            running_evals[:] = still_running

            started_any = True
            while started_any:
                started_any = False
                for spec in list(pending_evals):
                    lane = free_lane_for(spec)
                    if lane is None:
                        continue
                    pending_evals.remove(spec)
                    start_eval_locked(spec, lane)
                    started_any = True
                    break
            write_manifest_locked()
        time.sleep(POLL_SECONDS)


def wait_for_bootstrap_skill(run_name: str) -> Path:
    while True:
        skill = bootstrap_skill_path(run_name)
        if skill is not None:
            log(f"bootstrap skill ready for {run_name}: {skill}")
            run_dir = latest_run_dir(run_name)
            if run_dir is not None:
                strong_source_roots.append(run_dir / "bootstrap")
            return skill
        with status_lock:
            failed = run_name in eval_failures
            completed = run_name in completed_evals
        if failed or completed:
            raise RuntimeError(f"bootstrap skill not produced for completed run {run_name}")
        time.sleep(10)


def wait_eval_done(run_name: str) -> dict[str, Any]:
    while True:
        with status_lock:
            record = completed_evals.get(run_name)
        if record is not None:
            return record
        time.sleep(POLL_SECONDS)


def wait_counted_evals_done() -> None:
    while True:
        with status_lock:
            counted_names = {spec.run_name for spec in all_eval_specs}
            done_names = {name for name, record in completed_evals.items() if record.get("counted")}
            pending_count = len([spec for spec in pending_evals if spec.counted])
            running_count = len([item for item in running_evals if item.spec.counted and item.process.poll() is None])
        if counted_names and counted_names <= done_names and pending_count == 0 and running_count == 0:
            return
        log(f"waiting counted evals: done={len(done_names)}/{len(counted_names)} pending={pending_count} running={running_count}")
        time.sleep(max(POLL_SECONDS, 30))


def states_from_run_dirs(primary_run: Path, supplemental_runs: list[Path]) -> tuple[list[EnvState], dict[str, Any]]:
    ordered_ids = selected_task_ids(primary_run)
    state_by_id: dict[str, EnvState] = {}
    sources: dict[str, str] = {}
    for run_dir in [primary_run, *supplemental_runs]:
        iteration = latest_iteration_dir(run_dir)
        if iteration is None:
            continue
        for state_path in iteration.glob("*/state.json"):
            state = load_state(state_path)
            if state.task_id not in state_by_id:
                state_by_id[state.task_id] = state
                sources[state.task_id] = str(state_path)
    missing = [task_id for task_id in ordered_ids if task_id not in state_by_id]
    states = [state_by_id[task_id] for task_id in ordered_ids if task_id in state_by_id]
    return states, {
        "primary_run": str(primary_run),
        "supplemental_runs": [str(path) for path in supplemental_runs],
        "ordered_task_count": len(ordered_ids),
        "state_count": len(states),
        "missing_task_ids": missing,
        "source_paths": sources,
    }


def run_offline_variant(source: BootSource, *, method_key: str, strategy: str, graph_policy: str) -> Path:
    os.environ.setdefault("NLRL_ACTOR_MODEL", "gpt-5.4")
    os.environ.setdefault("NLRL_ACTOR_BASE_URL", "codex-cli")
    os.environ.setdefault("NLRL_ACTOR_API_KEY", "EMPTY")
    os.environ.setdefault("NLRL_ACTOR_API_MODE", "codex_cli")
    os.environ.setdefault("NLRL_ACTOR_TIMEOUT_SECONDS", str(CODEX_TIMEOUT_SECONDS))
    os.environ.setdefault("NLRL_CRITIC_MODEL", "gpt-5.4")
    os.environ.setdefault("NLRL_CRITIC_BASE_URL", "codex-cli")
    os.environ.setdefault("NLRL_CRITIC_API_KEY", "EMPTY")
    os.environ.setdefault("NLRL_CRITIC_API_MODE", "codex_cli")
    os.environ.setdefault("NLRL_CRITIC_TIMEOUT_SECONDS", str(CODEX_TIMEOUT_SECONDS))
    os.environ.setdefault("NLRL_CODEX_CLI_TIMEOUT_SECONDS", str(CODEX_TIMEOUT_SECONDS))
    os.environ["NLRL_CRITIC_SHARD_CONCURRENCY"] = CRITIC_SHARD_CONCURRENCY

    run_name = f"{PREFIX}_{source.model_key}_{source.boot_key}_{method_key}_offline_actor_iter1"
    log(f"offline start {run_name}: strategy={strategy} graph_policy={graph_policy} source={source.boot_dev_run}")
    started_monotonic = time.monotonic()
    started_at = now()
    config = clone_system_config(
        load_system_config(CONFIG),
        runtime={
            "critic_strategy": strategy,
            "critic_shard_size": 12,
            "actor_graph_edit_policy": graph_policy,
        },
    )
    trainer = GaiaSkillTrainer(config)
    run_dir = trainer.prepare_run_dir(run_name, mode="gaia-offline-critic-actor")
    state_config = trainer._batch_state_config(run_dir)
    reset_experience_buffer(state_config.experience_buffer_path)
    reset_skill_library(state_config.skill_library_root)
    write_skill_bundle(
        state_config.skill_library_root,
        "gaia-general-skill",
        {"SKILL.md": source.boot_skill.read_text(encoding="utf-8")},
    )
    headers = discover_skills(state_config.skill_library_root)
    if not headers:
        raise RuntimeError(f"No skill loaded for offline actor run {run_name}.")
    active_skill = load_skill_detail(headers[0])
    iteration_dir = ensure_dir(run_dir / "offline_iter1")
    states, source_state_info = states_from_run_dirs(source.boot_dev_run, source.supplemental_runs)
    if source_state_info["missing_task_ids"] and not ALLOW_PARTIAL_SOURCE_STATES:
        raise RuntimeError(f"{run_name} source states missing: {source_state_info['missing_task_ids']}")
    if source_state_info["missing_task_ids"]:
        log(
            f"offline partial source accepted for {run_name}: "
            f"{source_state_info['state_count']}/{source_state_info['ordered_task_count']} states; "
            f"missing={source_state_info['missing_task_ids']}"
        )
    critic = SkillCritic(state_config)
    actor = SkillActor(state_config)
    history_poll: list[dict[str, Any]] = [
        {
            "iteration_index": 0,
            "summary": (
                f"{source.label} states are reused as off-policy diagnosis input; "
                f"strategy={strategy}; graph_policy={graph_policy}; source={source.boot_dev_run}; "
                f"supplemental={source.supplemental_runs}"
            ),
        }
    ]
    reward = critic.evaluate_batch(states, active_skill, history_poll, iteration_dir)
    decision = actor.act(reward, active_skill, history_poll, iteration_dir)
    actor.apply(decision)
    updated_skill = load_skill_detail(discover_skills(state_config.skill_library_root)[0])
    skill_after = iteration_dir / "skill_after_actor/gaia-general-skill"
    if skill_after.exists():
        import shutil

        shutil.rmtree(skill_after)
    import shutil

    shutil.copytree(Path(updated_skill.header.skill_dir), skill_after)
    elapsed = time.monotonic() - started_monotonic
    summary = {
        "run_name": run_dir.name,
        "method_key": method_key,
        "strategy": strategy,
        "graph_policy": graph_policy,
        "source": asdict(source) | {"boot_dev_run": str(source.boot_dev_run), "boot_skill": str(source.boot_skill), "supplemental_runs": [str(path) for path in source.supplemental_runs]},
        "source_state_info": source_state_info,
        "input_skill": str(source.boot_skill),
        "reward": to_dict(reward),
        "actor_decision": to_dict(decision),
        "skill_after_actor": str(skill_after / "SKILL.md"),
        "started_at": started_at,
        "ended_at": now(),
        "elapsed_seconds": elapsed,
    }
    write_json(run_dir / "offline_actor_summary.json", summary)
    record = {
        "run_name": run_dir.name,
        "run_dir": str(run_dir),
        "method_key": method_key,
        "model_key": source.model_key,
        "boot_key": source.boot_key,
        "strategy": strategy,
        "graph_policy": graph_policy,
        "skill_path": str(skill_after / "SKILL.md"),
        "source_state_count": source_state_info["state_count"],
        "source_ordered_task_count": source_state_info["ordered_task_count"],
        "started_at": started_at,
        "ended_at": now(),
        "elapsed_seconds": elapsed,
    }
    offline_records.append(record)
    strong_source_roots.append(iteration_dir)
    log(f"offline done {run_name}: skill={skill_after / 'SKILL.md'} elapsed={elapsed:.1f}s")
    return skill_after / "SKILL.md"


def eval_run_stats(spec: EvalSpec) -> dict[str, Any]:
    run_dir = latest_run_dir(spec.run_name)
    if run_dir is None:
        return {
            "run_name": spec.run_name,
            "label": spec.label,
            "counted": spec.counted,
            "status": "missing_run_dir",
            "run_dir": "",
        }
    selected_ids = selected_task_ids(run_dir)
    iteration = latest_iteration_dir(run_dir)
    states: list[dict[str, Any]] = []
    if iteration is not None:
        for state_path in sorted(iteration.glob("*/state.json")):
            try:
                states.append(json.loads(state_path.read_text(encoding="utf-8")))
            except Exception:
                pass
    success = 0
    nonempty_answer = 0
    step_counts: list[int] = []
    tool_calls = 0
    tool_failures = 0
    web_calls = 0
    web_failures = 0
    max_step_like = 0
    for data in states:
        env = data.get("env_result", {})
        evaluation = env.get("evaluation", {})
        success += int(bool(evaluation.get("task_success")))
        nonempty_answer += int(bool(str(env.get("final_answer", "")).strip()))
        action_trace = env.get("action_trace", [])
        step_counts.append(len(action_trace))
        if len(action_trace) >= 24:
            max_step_like += 1
        for item in env.get("tool_trajectory", []):
            tool_calls += 1
            name = str(item.get("tool_name") or item.get("name") or item.get("tool") or "")
            ok = bool(item.get("success", True))
            if not ok:
                tool_failures += 1
            if name == "web_search":
                web_calls += 1
                if not ok:
                    web_failures += 1
    log_text = ""
    run_log = run_dir / "run.log"
    if run_log.exists():
        try:
            log_text = run_log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            log_text = ""
    config_snapshot = {}
    config_path = run_dir / "config_snapshot.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            base_cfg = cfg.get("base_config", cfg)
            config_snapshot = {
                "executor": base_cfg.get("executor", {}),
                "runtime": base_cfg.get("runtime", {}),
                "actor": base_cfg.get("actor", {}),
                "critic": base_cfg.get("critic", {}),
            }
        except Exception:
            config_snapshot = {}
    total = len(selected_ids) or len(states)
    return {
        "run_name": spec.run_name,
        "label": spec.label,
        "counted": spec.counted,
        "model_key": spec.model_key,
        "dataset": str(spec.dataset),
        "task_ids": spec.task_ids,
        "run_dir": str(run_dir),
        "total": total,
        "landed": len(states),
        "missing": max(0, total - len(states)),
        "success": success,
        "score": f"{success}/{total or '?'}",
        "success_rate": (success / total) if total else None,
        "nonempty_answer": nonempty_answer,
        "avg_steps": statistics.mean(step_counts) if step_counts else None,
        "max_steps": max(step_counts) if step_counts else None,
        "max_step_like_count": max_step_like,
        "tool_calls": tool_calls,
        "tool_failures": tool_failures,
        "web_search_calls": web_calls,
        "web_search_failures": web_failures,
        "log_counts": {
            "http_400": log_text.count("HTTP 400"),
            "empty_content_retry": log_text.count("Streaming response produced empty content"),
            "fail6": log_text.lower().count("fail6"),
            "read_timeout": log_text.count("ReadTimeout"),
        },
        "config_snapshot": config_snapshot,
    }


def estimate_tokens_from_chars(chars: int) -> int:
    return int(math.ceil(chars / 4))


def strong_call_records() -> list[dict[str, Any]]:
    request_files: list[Path] = []
    for root in strong_source_roots:
        if root.exists():
            if root == QUEUE_LOG_ROOT:
                final_request = QUEUE_LOG_ROOT / f"{PREFIX}_final_codex_analysis_request.json"
                if final_request.exists():
                    request_files.append(final_request)
            else:
                request_files.extend(sorted(root.rglob("*_request.json")))
    seen: set[Path] = set()
    records: list[dict[str, Any]] = []
    for request_path in request_files:
        if request_path in seen:
            continue
        seen.add(request_path)
        response_path = Path(str(request_path).replace("_request.json", "_response.json"))
        try:
            request_payload = json.loads(request_path.read_text(encoding="utf-8"))
        except Exception:
            request_payload = {}
        try:
            response_payload = json.loads(response_path.read_text(encoding="utf-8")) if response_path.exists() else {}
        except Exception:
            response_payload = {}
        prompt = str(request_payload.get("prompt", ""))
        text = str(response_payload.get("text", ""))
        raw_response = response_payload.get("raw_response", {}) if isinstance(response_payload.get("raw_response"), dict) else {}
        records.append(
            {
                "request_path": str(request_path),
                "response_path": str(response_path) if response_path.exists() else "",
                "api_mode": request_payload.get("api_mode"),
                "model": request_payload.get("model"),
                "prompt_chars": len(prompt),
                "response_chars": len(text),
                "estimated_prompt_tokens": estimate_tokens_from_chars(len(prompt)),
                "estimated_response_tokens": estimate_tokens_from_chars(len(text)),
                "estimated_total_tokens": estimate_tokens_from_chars(len(prompt) + len(text)),
                "elapsed_seconds": raw_response.get("elapsed_seconds"),
            }
        )
    return records


def summarize_strong_usage() -> dict[str, Any]:
    records = strong_call_records()
    return {
        "estimation_note": "Codex CLI response logs do not expose official usage; token counts are estimated as ceil(chars/4).",
        "call_count": len(records),
        "estimated_prompt_tokens": sum(item["estimated_prompt_tokens"] for item in records),
        "estimated_response_tokens": sum(item["estimated_response_tokens"] for item in records),
        "estimated_total_tokens": sum(item["estimated_total_tokens"] for item in records),
        "elapsed_seconds_sum": sum(float(item["elapsed_seconds"] or 0) for item in records),
        "records": records,
    }


def write_summary(status: str, *, start_time: str, end_time: str | None = None, analysis_text: str = "") -> dict[str, Any]:
    eval_stats = [eval_run_stats(spec) for spec in all_eval_specs]
    dependency_stats = [eval_run_stats(spec) for spec in dependency_specs]
    strong_usage = summarize_strong_usage()
    payload = {
        "prefix": PREFIX,
        "queue_name": QUEUE_NAME,
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": None,
        "report_doc": str(REPORT_DOC),
        "summary_json": str(SUMMARY_JSON),
        "manifest_json": str(MANIFEST_JSON),
        "lanes": [asdict(lane) for lane in LANES],
        "matrix": {
            "counted_eval_count": len(all_eval_specs),
            "expected_counted_eval_count": 20,
            "dependency_eval_count": len(dependency_specs),
            "8b_counted": len([spec for spec in all_eval_specs if spec.model_key == "8b"]),
            "9b_counted": len([spec for spec in all_eval_specs if spec.model_key == "9b"]),
        },
        "config_card": {
            "runtime_max_context_chars": 0,
            "executor_max_tokens": 12288,
            "executor_token_guard_safety_margin": 512,
            "executor_enable_thinking": 1,
            "thinking_token_budget": None,
            "runtime_max_executor_steps": 24,
            "task_concurrency_per_lane": TASK_CONCURRENCY,
            "llm_max_concurrent_requests_per_lane": TASK_CONCURRENCY,
            "answer_acceptance_policy": "any_phase",
            "tool_profile": "atomic_v2",
            "critic_shard_concurrency": CRITIC_SHARD_CONCURRENCY,
            "strong_model_serial": False,
            "offline_workers": OFFLINE_WORKERS,
            "allow_partial_source_states": ALLOW_PARTIAL_SOURCE_STATES,
        },
        "search_runtime": build_search_runtime_env(load_search_runtime_config()),
        "eval_stats": eval_stats,
        "dependency_stats": dependency_stats,
        "offline_records": offline_records,
        "offline_failures": offline_failures,
        "strong_usage": strong_usage,
        "eval_failures": eval_failures,
        "codex_analysis_text": analysis_text,
        "updated_at": now(),
    }
    if end_time:
        try:
            start_dt = datetime.fromisoformat(start_time)
            end_dt = datetime.fromisoformat(end_time)
            payload["duration_seconds"] = (end_dt - start_dt).total_seconds()
        except Exception:
            payload["duration_seconds"] = None
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def markdown_table(rows: list[list[Any]], headers: list[str]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return lines


def append_report(summary: dict[str, Any]) -> None:
    eval_rows = []
    for item in summary["eval_stats"]:
        eval_rows.append(
            [
                item["model_key"],
                item["label"],
                item["score"],
                item["landed"],
                item["missing"],
                item.get("avg_steps"),
                item["web_search_calls"],
                item["web_search_failures"],
                item["run_dir"],
            ]
        )
    offline_rows = [
        [
            item["model_key"],
            item["boot_key"],
            item["method_key"],
            item["source_state_count"],
            f"{float(item['elapsed_seconds']):.1f}",
            item["skill_path"],
        ]
        for item in summary["offline_records"]
    ]
    strong = summary["strong_usage"]
    lines = [
        "",
        "---",
        "",
        "# remain20-fast final report",
        "",
        f"- prefix: `{PREFIX}`",
        f"- status: `{summary['status']}`",
        f"- start: `{summary['start_time']}`",
        f"- end: `{summary['end_time']}`",
        f"- duration_seconds: `{summary['duration_seconds']}`",
        f"- counted evals: `{summary['matrix']['counted_eval_count']}/20`",
        f"- dependency evals: `{summary['matrix']['dependency_eval_count']}`",
        f"- summary json: `{SUMMARY_JSON}`",
        "",
        "## Score table",
        "",
        *markdown_table(
            eval_rows,
            ["model", "label", "score", "landed", "missing", "avg_steps", "web_calls", "web_fail", "run_dir"],
        ),
        "",
        "## Offline skill generation",
        "",
        *markdown_table(
            offline_rows,
            ["model", "boot", "method", "source_states", "elapsed_s", "skill"],
        ),
        "",
        f"- offline failures: `{len(summary.get('offline_failures', []))}`",
        "",
        "## Strong model usage",
        "",
        f"- call_count: `{strong['call_count']}`",
        f"- estimated_prompt_tokens: `{strong['estimated_prompt_tokens']}`",
        f"- estimated_response_tokens: `{strong['estimated_response_tokens']}`",
        f"- estimated_total_tokens: `{strong['estimated_total_tokens']}`",
        f"- elapsed_seconds_sum: `{strong['elapsed_seconds_sum']}`",
        f"- note: {strong['estimation_note']}",
        "",
        "## Codex analysis",
        "",
        summary.get("codex_analysis_text") or "(analysis not available)",
        "",
    ]
    REPORT_DOC.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_DOC.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run_final_codex_analysis(summary: dict[str, Any]) -> str:
    prompt = f"""You are reviewing a GAIA SkillRL remain20-fast experiment matrix.

Read the machine summary JSON at:
{SUMMARY_JSON}

Write a concise Chinese analysis for the experiment log document. Cover:
- best/worst scores and whether B1/B2/B3 helped;
- 8B vs 9B differences;
- BOOT-V3 vs BOOT-V4 differences;
- partial-source anomalies;
- strong-model estimated tokens and elapsed time;
- next experimental priority.

Use concrete run names and scores. Do not modify files.
"""
    request_path = QUEUE_LOG_ROOT / f"{PREFIX}_final_codex_analysis_request.json"
    response_path = QUEUE_LOG_ROOT / f"{PREFIX}_final_codex_analysis_response.json"
    output_path = QUEUE_LOG_ROOT / f"{PREFIX}_final_codex_last_message.md"
    cmd = [
        "codex",
        "exec",
        "-m",
        os.environ.get("GAIA_24EXP_FINAL_CODEX_MODEL", "gpt-5.4"),
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-rules",
        "-c",
        'approval_policy="never"',
        "--output-last-message",
        str(output_path),
        "-C",
        str(ROOT),
        "-",
    ]
    request_path.write_text(
        json.dumps(
            {
                "api_mode": "codex_cli",
                "model": os.environ.get("GAIA_24EXP_FINAL_CODEX_MODEL", "gpt-5.4"),
                "prompt": prompt,
                "command": cmd,
                "summary_json": str(SUMMARY_JSON),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(ROOT),
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CODEX_TIMEOUT_SECONDS,
            check=False,
        )
        elapsed = time.monotonic() - started
        text = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else completed.stdout.strip()
        response_path.write_text(
            json.dumps(
                {
                    "text": text,
                    "raw_response": {
                        "provider": "codex_cli",
                        "command": cmd,
                        "returncode": completed.returncode,
                        "stdout_tail": completed.stdout[-4000:],
                        "stderr_tail": completed.stderr[-4000:],
                        "elapsed_seconds": elapsed,
                        "output_last_message": str(output_path),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        strong_source_roots.append(QUEUE_LOG_ROOT)
        if completed.returncode != 0:
            return f"Final Codex analysis failed with returncode={completed.returncode}. stderr_tail={completed.stderr[-1000:]}"
        return text or "(final Codex analysis returned empty text)"
    except Exception as exc:
        response_path.write_text(
            json.dumps({"text": "", "raw_response": {"provider": "codex_cli", "error": repr(exc)}}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        strong_source_roots.append(QUEUE_LOG_ROOT)
        return f"Final Codex analysis failed: {exc!r}"


def cleanup_remote132() -> None:
    if not DO_REMOTE_CLEANUP:
        log("remote cleanup skipped by GAIA_24EXP_CLEANUP_REMOTE132=0")
        return
    remote_cmd = (
        "for port in 8120 8121 8122 8123 8124 8125 8126 8127; do "
        "pids=$(ps -eo pid,args | awk -v p=\"$port\" "
        "'$0 ~ /vllm.entrypoints.openai.api_server/ && $0 ~ \"--port \" p {print $1}'); "
        "for pid in $pids; do kill $pid 2>/dev/null || true; done; "
        "done"
    )
    try:
        import pexpect

        child = pexpect.spawn(
            "ssh",
            [
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "UserKnownHostsFile=/data/xsy/.ssh/known_hosts",
                "-p",
                REMOTE_PORT,
                f"{REMOTE_USER}@{REMOTE_HOST}",
                remote_cmd,
            ],
            timeout=60,
            encoding="utf-8",
        )
        while True:
            index = child.expect(["password:", "yes/no", pexpect.EOF, pexpect.TIMEOUT])
            if index == 0:
                child.sendline(REMOTE_PASSWORD)
            elif index == 1:
                child.sendline("yes")
            elif index == 2:
                break
            else:
                raise TimeoutError("ssh cleanup timed out")
        log("remote132 vLLM cleanup command completed")
    except Exception as exc:
        log(f"remote132 vLLM cleanup failed: {exc!r}")
    subprocess.run(["pkill", "-f", "ssh_tunnel_pexpect.py.*1812"], check=False)
    subprocess.run(["pkill", "-f", "ssh -N .*1812"], check=False)
    mark_process_rows_by_name(["remote132_qwen3", "ssh_tunnel_1812"], "stopped_after_24exp_cleanup")


def source_missing_task_ids(run_dir: Path) -> list[str]:
    selected = selected_task_ids(run_dir)
    iteration = latest_iteration_dir(run_dir)
    landed = {path.parent.name for path in iteration.glob("*/state.json")} if iteration else set()
    return [task_id for task_id in selected if task_id not in landed]


def main() -> int:
    start_time = now()
    status = "finished"
    ensure_process_csv_header()
    refresh_process_registry()
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    for path in (
        PYTHON,
        CONFIG,
        DEV_DATASET,
        TEST_DATASET,
        EXISTING_9B_BOOTV3_DEV,
        EXISTING_9B_BOOTV4_DEV,
        EXISTING_8B_BOOTV3_DEV,
        EXISTING_8B_BOOTV4_DEV,
    ):
        if not path.exists():
            raise RuntimeError(f"missing required path: {path}")
    wait_endpoints()
    append_process_row(
        kind="experiment_queue",
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(GAIA 8B/9B 24 experiment master queue)",
        command=command_display(os.environ, [str(PYTHON), "-u", __file__]),
        log_path=QUEUE_LOG,
        notes=(
            f"remain20 fast matrix; strong Codex calls concurrent; "
            f"offline_workers={OFFLINE_WORKERS}; critic_shard_concurrency={CRITIC_SHARD_CONCURRENCY}; "
            f"partial_sources={ALLOW_PARTIAL_SOURCE_STATES}; report={REPORT_DOC}; summary={SUMMARY_JSON}"
        ),
    )
    scheduler = threading.Thread(target=scheduler_loop, name="gaia-24exp-eval-scheduler", daemon=True)
    scheduler.start()
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))

        skill_paths = {
            "9b_bootv3": EXISTING_9B_BOOTV3_DEV / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md",
            "9b_bootv4": EXISTING_9B_BOOTV4_DEV / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md",
            "8b_bootv3": EXISTING_8B_BOOTV3_DEV / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md",
            "8b_bootv4": EXISTING_8B_BOOTV4_DEV / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md",
        }
        missing_skills = [str(path) for path in skill_paths.values() if not path.exists()]
        if missing_skills:
            raise RuntimeError("existing boot skill missing: " + ", ".join(missing_skills))

        sources: list[BootSource] = [
            BootSource("9b", "bootv3", "9B BOOT-V3 ARCH5.3", EXISTING_9B_BOOTV3_DEV, skill_paths["9b_bootv3"]),
            BootSource(
                "9b",
                "bootv4",
                "9B BOOT-V4 ARCH5.3 preprocess",
                EXISTING_9B_BOOTV4_DEV,
                skill_paths["9b_bootv4"],
                source_notes="partial source accepted; no dependency fill",
            ),
            BootSource(
                "8b",
                "bootv3",
                "8B BOOT-V3 ARCH5.3",
                EXISTING_8B_BOOTV3_DEV,
                skill_paths["8b_bootv3"],
                source_notes="partial source accepted; no dependency fill",
            ),
            BootSource("8b", "bootv4", "8B BOOT-V4 ARCH5.3 preprocess", EXISTING_8B_BOOTV4_DEV, skill_paths["8b_bootv4"]),
        ]
        method_specs = {
            "9b": [
                ("B1", "sharded", "locked"),
                ("B2", "graph_b2", "graph_unlocked"),
                ("B3", "graph_v3", "graph_v3"),
            ],
            "8b": [
                ("B1", "sharded", "locked"),
                ("B2", "graph_b2", "graph_unlocked"),
            ],
        }
        preferred_lanes = {
            "9b": ["remote132_gpu0_9b", "remote132_gpu1_9b"],
            "8b": [
                "remote132_gpu2_8b",
                "remote132_gpu3_8b",
                "remote132_gpu4_8b",
                "remote132_gpu5_8b",
                "remote132_gpu6_8b",
                "remote132_gpu7_8b",
            ],
        }

        def enqueue_skill_evals(source: BootSource, method_key: str, skill: Path) -> None:
            for split, dataset in [("dev", DEV_DATASET), ("test", TEST_DATASET)]:
                enqueue_eval(
                    EvalSpec(
                        key=f"{source.model_key}_{source.boot_key}_{method_key}_{split}",
                        label=f"{source.model_key.upper()} {source.boot_key.upper()} {method_key} {split}",
                        model_key=source.model_key,
                        dataset=dataset,
                        run_name=f"{PREFIX}_{source.model_key}_{source.boot_key}_{method_key}_{split}_eval_c{TASK_CONCURRENCY}",
                        skill_path=skill,
                        preferred_lanes=preferred_lanes[source.model_key],
                        notes=f"skill from {skill}; source_notes={source.source_notes}",
                    )
                )

        futures: dict[Future[Path], tuple[BootSource, str, str, str]] = {}
        log(
            f"starting remain20 offline generation with workers={OFFLINE_WORKERS}, "
            f"critic_shard_concurrency={CRITIC_SHARD_CONCURRENCY}, partial_sources={ALLOW_PARTIAL_SOURCE_STATES}"
        )
        with ThreadPoolExecutor(max_workers=max(1, OFFLINE_WORKERS), thread_name_prefix="remain20-offline") as pool:
            for source in sources:
                for method_key, strategy, graph_policy in method_specs[source.model_key]:
                    future = pool.submit(run_offline_variant, source, method_key=method_key, strategy=strategy, graph_policy=graph_policy)
                    futures[future] = (source, method_key, strategy, graph_policy)

            pending = set(futures)
            while pending:
                completed_now = [future for future in pending if future.done()]
                if not completed_now:
                    log(f"waiting offline skills: done={len(futures) - len(pending)}/{len(futures)}")
                    time.sleep(max(POLL_SECONDS, 30))
                    continue
                for future in completed_now:
                    pending.remove(future)
                    source, method_key, strategy, graph_policy = futures[future]
                    try:
                        skill = future.result()
                    except Exception as exc:
                        failure = {
                            "model_key": source.model_key,
                            "boot_key": source.boot_key,
                            "method_key": method_key,
                            "strategy": strategy,
                            "graph_policy": graph_policy,
                            "source": str(source.boot_dev_run),
                            "error": repr(exc),
                            "ended_at": now(),
                        }
                        offline_failures.append(failure)
                        log(f"offline failed {source.model_key} {source.boot_key} {method_key}: {exc!r}")
                        continue
                    log(f"skill ready; enqueue evals immediately: {source.model_key} {source.boot_key} {method_key} skill={skill}")
                    enqueue_skill_evals(source, method_key, skill)

        wait_counted_evals_done()
        end_time = now()
        status_label = "finished_with_offline_failures" if offline_failures else "finished"
        if SKIP_FINAL_CODEX_ANALYSIS:
            analysis = "(final Codex analysis skipped for remain20 fast launch)"
        else:
            summary_before_analysis = write_summary("evals_finished_before_final_analysis", start_time=start_time, end_time=end_time)
            analysis = run_final_codex_analysis(summary_before_analysis)
            end_time = now()
        summary = write_summary(status_label, start_time=start_time, end_time=end_time, analysis_text=analysis)
        append_report(summary)
        log(f"final report appended: {REPORT_DOC}")
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception:
        status = "dead"
        summary = write_summary("dead", start_time=start_time, end_time=now())
        append_report(summary)
        raise
    finally:
        scheduler_stop.set()
        scheduler.join(timeout=60)
        if status == "finished":
            cleanup_remote132()
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
