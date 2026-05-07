from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
PREFIX = "20260507_0848_8b9b_archv53_bootv3v4_remain20_fast"
LOG_ROOT = ROOT / "runs/_launch_logs"
QUEUE_LOG_ROOT = ROOT / "runs/_queue_logs"
STATUS_JSONL = QUEUE_LOG_ROOT / f"{PREFIX}_oversub_live_status.jsonl"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")
POLL_SECONDS = int(os.environ.get("GAIA_OVERSUB_WATCH_POLL_SECONDS", "180"))


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


def parse_log(log_path: Path) -> dict[str, object]:
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    progress = re.findall(r"Iteration \d+ progress (\d+)/(\d+) task=([^ ]+) success=(\w+)", text)
    attempts = [int(m.group(1)) for m in re.finditer(r"attempt (\d+)/(\d+)", text, re.I)]
    return {
        "log_path": str(log_path),
        "last_progress": progress[-1][:2] if progress else None,
        "last_task": progress[-1][2] if progress else None,
        "last_success": progress[-1][3] if progress else None,
        "empty_stream": text.count("Streaming response produced empty content"),
        "max_attempt": max(attempts) if attempts else 0,
        "traceback": text.count("Traceback"),
        "runtime_error": text.count("RuntimeError"),
        "fail6": text.count("Streaming LLM call FAILED after 6 attempts"),
    }


def known_logs() -> dict[str, Path]:
    logs: dict[str, Path] = {}
    for path in LOG_ROOT.glob(f"{PREFIX}*.log"):
        run_name = path.name[:-4]
        logs[run_name] = path
    for path in LOG_ROOT.glob("20260507_0959_remain20_retry_*.log"):
        logs[path.name[:-4]] = path
    return logs


def log_path_for_run(run_name: str, logs: dict[str, Path]) -> Path:
    exact = logs.get(run_name)
    if exact is not None:
        return exact
    candidates = [path for path in LOG_ROOT.glob(f"{run_name}_*.log") if path.is_file()]
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    return LOG_ROOT / f"{run_name}.log"


def append_process_row(pid: int) -> None:
    if not PROCESS_CSV.exists():
        return
    command = " ".join(
        [
            "/data/xsy/project_gaia_skillrl/.venv/bin/python",
            "-u",
            "/data/xsy/project_gaia_skillrl/scripts/watch_gaia_remain20_oversub_live_20260507.py",
        ]
    )
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
                now(),
                now(),
                "running",
                "gaia_live_watcher",
                f"{PREFIX}_oversub_live_watcher",
                str(pid),
                now(),
                "",
                str(ROOT),
                "",
                command,
                "",
                "live PID/log watcher after replacing master scheduler with oversub refill",
                str(STATUS_JSONL),
            ]
        )


def main() -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    append_process_row(os.getpid())
    while True:
        processes = run_ps()
        live: list[dict[str, object]] = []
        for pid, cmd in processes.items():
            if "gaia_skillrl.cli" not in cmd or PREFIX not in cmd:
                continue
            run_name = run_name_from_cmd(cmd)
            if not run_name:
                continue
            live.append({"pid": pid, "run_name": run_name, "cmd": cmd})
        logs = known_logs()
        live_names = {str(item["run_name"]) for item in live}
        records = []
        for item in live:
            run_name = str(item["run_name"])
            log_path = log_path_for_run(run_name, logs)
            records.append({**item, **parse_log(log_path)})
        payload = {
            "checked_at": now(),
            "prefix": PREFIX,
            "live_count": len(live),
            "live_9b_count": sum(1 for item in live if "_9b_" in str(item["run_name"])),
            "live_8b_count": sum(1 for item in live if "_8b_" in str(item["run_name"])),
            "live_oversub_count": sum(1 for item in live if "oversub_refill" in str(item["run_name"])),
            "finished_log_count": sum(1 for name in logs if name not in live_names),
            "records": records,
        }
        with STATUS_JSONL.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
