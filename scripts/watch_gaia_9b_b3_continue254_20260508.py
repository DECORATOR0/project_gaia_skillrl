from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
RUN_ROOT = ROOT / "runs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")
DOC_PATH = ROOT / "实验设计与迭代/26.5.07_0123_GAIA_8B9B_ARCH53_BOOTV3V4_24实验启动与结果.md"

PREFIX = os.environ.get("GAIA_B3_CONTINUE254_PREFIX", "20260508_0009_9b_bootv4_b3_continue254")
MASTER_NAME = f"{PREFIX}_master"
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"
STATUS_JSONL = QUEUE_LOG_ROOT / f"{PREFIX}_watch_status.jsonl"
WATCH_LOG = QUEUE_LOG_ROOT / f"{PREFIX}_watcher.log"
CLEANUP_GPU_INDICES = [
    item.strip()
    for item in os.environ.get("GAIA_B3_CONTINUE254_CLEANUP_GPU_INDICES", "2,3").split(",")
    if item.strip()
]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def ensure_process_csv_header() -> None:
    if PROCESS_CSV.exists() and PROCESS_CSV.stat().st_size:
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
                "return_code",
                "notes",
                "log_path",
                "exit_code",
            ]
        )


def append_process_row(master_pid: int) -> None:
    ensure_process_csv_header()
    command = " ".join([sys.executable, *sys.argv])
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
                now(),
                now(),
                "running",
                "experiment_monitor",
                f"{PREFIX}_watcher",
                os.getpid(),
                now(),
                "",
                str(ROOT),
                str(QUEUE_LOG_ROOT),
                command,
                "",
                f"3-minute watcher for {MASTER_NAME} pid={master_pid}; writes {STATUS_JSONL}",
                str(WATCH_LOG),
                "",
            ]
        )


def update_own_process(status: str, exit_code: int | None = None) -> None:
    if not PROCESS_CSV.exists():
        return
    rows: list[list[str]] = []
    with PROCESS_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    pid = str(os.getpid())
    stamp = now()
    for row in rows[1:]:
        if len(row) > 5 and row[5] == pid:
            while len(row) < 15:
                row.append("")
            row[1] = stamp
            row[2] = status
            row[7] = stamp
            if exit_code is not None:
                row[14] = str(exit_code)
    with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_load_error": repr(exc), "_path": str(path)}


