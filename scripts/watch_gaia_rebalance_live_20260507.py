from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/project_gaia_skillrl")
DAY_ROOT = ROOT / "runs/2026/5/2026-5-7"
LOG_ROOT = ROOT / "runs/_launch_logs"
QUEUE_LOG_ROOT = ROOT / "runs/_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

WATCH_PREFIXES = [
    "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast",
    "20260507_1038_rebalance_8b9b_remain20",
]
STATUS_JSONL = QUEUE_LOG_ROOT / "20260507_1038_rebalance_8b9b_live_status.jsonl"
POLL_SECONDS = int(os.environ.get("GAIA_REBALANCE_WATCH_POLL_SECONDS", "180"))

REMOTE_HOST = "124.115.123.132"
REMOTE_PORT = "22219"
REMOTE_USER = "xsy"
REMOTE_PASSWORD = "xsy945@j8ca0"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_ps() -> dict[int, str]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    processes: dict[int, str] = {}
    for line in result.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        pid_text, _, cmd = raw.partition(" ")
        if pid_text.isdigit():
            processes[int(pid_text)] = cmd
    return processes


def run_name_from_cmd(cmd: str) -> str:
    match = re.search(r"--run-name\s+(\S+)", cmd)
    return match.group(1) if match else ""


def recent_text(path: Path, max_bytes: int = 512_000) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        return handle.read().decode("utf-8", errors="replace")


def run_dir_for_name(run_name: str) -> Path | None:
    if not DAY_ROOT.exists():
        return None
    exact = [path for path in DAY_ROOT.glob(f"*{run_name}") if path.is_dir()]
    if exact:
        return max(exact, key=lambda item: item.stat().st_mtime)
    # Runtime names directories as YYYYMMDD_HHMMSS_<run suffix>, while
    # --run-name often uses YYYYMMDD_HHMM_<run suffix>.
    match = re.match(r"^(\d{8})_(\d{4})_(.+)$", run_name)
    if match:
        day, hour_minute, suffix = match.groups()
        candidates = [
            path
            for path in DAY_ROOT.glob(f"{day}_{hour_minute}??_{suffix}")
            if path.is_dir()
        ]
        if candidates:
            return max(candidates, key=lambda item: item.stat().st_mtime)
    return None


def log_path_for_run(run_name: str) -> Path:
    exact = LOG_ROOT / f"{run_name}.log"
    if exact.exists():
        return exact
    candidates = list(LOG_ROOT.glob(f"*{run_name}*.log"))
    if candidates:
        return max(candidates, key=lambda item: item.stat().st_mtime)
    return exact


def selected_total(run_dir: Path | None) -> int | None:
    if run_dir is None:
        return None
    path = run_dir / "selected_tasks.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        task_ids = data.get("task_ids")
        return len(task_ids) if isinstance(task_ids, list) else None
    except Exception:
        return None


