from __future__ import annotations

import csv
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import gaia_queue_guard as queue_guard  # noqa: E402
from gaia_skillrl.search_config import apply_search_runtime_env, build_search_runtime_env, load_search_runtime_config  # noqa: E402

PYTHON = ROOT / ".venv/bin/python"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
STATUS_FILE = RUN_ROOT / "_codex_tasks/20260503_qwen35_9b_remote_master_status.md"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"

SEARCH_RUNTIME_CONFIG = load_search_runtime_config()
SEARCH_RUNTIME_ENV = build_search_runtime_env(SEARCH_RUNTIME_CONFIG)
SERPER_API_KEY = SEARCH_RUNTIME_ENV.get("NLRL_SERPER_API_KEY", "")
SERPER_ENDPOINT = SEARCH_RUNTIME_ENV.get("NLRL_SERPER_SEARCH_ENDPOINT", "https://google.serper.dev/search")
REMOTE_EXECUTOR_BASE_URL = os.environ.get("GAIA_QWEN35_REMOTE_BASE_URL", "http://127.0.0.1:18000/v1").strip()
REMOTE_EXECUTOR_MODEL = os.environ.get("GAIA_QWEN35_REMOTE_MODEL", "qwen3.5-9b").strip()

STAMP = os.environ.get("GAIA_QWEN35_MASTER_PREFIX", "").strip() or datetime.now().strftime("%Y%m%d_%H%M")
MAX_VERSION_QUEUES = max(1, int(os.environ.get("GAIA_QWEN35_MASTER_MAX_VERSION_QUEUES", "2")))
REMOTE_LANES_PER_QUEUE = max(1, int(os.environ.get("GAIA_QWEN35_REMOTE_LANES_PER_QUEUE", "3")))
CONCURRENCY = int(os.environ.get("GAIA_QWEN35_QUEUE_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_QWEN35_MASTER_POLL_SECONDS", "60"))
SKIP_SMOKE = os.environ.get("GAIA_QWEN35_MASTER_SKIP_SMOKE", "").strip().lower() in {"1", "true", "yes", "on"}
LONGTAIL_FRACTION = float(os.environ.get("GAIA_QWEN35_MASTER_LONGTAIL_FRACTION", "0.95"))
LONGTAIL_MAX_MISSING = int(os.environ.get("GAIA_QWEN35_MASTER_LONGTAIL_MAX_MISSING", "2"))
LONGTAIL_STALLED_SECONDS = int(os.environ.get("GAIA_QWEN35_MASTER_LONGTAIL_STALLED_SECONDS", "900"))

DIRECT_BASELINE_RUN_NAME = f"{STAMP}_qwen35_9b_serper_direct_test82_c{CONCURRENCY}"
SMOKE_RUN_NAME = f"{STAMP}_qwen35_9b_serper_smoke_direct_max1"

BASE_ARCHV5_QUEUE = ROOT / "scripts/run_gaia_archv5_toolv4_direct_boot_ab_queue.py"
FRESH_BOOT_QUEUE = ROOT / "scripts/run_gaia_fresh_bootstrap_ab_tail_queue.py"

ARCHV4_CONFIG = ROOT / "configs/system_archv4_799deda_prompts_20260503.json"
ARCHV5_CONCLUDE_CONFIG = ROOT / "configs/system_archv5_conclude_prompts_20260503.json"
ARCHV52_CONFIG = ROOT / "configs/system.json"


@dataclass
class ManagedProcess:
    label: str
    pid: int
    log_path: Path
    command: str
    prefix: str
    kind: str
    process: subprocess.Popen[str] | None = None
    status: str = "running"
    returncode: int | None = None


@dataclass
class QueueSpec:
    label: str
    prefix_suffix: str
    script: Path
    env: dict[str, str] = field(default_factory=dict)


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


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


def ensure_process_csv_header() -> None:
    if PROCESS_CSV.exists():
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
                "recorded_at",
                "notes",
                "log_path",
            ]
        )


def read_process_rows() -> list[list[str]]:
    if not PROCESS_CSV.exists():
        return []
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def write_process_rows(rows: list[list[str]]) -> None:
    with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def refresh_process_registry() -> None:
    rows = read_process_rows()
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
        write_process_rows(rows)