def latest_run_dirs() -> list[Path]:
    suffix_match = re.match(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_(.+)$", PREFIX)
    suffix = suffix_match.group(1) if suffix_match else PREFIX
    patterns = [f"2026/5/*/*{PREFIX}*", f"2026/5/*/*{suffix}*"]
    dirs = []
    seen = set()
    for pattern in patterns:
        for path in RUN_ROOT.glob(pattern):
            if path.is_dir() and path not in seen:
                seen.add(path)
                dirs.append(path)
    return sorted(dirs, key=lambda path: path.stat().st_mtime)


def latest_iteration_dir(run_dir: Path) -> Path | None:
    iterations = [path for path in run_dir.glob("iteration_*") if path.is_dir()]
    return max(iterations, key=lambda path: path.name) if iterations else None


def stats_for_run(run_dir: Path) -> dict:
    selected_path = run_dir / "selected_tasks.json"
    total = 0
    if selected_path.exists():
        try:
            total = len(json.loads(selected_path.read_text(encoding="utf-8")).get("task_ids", []))
        except Exception:
            total = 0
    iteration = latest_iteration_dir(run_dir)
    states = list(iteration.glob("*/state.json")) if iteration else []
    success = 0
    for state_path in states:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            success += int(bool(state.get("env_result", {}).get("evaluation", {}).get("task_success")))
        except Exception:
            pass
    total = total or len(states)
    offline_skill = run_dir / "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
    return {
        "run_name": run_dir.name,
        "run_dir": str(run_dir),
        "is_offline": (run_dir / "offline_iter1").exists(),
        "offline_skill_exists": offline_skill.exists(),
        "offline_skill_path": str(offline_skill) if offline_skill.exists() else "",
        "landed": len(states),
        "success": success,
        "total": total,
        "missing": max(0, total - len(states)),
        "score": f"{success}/{total or '?'}" if states or total else "",
    }


def gpu_snapshot() -> list[dict]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return [{"error": repr(exc)}]
    rows = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 4:
            rows.append({"index": parts[0], "memory_used_mib": parts[1], "memory_total_mib": parts[2], "gpu_util": parts[3]})
    return rows


def gpu_index_bus_map() -> dict[str, str]:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,pci.bus_id", "--format=csv,noheader,nounits"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    mapping = {}
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 2:
            mapping[parts[0]] = parts[1]
    return mapping


def gpu_compute_apps() -> list[dict]:
    result = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=gpu_bus_id,pid,process_name,used_memory", "--format=csv,noheader,nounits"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    apps = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 4 and parts[1].isdigit():
            apps.append({"gpu_bus_id": parts[0], "pid": int(parts[1]), "process_name": parts[2], "used_memory_mib": parts[3]})
    return apps


def process_group(pid: int) -> int | None:
    result = subprocess.run(
        ["ps", "-o", "pgid=", "-p", str(pid)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def cleanup_gpu_indices(indices: list[str]) -> dict:
    if not indices:
        return {"enabled": False, "gpu_indices": [], "targets": [], "killed_pgids": []}
    bus_by_index = gpu_index_bus_map()
    target_buses = {bus_by_index[index]: index for index in indices if index in bus_by_index}
    targets = [app for app in gpu_compute_apps() if app["gpu_bus_id"] in target_buses]
    pgids = sorted({pgid for app in targets for pgid in [process_group(int(app["pid"]))] if pgid})
    result = {"enabled": True, "gpu_indices": indices, "targets": targets, "killed_pgids": pgids, "after": []}
    for pgid in pgids:
        try:
            os.killpg(pgid, 15)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            result.setdefault("errors", []).append(f"SIGTERM pgid={pgid}: {exc}")
    time.sleep(5)
    for pgid in pgids:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
        try:
            os.killpg(pgid, 9)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            result.setdefault("errors", []).append(f"SIGKILL pgid={pgid}: {exc}")
    time.sleep(2)
    result["after"] = [app for app in gpu_compute_apps() if app["gpu_bus_id"] in target_buses]
    return result


def collect_status(master_pid: int) -> dict:
    manifest = load_json(MANIFEST_JSON)
    summary = load_json(SUMMARY_JSON)
    return {
        "updated_at": now(),
        "prefix": PREFIX,
        "master_pid": master_pid,
        "master_alive": pid_alive(master_pid),
        "manifest_status": manifest.get("status", ""),
        "summary_status": summary.get("status", ""),
        "manifest_json": str(MANIFEST_JSON),
        "summary_json": str(SUMMARY_JSON),
        "cleanup_gpu_indices_on_master_exit": CLEANUP_GPU_INDICES,
        "runs": [stats_for_run(path) for path in latest_run_dirs()],
        "gpu": gpu_snapshot(),
    }


def append_status(payload: dict) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    with STATUS_JSONL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def append_doc_final(status: dict) -> None:
    lines = [
        "",
        f"### {datetime.now().strftime('%Y-%m-%d %H:%M')} watcher 自动收尾快照",
        "",
        f"- prefix：`{PREFIX}`",
        f"- master_alive：`{status.get('master_alive')}`；manifest_status：`{status.get('manifest_status')}`；summary_status：`{status.get('summary_status')}`",
        f"- watcher 状态文件：`{STATUS_JSONL}`",
        f"- manifest：`{MANIFEST_JSON}`",
        f"- summary：`{SUMMARY_JSON}`",
        f"- master 结束后 GPU 清理目标：`{','.join(CLEANUP_GPU_INDICES) if CLEANUP_GPU_INDICES else 'disabled'}`",
    "- run 快照：",
    ]
    for run in status.get("runs", []):
        detail = run.get("score") or ("skill_ready" if run.get("offline_skill_exists") else "pending")
        lines.append(f"  - `{run.get('run_name')}`：{detail}，landed `{run.get('landed')}`，missing `{run.get('missing')}`")
    DOC_PATH.write_text(DOC_PATH.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit("usage: watch_gaia_9b_b3_continue254_20260508.py MASTER_PID")
    master_pid = int(sys.argv[1])
    append_process_row(master_pid)
    status = "finished"
    exit_code = 0
    try:
        with WATCH_LOG.open("a", encoding="utf-8") as log:
            log.write(f"[{now()}] watching master_pid={master_pid} prefix={PREFIX}\n")
            while True:
                payload = collect_status(master_pid)
                append_status(payload)
                log.write(f"[{now()}] master_alive={payload['master_alive']} runs={len(payload['runs'])}\n")
                log.flush()
                if not payload["master_alive"]:
                    time.sleep(5)
                    final_status = collect_status(master_pid)
                    cleanup_result = cleanup_gpu_indices(CLEANUP_GPU_INDICES)
                    final_status["cleanup_result"] = cleanup_result
                    append_status(final_status)
                    append_doc_final(final_status)
                    log.write(f"[{now()}] cleanup_result={json.dumps(cleanup_result, ensure_ascii=False, sort_keys=True)}\n")
                    break
                time.sleep(180)
    except Exception as exc:
        status = "dead"
        exit_code = 1
        with WATCH_LOG.open("a", encoding="utf-8") as log:
            log.write(f"[{now()}] watcher failed: {exc!r}\n")
        raise
    finally:
        update_own_process(status, exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
