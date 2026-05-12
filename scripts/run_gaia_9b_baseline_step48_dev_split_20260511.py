from __future__ import annotations

import csv
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
SPLIT_ROOT = ROOT / "data/converted/splits"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

RUN_PREFIX = os.environ.get("GAIA_9B_BASELINE_STEP48_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M_9b_baseline_step48_devsplit"
)
MASTER_NAME = f"{RUN_PREFIX}_master"
SUMMARY_PATH = QUEUE_LOG_ROOT / f"{MASTER_NAME}_summary.json"
MANIFEST_PATH = QUEUE_LOG_ROOT / f"{MASTER_NAME}_manifest.json"
CONCURRENCY = int(os.environ.get("GAIA_9B_BASELINE_STEP48_CONCURRENCY", "20"))
MAX_EXECUTOR_STEPS = int(os.environ.get("GAIA_9B_BASELINE_STEP48_MAX_EXECUTOR_STEPS", "48"))
GPU_IDLE_UTIL_THRESHOLD = int(os.environ.get("GAIA_9B_BASELINE_STEP48_GPU_IDLE_UTIL_THRESHOLD", "10"))
LANE_WAIT_SECONDS = int(os.environ.get("GAIA_9B_BASELINE_STEP48_LANE_WAIT_SECONDS", "20"))


@dataclass(frozen=True)
class Lane:
    name: str
    half_label: str
    gpu: int
    port: int
    model_path: str = "/data/xsy/codes/checkpoints/Qwen3.5-9B"
    served_name: str = "Qwen3.5-9B-local"
    tokenizer_path: str = "/data/xsy/codes/checkpoints/Qwen3.5-9B"
    max_model_len: int = 49152

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


LANES = [
    Lane("254_gpu0_9b", "half1", 0, 8610),
    Lane("254_gpu1_9b", "half2", 1, 8611),
]


@dataclass
class EvalRun:
    label: str
    run_name: str
    dataset_path: Path
    lane: Lane
    process: subprocess.Popen[str] | None = None
    pid: int | None = None
    log_path: Path | None = None
    started_at: str = ""
    ended_at: str = ""
    returncode: int | None = None
    run_dir: str = ""
    config_snapshot: dict | None = None
    status: str = "pending"


evals: dict[str, EvalRun] = {}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


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
        if len(row) < 8 or row[2].strip() != "running":
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
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
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
        )