def append_process_row(
    *,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
    kind: str,
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


def update_process_row(pid: int, status: str) -> None:
    rows = read_process_rows()
    if not rows:
        return
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
        write_process_rows(rows)


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_WEB_SEARCH_PROVIDER",
        "NLRL_WEB_SEARCH_FALLBACK_PROVIDER",
        "NLRL_SERPER_API_KEY",
        "NLRL_SERPER_SEARCH_ENDPOINT",
        "NLRL_SERPER_SEARCH_TIMEOUT_SECONDS",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_API_KEY",
        "NLRL_EXECUTOR_STREAM",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS",
        "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS",
        "GAIA_ARCHV5_CONFIG",
        "GAIA_BOOTV3_CONFIG",
        "GAIA_ARCHV5_QUEUE_PREFIX",
        "GAIA_FRESH_QUEUE_PREFIX",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URL",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_LANES",
        "GAIA_ARCHV5_EXECUTOR_MODEL",
        "GAIA_ARCHV5_QUEUE_INCLUDE_DIRECT",
        "GAIA_ARCHV5_QUEUE_PROFILE_LABEL",
        "GAIA_ARCHV5_SKIP_BOOTSTRAP_PREFLIGHT",
        "GAIA_ARCHV5_OFFLINE_AB_WORKERS",
        "GAIA_FRESH_QUEUE_SKIP_BOOTSTRAP_PREFLIGHT",
        "GAIA_FRESH_OFFLINE_AB_WORKERS",
        "NLRL_QUEUE_LOG_PATH",
    ]
    return {key: env[key] for key in keys if env.get(key)}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    cmd_parts = [shlex.quote(part) for part in cmd]
    return "env " + " ".join([*env_parts, *cmd_parts])


def common_env() -> dict[str, str]:
    env = {
        "PYTHONUNBUFFERED": "1",
        "PYTHONFAULTHANDLER": "1",
        "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
        "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
        "NLRL_EXECUTOR_MODEL": REMOTE_EXECUTOR_MODEL,
        "NLRL_EXECUTOR_BASE_URL": REMOTE_EXECUTOR_BASE_URL,
        "NLRL_EXECUTOR_API_KEY": "EMPTY",
        "NLRL_EXECUTOR_API_MODE": "chat_completions",
        "NLRL_EXECUTOR_STREAM": "1",
        "NLRL_EXECUTOR_ENABLE_THINKING": "1",
        "NLRL_EXECUTOR_TEMPERATURE": "0.1",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS": "720",
        "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "720",
        "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
        "GAIA_ARCHV5_REMOTE_EXECUTOR_BASE_URL": REMOTE_EXECUTOR_BASE_URL,
        "GAIA_ARCHV5_REMOTE_EXECUTOR_LANES": str(REMOTE_LANES_PER_QUEUE),
        "GAIA_ARCHV5_EXECUTOR_MODEL": REMOTE_EXECUTOR_MODEL,
        "GAIA_ARCHV5_QUEUE_CONCURRENCY": str(CONCURRENCY),
        "GAIA_ARCHV5_QUEUE_TAIL_THRESHOLD": "3",
        "GAIA_ARCHV5_QUEUE_ALLOW_PARTIAL_TAIL": "1",
        "GAIA_ARCHV5_QUEUE_TAIL_GRACE_POLLS": "3",
        "GAIA_ARCHV5_QUEUE_INCLUDE_DIRECT": "0",
        "GAIA_ARCHV5_OFFLINE_AB_WORKERS": "1",
        "GAIA_FRESH_QUEUE_CONCURRENCY": str(CONCURRENCY),
        "GAIA_FRESH_QUEUE_TAIL_THRESHOLD": "3",
        "GAIA_FRESH_QUEUE_ALLOW_PARTIAL_TAIL": "1",
        "GAIA_FRESH_QUEUE_TAIL_GRACE_POLLS": "3",
        "GAIA_FRESH_OFFLINE_AB_WORKERS": "1",
    }
    apply_search_runtime_env(env)
    return env


def latest_run_dir(run_name: str) -> Path | None:
    matches: list[Path] = []
    for pattern in (f"**/{run_name}", f"**/*_{run_name}"):
        matches.extend(path for path in RUN_ROOT.glob(pattern) if path.is_dir())
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def smoke_has_content(run_name: str) -> tuple[bool, str]:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return False, "run directory not found"
    states = sorted(run_dir.glob("iteration_*/*/state.json"))
    if not states:
        return False, f"state.json not found in {run_dir}"
    data = json.loads(states[0].read_text(encoding="utf-8"))
    env_result = data.get("env_result", {})
    raw_output = str(env_result.get("raw_executor_output") or "")
    final_answer = str(env_result.get("final_answer") or "")
    if raw_output.strip() or final_answer.strip():
        return True, f"state={states[0]} final_answer={final_answer!r} raw_len={len(raw_output)}"
    return False, f"empty raw output and final_answer in {states[0]}"


