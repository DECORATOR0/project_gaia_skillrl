from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import run_gaia_gpt52_sssai_baseline_devtest_c10_20260507 as runner


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit("usage: watch_gaia_gpt52_sssai_baseline_devtest_20260507.py MASTER_PID")
    master_pid = int(sys.argv[1])
    log_path = runner.QUEUE_LOG_ROOT / f"{runner.MASTER_NAME}_finish_watcher.log"
    runner.append_process_row(
        kind="experiment_monitor",
        name=f"{runner.MASTER_NAME}_finish_watcher",
        pid=os.getpid(),
        cwd=runner.ROOT,
        run_dir=str(runner.QUEUE_LOG_ROOT),
        command=" ".join([str(runner.PYTHON), *sys.argv]),
        log_path=log_path,
        notes=f"waits for {runner.MASTER_NAME} pid={master_pid}, then appends corrected score report.",
    )
    status = "finished"
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"[{runner.now()}] watching master_pid={master_pid}\n")
            while runner.pid_alive(master_pid):
                time.sleep(60)
            time.sleep(5)
            stats = runner.collect_stats()
            payload = {
                "watcher_updated_at": runner.now(),
                "watcher_status": "master_exited",
                "corrected_stats": stats,
            }
            summary = {}
            if runner.SUMMARY_PATH.exists():
                try:
                    import json

                    summary = json.loads(runner.SUMMARY_PATH.read_text(encoding="utf-8"))
                except Exception:
                    summary = {}
            eval_pids = dict(summary.get("eval_pids") or {})
            summary.update(payload)
            import json

            runner.SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            runner.append_final_report("watcher_corrected", stats, eval_pids)
            log.write(f"[{runner.now()}] appended corrected stats: {stats}\n")
        return 0
    except Exception as exc:
        status = "dead"
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"[{runner.now()}] watcher failed: {exc}\n")
        raise
    finally:
        runner.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
