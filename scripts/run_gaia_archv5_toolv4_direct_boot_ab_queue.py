from __future__ import annotations

import csv
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = Path(os.environ.get("GAIA_ARCHV5_CONFIG", str(ROOT / "configs/system.json")))
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

VLLM_PYTHON = Path(os.environ.get("GAIA_ARCHV5_VLLM_PYTHON", "/data/xsy/miniconda3/envs/vllm_env/bin/python"))
QWEN_MODEL = Path(os.environ.get("GAIA_ARCHV5_QWEN_MODEL", "/data/xsy/codes/checkpoints/Qwen3-8B"))
CODEX_BIN = os.environ.get(
    "NLRL_CODEX_CLI_PATH",
    "/data/xsy/.vscode-server/extensions/openai.chatgpt-26.422.30944/bin/linux-x86_64/codex",
)

CONCURRENCY = int(
    os.environ.get("GAIA_ARCHV5_QUEUE_CONCURRENCY", "").strip()
    or os.environ.get("NLRL_RUNTIME_TASK_CONCURRENCY", "").strip()
    or "20"
)
TAIL_THRESHOLD = int(os.environ.get("GAIA_ARCHV5_QUEUE_TAIL_THRESHOLD", "3"))
POLL_SECONDS = int(os.environ.get("GAIA_ARCHV5_QUEUE_POLL_SECONDS", "60"))
ALLOW_PARTIAL_TAIL = os.environ.get("GAIA_ARCHV5_QUEUE_ALLOW_PARTIAL_TAIL", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
TAIL_GRACE_POLLS = int(os.environ.get("GAIA_ARCHV5_QUEUE_TAIL_GRACE_POLLS", "3"))
AB_AFTER_BOTH_BOOT = os.environ.get("GAIA_ARCHV5_QUEUE_AB_AFTER_BOTH_BOOT", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RUN_PREFIX = os.environ.get("GAIA_ARCHV5_QUEUE_PREFIX", "").strip() or "20260428_archv5_toolv2_boot_ab"
TOOL_PROFILE = os.environ.get("GAIA_ARCHV5_TOOL_PROFILE", "atomic_v2").strip() or "atomic_v2"
INCLUDE_DIRECT = os.environ.get("GAIA_ARCHV5_QUEUE_INCLUDE_DIRECT", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
CONCURRENCY_LABEL = f"c{CONCURRENCY}"
PROFILE_LABEL = os.environ.get("GAIA_ARCHV5_QUEUE_PROFILE_LABEL", "").strip()
if not PROFILE_LABEL:
    if TOOL_PROFILE == "atomic_v2":
        PROFILE_LABEL = "toolv2"
    elif TOOL_PROFILE == "atomic_v4":
        PROFILE_LABEL = "toolv4"
    else:
        PROFILE_LABEL = TOOL_PROFILE.replace("_", "-")
SCHEDULE_LABEL = "direct_boot_ab" if INCLUDE_DIRECT else "boot_ab"

DIRECT_DEV_RUN_NAME = f"{RUN_PREFIX}_direct_dev_{CONCURRENCY_LABEL}"
DIRECT_TEST_RUN_NAME = f"{RUN_PREFIX}_direct_test_{CONCURRENCY_LABEL}"
BOOT_DEV_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_boot_dev_{CONCURRENCY_LABEL}"
BOOT_TEST_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_boot_test_{CONCURRENCY_LABEL}"
A_OFFLINE_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_A_full_offline_actor_iter1"
B_OFFLINE_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_B_sharded_offline_actor_iter1"
A_DEV_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_A_full_dev_eval_{CONCURRENCY_LABEL}"
A_TEST_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_A_full_test_eval_{CONCURRENCY_LABEL}"
B_DEV_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_B_sharded_dev_eval_{CONCURRENCY_LABEL}"
B_TEST_RUN_NAME = f"{RUN_PREFIX}_fresh_bootv3_B_sharded_test_eval_{CONCURRENCY_LABEL}"
QUEUE_NAME = f"{RUN_PREFIX}_{PROFILE_LABEL}_{SCHEDULE_LABEL}_queue_{CONCURRENCY_LABEL}"

REMOTE_EXECUTOR_BASE_URL = os.environ.get("GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URL", "").strip()
REMOTE_EXECUTOR_LANES = max(1, int(os.environ.get("GAIA_ARCHV5_REMOTE_EXECUTOR_LANES", "3")))
EXECUTOR_MODEL = os.environ.get(
    "GAIA_ARCHV5_EXECUTOR_MODEL",
    "qwen3.5-9b" if REMOTE_EXECUTOR_BASE_URL else "Qwen3-8B-local",
).strip()
if REMOTE_EXECUTOR_BASE_URL:
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
from gaia_skillrl.search_config import apply_search_runtime_env  # noqa: E402
from gaia_skillrl.tools import available_tool_names_for_profile  # noqa: E402
from gaia_skillrl.trainer import GaiaSkillTrainer  # noqa: E402
from gaia_skillrl.utils import ensure_dir, write_json  # noqa: E402


@dataclass
class RunJob:
    key: str
    run_name: str
    dataset: Path
    command: str
    lane: str = ""
    iterations: int = 0
    skill_path: Path | None = None
    bootstrap: bool = False
    strategy: str = "full"
    process: subprocess.Popen[str] | None = None
    log_path: Path | None = None
    terminal_status: str | None = None


@dataclass
class EvalSpec:
    key: str
    run_name: str
    dataset: Path
    skill_path: Path
    notes: str


model_server_pids: dict[str, int] = {}
tail_seen: dict[str, int] = {}


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        stat = stat_path.read_text(encoding="utf-8", errors="replace").split()
        return len(stat) > 2 and stat[2] != "Z"
    except FileNotFoundError:
        return False
    except Exception:
        return True


def refresh_process_registry() -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return
    changed = False
    for row in rows[1:]:
        if len(row) < 8:
            continue
        status = row[2].strip().lower()
        pid_text = row[5].strip()
        if status != "running" or not pid_text.isdigit():
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


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "CUDA_VISIBLE_DEVICES",
        "PYTHONUNBUFFERED",
        "PYTHONFAULTHANDLER",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "GAIA_ARCHV5_QUEUE_PREFIX",
        "GAIA_ARCHV5_QUEUE_INCLUDE_DIRECT",
        "GAIA_ARCHV5_QUEUE_CONCURRENCY",
        "GAIA_ARCHV5_QUEUE_TAIL_THRESHOLD",
        "GAIA_ARCHV5_QUEUE_ALLOW_PARTIAL_TAIL",
        "GAIA_ARCHV5_QUEUE_TAIL_GRACE_POLLS",
        "GAIA_ARCHV5_QUEUE_AB_AFTER_BOTH_BOOT",
        "GAIA_ARCHV5_QUEUE_AB_WAIT_FOR_PIDS",
        "GAIA_ARCHV5_QUEUE_WAIT_FOR_PIDS",
        "GAIA_ARCHV5_QUEUE_WAIT_POLL_SECONDS",
        "GAIA_ARCHV5_BOOT_DEV_LANE",
        "GAIA_ARCHV5_BOOT_TEST_LANE",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URL",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_LANES",
        "GAIA_ARCHV5_EXECUTOR_MODEL",
        "GAIA_ARCHV5_TOOL_PROFILE",
        "GAIA_ARCHV5_SKIP_BOOTSTRAP_PREFLIGHT",
        "NLRL_QUEUE_LOG_PATH",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_CRITIC_STRATEGY",
        "NLRL_RUNTIME_CRITIC_SHARD_SIZE",
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
        "NLRL_EXECUTOR_TEMPERATURE",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS",
        "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS",
        "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_API_KEY",
        "NLRL_TOOL_TIMEOUT_SECONDS",
        "NLRL_WEB_SEARCH_PROVIDER",
        "NLRL_WEB_SEARCH_FALLBACK_PROVIDER",
        "NLRL_SERPER_API_KEY",
        "NLRL_SERPER_SEARCH_ENDPOINT",
        "NLRL_SERPER_SEARCH_TIMEOUT_SECONDS",
        "NLRL_BRAVE_SEARCH_API_KEY",
        "NLRL_BRAVE_SEARCH_SAFESEARCH",
        "NLRL_BRAVE_SEARCH_TIMEOUT_SECONDS",
        "NLRL_BRAVE_SEARCH_ENDPOINT",
        "NLRL_BRAVE_SEARCH_COUNTRY",
    ]
    return {key: env[key] for key in keys if env.get(key)}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    cmd_parts = [shlex.quote(part) for part in cmd]
    return "env " + " ".join([*env_parts, *cmd_parts])


def codex_role_env() -> dict[str, str]:
    return {
        "NLRL_ACTOR_MODEL": "gpt-5.4",
        "NLRL_ACTOR_BASE_URL": "codex-cli",
        "NLRL_ACTOR_API_KEY": "EMPTY",
        "NLRL_ACTOR_API_MODE": "codex_cli",
        "NLRL_ACTOR_TIMEOUT_SECONDS": os.environ.get("NLRL_ACTOR_TIMEOUT_SECONDS", "1800"),
        "NLRL_CRITIC_MODEL": "gpt-5.4",
        "NLRL_CRITIC_BASE_URL": "codex-cli",
        "NLRL_CRITIC_API_KEY": "EMPTY",
        "NLRL_CRITIC_API_MODE": "codex_cli",
        "NLRL_CRITIC_TIMEOUT_SECONDS": os.environ.get("NLRL_CRITIC_TIMEOUT_SECONDS", "1800"),
        "NLRL_CODEX_CLI_PATH": CODEX_BIN,
        "NLRL_CODEX_CLI_WORKDIR": os.environ.get("NLRL_CODEX_CLI_WORKDIR", "/tmp"),
        "NLRL_CODEX_CLI_TIMEOUT_SECONDS": os.environ.get("NLRL_CODEX_CLI_TIMEOUT_SECONDS", "1800"),
    }


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


def lane_port(lane: str) -> int | None:
    if not lane.startswith("gpu"):
        return None
    suffix = lane.replace("gpu", "", 1)
    if not suffix.isdigit():
        return None
    return 8100 + int(suffix)


def lane_model_pid(lane: str) -> int | None:
    port = lane_port(lane)
    if port is None:
        return None
    return model_server_pids.get(lane) or find_vllm_pid(port)


def stop_pid(pid: int, status: str) -> None:
    if not pid_alive(pid):
        update_process_row(pid, "dead")
        return
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        update_process_row(pid, status)
        log(f"sent SIGTERM pid={pid} pgid={pgid} status={status}")
    except ProcessLookupError:
        update_process_row(pid, "dead")
        return
    except Exception as exc:
        log(f"failed to SIGTERM pid={pid}: {exc}")
        return
    time.sleep(8)
    if pid_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            update_process_row(pid, status + "_sigkill")
            log(f"sent SIGKILL pid={pid}")
        except ProcessLookupError:
            update_process_row(pid, status)
        except Exception as exc:
            log(f"failed to SIGKILL pid={pid}: {exc}")


def start_vllm(lane: str) -> None:
    if REMOTE_EXECUTOR_BASE_URL:
        base_url = LANES[lane]
        if not endpoint_ready(base_url):
            raise RuntimeError(f"Remote executor endpoint is not ready: lane={lane} endpoint={base_url}")
        log(f"remote executor ready lane={lane} endpoint={base_url} model={EXECUTOR_MODEL}")
        return
    gpu = int(lane.replace("gpu", ""))
    base_url = LANES[lane]
    port = 8100 + gpu
    existing_pid = find_vllm_pid(port)
    if endpoint_ready(base_url):
        log(f"reuse healthy vLLM lane={lane} port={port} pid={existing_pid or 'unknown'}")
        if existing_pid is not None:
            model_server_pids[lane] = existing_pid
        return
    if existing_pid is not None and pid_alive(existing_pid):
        stop_pid(existing_pid, f"stopped_unhealthy_before_archv5_{PROFILE_LABEL}_queue_{lane}")
        time.sleep(5)

    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"qwen3_8b_local_vllm_{lane}_p{port}_{RUN_PREFIX}.log"
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
    model_server_pids[lane] = process.pid
    append_process_row(
        name=f"qwen3_8b_local_vllm_{lane}_p{port}_{RUN_PREFIX}",
        pid=process.pid,
        cwd=Path("/data/xsy"),
        run_dir=str(LAUNCH_LOG_ROOT),
        command=command_display(env, cmd),
        log_path=log_path,
        notes=(
            "Local Qwen3-8B vLLM service for ARCH-V5 queue; "
            f"tool_profile={TOOL_PROFILE}; lane={lane}; port={port}."
        ),
        kind="model_server",
    )
    log(f"started vLLM lane={lane} pid={process.pid} port={port} log={log_path}")


def wait_vllm_ready() -> None:
    deadline = time.monotonic() + int(os.environ.get("GAIA_ARCHV5_VLLM_READY_TIMEOUT", "900"))
    pending = set(LANES)
    while pending:
        for lane in list(pending):
            if endpoint_ready(LANES[lane]):
                pending.remove(lane)
                log(f"vLLM ready lane={lane} endpoint={LANES[lane]}")
        if not pending:
            return
        for lane, pid in list(model_server_pids.items()):
            if lane in pending and not pid_alive(pid):
                update_process_row(pid, "dead")
                raise RuntimeError(f"vLLM {lane} pid={pid} exited before readiness")
        if time.monotonic() > deadline:
            raise TimeoutError(f"vLLM readiness timeout, pending={sorted(pending)}")
        log(f"waiting for vLLM readiness: pending={sorted(pending)}")
        time.sleep(15)


def wait_for_prior_pids() -> None:
    raw = os.environ.get("GAIA_ARCHV5_QUEUE_WAIT_FOR_PIDS", "").strip()
    if not raw:
        return
    pids: list[int] = []
    for token in raw.replace(",", " ").split():
        if token.isdigit():
            pid = int(token)
            if pid != os.getpid():
                pids.append(pid)
    if not pids:
        return
    poll_seconds = int(os.environ.get("GAIA_ARCHV5_QUEUE_WAIT_POLL_SECONDS", "60"))
    pending = sorted(set(pids))
    log(f"waiting for prior queue pids before claiming lanes: {pending}")
    while pending:
        refresh_process_registry()
        alive = [pid for pid in pending if pid_alive(pid)]
        finished = [pid for pid in pending if pid not in alive]
        if finished:
            log(f"prior queue pids finished: {finished}")
        pending = alive
        if not pending:
            log("all prior queue pids finished; continuing")
            return
        log(f"prior queue pids still alive: {pending}; sleep={poll_seconds}s")
        time.sleep(poll_seconds)


def parse_pid_list(raw: str) -> list[int]:
    pids: list[int] = []
    for token in raw.replace(",", " ").split():
        if token.isdigit():
            pid = int(token)
            if pid != os.getpid():
                pids.append(pid)
    return sorted(set(pids))


def ab_wait_pids_done() -> bool:
    raw = os.environ.get("GAIA_ARCHV5_QUEUE_AB_WAIT_FOR_PIDS", "").strip()
    if not raw:
        return True
    pending = [pid for pid in parse_pid_list(raw) if pid_alive(pid)]
    if pending:
        log(f"waiting before A/B for external pids to finish: {pending}")
        return False
    log("external pids required before A/B are finished")
    return True


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
    for pattern in (f"**/{run_name}", f"**/*_{run_name}"):
        matches.extend(path for path in RUN_ROOT.glob(pattern) if path.is_dir())
    minute_prefix = re.match(r"^(\d{8}_\d{4})(_.+)$", run_name)
    if minute_prefix is not None:
        minute, suffix = minute_prefix.groups()
        for pattern in (
            f"**/{minute}[0-9][0-9]{suffix}",
            f"**/*_{minute}[0-9][0-9]{suffix}",
        ):
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


def tail_ready(job: RunJob) -> bool:
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


def build_env(job: RunJob) -> dict[str, str]:
    if job.lane not in LANES:
        raise RuntimeError(f"Unknown lane {job.lane!r}")
    env = os.environ.copy()
    for key in [
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_EXECUTOR_BASE_URL",
    ]:
        env.pop(key, None)
    env.update(codex_role_env())
    apply_search_runtime_env(env)
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
            "NLRL_RUNTIME_TOOL_PROFILE": TOOL_PROFILE,
            "NLRL_RUNTIME_CRITIC_STRATEGY": job.strategy,
            "NLRL_RUNTIME_CRITIC_SHARD_SIZE": "12",
            "NLRL_EXECUTOR_MODEL": EXECUTOR_MODEL,
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


def start_job(job: RunJob, notes: str) -> RunJob:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job.log_path = LAUNCH_LOG_ROOT / f"{job.run_name}_{stamp}.log"
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
    if job.command == "train-local" and job.bootstrap:
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
        run_dir="(created by gaia cli after launch)",
        command=command_display(env, cmd),
        log_path=job.log_path,
        notes=notes,
    )
    log(
        f"started {job.key} pid={process.pid} lane={job.lane} "
        f"base_url={LANES[job.lane]} log={job.log_path}"
    )
    return job


def poll_job(job: RunJob) -> int | None:
    if job.process is None:
        return None
    code = job.process.poll()
    if code is None:
        return None
    if job.terminal_status is not None:
        return code
    if code == 0:
        job.terminal_status = "finished"
    elif run_has_required_states(job.run_name):
        job.terminal_status = "partial_tail_accepted"
        done, total = progress_for(job.run_name)
        log(f"accepting partial {job.key} after returncode={code}: {done}/{total}")
    else:
        job.terminal_status = "dead"
    update_process_row(job.process.pid, job.terminal_status)
    return code


def stop_job(job: RunJob, status: str) -> None:
    process = job.process
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        update_process_row(process.pid, status)
        log(f"stopped {job.key} pid={process.pid} status={status}")
    except ProcessLookupError:
        update_process_row(process.pid, "dead")
    except Exception as exc:
        log(f"failed to stop {job.key} pid={process.pid}: {exc}")


def monitor_jobs(jobs: list[RunJob]) -> None:
    for job in jobs:
        code = poll_job(job)
        if code is not None:
            if job.terminal_status == "dead":
                done, total = progress_for(job.run_name)
                raise RuntimeError(
                    f"{job.key} failed before enough states were available: "
                    f"returncode={code}, states={done}/{total}, required={min_required_states(total)}"
                )
            continue
        if job.process is None or job.terminal_status is not None:
            continue
        if not ALLOW_PARTIAL_TAIL:
            tail_seen.pop(job.key, None)
            continue
        if tail_ready(job):
            tail_seen[job.key] = tail_seen.get(job.key, 0) + 1
            if tail_seen[job.key] >= TAIL_GRACE_POLLS:
                stop_job(job, "stopped_tail_partial_for_lane_reuse")
        else:
            tail_seen.pop(job.key, None)


def active_lanes(jobs: list[RunJob]) -> set[str]:
    lanes: set[str] = set()
    for job in jobs:
        if job.process is not None and job.process.poll() is None:
            lanes.add(job.lane)
    return lanes


def choose_free_lane(jobs: list[RunJob], preferred: str | None = None) -> str | None:
    free = [lane for lane in LANES if lane not in active_lanes(jobs)]
    if preferred and preferred in free:
        return preferred
    return free[0] if free else None


def all_terminal(jobs: list[RunJob]) -> bool:
    return all(job.process is not None and job.process.poll() is not None for job in jobs)


def log_running_progress(jobs: list[RunJob]) -> None:
    for job in jobs:
        if job.process is None or job.process.poll() is not None:
            continue
        done, total = progress_for(job.run_name)
        if total:
            log(f"running {job.key}: {done}/{total} lane={job.lane}")


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
                f"ARCH-V5 {PROFILE_LABEL} fresh boot dev states with {EXECUTOR_MODEL} executor, "
                f"Codex CLI GPT-5.4 roles, and tail-threshold={TAIL_THRESHOLD} scheduling."
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
            "tool_profile": TOOL_PROFILE,
            "input_skill": str(boot_skill),
            "reward": to_dict(reward),
            "actor_decision": to_dict(decision),
            "skill_after_actor": str(skill_after / "SKILL.md"),
        },
    )
    log(f"offline {strategy}: produced {skill_after / 'SKILL.md'}")
    return skill_after / "SKILL.md"


