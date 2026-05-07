from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
RUN_ROOT = ROOT / "runs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"

PREFIX = os.environ["GAIA_MONITOR_PREFIX"]
MASTER_PID = int(os.environ["GAIA_MONITOR_MASTER_PID"])
INTERVAL_SECONDS = int(os.environ.get("GAIA_MONITOR_INTERVAL_SECONDS", "180"))
STATUS_JSONL = Path(
    os.environ.get(
        "GAIA_MONITOR_STATUS_JSONL",
        str(QUEUE_LOG_ROOT / f"{PREFIX}_monitor_status.jsonl"),
    )
)

PORTS = {
    18120: "Qwen3.5-9B-local",
    18121: "Qwen3.5-9B-local",
    18122: "Qwen3-8B-local",
    18123: "Qwen3-8B-local",
    18124: "Qwen3-8B-local",
    18125: "Qwen3-8B-local",
    18126: "Qwen3-8B-local",
    18127: "Qwen3-8B-local",
}


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


def endpoint_health() -> dict[str, dict[str, object]]:
    health: dict[str, dict[str, object]] = {}
    for port, expected_model in PORTS.items():
        url = f"http://127.0.0.1:{port}/v1/models"
        item: dict[str, object] = {"ok": False, "expected_model": expected_model}
        try:
            req = Request(url, headers={"Authorization": "Bearer EMPTY"})
            with urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            ids = [str(entry.get("id", "")) for entry in payload.get("data", []) if isinstance(entry, dict)]
            item.update({"ok": expected_model in ids, "models": ids})
        except Exception as exc:
            item.update({"error": repr(exc)})
        health[str(port)] = item
    return health


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_load_error": repr(exc)}


def log_keyword_counts() -> dict[str, int]:
    counts = {
        "traceback": 0,
        "runtime_error": 0,
        "empty_stream": 0,
        "http_400": 0,
        "read_timeout": 0,
    }
    for path in LAUNCH_LOG_ROOT.glob(f"{PREFIX}*.log"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        counts["traceback"] += text.count("Traceback")
        counts["runtime_error"] += text.count("RuntimeError")
        counts["empty_stream"] += text.count("Streaming response produced empty content")
        counts["http_400"] += text.count("HTTP 400")
        counts["read_timeout"] += text.count("ReadTimeout")
    return counts


def snapshot() -> dict:
    manifest = load_json(QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json")
    summary = load_json(QUEUE_LOG_ROOT / f"{PREFIX}_summary.json")
    pending = manifest.get("pending", []) if isinstance(manifest.get("pending"), list) else []
    running = manifest.get("running", []) if isinstance(manifest.get("running"), list) else []
    completed = manifest.get("completed", {}) if isinstance(manifest.get("completed"), dict) else {}
    failures = manifest.get("failures", {}) if isinstance(manifest.get("failures"), dict) else {}
    offline_records = manifest.get("offline_records", []) if isinstance(manifest.get("offline_records"), list) else []
    offline_failures = manifest.get("offline_failures", []) if isinstance(manifest.get("offline_failures"), list) else []
    health = endpoint_health()
    unhealthy = [port for port, item in health.items() if not item.get("ok")]
    keywords = log_keyword_counts()
    return {
        "checked_at": now(),
        "prefix": PREFIX,
        "master_pid": MASTER_PID,
        "master_alive": pid_alive(MASTER_PID),
        "pending_count": len(pending),
        "running_count": len(running),
        "completed_count": len(completed),
        "failure_count": len(failures),
        "offline_done_count": len(offline_records),
        "offline_failure_count": len(offline_failures),
        "summary_status": summary.get("status"),
        "summary_updated_at": summary.get("updated_at"),
        "endpoint_unhealthy": unhealthy,
        "log_keyword_counts": keywords,
        "running_names": [item.get("run_name") for item in running if isinstance(item, dict)],
        "failure_names": sorted(failures),
        "offline_failure_items": offline_failures[-5:],
    }


def main() -> int:
    STATUS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    while True:
        item = snapshot()
        line = json.dumps(item, ensure_ascii=False)
        with STATUS_JSONL.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)
        if not item["master_alive"] and item["running_count"] == 0:
            return 0
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
