from __future__ import annotations

import json
import math
import os
import re
import signal
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"
CONFIGS = {
    "bootv7": ROOT / "configs/system_bootv7_pseudocomplete.json",
    "bootv8": ROOT / "configs/system_bootv8_pseudocomplete.json",
    "bootv3": ROOT / "configs/system_bootv3_sssai52_high.json",
    "bootv6": ROOT / "configs/system_bootv6_long_rules_sssai52_high.json",
}
DEFAULT_CONFIG = CONFIGS["bootv7"]
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
DESIGN_ROOT = ROOT / "实验设计与迭代"

PREFIX = os.environ.get(
    "GAIA_8B_BOOTV7V8_V3V6_MATRIX_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv7v8_v3v6_matrix_remote132"),
).strip()
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(
    os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_MASTER_LOG", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log")
)
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"
REPORT_DOC = Path(
    os.environ.get(
        "GAIA_8B_BOOTV7V8_V3V6_MATRIX_REPORT_DOC",
        DESIGN_ROOT / f"26.5.14_{PREFIX}_GAIA_8B_V7V8_V3V6矩阵执行记录.md",
    )
)

CONCURRENCY = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_POLL_SECONDS", "120"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_TIMEOUT_SECONDS", "3600"))
BOOT_WORKERS = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_BOOT_WORKERS", "6"))
BOOTSTRAP_WORKERS = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_BOOTSTRAP_WORKERS", "2"))
OFFLINE_WORKERS = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_OFFLINE_WORKERS", "2"))
CRITIC_SHARD_CONCURRENCY = os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CRITIC_SHARD_CONCURRENCY", "4").strip() or "4"
PARTIAL_SOURCE_MIN_STATES = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_PARTIAL_SOURCE_MIN_STATES", "80"))
PARTIAL_SOURCE_STALL_SECONDS = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_PARTIAL_SOURCE_STALL_SECONDS", "900"))
LONGTAIL_FRACTION = float(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_LONGTAIL_FRACTION", "0.95"))
LONGTAIL_MAX_MISSING = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_LONGTAIL_MAX_MISSING", "2"))
LONGTAIL_STALLED_SECONDS = int(os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_LONGTAIL_STALLED_SECONDS", "900"))

STRONG_MODEL = os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_MODEL", "gpt-5.2").strip()
STRONG_REASONING_EFFORT = os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_REASONING_EFFORT", "high").strip()
EXECUTOR_LABEL = os.environ.get("GAIA_BOOT_MATRIX_EXECUTOR_LABEL", "8B").strip() or "8B"
RUN_MODEL_TOKEN = os.environ.get("GAIA_BOOT_MATRIX_RUN_MODEL_TOKEN", EXECUTOR_LABEL.lower()).strip() or "8b"
EXECUTOR_MODEL_PATH = os.environ.get("GAIA_BOOT_MATRIX_MODEL_PATH", "/data/xsy/codes/checkpoints/Qwen3-8B").strip()
EXECUTOR_SERVED_NAME = os.environ.get("GAIA_BOOT_MATRIX_SERVED_NAME", "Qwen3-8B-local").strip()
EXECUTOR_TOKENIZER_PATH = os.environ.get("GAIA_BOOT_MATRIX_TOKENIZER_PATH", "/data/xsy/codes/checkpoints/Qwen3-8B").strip()
EXECUTOR_MAX_MODEL_LEN = int(os.environ.get("GAIA_BOOT_MATRIX_MAX_MODEL_LEN", "40960"))


def _parse_csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _parse_stages_env(name: str, default: str) -> list[list[str]]:
    stages: list[list[str]] = []
    for chunk in os.environ.get(name, default).split(";"):
        keys = [item.strip() for item in chunk.split(",") if item.strip()]
        if keys:
            stages.append(keys)
    return stages


BOOT_KEYS = _parse_csv_env("GAIA_8B_BOOTV7V8_V3V6_MATRIX_BOOT_KEYS", "bootv7,bootv8,bootv3,bootv6")
BOOT_STAGES = _parse_stages_env(
    "GAIA_8B_BOOTV7V8_V3V6_MATRIX_BOOT_STAGES",
    "bootv7,bootv8;bootv3,bootv6",
)
BOOTSTRAP_PREPROCESS_KEYS = {"bootv8", "bootv6"}
PSEUDOCOMPLETE_BOOT_KEYS = {"bootv7", "bootv8"}
METHOD_ORDER = _parse_csv_env("GAIA_8B_BOOTV7V8_V3V6_MATRIX_METHOD_ORDER", "A_full,B1")
DOWNSTREAM_BOOT_KEYS = [
    key.strip()
    for key in os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_DOWNSTREAM_BOOT_KEYS", ",".join(BOOT_KEYS)).split(",")
    if key.strip()
]
EXPECTED_COUNTED_EVALS = len(BOOT_KEYS) * 3 * 2 + len(DOWNSTREAM_BOOT_KEYS) * 3 * len(METHOD_ORDER) * 2


def _parse_gpu_ids() -> list[int]:
    raw = os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_GPU_IDS", "0,1,2,3,4,5").strip()
    gpu_ids: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        gpu_ids.append(int(item))
    if not gpu_ids:
        raise RuntimeError("GAIA_8B_BOOTV7V8_V3V6_MATRIX_GPU_IDS resolved to an empty GPU list.")
    return gpu_ids


GPU_IDS = _parse_gpu_ids()


def _strong_provider() -> dict[str, str]:
    data = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    actor = data["actor"]
    return {
        "base_url": os.environ.get("GAIA_GPT52_SSSAI_BASE_URL", actor["base_url"]).strip(),
        "api_key": os.environ.get("GAIA_GPT52_SSSAI_API_KEY", actor["api_key"]).strip(),
    }


STRONG_PROVIDER = _strong_provider()

os.environ["GAIA_24EXP_PREFIX"] = PREFIX
os.environ["GAIA_24EXP_TASK_CONCURRENCY"] = str(CONCURRENCY)
os.environ["GAIA_24EXP_CODEX_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
os.environ["GAIA_REMAIN20_ALLOW_PARTIAL_SOURCE_STATES"] = "1"
os.environ["GAIA_REMAIN20_CRITIC_SHARD_CONCURRENCY"] = CRITIC_SHARD_CONCURRENCY
for _role in ("ACTOR", "CRITIC"):
    os.environ[f"NLRL_{_role}_MODEL"] = STRONG_MODEL
    os.environ[f"NLRL_{_role}_BASE_URL"] = STRONG_PROVIDER["base_url"]
    os.environ[f"NLRL_{_role}_API_KEY"] = STRONG_PROVIDER["api_key"]
    os.environ[f"NLRL_{_role}_API_MODE"] = "responses_sse"
    os.environ[f"NLRL_{_role}_ENABLE_THINKING"] = "1"
    os.environ[f"NLRL_{_role}_REASONING_EFFORT"] = STRONG_REASONING_EFFORT
    os.environ[f"NLRL_{_role}_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_baseline_254_gpu01_20260507 as base  # noqa: E402
import run_gaia_8b9b_remain20_fast_20260507 as remain20  # noqa: E402


LANES = [
    base.Lane(
        f"132_gpu{gpu}_{RUN_MODEL_TOKEN}",
        gpu,
        8128 + gpu,
        model_path=EXECUTOR_MODEL_PATH,
        served_name=EXECUTOR_SERVED_NAME,
        tokenizer_path=EXECUTOR_TOKENIZER_PATH,
        max_model_len=EXECUTOR_MAX_MODEL_LEN,
    )
    for gpu in GPU_IDS
]

METHOD_SPECS: dict[str, tuple[str, str]] = {
    "A_full": ("full", "locked"),
    "B1": ("sharded", "locked"),
}


@dataclass
class EvalSpec:
    key: str
    label: str
    command: str
    config_path: Path
    dataset: Path
    run_name: str
    counted: bool = True
    boot_key: str = ""
    method_key: str = ""
    repeat: int = 0
    split: str = ""
    bootstrap: bool = False
    bootstrap_preprocess: bool = False
    skill_path: Path | None = None
    preferred_lanes: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class RunningEval:
    spec: EvalSpec
    lane: base.Lane
    process: subprocess.Popen[str]
    log_path: Path
    started_at: str


pending_evals: list[EvalSpec] = []
running_evals: list[RunningEval] = []
completed_evals: dict[str, dict[str, Any]] = {}
released_evals: dict[str, dict[str, Any]] = {}
eval_failures: dict[str, dict[str, Any]] = {}
all_eval_specs: list[EvalSpec] = []
offline_records: list[dict[str, Any]] = []
offline_failures: list[dict[str, Any]] = []
pipeline_records: dict[str, dict[str, Any]] = {}
progress_memory: dict[str, dict[str, Any]] = {}
status_lock = threading.RLock()
offline_config_lock = threading.Lock()
bootstrap_semaphore = threading.Semaphore(max(1, BOOTSTRAP_WORKERS))
scheduler_stop = threading.Event()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def configure_modules() -> None:
    base.ROOT = ROOT
    base.PYTHON = PYTHON
    base.CONFIG = DEFAULT_CONFIG
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
    base.CODEX_TIMEOUT_SECONDS = CODEX_TIMEOUT_SECONDS
    base.LANES = LANES
    base.CONFLICTING_VLLM_PORTS = [lane.port for lane in LANES]
    base.STOP_CONFLICTING_SERVICES = False
    base.VLLM_GPU_MEMORY_UTILIZATION = os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_VLLM_GPU_MEMORY_UTILIZATION", "0.90")
    base.VLLM_MAX_NUM_SEQS = os.environ.get("GAIA_8B_BOOTV7V8_V3V6_MATRIX_VLLM_MAX_NUM_SEQS", "48")

    remain20.ROOT = ROOT
    remain20.PYTHON = PYTHON
    remain20.CONFIG = DEFAULT_CONFIG
    remain20.DEV_DATASET = DEV_DATASET
    remain20.TEST_DATASET = TEST_DATASET
    remain20.RUN_ROOT = RUN_ROOT
    remain20.LAUNCH_LOG_ROOT = LAUNCH_LOG_ROOT
    remain20.QUEUE_LOG_ROOT = QUEUE_LOG_ROOT
    remain20.PREFIX = PREFIX
    remain20.SUMMARY_JSON = SUMMARY_JSON
    remain20.MANIFEST_JSON = MANIFEST_JSON
    remain20.REPORT_DOC = REPORT_DOC
    remain20.TASK_CONCURRENCY = CONCURRENCY
    remain20.POLL_SECONDS = POLL_SECONDS
    remain20.CODEX_TIMEOUT_SECONDS = CODEX_TIMEOUT_SECONDS
    remain20.OFFLINE_WORKERS = OFFLINE_WORKERS
    remain20.CRITIC_SHARD_CONCURRENCY = CRITIC_SHARD_CONCURRENCY
    remain20.ALLOW_PARTIAL_SOURCE_STATES = True


def verify_inputs() -> None:
    missing = [
        str(path)
        for path in [
            PYTHON,
            *CONFIGS.values(),
            SEARCH_RUNTIME_CONFIG,
            DEV_DATASET,
            TEST_DATASET,
        ]
        if not path.exists()
    ]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))


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


def selected_task_count(run_dir: Path | None) -> int:
    if run_dir is None:
        return 0
    selected = run_dir / "selected_tasks.json"
    if not selected.exists():
        return 0
    try:
        return len(json.loads(selected.read_text(encoding="utf-8")).get("task_ids", []))
    except Exception:
        return 0


def state_count(run_name: str) -> tuple[int, int, float]:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return 0, 0, 0.0
    total = selected_task_count(run_dir)
    iteration = latest_iteration_dir(run_dir)
    states = list(iteration.glob("*/state.json")) if iteration else []
    last_mtime = max((path.stat().st_mtime for path in states), default=0.0)
    return len(states), total, last_mtime


def eval_progress(run_name: str, *, live: bool) -> dict[str, Any]:
    done, total, last_mtime = state_count(run_name)
    record = progress_memory.get(run_name)
    changed = False
    if record is None:
        record = {"done": done, "last_progress_at": time.time(), "last_mtime": last_mtime}
        changed = True
    elif done > int(record.get("done", -1)) or (last_mtime and last_mtime > float(record.get("last_mtime") or 0)):
        record = {"done": done, "last_progress_at": time.time(), "last_mtime": last_mtime}
        changed = True
    if changed:
        progress_memory[run_name] = record
    last_progress_at = float(record.get("last_progress_at") or time.time())
    stalled_seconds = max(0, int(time.time() - last_progress_at))
    missing = max(0, total - done) if total else 0
    fraction_ready = bool(total and done >= math.ceil(total * LONGTAIL_FRACTION))
    missing_ready = bool(total and missing <= LONGTAIL_MAX_MISSING)
    longtail_ready = bool(live and total and (fraction_ready or missing_ready) and stalled_seconds >= LONGTAIL_STALLED_SECONDS)
    return {
        "run_name": run_name,
        "landed": done,
        "total": total,
        "missing": missing,
        "live": live,
        "stalled_seconds": stalled_seconds,
        "longtail_ready": longtail_ready,
        "last_state_mtime": last_mtime,
    }


def run_stats(spec: EvalSpec) -> dict[str, Any]:
    run_dir = latest_run_dir(spec.run_name)
    if run_dir is None:
        return {
            "run_name": spec.run_name,
            "label": spec.label,
            "status": "missing_run_dir",
            "counted": spec.counted,
        }
    iteration = latest_iteration_dir(run_dir)
    states: list[dict[str, Any]] = []
    if iteration is not None:
        for state_path in sorted(iteration.glob("*/state.json")):
            try:
                states.append(json.loads(state_path.read_text(encoding="utf-8")))
            except Exception:
                pass
    total = selected_task_count(run_dir) or len(states)
    success = 0
    nonempty_answer = 0
    max_step_like = 0
    for data in states:
        env = data.get("env_result", {})
        success += int(bool(env.get("evaluation", {}).get("task_success")))
        nonempty_answer += int(bool(str(env.get("final_answer", "")).strip()))
        max_step_like += int(len(env.get("action_trace", [])) >= 24)
    return {
        "run_name": spec.run_name,
        "label": spec.label,
        "counted": spec.counted,
        "command": spec.command,
        "boot_key": spec.boot_key,
        "method_key": spec.method_key,
        "repeat": spec.repeat,
        "split": spec.split,
        "run_dir": str(run_dir),
        "landed": len(states),
        "total": total,
        "missing": max(0, total - len(states)),
        "success": success,
        "score": f"{success}/{total or '?'}",
        "nonempty_answer": nonempty_answer,
        "max_step_like_count": max_step_like,
    }


def bootstrap_skill_path(run_name: str) -> Path | None:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def spec_payload(spec: EvalSpec) -> dict[str, Any]:
    return {
        **asdict(spec),
        "config_path": str(spec.config_path),
        "dataset": str(spec.dataset),
        "skill_path": str(spec.skill_path) if spec.skill_path else "",
    }


def write_manifest(status: str, extra: dict[str, Any] | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    with status_lock:
        payload: dict[str, Any] = {
            "prefix": PREFIX,
            "master": MASTER_NAME,
            "status": status,
            "updated_at": now(),
            "summary_json": str(SUMMARY_JSON),
            "report_doc": str(REPORT_DOC),
            "expected_counted_eval_count": EXPECTED_COUNTED_EVALS,
            "lanes": [asdict(lane) for lane in LANES],
            "config": {
                "executor_model": EXECUTOR_SERVED_NAME,
                "executor_label": EXECUTOR_LABEL,
                "executor_model_path": EXECUTOR_MODEL_PATH,
                "executor_tokenizer_path": EXECUTOR_TOKENIZER_PATH,
                "executor_max_model_len": EXECUTOR_MAX_MODEL_LEN,
                "boot_configs": {key: str(path) for key, path in CONFIGS.items()},
                "downstream_boot_keys": DOWNSTREAM_BOOT_KEYS,
                "ports": [lane.port for lane in LANES],
                "task_concurrency": CONCURRENCY,
                "boot_workers": BOOT_WORKERS,
                "bootstrap_workers": BOOTSTRAP_WORKERS,
                "offline_workers": OFFLINE_WORKERS,
                "critic_shard_concurrency": CRITIC_SHARD_CONCURRENCY,
                "partial_source_min_states": PARTIAL_SOURCE_MIN_STATES,
                "partial_source_stall_seconds": PARTIAL_SOURCE_STALL_SECONDS,
                "longtail_fraction": LONGTAIL_FRACTION,
                "longtail_max_missing": LONGTAIL_MAX_MISSING,
                "longtail_stalled_seconds": LONGTAIL_STALLED_SECONDS,
                "actor_model": STRONG_MODEL,
                "critic_model": STRONG_MODEL,
                "strong_provider": "sssaicode",
                "strong_reasoning_effort": STRONG_REASONING_EFFORT,
                "boot_stage_order": BOOT_STAGES,
                "methods": METHOD_ORDER,
                "pseudocomplete_boot_keys": sorted(PSEUDOCOMPLETE_BOOT_KEYS),
                "gpu_ids": GPU_IDS,
                "lane_count": len(LANES),
            },
            "pending": [spec_payload(spec) for spec in pending_evals],
            "running": [
                {
                    "spec": spec_payload(item.spec),
                    "lane": asdict(item.lane),
                    "pid": item.process.pid,
                    "log_path": str(item.log_path),
                    "started_at": item.started_at,
                    "progress": eval_progress(item.spec.run_name, live=item.process.poll() is None),
                }
                for item in running_evals
                if item.process.poll() is None
            ],
            "completed": completed_evals,
            "released_after_longtail": released_evals,
            "eval_failures": eval_failures,
            "offline_records": offline_records,
            "offline_failures": offline_failures,
            "pipelines": pipeline_records,
            "counted_eval_specs": [spec_payload(spec) for spec in all_eval_specs],
        }
        if extra:
            payload.update(extra)
        MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    keys = [
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS",
        "NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP",
        "NLRL_RUNTIME_BOOTSTRAP_PSEUDOCOMPLETE_GRAPH",
        "NLRL_CRITIC_SHARD_CONCURRENCY",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_REASONING_EFFORT",
        "NLRL_ACTOR_API_MODE",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_REASONING_EFFORT",
        "NLRL_CRITIC_API_MODE",
        "NLRL_SERPER_API_KEY",
        "NLRL_SERPER_API_KEYS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    env_parts = [f"{key}={shlex.quote(env[key])}" for key in keys if env.get(key)]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def build_env(lane: base.Lane, spec: EvalSpec) -> dict[str, str]:
    env = base.common_env(lane)
    env.update(
        {
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_RUNTIME_CONFIG),
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_CRITIC_SHARD_CONCURRENCY": CRITIC_SHARD_CONCURRENCY,
            "NLRL_ACTOR_MODEL": STRONG_MODEL,
            "NLRL_ACTOR_BASE_URL": STRONG_PROVIDER["base_url"],
            "NLRL_ACTOR_API_KEY": STRONG_PROVIDER["api_key"],
            "NLRL_ACTOR_API_MODE": "responses_sse",
            "NLRL_ACTOR_ENABLE_THINKING": "1",
            "NLRL_ACTOR_REASONING_EFFORT": STRONG_REASONING_EFFORT,
            "NLRL_ACTOR_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
            "NLRL_CRITIC_MODEL": STRONG_MODEL,
            "NLRL_CRITIC_BASE_URL": STRONG_PROVIDER["base_url"],
            "NLRL_CRITIC_API_KEY": STRONG_PROVIDER["api_key"],
            "NLRL_CRITIC_API_MODE": "responses_sse",
            "NLRL_CRITIC_ENABLE_THINKING": "1",
            "NLRL_CRITIC_REASONING_EFFORT": STRONG_REASONING_EFFORT,
            "NLRL_CRITIC_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
        }
    )
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    if spec.skill_path is not None:
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(spec.skill_path)
    if spec.bootstrap_preprocess:
        env["NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS"] = "1"
        env["NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP"] = "700"
    if spec.bootstrap and spec.boot_key in PSEUDOCOMPLETE_BOOT_KEYS:
        env["NLRL_RUNTIME_BOOTSTRAP_PSEUDOCOMPLETE_GRAPH"] = "1"
    return env


def running_blocks_lane(item: RunningEval) -> bool:
    live = item.process.poll() is None
    progress = eval_progress(item.spec.run_name, live=live)
    if live and progress["longtail_ready"] and item.spec.run_name not in released_evals:
        released_evals[item.spec.run_name] = {
            "released_at": now(),
            "reason": "longtail_ready_for_lane_reuse",
            "progress": progress,
        }
        log(
            "long-tail lane release: "
            f"{item.spec.run_name} lane={item.lane.name} states={progress['landed']}/{progress['total'] or '?'}"
        )
    return live and not progress["longtail_ready"]


def free_lane_for(spec: EvalSpec) -> base.Lane | None:
    busy = {item.lane.name for item in running_evals if running_blocks_lane(item)}
    candidates = [lane for lane in LANES if lane.name in spec.preferred_lanes]
    candidates.extend([lane for lane in LANES if lane.name not in {item.name for item in candidates}])
    for lane in candidates:
        if lane.name not in busy:
            return lane
    return None


def start_eval_locked(spec: EvalSpec, lane: base.Lane) -> RunningEval:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{spec.run_name}_{short_ts()}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(spec.config_path),
        spec.command,
        "--dataset-path",
        str(spec.dataset),
        "--run-name",
        spec.run_name,
    ]
    if spec.bootstrap:
        cmd.append("--bootstrap-skill")
    env = build_env(lane, spec)
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
    base.append_process_row(
        kind="experiment" if spec.counted else "dependency_eval",
        name=spec.run_name,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes=(
            f"{spec.label}; lane={lane.name}; endpoint={lane.base_url}; c{CONCURRENCY}; "
            f"bootstrap={spec.bootstrap}; command={spec.command}; skill={spec.skill_path or ''}; {spec.notes}"
        ),
    )
    item = RunningEval(spec=spec, lane=lane, process=process, log_path=log_path, started_at=now())
    running_evals.append(item)
    progress_memory[spec.run_name] = {"done": -1, "last_progress_at": time.time(), "last_mtime": 0.0}
    log(f"started {spec.run_name} pid={process.pid} lane={lane.name} log={log_path}")
    return item


def enqueue_eval(spec: EvalSpec) -> None:
    with status_lock:
        pending_evals.append(spec)
        if spec.counted:
            all_eval_specs.append(spec)
        write_manifest("running", {"stage": f"queued_{spec.key}"})
    log(f"queued eval {spec.run_name} label={spec.label} command={spec.command}")


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
                base.update_process_row(item.process.pid, status, exit_code=code)
                record = {
                    "spec": spec_payload(item.spec),
                    "lane": asdict(item.lane),
                    "pid": item.process.pid,
                    "returncode": code,
                    "log_path": str(item.log_path),
                    "started_at": item.started_at,
                    "ended_at": now(),
                    "stats": run_stats(item.spec),
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
            write_manifest("running")
        time.sleep(POLL_SECONDS)


def wait_for_bootstrap_skill(run_name: str) -> Path:
    while True:
        skill = bootstrap_skill_path(run_name)
        if skill is not None:
            log(f"bootstrap skill ready for {run_name}: {skill}")
            return skill
        with status_lock:
            failed = run_name in eval_failures
            completed = run_name in completed_evals
        if failed or completed:
            raise RuntimeError(f"bootstrap skill missing for completed run {run_name}")
        done, total, _ = state_count(run_name)
        log(f"waiting bootstrap skill for {run_name}; states={done}/{total or '?'}")
        time.sleep(30)


def wait_for_boot_source(run_name: str) -> Path:
    while True:
        run_dir = latest_run_dir(run_name)
        done, total, _ = state_count(run_name)
        if run_dir is not None and total and done >= total:
            return run_dir
        progress = eval_progress(run_name, live=run_name not in completed_evals)
        with status_lock:
            completed = run_name in completed_evals
            failed = run_name in eval_failures
        if run_dir is not None and completed and done >= PARTIAL_SOURCE_MIN_STATES:
            log(f"accept completed partial boot source {run_name}: {done}/{total or '?'} failed={failed}")
            return run_dir
        if run_dir is not None and progress["longtail_ready"]:
            log(
                f"accept longtail-ready boot source {run_name}: "
                f"{done}/{total or '?'} partial_min={PARTIAL_SOURCE_MIN_STATES}"
            )
            return run_dir
        if run_dir is not None and completed and total and done >= math.ceil(total * LONGTAIL_FRACTION):
            log(
                f"accept completed longtail-equivalent boot source {run_name}: "
                f"{done}/{total or '?'} failed={failed} partial_min={PARTIAL_SOURCE_MIN_STATES}"
            )
            return run_dir
        if run_dir is not None and done >= PARTIAL_SOURCE_MIN_STATES and progress["stalled_seconds"] >= PARTIAL_SOURCE_STALL_SECONDS:
            log(f"accept stalled partial boot source {run_name}: {done}/{total or '?'}")
            return run_dir
        if completed:
            raise RuntimeError(
                f"boot source ended with too few states: {run_name} states={done}/{total or '?'} min={PARTIAL_SOURCE_MIN_STATES}"
            )
        log(
            f"waiting boot source for {run_name}; states={done}/{total or '?'} "
            f"stalled={progress['stalled_seconds']}s partial_min={PARTIAL_SOURCE_MIN_STATES}"
        )
        time.sleep(POLL_SECONDS)


def wait_counted_evals_tail_or_done() -> None:
    while True:
        with status_lock:
            counted_names = {spec.run_name for spec in all_eval_specs}
            done_names = {name for name, record in completed_evals.items() if record.get("spec", {}).get("counted")}
            pending_count = len([spec for spec in pending_evals if spec.counted])
            running_progress = [
                eval_progress(item.spec.run_name, live=item.process.poll() is None)
                for item in running_evals
                if item.spec.counted and item.process.poll() is None
            ]
            released_names = {item["run_name"] for item in running_progress if item["longtail_ready"]}
            blocking = [item for item in running_progress if not item["longtail_ready"]]
        if counted_names and counted_names <= (done_names | released_names) and pending_count == 0:
            return
        summary = "; ".join(
            f"{item['run_name']}={item['landed']}/{item['total'] or '?'} live={item['live']} longtail={item['longtail_ready']}"
            for item in blocking[:8]
        )
        log(
            f"waiting counted evals tail-or-done: done={len(done_names)}/{len(counted_names)} "
            f"released={len(released_names)} pending={pending_count} blocking={len(blocking)}"
            + (f"; {summary}" if summary else "")
        )
        time.sleep(POLL_SECONDS)


def wait_method_evals_tail_or_done(method_key: str) -> None:
    while True:
        with status_lock:
            method_names = {spec.run_name for spec in all_eval_specs if spec.method_key == method_key}
            done_names = {
                name
                for name, record in completed_evals.items()
                if record.get("spec", {}).get("method_key") == method_key and record.get("spec", {}).get("counted")
            }
            pending_count = len([spec for spec in pending_evals if spec.method_key == method_key and spec.counted])
            running_progress = [
                eval_progress(item.spec.run_name, live=item.process.poll() is None)
                for item in running_evals
                if item.spec.method_key == method_key and item.spec.counted and item.process.poll() is None
            ]
            released_names = {item["run_name"] for item in running_progress if item["longtail_ready"]}
            blocking = [item for item in running_progress if not item["longtail_ready"]]
        if method_names and method_names <= (done_names | released_names) and pending_count == 0:
            return
        summary = "; ".join(
            f"{item['run_name']}={item['landed']}/{item['total'] or '?'} live={item['live']} longtail={item['longtail_ready']}"
            for item in blocking[:8]
        )
        log(
            f"waiting {method_key} evals tail-or-done: done={len(done_names)}/{len(method_names)} "
            f"released={len(released_names)} pending={pending_count} blocking={len(blocking)}"
            + (f"; {summary}" if summary else "")
        )
        time.sleep(POLL_SECONDS)


def offline_then_enqueue(source: remain20.BootSource, method_key: str, strategy: str, graph_policy: str) -> Path:
    started_at = now()
    boot_key, repeat_text = source.boot_key.rsplit("_r", 1)
    config_path = CONFIGS[boot_key]
    try:
        with offline_config_lock:
            previous_config = remain20.CONFIG
            remain20.CONFIG = config_path
            try:
                skill = remain20.run_offline_variant(
                    source,
                    method_key=method_key,
                    strategy=strategy,
                    graph_policy=graph_policy,
                )
            finally:
                remain20.CONFIG = previous_config
    except Exception as exc:
        failure = {
            "source_id": source.boot_key,
            "method_key": method_key,
            "strategy": strategy,
            "graph_policy": graph_policy,
            "config_path": str(config_path),
            "source_run": str(source.boot_dev_run),
            "error": repr(exc),
            "started_at": started_at,
            "ended_at": now(),
        }
        with status_lock:
            offline_failures.append(failure)
            write_manifest("running", {"stage": f"offline_failed_{source.boot_key}_{method_key}"})
        log(f"offline failed {source.boot_key} {method_key}: {exc!r}")
        raise

    record = {
        "source_id": source.boot_key,
        "method_key": method_key,
        "strategy": strategy,
        "graph_policy": graph_policy,
        "config_path": str(config_path),
        "source_run": str(source.boot_dev_run),
        "source_skill": str(source.boot_skill),
        "skill_path": str(skill),
        "started_at": started_at,
        "ended_at": now(),
    }
    with status_lock:
        offline_records.append(record)
        write_manifest("running", {"stage": f"offline_done_{source.boot_key}_{method_key}"})
    log(f"offline skill ready {source.boot_key} {method_key}: {skill}")

    repeat = int(repeat_text)
    for split, dataset in [("dev", DEV_DATASET), ("test", TEST_DATASET)]:
        enqueue_eval(
            EvalSpec(
                key=f"{source.boot_key}_{method_key}_{split}",
                label=f"{EXECUTOR_LABEL} {boot_key.upper()} r{repeat} {method_key} {split}",
                command="train-local",
                config_path=config_path,
                dataset=dataset,
                run_name=f"{PREFIX}_{RUN_MODEL_TOKEN}_{boot_key}_r{repeat}_{method_key}_{split}_eval_c{CONCURRENCY}",
                counted=True,
                boot_key=boot_key,
                method_key=method_key,
                repeat=repeat,
                split=split,
                skill_path=skill,
                notes=f"same-repeat boot source {source.boot_dev_run}",
            )
        )
    return skill


def boot_pipeline(boot_key: str, repeat: int) -> remain20.BootSource:
    source_id = f"{boot_key}_r{repeat}"
    config_path = CONFIGS[boot_key]
    bootstrap_preprocess = boot_key in BOOTSTRAP_PREPROCESS_KEYS
    record: dict[str, Any] = {
        "source_id": source_id,
        "boot_key": boot_key,
        "repeat": repeat,
        "status": "running",
        "config_path": str(config_path),
        "bootstrap_preprocess": bootstrap_preprocess,
        "pseudocomplete_graph": boot_key in PSEUDOCOMPLETE_BOOT_KEYS,
        "started_at": now(),
        "method_keys": METHOD_ORDER,
    }
    with status_lock:
        pipeline_records[source_id] = record
        write_manifest("running", {"stage": f"pipeline_start_{source_id}"})

    boot_dev_run = f"{PREFIX}_{RUN_MODEL_TOKEN}_{boot_key}_r{repeat}_boot_dev_c{CONCURRENCY}"
    boot_test_run = f"{PREFIX}_{RUN_MODEL_TOKEN}_{boot_key}_r{repeat}_boot_test_c{CONCURRENCY}"
    with bootstrap_semaphore:
        enqueue_eval(
            EvalSpec(
                key=f"{source_id}_boot_dev",
                label=f"{EXECUTOR_LABEL} {boot_key.upper()} r{repeat} boot-only dev",
                command="train-local",
                config_path=config_path,
                dataset=DEV_DATASET,
                run_name=boot_dev_run,
                counted=True,
                boot_key=boot_key,
                method_key="boot-only",
                repeat=repeat,
                split="dev",
                bootstrap=True,
                bootstrap_preprocess=bootstrap_preprocess,
                notes="fresh boot source; downstream methods reuse this source one-to-one",
            )
        )
        skill = wait_for_bootstrap_skill(boot_dev_run)
    record["boot_skill"] = str(skill)
    write_manifest("running", {"stage": f"boot_skill_ready_{source_id}"})

    enqueue_eval(
        EvalSpec(
            key=f"{source_id}_boot_test",
            label=f"{EXECUTOR_LABEL} {boot_key.upper()} r{repeat} boot-only test",
            command="train-local",
            config_path=config_path,
            dataset=TEST_DATASET,
            run_name=boot_test_run,
            counted=True,
            boot_key=boot_key,
            method_key="boot-only",
            repeat=repeat,
            split="test",
            skill_path=skill,
            notes=f"boot-only test from {source_id} skill",
        )
    )

    source_run = wait_for_boot_source(boot_dev_run)
    record["source_run"] = str(source_run)
    source = remain20.BootSource(
        model_key=RUN_MODEL_TOKEN,
        boot_key=source_id,
        label=f"{EXECUTOR_LABEL} {boot_key.upper()} repeat {repeat}",
        boot_dev_run=source_run,
        boot_skill=skill,
        supplemental_runs=[],
        source_notes=f"{PREFIX}: one-to-one source for {boot_key} repeat {repeat}",
    )
    record["status"] = "boot_source_ready"
    record["ended_at"] = now()
    write_manifest("running", {"stage": f"pipeline_done_{source_id}"})
    return source


def run_downstream_for_sources(
    offline_pool: ThreadPoolExecutor,
    sources: list[remain20.BootSource],
    *,
    stage_index: int,
) -> None:
    sources.sort(key=lambda source: (int(source.boot_key.rsplit("_r", 1)[1]), source.boot_key))
    downstream_sources = [
        source for source in sources if source.boot_key.rsplit("_r", 1)[0] in DOWNSTREAM_BOOT_KEYS
    ]
    skipped_sources = [source.boot_key for source in sources if source not in downstream_sources]
    if skipped_sources:
        log(f"skip downstream for boot sources: {', '.join(skipped_sources)}")
        write_manifest(
            "running",
            {
                "stage": f"stage_{stage_index}_downstream_filter_applied",
                "skipped_sources": skipped_sources,
            },
        )
    for method_key in METHOD_ORDER:
        strategy, graph_policy = METHOD_SPECS[method_key]
        method_futures = {
            offline_pool.submit(offline_then_enqueue, source, method_key, strategy, graph_policy): source
            for source in downstream_sources
        }
        if not method_futures:
            log(f"skip offline {method_key}: no downstream sources")
            continue
        pending_methods = set(method_futures)
        while pending_methods:
            done_now = {future for future in pending_methods if future.done()}
            for future in done_now:
                pending_methods.remove(future)
                source = method_futures[future]
                try:
                    future.result()
                except Exception as exc:
                    source_record = pipeline_records.setdefault(source.boot_key, {})
                    source_record.setdefault("offline_errors", {})[method_key] = repr(exc)
                    log(f"offline method failed: source={source.boot_key} method={method_key} error={exc!r}")
            log(
                f"waiting stage {stage_index} offline {method_key}: "
                f"done={len(method_futures) - len(pending_methods)}/{len(method_futures)}"
            )
            write_manifest("running", {"stage": f"waiting_stage_{stage_index}_offline_{method_key}"})
            if pending_methods:
                time.sleep(POLL_SECONDS)
        wait_method_evals_tail_or_done(method_key)


def write_summary(status: str, *, start_time: str, end_time: str | None = None) -> dict[str, Any]:
    with status_lock:
        eval_stats = [run_stats(spec) for spec in all_eval_specs]
        payload = {
            "prefix": PREFIX,
            "master": MASTER_NAME,
            "status": status,
            "start_time": start_time,
            "end_time": end_time,
            "summary_json": str(SUMMARY_JSON),
            "manifest_json": str(MANIFEST_JSON),
            "report_doc": str(REPORT_DOC),
            "expected_counted_eval_count": EXPECTED_COUNTED_EVALS,
            "actual_counted_eval_specs": len(all_eval_specs),
            "lanes": [asdict(lane) for lane in LANES],
            "matrix": {
                "baseline_repeats": 0,
                "boot_keys": BOOT_KEYS,
                "downstream_boot_keys": DOWNSTREAM_BOOT_KEYS,
                "boot_repeats_per_key": 3,
                "methods_per_boot_source": {
                    key: ["boot-only", *METHOD_ORDER] for key in BOOT_KEYS
                },
                "splits": ["dev", "validation_test"],
                "counted_eval_count": len(all_eval_specs),
            },
            "eval_stats": eval_stats,
            "completed": completed_evals,
            "released_after_longtail": released_evals,
            "eval_failures": eval_failures,
            "offline_records": offline_records,
            "offline_failures": offline_failures,
            "pipelines": pipeline_records,
            "updated_at": now(),
        }
        if end_time:
            try:
                payload["duration_seconds"] = (
                    datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)
                ).total_seconds()
            except Exception:
                payload["duration_seconds"] = None
        SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload


def append_report(summary: dict[str, Any]) -> None:
    DESIGN_ROOT.mkdir(parents=True, exist_ok=True)
    scores = [
        [
            item.get("boot_key") or "",
            item.get("method_key"),
            item.get("repeat"),
            item.get("split"),
            item.get("score"),
            item.get("landed"),
            item.get("missing"),
            item.get("run_dir"),
        ]
        for item in summary["eval_stats"]
    ]
    lines = [
        f"# GAIA {EXECUTOR_LABEL} BOOT 矩阵执行记录",
        "",
        f"- prefix: `{PREFIX}`",
        f"- status: `{summary['status']}`",
        f"- master_log: `{MASTER_LOG}`",
        f"- manifest_json: `{MANIFEST_JSON}`",
        f"- summary_json: `{SUMMARY_JSON}`",
        f"- 计数口径：`{','.join(BOOT_KEYS)}` 各 3 个 fresh boot source；每个 source 一对一扇出 boot-only、{','.join(METHOD_ORDER)} 的 dev/test。",
        f"- 目标 eval 数：`{EXPECTED_COUNTED_EVALS}`；当前已登记 counted specs：`{summary['actual_counted_eval_specs']}`。",
        "- Serper 预算口径：沿用当前 `configs/search_runtime.json` 活跃 key 池。",
        f"- 强模型：SSSAI `{STRONG_MODEL}`，reasoning effort `{STRONG_REASONING_EFFORT}`，用于 boot、critic、actor。",
        "",
        "## 运行策略",
        "",
        f"- 使用 GPU `{','.join(str(gpu) for gpu in GPU_IDS)}`，共 `{len(LANES)}` 个 {EXECUTOR_SERVED_NAME} vLLM endpoint："
        f"`{','.join(str(lane.port) for lane in LANES)}`。",
        f"- 优先级：按 boot stage `{BOOT_STAGES}` 依次启动。",
        "- 每个 repeat 绑定自己的 fresh boot source；后续 A_full/B1 只复用同 repeat 的 boot_dev source。",
        "- BOOT-V7/BOOT-V8 启用伪完全图：INIT 连通所有非 INIT 节点，非 INIT 节点之间互通，非 INIT 节点不回 INIT。",
        "- 长尾释放口径：达到 95% 或缺失不超过 2 个任务，且 15 分钟无新 state，可让后续任务补位。",
        "",
        "## 2026-05-14 验收计划",
        "",
        f"- 检查 manifest、summary、活进程表和 `{len(LANES)}` 个 endpoint，确认 72 条 counted eval 的完成/长尾释放/失败分布。",
        "- 按 boot_key、method_key、repeat、split 聚合均值和方差，优先看 test82，再用 dev83 判断稳定性。",
        "- 对异常项分三类处理：bootstrap 未产 skill、offline actor 失败、eval 长尾或 dead；只补缺口，保留已完成项。",
        "- 产出方向选择：V7 对 V3、V8 对 V6，分别比较 boot-only、A_full、B1。",
        "",
        "## 当前分数快照",
        "",
        "| boot | method | repeat | split | score | landed | missing | run_dir |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in scores:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    lines.extend(
        [
            "",
            "## Offline / Failure",
            "",
            f"- offline_records: `{len(offline_records)}`",
            f"- offline_failures: `{len(offline_failures)}`",
            f"- eval_failures: `{len(eval_failures)}`",
            f"- released_after_longtail: `{len(released_evals)}`",
            "",
        ]
    )
    REPORT_DOC.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    start_time = now()
    status = "finished"
    configure_modules()
    verify_inputs()
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    DESIGN_ROOT.mkdir(parents=True, exist_ok=True)
    base.ensure_process_csv_header()
    base.refresh_process_registry()
    base.append_process_row(
        kind="experiment_master",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir=str(QUEUE_LOG_ROOT),
        command=os.environ.get(
            "GAIA_8B_BOOTV7V8_V3V6_MATRIX_MASTER_COMMAND",
            shell_join([str(PYTHON), "-u", str(Path(__file__).resolve())]),
        ),
        log_path=MASTER_LOG,
        notes=(
            f"{EXECUTOR_LABEL} BOOT matrix remote132; "
            f"expected_counted_eval_count={EXPECTED_COUNTED_EVALS}; boot_workers={BOOT_WORKERS}; "
            f"offline_workers={OFFLINE_WORKERS}; summary={SUMMARY_JSON}; report={REPORT_DOC}"
        ),
    )
    scheduler = threading.Thread(target=scheduler_loop, name="gaia-8b-bootv7v8-v3v6-matrix-scheduler", daemon=True)
    write_manifest("starting")
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
        vllm_pids = [base.start_or_reuse_vllm(lane) for lane in LANES]
        base.wait_endpoints()
        write_manifest("vllm_ready", {"vllm_pids": vllm_pids})
        scheduler.start()
        boot_sources: list[remain20.BootSource] = []
        with ThreadPoolExecutor(max_workers=max(1, OFFLINE_WORKERS), thread_name_prefix="8b-offline") as offline_pool:
            with ThreadPoolExecutor(max_workers=max(1, BOOT_WORKERS), thread_name_prefix="8b-boot") as boot_pool:
                for stage_index, stage_boot_keys in enumerate(BOOT_STAGES, start=1):
                    stage_sources: list[remain20.BootSource] = []
                    futures = [
                        boot_pool.submit(boot_pipeline, boot_key, repeat)
                        for repeat in range(1, 4)
                        for boot_key in stage_boot_keys
                    ]
                    pending = set(futures)
                    while pending:
                        done_now = {future for future in pending if future.done()}
                        for future in done_now:
                            pending.remove(future)
                            try:
                                source = future.result()
                                boot_sources.append(source)
                                stage_sources.append(source)
                            except Exception as exc:
                                status = "finished_with_pipeline_failures"
                                log(f"boot pipeline failed: {exc!r}")
                        log(
                            f"waiting boot stage {stage_index} pipelines "
                            f"({','.join(stage_boot_keys)}): done={len(futures) - len(pending)}/{len(futures)}"
                        )
                        write_manifest(
                            "running",
                            {
                                "stage": f"waiting_boot_stage_{stage_index}",
                                "stage_boot_keys": stage_boot_keys,
                            },
                        )
                        if pending:
                            time.sleep(POLL_SECONDS)
                    wait_method_evals_tail_or_done("boot-only")
                    run_downstream_for_sources(offline_pool, stage_sources, stage_index=stage_index)
        wait_counted_evals_tail_or_done()
        if offline_failures and eval_failures:
            status = "finished_with_offline_and_eval_failures"
        elif offline_failures:
            status = "finished_with_offline_failures"
        elif eval_failures:
            status = "finished_with_eval_failures"
        elif released_evals:
            status = "finished_with_longtail_live"
        end_time = now()
        summary = write_summary(status, start_time=start_time, end_time=end_time)
        append_report(summary)
        write_manifest(status)
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        summary = write_summary(status, start_time=start_time, end_time=now())
        append_report(summary)
        write_manifest(status)
        raise
    except Exception:
        status = "dead"
        summary = write_summary(status, start_time=start_time, end_time=now())
        append_report(summary)
        write_manifest(status)
        raise
    finally:
        scheduler_stop.set()
        scheduler.join(timeout=60)
        base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