def initial_jobs() -> list[RunJob]:
    jobs: list[RunJob] = []
    if INCLUDE_DIRECT:
        jobs.extend(
            [
                RunJob(
                    key="direct_dev",
                    run_name=DIRECT_DEV_RUN_NAME,
                    dataset=DEV_DATASET,
                    command="direct-eval-local",
                    lane=os.environ.get("GAIA_ARCHV5_DIRECT_DEV_LANE", "").strip() or LANE_NAMES[0],
                ),
                RunJob(
                    key="direct_test",
                    run_name=DIRECT_TEST_RUN_NAME,
                    dataset=TEST_DATASET,
                    command="direct-eval-local",
                    lane=os.environ.get("GAIA_ARCHV5_DIRECT_TEST_LANE", "").strip() or LANE_NAMES[min(1, len(LANE_NAMES) - 1)],
                ),
            ]
        )
    boot_lane = os.environ.get("GAIA_ARCHV5_BOOT_DEV_LANE", "").strip() or (
        LANE_NAMES[min(2, len(LANE_NAMES) - 1)] if INCLUDE_DIRECT else LANE_NAMES[0]
    )
    jobs.append(
        RunJob(
            key="boot_dev",
            run_name=BOOT_DEV_RUN_NAME,
            dataset=DEV_DATASET,
            command="train-local",
            lane=boot_lane,
            bootstrap=True,
        )
    )
    for job in jobs:
        if job.key == "direct_dev":
            notes = (
                f"ARCH-V5 {PROFILE_LABEL} direct validation_dev83 no-skill baseline; "
                f"tool_profile={TOOL_PROFILE}; executor={EXECUTOR_MODEL}; lane={job.lane}; Codex roles unused."
            )
        elif job.key == "direct_test":
            notes = (
                f"ARCH-V5 {PROFILE_LABEL} direct validation_test82 no-skill baseline; "
                f"tool_profile={TOOL_PROFILE}; executor={EXECUTOR_MODEL}; lane={job.lane}; Codex roles unused."
            )
        else:
            notes = (
                f"ARCH-V5 {PROFILE_LABEL} fresh bootstrap skill + validation_dev83 eval; "
                f"tool_profile={TOOL_PROFILE}; ROLE=Codex CLI GPT-5.4; "
                f"executor={EXECUTOR_MODEL}; lane={job.lane}."
            )
        start_job(job, notes=notes)
    return jobs


