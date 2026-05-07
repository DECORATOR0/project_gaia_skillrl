from __future__ import annotations

import csv
import os
import re
import shlex
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"

DEV_RUN_NAME = os.environ["GAIA_BOOT_TEST_WATCH_DEV_RUN_NAME"]
TEST_RUN_NAME = os.environ["GAIA_BOOT_TEST_WATCH_TEST_RUN_NAME"]
DEV_PID = int(os.environ.get("GAIA_BOOT_TEST_WATCH_DEV_PID", "0") or "0")
BASE_URL = os.environ.get("GAIA_BOOT_TEST_WATCH_EXECUTOR_BASE_URL", "http://127.0.0.1:8103/v1")
CONCURRENCY = int(os.environ.get("GAIA_BOOT_TEST_WATCH_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_BOOT_TEST_WATCH_POLL_SECONDS", "60"))
QUEUE_NAME = os.environ.get("GAIA_BOOT_TEST_WATCH_NAME", f"{TEST_RUN_NAME}_watcher")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run_name_suffix_for_match(run_name: str) -> str:
    match = re.match(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_(.+)$", run_name)
    if match:
        return "_" + match.group(1)
    return run_name


def run_dir_for(run_name: str) -> Path | None:
    suffix = run_name_suffix_for_match(run_name)
    matches = [
        path
        for pattern in (f"**/{run_name}", f"**/*{suffix}")
        for path in RUN_ROOT.glob(pattern)
        if path.is_dir()
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


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
    rows = list(csv.reader(PROCESS_CSV.open("r", encoding="utf-8-sig", newline="")))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[5] != str(pid):
            continue
        row[1] = now()
        row[2] = status
        if status != "running" and not row[7]:
            row[7] = now()
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    keys = [
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    env_text = " ".join(f"{key}={shlex.quote(env[key])}" for key in keys if env.get(key))
    return "env " + env_text + " " + " ".join(shlex.quote(part) for part in cmd)


def build_env(skill_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("ALL_PROXY", "all_proxy"):
        env.pop(key, None)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_INITIAL_SKILL_PATH": str(skill_path),
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": "24",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_EXECUTOR_MODEL": "Qwen3.5-9B-local",
            "NLRL_EXECUTOR_BASE_URL": BASE_URL,
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
            "HTTP_PROXY": os.environ.get("HTTP_PROXY", "http://127.0.0.1:17890"),
            "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:17890"),
            "NO_PROXY": os.environ.get("NO_PROXY", "127.0.0.1,localhost,0.0.0.0"),
        }
    )
    return env


def main() -> int:
    queue_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", LAUNCH_LOG_ROOT / f"{QUEUE_NAME}.log"))
    append_process_row(
        kind="experiment_queue",
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(boot_test watcher)",
        command=" ".join(shlex.quote(part) for part in [str(PYTHON), "-u", __file__]),
        log_path=queue_log,
        notes=f"Wait for {DEV_RUN_NAME} pid={DEV_PID} then launch {TEST_RUN_NAME} on {BASE_URL}.",
    )
    status = "finished"
    try:
        while DEV_PID and pid_alive(DEV_PID):
            log(f"waiting dev pid={DEV_PID} before boot_test")
            time.sleep(POLL_SECONDS)
        while True:
            dev_dir = run_dir_for(DEV_RUN_NAME)
            skill = None if dev_dir is None else dev_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
            if skill is not None and skill.exists():
                break
            log(f"waiting bootstrap skill for {DEV_RUN_NAME}")
            time.sleep(POLL_SECONDS)

        log_path = LAUNCH_LOG_ROOT / f"{TEST_RUN_NAME}_watcher_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        cmd = [
            str(PYTHON),
            "-m",
            "gaia_skillrl.cli",
            "--config",
            str(CONFIG),
            "train-local",
            "--dataset-path",
            str(TEST_DATASET),
            "--run-name",
            TEST_RUN_NAME,
        ]
        env = build_env(skill)
        LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as handle:
            proc = subprocess.Popen(
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
            name=TEST_RUN_NAME,
            pid=proc.pid,
            cwd=ROOT,
            run_dir="(created by trainer after launch)",
            command=command_display(env, cmd),
            log_path=log_path,
            notes=f"boot_test launched by watcher after {DEV_RUN_NAME}; skill={skill}",
        )
        log(f"started boot_test pid={proc.pid} log={log_path}")
        code = proc.wait()
        update_process_row(proc.pid, "finished" if code == 0 else "dead")
        if code != 0:
            raise RuntimeError(f"boot_test failed returncode={code}; log={log_path}")
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception:
        status = "dead"
        raise
    finally:
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