def update_process_row(pid: int, status: str, *, exit_code: int | None = None, run_dir: str = "") -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[5].strip() != str(pid):
            continue
        while len(row) < 14:
            row.append("")
        row[1] = now()
        row[2] = status
        if status != "running" and not row[7].strip():
            row[7] = now()
        if run_dir:
            row[9] = run_dir
        if exit_code is not None:
            row[11] = str(exit_code)
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def load_search_runtime_env() -> dict[str, str]:
    try:
        data = json.loads(SEARCH_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    env: dict[str, str] = {}
    if data.get("http_proxy"):
        env["HTTP_PROXY"] = str(data["http_proxy"])
    if data.get("https_proxy"):
        env["HTTPS_PROXY"] = str(data["https_proxy"])
    no_proxy = str(data.get("no_proxy", "127.0.0.1,localhost,0.0.0.0"))
    parts = [part.strip() for part in no_proxy.split(",") if part.strip()]
    for item in ["127.0.0.1", "localhost", "0.0.0.0"]:
        if item not in parts:
            parts.append(item)
    env["NO_PROXY"] = ",".join(parts)
    return env


def endpoint_models(base_url: str) -> list[str]:
    try:
        request = Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    except Exception:
        return []


def wait_endpoint(lane: Lane) -> None:
    deadline = time.time() + int(os.environ.get("GAIA_9B_BASELINE_STEP48_ENDPOINT_WAIT_SECONDS", "180"))
    while time.time() < deadline:
        if lane.served_name in endpoint_models(lane.base_url):
            return
        log(f"waiting endpoint {lane.name} {lane.base_url}")
        time.sleep(10)
    raise RuntimeError(f"endpoint not ready: {lane.name} {lane.base_url}")


def gpu_util(gpu_index: int) -> int | None:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        if parts[0] == str(gpu_index):
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def active_eval_on_lane(lane: Lane) -> list[tuple[str, str]]:
    if not PROCESS_CSV.exists():
        return []
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    active: list[tuple[str, str]] = []
    for row in rows:
        if row.get("status") != "running":
            continue
        pid_text = (row.get("pid") or "").strip()
        if not pid_text.isdigit() or not pid_alive(int(pid_text)):
            continue
        if (row.get("kind") or "") != "experiment":
            continue
        haystack = " ".join([row.get("name") or "", row.get("command") or "", row.get("notes") or ""])
        if lane.base_url in haystack or f"gpu{lane.gpu}" in haystack:
            active.append((row.get("name") or "", pid_text))
    return active


def wait_lane_ready(lane: Lane) -> None:
    wait_endpoint(lane)
    while True:
        refresh_process_registry()
        active = active_eval_on_lane(lane)
        util = gpu_util(lane.gpu)
        if not active and (util is None or util <= GPU_IDLE_UTIL_THRESHOLD):
            log(f"lane ready {lane.name} util={util}")
            return
        log(f"waiting lane {lane.name}; active={active}; gpu_util={util}")
        time.sleep(LANE_WAIT_SECONDS)


def common_env(lane: Lane) -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "ALL_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_THINKING_BUDGET",
        "NLRL_LLM_THINKING_TOKEN_BUDGET",
        "NLRL_LLM_THINKING_BUDGET",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
    ]:
        env.pop(key, None)
    env.update(load_search_runtime_env())
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
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_RUNTIME_CONFIG),
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": str(MAX_EXECUTOR_STEPS),
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
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


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "GAIA_9B_BASELINE_STEP48_PREFIX",
        "GAIA_9B_BASELINE_STEP48_CONCURRENCY",
        "GAIA_9B_BASELINE_STEP48_MAX_EXECUTOR_STEPS",
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "CUDA_VISIBLE_DEVICES",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_API_MODE",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_MODEL",
    ]
    return {key: env[key] for key in keys if env.get(key) is not None}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def split_level_counts(tasks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        level = str((task.get("metadata") or {}).get("level", "unknown"))
        counts[level] = counts.get(level, 0) + 1
    return counts


def ensure_split_datasets() -> dict[str, Path]:
    payload = json.loads(DEV_DATASET.read_text(encoding="utf-8"))
    tasks = list(payload.get("tasks", []))
    if len(tasks) != 83:
        raise RuntimeError(f"expected 83 dev tasks, got {len(tasks)} from {DEV_DATASET}")
    shards = {"half1": tasks[:42], "half2": tasks[42:]}
    SPLIT_ROOT.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for label, shard_tasks in shards.items():
        out = SPLIT_ROOT / f"gaia_2023_all_validation_dev_tasks_baseline_step48_{label}.json"
        shard_payload = dict(payload)
        shard_payload["tasks"] = shard_tasks
        shard_payload["task_count"] = len(shard_tasks)
        shard_payload["split_strategy"] = {
            "source_dataset": str(DEV_DATASET),
            "strategy": "contiguous_halves",
            "label": label,
            "run_prefix": RUN_PREFIX,
            "purpose": "9B direct baseline max_executor_steps=48 bottleneck check",
        }
        shard_payload["level_counts"] = split_level_counts(shard_tasks)
        out.write_text(json.dumps(shard_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result[label] = out
    return result


def run_name_suffix_for_match(run_name: str) -> str:
    match = re.match(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_(.+)$", run_name)
    return "_" + match.group(1) if match else run_name


def latest_run_dir(run_name: str) -> str:
    suffix = run_name_suffix_for_match(run_name)
    matches = [
        path
        for pattern in (f"**/{run_name}", f"**/*{suffix}")
        for path in RUN_ROOT.glob(pattern)
        if path.is_dir()
    ]
    return str(max(matches, key=lambda path: path.stat().st_mtime)) if matches else ""


def read_config_snapshot(run_name: str, timeout: int = 180) -> tuple[str, dict | None]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run_dir = latest_run_dir(run_name)
        if run_dir:
            snapshot_path = Path(run_dir) / "config_snapshot.json"
            if snapshot_path.exists():
                try:
                    return run_dir, json.loads(snapshot_path.read_text(encoding="utf-8"))
                except Exception:
                    return run_dir, None
        time.sleep(5)
    return "", None


def run_stats(run_dir: str, total: int) -> dict[str, object]:
    if not run_dir:
        return {"score": f"0/{total}", "success": 0, "landed": 0, "total": total, "missing": total}
    state_paths = sorted(Path(run_dir).glob("iteration_01/*/state.json"))
    success = 0
    forced = 0
    max_step_like = 0
    max_step_48_like = 0
    for path in state_paths:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        env_result = state.get("env_result") or {}
        evaluation = env_result.get("evaluation") or {}
        if evaluation.get("task_success"):
            success += 1
        action_trace = env_result.get("action_trace") or []
        if any((action.get("outcome") or "") == "forced_final_answer_after_max_steps" for action in action_trace):
            forced += 1
        if len(action_trace) >= 24 or any("max_steps" in str(action.get("outcome") or "") for action in action_trace):
            max_step_like += 1
        if len(action_trace) >= MAX_EXECUTOR_STEPS or any("max_steps" in str(action.get("outcome") or "") for action in action_trace):
            max_step_48_like += 1
    landed = len(state_paths)
    return {
        "score": f"{success}/{total}",
        "success": success,
        "landed": landed,
        "total": total,
        "missing": max(total - landed, 0),
        "forced": forced,
        "max_step_24_or_more_like": max_step_like,
        "max_step_48_or_forced_like": max_step_48_like,
    }


def write_manifest(status: str, *, failures: dict[str, int | str] | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": MASTER_NAME,
        "run_prefix": RUN_PREFIX,
        "updated_at": now(),
        "status": status,
        "summary_json": str(SUMMARY_PATH),
        "manifest_json": str(MANIFEST_PATH),
        "lanes": [asdict(lane) for lane in LANES],
        "config": {
            "runner": "direct-eval-local",
            "dataset": "validation_dev83 split into half1=42 and half2=41",
            "bootstrap": "disabled",
            "initial_skill": None,
            "model": "Qwen3.5-9B-local",
            "tokenizer_path": "/data/xsy/codes/checkpoints/Qwen3.5-9B",
            "max_model_len": 49152,
            "max_tokens": 12288,
            "token_guard_safety_margin": 512,
            "max_context_chars": 0,
            "thinking_token_budget": None,
            "max_executor_steps": MAX_EXECUTOR_STEPS,
            "task_concurrency_per_half": CONCURRENCY,
            "tool_profile": "atomic_v2",
            "answer_acceptance_policy": "any_phase",
        },
        "evals": {
            label: {
                "label": item.label,
                "status": item.status,
                "run_name": item.run_name,
                "dataset_path": str(item.dataset_path),
                "lane": asdict(item.lane),
                "pid": item.pid,
                "log_path": str(item.log_path) if item.log_path else "",
                "started_at": item.started_at,
                "ended_at": item.ended_at,
                "returncode": item.returncode,
                "run_dir": item.run_dir,
                "config_snapshot": item.config_snapshot,
                "stats": run_stats(item.run_dir, 42 if item.label == "half1" else 41),
            }
            for label, item in evals.items()
        },
        "failures": failures or {},
    }
    MANIFEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def start_eval(label: str, dataset_path: Path, lane: Lane) -> EvalRun:
    run_name = f"{RUN_PREFIX}_baseline_step48_dev_{label}_c{CONCURRENCY}_gpu{lane.gpu}"
    log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "direct-eval-local",
        "--dataset-path",
        str(dataset_path),
        "--run-name",
        run_name,
    ]
    env = common_env(lane)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
        kind="experiment",
        name=run_name,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes=(
            f"9B direct baseline step48 dev split {label}; no bootstrap; no initial skill; "
            f"lane={lane.name}; endpoint={lane.base_url}; c{CONCURRENCY}; prefix={RUN_PREFIX}."
        ),
    )
    item = evals[label]
    item.process = process
    item.pid = process.pid
    item.log_path = log_path
    item.started_at = now()
    item.status = "running"
    log(f"started {label} pid={process.pid} lane={lane.name} log={log_path}")
    write_manifest("running")
    run_dir, snapshot = read_config_snapshot(run_name, timeout=180)
    item.run_dir = run_dir
    item.config_snapshot = snapshot
    if run_dir:
        log(f"config snapshot checked {label} run_dir={run_dir}")
        write_manifest("running")
    return item


def poll_evals() -> None:
    changed = False
    for item in evals.values():
        if item.process is None or item.returncode is not None:
            continue
        code = item.process.poll()
        if code is None:
            continue
        item.returncode = code
        item.ended_at = now()
        item.run_dir = item.run_dir or latest_run_dir(item.run_name)
        item.status = "finished" if code == 0 else "dead"
        update_process_row(item.process.pid, item.status, exit_code=code, run_dir=item.run_dir)
        log(f"eval exited {item.label} pid={item.process.pid} code={code} run_dir={item.run_dir}")
        changed = True
    if changed:
        write_manifest("running")


def stop_running_children(status: str = "stopped") -> None:
    for item in evals.values():
        if item.process is None or item.returncode is not None:
            continue
        try:
            os.killpg(item.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        item.status = status
        update_process_row(item.process.pid, status, run_dir=item.run_dir or latest_run_dir(item.run_name))


def wait_all() -> dict[str, int]:
    failures: dict[str, int] = {}
    while True:
        poll_evals()
        live = [label for label, item in evals.items() if item.process is not None and item.returncode is None]
        pending = [label for label, item in evals.items() if item.process is None]
        if not live and not pending:
            break
        log(f"waiting evals; live={live}; pending={pending}")
        time.sleep(60)
    for item in evals.values():
        if item.returncode:
            failures[item.run_name] = item.returncode
    return failures


def main() -> int:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    master_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"))
    ensure_process_csv_header()
    refresh_process_registry()
    split_paths = ensure_split_datasets()
    for lane in LANES:
        evals[lane.half_label] = EvalRun(
            label=lane.half_label,
            run_name=f"{RUN_PREFIX}_baseline_step48_dev_{lane.half_label}_c{CONCURRENCY}_gpu{lane.gpu}",
            dataset_path=split_paths[lane.half_label],
            lane=lane,
        )
    append_process_row(
        kind="experiment_queue",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(9B baseline step48 dev split master)",
        command=os.environ.get("GAIA_9B_BASELINE_STEP48_MASTER_COMMAND", command_display(os.environ, [str(PYTHON), *sys.argv])),
        log_path=master_log,
        notes=(
            f"9B direct baseline dev split; only step change from baseline is max_executor_steps={MAX_EXECUTOR_STEPS}; "
            f"half1 gpu0 waits for lane idle; half2 gpu1 starts when ready; summary={SUMMARY_PATH}."
        ),
    )
    status = "finished"
    failures: dict[str, int | str] = {}
    write_manifest("starting")
    try:
        lane1 = LANES[1]
        wait_lane_ready(lane1)
        start_eval(lane1.half_label, split_paths[lane1.half_label], lane1)

        lane0 = LANES[0]
        while True:
            poll_evals()
            refresh_process_registry()
            active = active_eval_on_lane(lane0)
            util = gpu_util(lane0.gpu)
            if not active and (util is None or util <= GPU_IDLE_UTIL_THRESHOLD):
                break
            log(f"waiting lane {lane0.name} before starting half1; active={active}; gpu_util={util}")
            time.sleep(LANE_WAIT_SECONDS)
        wait_endpoint(lane0)
        start_eval(lane0.half_label, split_paths[lane0.half_label], lane0)

        child_failures = wait_all()
        failures.update(child_failures)
        if failures:
            status = "finished_with_eval_failures"
            write_manifest(status, failures=failures)
            return 1
        write_manifest("finished")
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        stop_running_children(status)
        write_manifest(status, failures=failures)
        raise
    except Exception as exc:
        status = "dead"
        failures["master"] = str(exc)
        stop_running_children("stopped_by_dead_master")
        write_manifest(status, failures=failures)
        raise
    finally:
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