def eval_specs_for_a(skill: Path) -> list[EvalSpec]:
    return [
        EvalSpec(
            key="a_dev",
            run_name=A_DEV_RUN_NAME,
            dataset=DEV_DATASET,
            skill_path=skill,
            notes=f"ARCH-V5 {PROFILE_LABEL} A_full offline actor skill validation_dev83 eval; skill={skill}",
        ),
        EvalSpec(
            key="a_test",
            run_name=A_TEST_RUN_NAME,
            dataset=TEST_DATASET,
            skill_path=skill,
            notes=f"ARCH-V5 {PROFILE_LABEL} A_full offline actor skill validation_test82 eval; skill={skill}",
        ),
    ]


def eval_specs_for_b(skill: Path) -> list[EvalSpec]:
    return [
        EvalSpec(
            key="b_dev",
            run_name=B_DEV_RUN_NAME,
            dataset=DEV_DATASET,
            skill_path=skill,
            notes=f"ARCH-V5 {PROFILE_LABEL} B_sharded offline actor skill validation_dev83 eval; skill={skill}",
        ),
        EvalSpec(
            key="b_test",
            run_name=B_TEST_RUN_NAME,
            dataset=TEST_DATASET,
            skill_path=skill,
            notes=f"ARCH-V5 {PROFILE_LABEL} B_sharded offline actor skill validation_test82 eval; skill={skill}",
        ),
    ]