def managed_run_progress(item: ManagedProcess, run_name: str) -> dict[str, object]:
    live = poll_process(item) == "running"
    progress = queue_guard.state_progress(latest_run_dir(run_name))
    payload = queue_guard.progress_payload(
        item.label,
        progress,
        live=live,
        fraction=LONGTAIL_FRACTION,
        max_missing=LONGTAIL_MAX_MISSING,
        stalled_seconds=LONGTAIL_STALLED_SECONDS,
    )
    payload.update(
        {
            "run_name": run_name,
            "pid": item.pid,
            "status": item.status,
        }
    )
    return payload


def process_blocks_master(item: ManagedProcess, run_name: str) -> bool:
    progress = managed_run_progress(item, run_name)
    return bool(progress["live"]) and not bool(progress["longtail_ready"])


def spawn_process(
    *,
    label: str,
    prefix: str,
    cmd: list[str],
    env_updates: dict[str, str],
    log_path: Path,
    kind: str,
    notes: str,
) -> ManagedProcess:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(env_updates)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
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
    command = command_display(env, cmd)
    append_process_row(
        name=label,
        pid=process.pid,
        cwd=ROOT,
        run_dir="(created by qwen35 9b master)",
        command=command,
        log_path=log_path,
        notes=notes,
        kind=kind,
    )
    log(f"started {label} pid={process.pid} log={log_path}")
    return ManagedProcess(
        label=label,
        pid=process.pid,
        log_path=log_path,
        command=command,
        prefix=prefix,
        kind=kind,
        process=process,
    )


def poll_process(item: ManagedProcess) -> str:
    if item.status != "running":
        return item.status
    if item.process is not None:
        code = item.process.poll()
        if code is None:
            return "running"
        item.returncode = code
        item.status = "finished" if code == 0 else "dead"
        update_process_row(item.pid, item.status)
        return item.status
    if pid_alive(item.pid):
        return "running"
    item.status = "dead"
    update_process_row(item.pid, "dead")
    return item.status


def wait_for_process(item: ManagedProcess, *, expect_content_run: str | None = None) -> None:
    while poll_process(item) == "running":
        refresh_process_registry()
        write_status("smoke_wait", [item], [], [])
        time.sleep(10)
    if item.returncode not in (0, None):
        raise RuntimeError(f"{item.label} failed with returncode={item.returncode}; log={item.log_path}")
    if expect_content_run:
        ok, detail = smoke_has_content(expect_content_run)
        if not ok:
            raise RuntimeError(f"smoke finished but content validation failed: {detail}")
        log(f"smoke content ok: {detail}")


def launch_direct(label: str, run_name: str, *, max_tasks: int | None) -> ManagedProcess:
    env = common_env()
    if max_tasks is not None:
        env["NLRL_RUNTIME_TASK_CONCURRENCY"] = str(max_tasks)
        env["NLRL_LLM_MAX_CONCURRENT_REQUESTS"] = str(max_tasks)
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(ARCHV52_CONFIG),
        "direct-eval-local",
        "--dataset-path",
        str(TEST_DATASET),
        "--run-name",
        run_name,
    ]
    if max_tasks is not None:
        cmd.extend(["--max-tasks", str(max_tasks)])
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    return spawn_process(
        label=label,
        prefix=run_name,
        cmd=cmd,
        env_updates=env,
        log_path=log_path,
        kind="experiment",
        notes=(
            f"Qwen3.5-9B remote direct eval; provider=serper; model={REMOTE_EXECUTOR_MODEL}; "
            f"base_url={REMOTE_EXECUTOR_BASE_URL}; max_tasks={max_tasks or 'all'}."
        ),
    )


def launch_queue(spec: QueueSpec) -> ManagedProcess:
    prefix = f"{STAMP}_{spec.prefix_suffix}"
    env = common_env()
    env.update(spec.env)
    if spec.script == FRESH_BOOT_QUEUE:
        env["GAIA_FRESH_QUEUE_PREFIX"] = prefix
    else:
        env["GAIA_ARCHV5_QUEUE_PREFIX"] = prefix
    log_path = LAUNCH_LOG_ROOT / f"{prefix}_{spec.label}_queue_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    env["NLRL_QUEUE_LOG_PATH"] = str(log_path)
    return spawn_process(
        label=f"{prefix}_{spec.label}_queue",
        prefix=prefix,
        cmd=[str(PYTHON), str(spec.script)],
        env_updates=env,
        log_path=log_path,
        kind="experiment_queue",
        notes=(
            f"{spec.label}; Qwen3.5-9B remote executor; Serper primary; "
            f"max_version_queues={MAX_VERSION_QUEUES}; remote_lanes_per_queue={REMOTE_LANES_PER_QUEUE}; "
            "offline_ab_workers=1."
        ),
    )


