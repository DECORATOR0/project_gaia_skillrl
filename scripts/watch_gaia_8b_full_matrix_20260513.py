from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
RUN_ROOT = ROOT / "runs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

PREFIX = os.environ.get("GAIA_8B_FULL_MATRIX_PREFIX", "").strip()
MANIFEST_JSON = Path(
    os.environ.get("GAIA_8B_FULL_MATRIX_MANIFEST", QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json")
)
STATUS_JSONL = Path(
    os.environ.get("GAIA_8B_FULL_MATRIX_WATCH_JSONL", QUEUE_LOG_ROOT / f"{PREFIX}_watch_2min.jsonl")
)
POLL_SECONDS = int(os.environ.get("GAIA_8B_FULL_MATRIX_WATCH_POLL_SECONDS", "120"))

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_baseline_254_gpu01_20260507 as base  # noqa: E402


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "manifest_unreadable", "error": repr(exc), "path": str(path)}


def nvidia_snapshot() -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    rows = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue
        rows.append(
            {
                "gpu": int(parts[0]),
                "util": int(parts[1]),
                "mem_used_mib": int(parts[2]),
                "mem_total_mib": int(parts[3]),
            }
        )
    return rows


def summarize(manifest: dict[str, Any]) -> dict[str, Any]:
    pending = manifest.get("pending") if isinstance(manifest.get("pending"), list) else []
    running = manifest.get("running") if isinstance(manifest.get("running"), list) else []
    completed = manifest.get("completed") if isinstance(manifest.get("completed"), dict) else {}
    failures = manifest.get("eval_failures") if isinstance(manifest.get("eval_failures"), dict) else {}
    released = manifest.get("released_after_longtail") if isinstance(manifest.get("released_after_longtail"), dict) else {}
    specs = manifest.get("counted_eval_specs") if isinstance(manifest.get("counted_eval_specs"), list) else []
    offline_records = manifest.get("offline_records") if isinstance(manifest.get("offline_records"), list) else []
    offline_failures = manifest.get("offline_failures") if isinstance(manifest.get("offline_failures"), list) else []
    landed = 0
    total = 0
    running_brief = []
    for item in running:
        progress = item.get("progress", {}) if isinstance(item, dict) else {}
        landed += int(progress.get("landed") or 0)
        total += int(progress.get("total") or 0)
        spec = item.get("spec", {}) if isinstance(item, dict) else {}
        running_brief.append(
            {
                "run_name": spec.get("run_name"),
                "lane": item.get("lane", {}).get("name") if isinstance(item.get("lane"), dict) else "",
                "pid": item.get("pid"),
                "landed": progress.get("landed"),
                "total": progress.get("total"),
                "longtail_ready": progress.get("longtail_ready"),
            }
        )
    return {
        "prefix": manifest.get("prefix") or PREFIX,
        "status": manifest.get("status"),
        "manifest_updated_at": manifest.get("updated_at"),
        "pending": len(pending),
        "running": len(running),
        "completed": len(completed),
        "eval_failures": len(failures),
        "released_after_longtail": len(released),
        "counted_specs": len(specs),
        "expected_counted_eval_count": manifest.get("expected_counted_eval_count"),
        "offline_records": len(offline_records),
        "offline_failures": len(offline_failures),
        "running_landed_sum": landed,
        "running_total_sum": total,
        "running_brief": running_brief[:12],
    }


def append_process_row() -> None:
    base.ROOT = ROOT
    base.QUEUE_LOG_ROOT = QUEUE_LOG_ROOT
    base.ensure_process_csv_header()
    base.append_process_row(
        kind="experiment_watcher",
        name=f"{PREFIX}_watch_2min",
        pid=os.getpid(),
        cwd=ROOT,
        run_dir=str(QUEUE_LOG_ROOT),
        command=" ".join(sys.argv),
        log_path=STATUS_JSONL,
        notes=f"2-minute watcher for {PREFIX}; manifest={MANIFEST_JSON}",
    )


def main() -> int:
    if not PREFIX and not MANIFEST_JSON.exists():
        raise RuntimeError("GAIA_8B_FULL_MATRIX_PREFIX is required when manifest path does not exist")
    STATUS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    append_process_row()
    terminal_seen = 0
    while True:
        manifest = load_json(MANIFEST_JSON)
        summary = summarize(manifest)
        summary["sampled_at"] = now()
        summary["gpu"] = nvidia_snapshot()
        STATUS_JSONL.open("a", encoding="utf-8").write(json.dumps(summary, ensure_ascii=False) + "\n")
        log(
            f"status={summary['status']} pending={summary['pending']} running={summary['running']} "
            f"completed={summary['completed']} specs={summary['counted_specs']}/{summary['expected_counted_eval_count']} "
            f"failures={summary['eval_failures']} offline={summary['offline_records']}/{summary['offline_failures']}"
        )
        if str(summary["status"]).startswith(("finished", "dead", "stopped")):
            terminal_seen += 1
            if terminal_seen >= 2:
                break
        else:
            terminal_seen = 0
        time.sleep(POLL_SECONDS)
    base.update_process_row(os.getpid(), "finished", exit_code=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