def start_eval_if_possible(jobs: list[RunJob], ready_specs: list[EvalSpec], eval_jobs: list[RunJob]) -> None:
    while ready_specs:
        lane = choose_free_lane(jobs)
        if lane is None:
            return
        spec = ready_specs.pop(0)
        job = RunJob(
            key=spec.key,
            run_name=spec.run_name,
            dataset=spec.dataset,
            command="train-local",
            lane=lane,
            skill_path=spec.skill_path,
        )
        start_job(job, notes=f"{spec.notes}; lane={lane}; {CONCURRENCY_LABEL}.")
        jobs.append(job)
        eval_jobs.append(job)


def run_schedule() -> None:
    jobs = initial_jobs()
    direct_jobs = [job for job in jobs if job.key.startswith("direct_")]
    boot_dev = next(job for job in jobs if job.key == "boot_dev")
    boot_test: RunJob | None = None
    boot_skill: Path | None = None
    boot_run_dir: Path | None = None
    offline_started = False
    pending_futures: dict[Future[Path], str] = {}
    ready_specs: list[EvalSpec] = []
    eval_jobs: list[RunJob] = []
    expected_eval_count = 0

    offline_workers = max(1, int(os.environ.get("GAIA_ARCHV5_OFFLINE_AB_WORKERS", "1")))
    with ThreadPoolExecutor(max_workers=offline_workers, thread_name_prefix="offline-ab") as pool:
        while True:
            monitor_jobs(jobs)

            if boot_skill is None:
                boot_skill = bootstrap_skill_path(BOOT_DEV_RUN_NAME)
                if boot_skill is not None:
                    log(f"bootstrap skill ready: {boot_skill}")

            if boot_skill is not None and boot_test is None:
                preferred_boot_test_lane = os.environ.get("GAIA_ARCHV5_BOOT_TEST_LANE", "").strip() or (
                    LANE_NAMES[min(3, len(LANE_NAMES) - 1)] if INCLUDE_DIRECT else LANE_NAMES[min(1, len(LANE_NAMES) - 1)]
                )
                lane = choose_free_lane(jobs, preferred=preferred_boot_test_lane)
                if lane is not None:
                    boot_test = RunJob(
                        key="boot_test",
                        run_name=BOOT_TEST_RUN_NAME,
                        dataset=TEST_DATASET,
                        command="train-local",
                        lane=lane,
                        skill_path=boot_skill,
                    )
                    start_job(
                        boot_test,
                        notes=(
                            f"ARCH-V5 {PROFILE_LABEL} fresh bootstrap skill validation_test82 eval; "
                            f"tool_profile={TOOL_PROFILE}; skill={boot_skill}; lane={lane}; {CONCURRENCY_LABEL}."
                        ),
                    )
                    jobs.append(boot_test)

            if not offline_started and run_has_required_states(BOOT_DEV_RUN_NAME):
                if AB_AFTER_BOTH_BOOT and (boot_test is None or not all_terminal([boot_test])):
                    log("boot_dev has enough states; waiting for boot_test to finish before offline A/B")
                    log_running_progress(jobs)
                    time.sleep(POLL_SECONDS)
                    continue
                if not ab_wait_pids_done():
                    log_running_progress(jobs)
                    time.sleep(POLL_SECONDS)
                    continue
                boot_run_dir = latest_run_dir(BOOT_DEV_RUN_NAME)
                if boot_run_dir is None:
                    raise RuntimeError("boot_dev run directory was not created.")
                if boot_skill is None:
                    boot_skill = bootstrap_skill_path(BOOT_DEV_RUN_NAME)
                if boot_skill is None:
                    raise RuntimeError("boot_dev states are ready but bootstrap skill is missing.")
                if boot_dev.process is not None and boot_dev.process.poll() is None and tail_ready(boot_dev):
                    stop_job(boot_dev, "stopped_tail_partial_for_ab")
                pending_futures[
                    pool.submit(
                        run_offline_variant,
                        strategy="full",
                        run_name=A_OFFLINE_RUN_NAME,
                        boot_run_dir=boot_run_dir,
                        boot_skill=boot_skill,
                    )
                ] = "a"
                pending_futures[
                    pool.submit(
                        run_offline_variant,
                        strategy="sharded",
                        run_name=B_OFFLINE_RUN_NAME,
                        boot_run_dir=boot_run_dir,
                        boot_skill=boot_skill,
                    )
                ] = "b"
                offline_started = True
                done, total = progress_for(BOOT_DEV_RUN_NAME)
                log(f"boot_dev states for AB ready: {done}/{total}; offline A/B submitted")

            for future, variant in list(pending_futures.items()):
                if not future.done():
                    continue
                del pending_futures[future]
                skill = future.result()
                if variant == "a":
                    log(f"A skill ready; queueing A dev/test: {skill}")
                    specs = eval_specs_for_a(skill)
                else:
                    log(f"B skill ready; queueing B dev/test: {skill}")
                    specs = eval_specs_for_b(skill)
                expected_eval_count += len(specs)
                ready_specs.extend(specs)

            start_eval_if_possible(jobs, ready_specs, eval_jobs)

            direct_done = all_terminal(direct_jobs)
            boot_dev_done_or_required = all_terminal([boot_dev]) or run_has_required_states(BOOT_DEV_RUN_NAME)
            boot_test_done = boot_test is not None and all_terminal([boot_test])
            offline_done = offline_started and not pending_futures
            eval_done = expected_eval_count == 4 and len(eval_jobs) == 4 and all_terminal(eval_jobs)
            if direct_done and boot_dev_done_or_required and boot_test_done and offline_done and eval_done:
                log(f"ARCH-V5 {PROFILE_LABEL} {SCHEDULE_LABEL} queue completed")
                return

            log_running_progress(jobs)
            if ready_specs:
                log(f"ready eval jobs waiting for a free lane: {[spec.key for spec in ready_specs]}")
            time.sleep(POLL_SECONDS)


