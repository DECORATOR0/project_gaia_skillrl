from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunProgress:
    run_dir: str
    total: int
    landed: int
    missing: int
    last_state_mtime: float
    last_state_at: str


def latest_iteration_dir(run_dir: Path) -> Path | None:
    iterations = [path for path in run_dir.glob("iteration_*") if path.is_dir()]
    return max(iterations, key=lambda path: path.name) if iterations else None


def selected_task_ids(run_dir: Path) -> list[str]:
    path = run_dir / "selected_tasks.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        raw_ids = data.get("task_ids") or data.get("tasks") or []
    elif isinstance(data, list):
        raw_ids = data
    else:
        raw_ids = []
    return [str(item) for item in raw_ids]


def state_progress(run_dir: Path | None) -> RunProgress:
    if run_dir is None:
        return RunProgress("", 0, 0, 0, 0.0, "")
    task_ids = selected_task_ids(run_dir)
    total = len(task_ids)
    iteration = latest_iteration_dir(run_dir)
    state_paths = list(iteration.glob("*/state.json")) if iteration else []
    last_mtime = max((path.stat().st_mtime for path in state_paths), default=0.0)
    last_at = (
        datetime.fromtimestamp(last_mtime).astimezone().isoformat(timespec="seconds")
        if last_mtime
        else ""
    )
    landed = len(state_paths)
    return RunProgress(
        run_dir=str(run_dir),
        total=total,
        landed=landed,
        missing=max(total - landed, 0) if total else 0,
        last_state_mtime=last_mtime,
        last_state_at=last_at,
    )


def longtail_threshold(total: int, fraction: float) -> int:
    return int(math.ceil(total * fraction)) if total else 0


def tail_ready(
    progress: RunProgress,
    *,
    live: bool,
    fraction: float = 0.95,
    max_missing: int = 2,
    stalled_seconds: int = 0,
    now_ts: float | None = None,
) -> bool:
    if not live:
        return True
    if progress.total <= 0:
        return False
    if progress.landed >= longtail_threshold(progress.total, fraction):
        return True
    if max_missing >= 0 and progress.missing <= max_missing:
        return True
    if stalled_seconds > 0 and progress.last_state_mtime > 0:
        now_value = time.time() if now_ts is None else now_ts
        if now_value - progress.last_state_mtime >= stalled_seconds:
            return True
    return False


def progress_payload(
    label: str,
    progress: RunProgress,
    *,
    live: bool,
    fraction: float = 0.95,
    max_missing: int = 2,
    stalled_seconds: int = 0,
) -> dict[str, Any]:
    ready = tail_ready(
        progress,
        live=live,
        fraction=fraction,
        max_missing=max_missing,
        stalled_seconds=stalled_seconds,
    )
    payload = asdict(progress)
    payload.update(
        {
            "label": label,
            "live": live,
            "threshold": longtail_threshold(progress.total, fraction),
            "longtail_ready": ready,
            "tail_fraction": fraction,
            "tail_max_missing": max_missing,
            "tail_stalled_seconds": stalled_seconds,
        }
    )
    return payload