def queue_specs() -> list[QueueSpec]:
    return [
        QueueSpec(
            label="archv4",
            prefix_suffix="archv4_r6_qwen35_9b_serper",
            script=FRESH_BOOT_QUEUE,
            env={
                "GAIA_BOOTV3_CONFIG": str(ARCHV4_CONFIG),
                "GAIA_FRESH_QUEUE_SKIP_BOOTSTRAP_PREFLIGHT": "1",
                "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "conclude_only",
            },
        ),
        QueueSpec(
            label="archv5_strict",
            prefix_suffix="archv5_strict_qwen35_9b_serper",
            script=BASE_ARCHV5_QUEUE,
            env={
                "GAIA_ARCHV5_CONFIG": str(ARCHV5_CONCLUDE_CONFIG),
                "GAIA_ARCHV5_QUEUE_PROFILE_LABEL": "v5_strict_toolv2",
                "GAIA_ARCHV5_SKIP_BOOTSTRAP_PREFLIGHT": "1",
                "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "conclude_only",
            },
        ),
        QueueSpec(
            label="archv5_1_anyphase",
            prefix_suffix="archv5_1_anyphase_qwen35_9b_serper",
            script=BASE_ARCHV5_QUEUE,
            env={
                "GAIA_ARCHV5_CONFIG": str(ARCHV5_CONCLUDE_CONFIG),
                "GAIA_ARCHV5_QUEUE_PROFILE_LABEL": "v5-1_anyphase_toolv2",
                "GAIA_ARCHV5_SKIP_BOOTSTRAP_PREFLIGHT": "1",
                "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            },
        ),
        QueueSpec(
            label="archv5_2_noconclude",
            prefix_suffix="archv5_2_noconclude_qwen35_9b_serper",
            script=BASE_ARCHV5_QUEUE,
            env={
                "GAIA_ARCHV5_CONFIG": str(ARCHV52_CONFIG),
                "GAIA_ARCHV5_QUEUE_PROFILE_LABEL": "v5-2_noconclude_toolv2",
                "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            },
        ),
    ]


def write_status(
    phase: str,
    all_processes: list[ManagedProcess],
    pending: list[QueueSpec],
    failures: list[str],
    progress: list[dict[str, object]] | None = None,
) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Qwen3.5-9B Remote Master Queue Status",
        "",
        f"- updated_at: `{now()}`",
        f"- phase: `{phase}`",
        f"- remote_executor: `{REMOTE_EXECUTOR_BASE_URL}`",
        f"- model: `{REMOTE_EXECUTOR_MODEL}`",
        f"- serper_key: `{SERPER_API_KEY}`",
        f"- concurrency: child_c{CONCURRENCY}, max_version_queues={MAX_VERSION_QUEUES}, remote_lanes_per_queue={REMOTE_LANES_PER_QUEUE}",
        "",
        "## Processes",
    ]
    if all_processes:
        for item in all_processes:
            lines.append(
                f"- {item.label}: pid=`{item.pid}` status=`{item.status}` prefix=`{item.prefix}` log=`{item.log_path}`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Pending"])
    if pending:
        lines.extend(f"- {item.label}" for item in pending)
    else:
        lines.append("- none")
    if failures:
        lines.extend(["", "## Failures", *[f"- {item}" for item in failures]])
    if progress:
        lines.extend(["", "## Long-Tail Progress"])
        for item in progress:
            lines.append(
                f"- {item['label']}: run=`{item['run_name']}` pid=`{item['pid']}` "
                f"live=`{item['live']}` longtail_ready=`{item['longtail_ready']}` "
                f"states=`{item['landed']}/{item['total'] or '?'}` last_state_at=`{item['last_state_at']}`"
            )
    STATUS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stop_process_group(pid: int, status: str) -> None:
    if not pid_alive(pid):
        update_process_row(pid, "dead")
        return
    try:
        os.killpg(pid, signal.SIGTERM)
        update_process_row(pid, status)
    except ProcessLookupError:
        update_process_row(pid, "dead")