def cleanup_model_servers() -> None:
    if REMOTE_EXECUTOR_BASE_URL:
        for lane in sorted(LANES):
            log(f"kept remote executor lane={lane} endpoint={LANES[lane]} model={EXECUTOR_MODEL}")
        return
    if not truthy_env("GAIA_ARCHV5_QUEUE_CLEANUP_SERVERS"):
        for lane in sorted(LANES):
            pid = lane_model_pid(lane)
            log(f"kept vLLM lane={lane} pid={pid or 'unknown'} endpoint={LANES[lane]}")
        return
    for lane in sorted(LANES):
        pid = lane_model_pid(lane)
        if pid is None:
            log(f"no vLLM pid found for cleanup lane={lane}")
            continue
        stop_pid(pid, f"stopped_after_archv5_{PROFILE_LABEL}_queue_{lane}")


def preflight() -> None:
    required = [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET, Path(CODEX_BIN)]
    if not REMOTE_EXECUTOR_BASE_URL:
        required.extend([VLLM_PYTHON, QWEN_MODEL])
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required paths: {missing}")
    tools = available_tool_names_for_profile(TOOL_PROFILE)
    if TOOL_PROFILE == "atomic_v4" and "read_json_file" in tools:
        raise RuntimeError(f"TOOL-V4 preflight failed: read_json_file is still visible in {TOOL_PROFILE}.")
    if TOOL_PROFILE == "atomic_v2" and "read_json_file" not in tools:
        raise RuntimeError(f"TOOL-V2 preflight failed: read_json_file is missing from {TOOL_PROFILE}.")
    if not truthy_env("GAIA_ARCHV5_SKIP_BOOTSTRAP_PREFLIGHT"):
        bootstrap_prompt = (ROOT / "prompts/bootstrap_skill_system.md").read_text(encoding="utf-8")
        if "Do not include a phase named `CONCLUDE`" not in bootstrap_prompt:
            raise RuntimeError("bootstrap_skill_system.md is not the no-CONCLUDE V5-2 bootstrap prompt.")


