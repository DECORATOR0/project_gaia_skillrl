from __future__ import annotations

import csv
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")
LAUNCH_LOG_ROOT = ROOT / "runs/_launch_logs"
MANIFEST = ROOT / "runs/_queue_logs/20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_manifest.json"
MASTER_PID = 3220312

RUN_NAME = "20260507_0922_missing_tail_fill_9b_bootv4_dependency_fill_dev_missing2_c1_retry_after_9b_drain"
CHILD_LOG = LAUNCH_LOG_ROOT / f"{RUN_NAME}.log"
SKILL = (
    ROOT
    / "runs/2026/5/2026-5-5/20260505_005556_9b_bootv4_archv53_fresh_bootv4_boot_dev_c20/"
    / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
)
TASK_IDS = [
    "14569e28-c88c-43e4-8c32-097d35b9a67d",
    "9b54f9d9-35ee-4a14-b62f-d130ea00317f",
]

sys.path.insert(0, str(ROOT))
from gaia_skillrl.search_config import apply_search_runtime_env  # noqa: E402


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def append_csv_row(*, kind: str, name: str, pid: int, run_dir: str, command: str, log_path: Path, notes: str) -> None:
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
                str(ROOT),
                run_dir,
                command,
                "",
                notes,
                str(log_path),
            ]
        )


def update_csv_row(pid: int, status: str, *, exit_code: int | None = None, run_dir: str | None = None, note: str = "") -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 6 or row[5].strip() != str(pid):
            continue
        while len(row) < 15:
            row.append("")
        row[1] = now()
        row[2] = status
        if status != "running" and not row[7].strip():
            row[7] = now()
        if run_dir is not None:
            row[9] = run_dir
        if exit_code is not None:
            row[11] = str(exit_code)
        if note:
            row[12] = f"{row[12]}; {note}" if row[12].strip() else note
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def manifest_state() -> tuple[int, int]:
    if not MANIFEST.exists():
        return 99, 99
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pending_9b = 0
    running_9b = 0
    for item in data.get("pending", []):
        if str(item.get("model_key", "")).lower() == "9b":
            pending_9b += 1
    for item in data.get("running", []):
        lane = item.get("lane", {})
        if str(lane.get("model_key", "")).lower() == "9b":
            running_9b += 1
    return pending_9b, running_9b


def live_9b_eval_count() -> int:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast_9b_.*eval"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return 99
    count = 0
    for line in result.stdout.splitlines():
        if "pgrep -af" in line:
            continue
        if "gaia_skillrl.cli" in line:
            count += 1
    return count


def build_env() -> dict[str, str]:
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
            "NLRL_RUNTIME_INITIAL_SKILL_PATH": str(SKILL),
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": "24",
            "NLRL_RUNTIME_TASK_CONCURRENCY": "1",
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": "1",
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
            "NLRL_CRITIC_SHARD_CONCURRENCY": "1",
            "NLRL_EXECUTOR_MODEL": "Qwen3.5-9B-local",
            "NLRL_EXECUTOR_BASE_URL": "http://127.0.0.1:18120/v1",
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_EXECUTOR_MAX_TOKENS": "12288",
            "NLRL_EXECUTOR_TOKENIZER_PATH": "/data/xsy/codes/checkpoints/Qwen3.5-9B",
            "NLRL_EXECUTOR_MAX_MODEL_LEN": "49152",
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": "512",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_ACTOR_MODEL": os.environ.get("GAIA_24EXP_ACTOR_MODEL", "gpt-5.4"),
            "NLRL_ACTOR_BASE_URL": "codex-cli",
            "NLRL_ACTOR_API_KEY": "EMPTY",
            "NLRL_ACTOR_API_MODE": "codex_cli",
            "NLRL_ACTOR_TIMEOUT_SECONDS": "3600",
            "NLRL_CRITIC_MODEL": os.environ.get("GAIA_24EXP_CRITIC_MODEL", "gpt-5.4"),
            "NLRL_CRITIC_BASE_URL": "codex-cli",
            "NLRL_CRITIC_API_KEY": "EMPTY",
            "NLRL_CRITIC_API_MODE": "codex_cli",
            "NLRL_CRITIC_TIMEOUT_SECONDS": "3600",
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": "3600",
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


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    keys = [
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_WEB_SEARCH_PROVIDER",
        "HTTP_PROXY",
        "HTTPS_PROXY",
    ]
    env_parts = [f"{key}={shlex.quote(env[key])}" for key in keys if env.get(key) is not None]
    return "nohup env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]]) + f" > {shlex.quote(str(CHILD_LOG))} 2>&1 &"


def latest_run_dir() -> str:
    matches = list((ROOT / "runs").glob(f"**/{RUN_NAME}"))
    if not matches:
        suffix = "_" + RUN_NAME.split("_", 2)[-1]
        matches = [path for path in (ROOT / "runs").glob(f"**/*{suffix}") if path.is_dir()]
    return str(max(matches, key=lambda path: path.stat().st_mtime)) if matches else ""


def main() -> int:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    append_csv_row(
        kind="gaia_tail_fill_waiter",
        name="20260507_0922_9b_dependency_fill_wait_until_9b_drain",
        pid=os.getpid(),
        run_dir="",
        command=" ".join(shlex.quote(part) for part in sys.argv),
        log_path=Path(os.environ.get("WAITER_LOG", "")),
        notes="waits until remain20 has no pending/running 9B evals or master exits, then retries 9B BOOT-V4 missing dependency fill c1",
    )
    while True:
        pending_9b, running_9b = manifest_state()
        master_alive = is_alive(MASTER_PID)
        live_9b = live_9b_eval_count()
        if not master_alive:
            pending_9b = 0
            running_9b = live_9b
        log(
            f"poll pending_9b={pending_9b} running_9b={running_9b} live_9b={live_9b} "
            f"master_alive={master_alive}"
        )
        if running_9b == 0 and (pending_9b == 0 or not master_alive):
            break
        time.sleep(60)

    env = build_env()
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "train-local",
        "--dataset-path",
        str(DEV_DATASET),
        "--run-name",
        RUN_NAME,
    ]
    for task_id in TASK_IDS:
        cmd.extend(["--task-id", task_id])
    with CHILD_LOG.open("a", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            ["nohup", *cmd],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    display = command_display(env, cmd)
    append_csv_row(
        kind="gaia_tail_fill",
        name=RUN_NAME,
        pid=proc.pid,
        run_dir="",
        command=display,
        log_path=CHILD_LOG,
        notes="deferred retry for 9B BOOT-V4 dependency fill missing2 after 9B pressure drains",
    )
    log(f"launched child pid={proc.pid} log={CHILD_LOG}")
    exit_code = proc.wait()
    status = "completed" if exit_code == 0 else "failed"
    update_csv_row(proc.pid, status, exit_code=exit_code, run_dir=latest_run_dir())
    update_csv_row(os.getpid(), "completed", exit_code=0, note=f"child_pid={proc.pid} child_exit={exit_code}")
    log(f"child exited {exit_code}")
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        update_csv_row(os.getpid(), "stopped", exit_code=130)
        raise
