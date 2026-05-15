from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
SCRIPTS = ROOT / "scripts"
QUEUE_LOG_ROOT = ROOT / "runs/_queue_logs"

STAMP = os.environ.get(
    "GAIA_8B_BOOTV6_SSSAI52_RERUN_STAMP",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv6_sssai52_high_xhigh_rerun2_remote132"),
).strip()

HIGH_PREFIX = f"{STAMP}_bootonly_high"
XHIGH_PREFIX = f"{STAMP}_bootonly_xhigh"
EVAL_PREFIX = f"{STAMP}_eval_shards"

SUMMARY_JSON = QUEUE_LOG_ROOT / f"{STAMP}_pipeline_summary.json"
HIGH_LOG = QUEUE_LOG_ROOT / f"{STAMP}_boot_high_generate.log"
XHIGH_LOG = QUEUE_LOG_ROOT / f"{STAMP}_boot_xhigh_generate.log"
EVAL_LOG = QUEUE_LOG_ROOT / f"{STAMP}_eval_master.log"
HIGH_COMPARE = QUEUE_LOG_ROOT / f"{HIGH_PREFIX}_compare.json"
XHIGH_COMPARE = QUEUE_LOG_ROOT / f"{XHIGH_PREFIX}_compare.json"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_pids(raw: str) -> None:
    pids = [int(item) for item in raw.split(",") if item.strip().isdigit()]
    while True:
        live = [pid for pid in pids if pid_alive(pid)]
        if not live:
            return
        log(f"waiting for earlier eval master pid(s) to finish: {live}")
        time.sleep(60)


def write_summary(payload: dict[str, Any]) -> None:
    SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_generation(effort: str, prefix: str, log_path: Path) -> subprocess.Popen[bytes]:
    cmd = [
        str(PYTHON),
        "-u",
        str(SCRIPTS / "run_gaia_8b_bootv6_sssai52_bootstrap_compare_20260513.py"),
        "--reasoning-effort",
        effort,
        "--prefix",
        prefix,
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("wb")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    handle.close()
    log(f"started {effort} generation pid={proc.pid} log={log_path}")
    return proc


def wait_existing_pid(pid: int, label: str) -> int:
    log(f"attached to existing {label} generation pid={pid}")
    while pid_alive(pid):
        time.sleep(30)
    try:
        _, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return 0
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 0


def launch_eval() -> int:
    env = os.environ.copy()
    env["GAIA_8B_BOOTV6_SSSAI52_BOOT_EVAL_PREFIX"] = EVAL_PREFIX
    env["GAIA_8B_BOOTV6_SSSAI52_BOOT_HIGH_COMPARE_JSON"] = str(HIGH_COMPARE)
    env["GAIA_8B_BOOTV6_SSSAI52_BOOT_XHIGH_COMPARE_JSON"] = str(XHIGH_COMPARE)
    cmd = [
        str(PYTHON),
        "-u",
        str(SCRIPTS / "run_gaia_8b_bootv6_sssai52_boot_high_xhigh_eval_shards_20260513.py"),
    ]
    with EVAL_LOG.open("wb") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    log(f"started eval master pid={proc.pid} prefix={EVAL_PREFIX} log={EVAL_LOG}")
    payload = base_payload()
    payload["eval"] = {
        "pid": proc.pid,
        "prefix": EVAL_PREFIX,
        "log": str(EVAL_LOG),
        "summary_json": str(QUEUE_LOG_ROOT / f"{EVAL_PREFIX}_summary.json"),
        "manifest_json": str(QUEUE_LOG_ROOT / f"{EVAL_PREFIX}_manifest.json"),
    }
    payload["status"] = "eval_running"
    write_summary(payload)
    return proc.wait()


def base_payload() -> dict[str, Any]:
    return {
        "stamp": STAMP,
        "updated_at": now(),
        "status": "running",
        "high": {
            "prefix": HIGH_PREFIX,
            "compare_json": str(HIGH_COMPARE),
            "log": str(HIGH_LOG),
        },
        "xhigh": {
            "prefix": XHIGH_PREFIX,
            "compare_json": str(XHIGH_COMPARE),
            "log": str(XHIGH_LOG),
        },
        "eval_prefix": EVAL_PREFIX,
    }


def main() -> int:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = base_payload()
    payload["status"] = "generation_starting"
    write_summary(payload)

    existing_high_pid = os.environ.get("GAIA_8B_BOOTV6_SSSAI52_RERUN_EXISTING_HIGH_PID", "").strip()
    existing_xhigh_pid = os.environ.get("GAIA_8B_BOOTV6_SSSAI52_RERUN_EXISTING_XHIGH_PID", "").strip()
    high_proc = None
    xhigh_proc = None
    if existing_high_pid and existing_xhigh_pid:
        high_pid = int(existing_high_pid)
        xhigh_pid = int(existing_xhigh_pid)
    else:
        high_proc = run_generation("high", HIGH_PREFIX, HIGH_LOG)
        xhigh_proc = run_generation("xhigh", XHIGH_PREFIX, XHIGH_LOG)
        high_pid = high_proc.pid
        xhigh_pid = xhigh_proc.pid
    payload = base_payload()
    payload["status"] = "generation_running"
    payload["high"]["pid"] = high_pid
    payload["xhigh"]["pid"] = xhigh_pid
    write_summary(payload)

    high_code = high_proc.wait() if high_proc is not None else wait_existing_pid(high_pid, "high")
    xhigh_code = xhigh_proc.wait() if xhigh_proc is not None else wait_existing_pid(xhigh_pid, "xhigh")
    payload = base_payload()
    payload["status"] = "generation_completed"
    payload["high"]["exit_code"] = high_code
    payload["xhigh"]["exit_code"] = xhigh_code
    payload["high"]["compare_exists"] = HIGH_COMPARE.exists()
    payload["xhigh"]["compare_exists"] = XHIGH_COMPARE.exists()
    write_summary(payload)
    log(f"generation finished high_exit={high_code} xhigh_exit={xhigh_code}")
    if high_code != 0 or xhigh_code != 0:
        return 10
    if not HIGH_COMPARE.exists() or not XHIGH_COMPARE.exists():
        return 11

    wait_for_pids(os.environ.get("GAIA_8B_BOOTV6_SSSAI52_RERUN_WAIT_PIDS", "1896574"))
    payload = base_payload()
    payload["status"] = "launching_eval"
    write_summary(payload)
    eval_code = launch_eval()
    payload = base_payload()
    payload["status"] = "completed" if eval_code == 0 else "eval_failed"
    payload["eval_exit_code"] = eval_code
    payload["eval_summary_json"] = str(QUEUE_LOG_ROOT / f"{EVAL_PREFIX}_summary.json")
    payload["updated_at"] = now()
    write_summary(payload)
    log(f"pipeline finished eval_exit={eval_code}")
    return eval_code


if __name__ == "__main__":
    raise SystemExit(main())