def main() -> int:
    refresh_process_registry()
    apply_search_runtime_env()
    os.environ.update(codex_role_env())
    os.environ["NLRL_RUNTIME_TOOL_PROFILE"] = TOOL_PROFILE
    preflight()
    if "--preflight-only" in sys.argv:
        log(
            f"preflight ok: tool_profile={TOOL_PROFILE}; "
            f"schedule={SCHEDULE_LABEL}; concurrency={CONCURRENCY}"
        )
        return 0
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
            "ARCH-V5.2 no-CONCLUDE bootstrap graph with any-phase runtime answer acceptance; "
            f"tool_profile={TOOL_PROFILE}; schedule={SCHEDULE_LABEL}; "
            f"ab_after_both_boot={AB_AFTER_BOTH_BOOT}; "
            f"answer_policy={os.environ.get('NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY', 'conclude_only')}; "
            "fresh Codex CLI GPT-5.4 bootstrap and A/B; "
            f"executor={EXECUTOR_MODEL}; lanes={','.join(LANES)}; {CONCURRENCY_LABEL}; "
            f"partial tail allowed={ALLOW_PARTIAL_TAIL}; "
            f"wait_before_start_pids={os.environ.get('GAIA_ARCHV5_QUEUE_WAIT_FOR_PIDS', '').strip() or 'none'}; "
            f"ab_wait_for_pids={os.environ.get('GAIA_ARCHV5_QUEUE_AB_WAIT_FOR_PIDS', '').strip() or 'none'}."
        ),
        kind="experiment_queue",
    )
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        wait_for_prior_pids()
        for lane in sorted(LANES):
            start_vllm(lane)
        wait_vllm_ready()
        for lane in sorted(LANES):
            pid = lane_model_pid(lane)
            log(f"model lane={lane} endpoint={LANES[lane]} pid={pid or 'remote'}")
        run_schedule()
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception:
        status = "dead"
        raise
    finally:
        cleanup_model_servers()
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