def state_summary(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {"run_dir": None, "landed": 0, "success": 0, "total": None}
    states = list((run_dir / "iteration_01").glob("*/state.json"))
    success = 0
    for path in states:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        env_result = data.get("env_result") if isinstance(data.get("env_result"), dict) else {}
        evaluation = (
            env_result.get("evaluation")
            if isinstance(env_result.get("evaluation"), dict)
            else {}
        )
        if (
            data.get("task_success") is True
            or env_result.get("task_success") is True
            or evaluation.get("task_success") is True
        ):
            success += 1
    total = selected_total(run_dir)
    return {
        "run_dir": str(run_dir),
        "landed": len(states),
        "success": success,
        "total": total,
        "missing": None if total is None else max(total - len(states), 0),
    }


def config_summary(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {}
    path = run_dir / "config_snapshot.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"config_error": str(exc)}
    base_config = data.get("base_config") if isinstance(data.get("base_config"), dict) else data
    executor = (
        base_config.get("executor")
        if isinstance(base_config.get("executor"), dict)
        else {}
    )
    runtime = (
        base_config.get("runtime")
        if isinstance(base_config.get("runtime"), dict)
        else {}
    )
    return {
        "model": executor.get("model"),
        "base_url": executor.get("base_url"),
        "max_model_len": executor.get("max_model_len"),
        "task_concurrency": runtime.get("task_concurrency"),
        "llm_max_concurrent_requests": data.get("llm_max_concurrent_requests")
        or runtime.get("llm_max_concurrent_requests"),
    }


def parse_log(log_path: Path) -> dict[str, Any]:
    text = recent_text(log_path)
    progress = re.findall(
        r"Iteration \d+ progress (\d+)/(\d+) task=([^ ]+) success=(\w+)", text
    )
    attempts = [int(item.group(1)) for item in re.finditer(r"attempt (\d+)/(\d+)", text, re.I)]
    return {
        "log_path": str(log_path),
        "log_exists": log_path.exists(),
        "last_progress": list(progress[-1][:2]) if progress else None,
        "last_task": progress[-1][2] if progress else None,
        "last_success": progress[-1][3] if progress else None,
        "empty_stream": text.count("Streaming response produced empty content"),
        "max_attempt": max(attempts) if attempts else 0,
        "fail6": text.count("Streaming LLM call FAILED after 6 attempts"),
        "traceback": text.count("Traceback"),
        "runtime_error": text.count("RuntimeError"),
    }


def sample_remote_gpu() -> dict[str, Any]:
    try:
        import pexpect
    except Exception as exc:
        return {"ok": False, "error": f"pexpect import failed: {exc}"}

    cmd = (
        "nvidia-smi --query-gpu=index,utilization.gpu,memory.used,power.draw "
        "--format=csv,noheader,nounits"
    )
    child = pexpect.spawn(
        f"ssh -p {REMOTE_PORT} -o StrictHostKeyChecking=no "
        f"{REMOTE_USER}@{REMOTE_HOST} {cmd!r}",
        encoding="utf-8",
        timeout=25,
    )
    try:
        idx = child.expect([r"[Pp]assword:", pexpect.EOF, pexpect.TIMEOUT])
        if idx == 0:
            child.sendline(REMOTE_PASSWORD)
            child.expect(pexpect.EOF)
        output = child.before
    except Exception as exc:
        child.close(force=True)
        return {"ok": False, "error": str(exc)}
    child.close()

    rows = []
    for line in output.splitlines():
        raw = line.strip()
        if not raw or "password" in raw.lower() or "warning" in raw.lower():
            continue
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 4 or not parts[0].isdigit():
            continue
        rows.append(
            {
                "gpu": int(parts[0]),
                "util": int(float(parts[1])),
                "memory_mib": int(float(parts[2])),
                "power_w": float(parts[3]),
            }
        )
    return {"ok": bool(rows), "rows": rows}


def append_process_row(pid: int) -> None:
    if not PROCESS_CSV.exists():
        return
    command = (
        "python3 -u /data/xsy/project_gaia_skillrl/scripts/"
        "watch_gaia_rebalance_live_20260507.py"
    )
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
                now(),
                now(),
                "running",
                "gaia_live_watcher",
                "20260507_1038_rebalance_8b9b_live_watcher",
                str(pid),
                now(),
                "",
                str(ROOT),
                str(QUEUE_LOG_ROOT),
                command,
                "",
                "3-minute live watcher for mixed 8B/9B rebalance queue and old remain20 evals",
                str(STATUS_JSONL),
                "",
            ]
        )


def collect_once() -> dict[str, Any]:
    processes = run_ps()
    records: list[dict[str, Any]] = []
    for pid, cmd in processes.items():
        if "gaia_skillrl.cli" not in cmd:
            continue
        run_name = run_name_from_cmd(cmd)
        if not run_name or not any(prefix in run_name for prefix in WATCH_PREFIXES):
            continue
        run_dir = run_dir_for_name(run_name)
        log_path = log_path_for_run(run_name)
        records.append(
            {
                "pid": pid,
                "run_name": run_name,
                "family": "rebalance" if "20260507_1038_rebalance" in run_name else "remain20_old",
                "model_family": "9b" if "_9b_" in run_name else "8b",
                **state_summary(run_dir),
                **config_summary(run_dir),
                **parse_log(log_path),
            }
        )
    records.sort(key=lambda item: str(item["run_name"]))
    payload = {
        "checked_at": now(),
        "prefixes": WATCH_PREFIXES,
        "live_count": len(records),
        "live_8b_count": sum(1 for item in records if item["model_family"] == "8b"),
        "live_9b_count": sum(1 for item in records if item["model_family"] == "9b"),
        "live_rebalance_count": sum(1 for item in records if item["family"] == "rebalance"),
        "live_old_count": sum(1 for item in records if item["family"] == "remain20_old"),
        "total_landed": sum(int(item.get("landed") or 0) for item in records),
        "total_success": sum(int(item.get("success") or 0) for item in records),
        "total_fail6": sum(int(item.get("fail6") or 0) for item in records),
        "total_traceback": sum(int(item.get("traceback") or 0) for item in records),
        "remote_gpu": sample_remote_gpu(),
        "records": records,
    }
    return payload


def main() -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    append_process_row(os.getpid())
    while True:
        payload = collect_once()
        with STATUS_JSONL.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