def preflight() -> None:
    required = [
        PYTHON,
        DEV_DATASET,
        TEST_DATASET,
        BASE_ARCHV5_QUEUE,
        FRESH_BOOT_QUEUE,
        ARCHV4_CONFIG,
        ARCHV5_CONCLUDE_CONFIG,
        ARCHV52_CONFIG,
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required paths: {missing}")
    result = subprocess.run(
        ["curl", "-sS", "--max-time", "5", REMOTE_EXECUTOR_BASE_URL.rstrip("/") + "/models"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0 or REMOTE_EXECUTOR_MODEL not in result.stdout:
        raise RuntimeError(f"Remote executor preflight failed: {result.stdout[:500]}")


def main() -> int:
    refresh_process_registry()
    preflight()
    if "--preflight-only" in os.sys.argv:
        log("preflight ok")
        return 0
    master_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", str(LAUNCH_LOG_ROOT / f"{STAMP}_qwen35_9b_remote_master.log")))
    append_process_row(
        name=f"{STAMP}_qwen35_9b_remote_master_queue",
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(master queue manager)",
        command=command_display(os.environ, [str(PYTHON), *os.sys.argv]),
        log_path=master_log,
        notes=(
            "Master watchdog for baseline + ARCH-V4/V5/V5.1/V5.2 boot+AB queues; "
            f"remote_executor={REMOTE_EXECUTOR_BASE_URL}; Serper key={SERPER_API_KEY}; "
            f"max_version_queues={MAX_VERSION_QUEUES}."
        ),
        kind="experiment_queue",
    )
    all_processes: list[ManagedProcess] = []
    active_versions: list[ManagedProcess] = []
    pending = queue_specs()
    failures: list[str] = []
    baseline_failure_recorded = False
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        if not SKIP_SMOKE:
            smoke = launch_direct("qwen35_9b_serper_smoke_direct_max1", SMOKE_RUN_NAME, max_tasks=1)
            all_processes.append(smoke)
            write_status("smoke_running", all_processes, pending, failures)
            wait_for_process(smoke, expect_content_run=SMOKE_RUN_NAME)
        else:
            log("smoke skipped by GAIA_QWEN35_MASTER_SKIP_SMOKE=1")

        baseline = launch_direct("qwen35_9b_serper_direct_test82_baseline", DIRECT_BASELINE_RUN_NAME, max_tasks=None)
        all_processes.append(baseline)

        while pending or active_versions or process_blocks_master(baseline, DIRECT_BASELINE_RUN_NAME):
            baseline_progress = managed_run_progress(baseline, DIRECT_BASELINE_RUN_NAME)
            refresh_process_registry()
            for item in list(active_versions):
                state = poll_process(item)
                if state == "running":
                    continue
                active_versions.remove(item)
                if item.returncode not in (0, None):
                    failures.append(f"{item.label}: returncode={item.returncode}; log={item.log_path}")
                log(f"version queue exited: {item.label} pid={item.pid}")

            if baseline.status != "running" and not baseline_failure_recorded:
                if baseline.returncode not in (0, None):
                    failures.append(f"{baseline.label}: returncode={baseline.returncode}; log={baseline.log_path}")
                baseline_failure_recorded = True
                log(f"baseline exited: pid={baseline.pid}")
            elif baseline_progress["live"] and baseline_progress["longtail_ready"]:
                log(
                    "baseline reached long-tail; master will not wait for final summary: "
                    f"{baseline_progress['landed']}/{baseline_progress['total'] or '?'} states"
                )

            while pending and len(active_versions) < MAX_VERSION_QUEUES:
                spec = pending.pop(0)
                try:
                    launched = launch_queue(spec)
                except Exception as exc:
                    failures.append(f"{spec.label}: launch failed: {exc}")
                    continue
                active_versions.append(launched)
                all_processes.append(launched)

            write_status("running", all_processes, pending, failures, [baseline_progress])
            if pending or active_versions or process_blocks_master(baseline, DIRECT_BASELINE_RUN_NAME):
                time.sleep(POLL_SECONDS)

        baseline_progress = managed_run_progress(baseline, DIRECT_BASELINE_RUN_NAME)
        if baseline.status != "running" and not baseline_failure_recorded and baseline.returncode not in (0, None):
            failures.append(f"{baseline.label}: returncode={baseline.returncode}; log={baseline.log_path}")
        if failures:
            final_status = "completed_with_failures"
        elif baseline_progress["live"] and baseline_progress["longtail_ready"]:
            final_status = "completed_with_longtail_baseline_live"
        else:
            final_status = "completed"
        write_status(final_status, all_processes, pending, failures, [baseline_progress])
        return 0 if not failures else 1
    except KeyboardInterrupt:
        status = "stopped"
        failures.append("master stopped by signal")
        for item in all_processes:
            if item.status == "running":
                stop_process_group(item.pid, "stopped_by_master")
        write_status("stopped", all_processes, pending, failures)
        return 130
    except Exception as exc:
        status = "dead"
        failures.append(str(exc))
        write_status("error", all_processes, pending, failures)
        raise
    finally:
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
