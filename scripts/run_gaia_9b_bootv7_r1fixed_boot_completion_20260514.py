from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any


PREFIX_DEFAULT = "20260514_1918_9b_bootv7_r1fixed_boot_completion_gpu0_1_2_4_5"

os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_PREFIX", PREFIX_DEFAULT)
os.environ.setdefault("GAIA_BOOT_MATRIX_EXECUTOR_LABEL", "9B")
os.environ.setdefault("GAIA_BOOT_MATRIX_RUN_MODEL_TOKEN", "9b")
os.environ.setdefault("GAIA_BOOT_MATRIX_MODEL_PATH", "/data/xsy/codes/checkpoints/Qwen3.5-9B")
os.environ.setdefault("GAIA_BOOT_MATRIX_SERVED_NAME", "Qwen3.5-9B-local")
os.environ.setdefault("GAIA_BOOT_MATRIX_TOKENIZER_PATH", "/data/xsy/codes/checkpoints/Qwen3.5-9B")
os.environ.setdefault("GAIA_BOOT_MATRIX_MAX_MODEL_LEN", "49152")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_BOOT_KEYS", "")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_BOOT_STAGES", "")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_DOWNSTREAM_BOOT_KEYS", "")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_METHOD_ORDER", "")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_GPU_IDS", "0,1,2,4,5")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CONCURRENCY", "20")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_POLL_SECONDS", "120")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_MODEL", "gpt-5.2")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_REASONING_EFFORT", "high")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_TIMEOUT_SECONDS", "3600")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_OFFLINE_WORKERS", "1")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CRITIC_SHARD_CONCURRENCY", "4")
os.environ.setdefault("GAIA_8B_BASELINE_VLLM_PYTHON", "/data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python")
os.environ.setdefault("GAIA_9B_BOOTV7_R1FIXED_BOOT_REPEATS", "4,5")
os.environ.setdefault(
    "GAIA_8B_BOOTV7V8_V3V6_MATRIX_REPORT_DOC",
    str(
        Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
        / "实验设计与迭代/26.5.14_1918_GAIA_9B_BOOTV7_R1固定skill_BOOT补跑执行记录.md"
    ),
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_gaia_9b_bootv7_r1fixed_bestsource_afull_20260514 as r1fixed  # noqa: E402

matrix = r1fixed.matrix


def collect_fixed_boot_stats() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for repeat in r1fixed.BOOT_FIXED_REPEATS:
        row: dict[str, Any] = {"repeat": repeat}
        total_success = 0
        total_landed = 0
        total_total = 0
        for split in ("dev", "test"):
            run_name = r1fixed.fixed_boot_run_name(repeat, split)
            stats = r1fixed.stats_for(
                run_name,
                label=f"fixed_boot_rep{repeat} {split}",
                method_key="boot_fixed_completion",
                repeat=repeat,
                split=split,
            )
            row[f"{split}_run_name"] = run_name
            row[f"{split}_run_dir"] = stats.get("run_dir", "")
            row[f"{split}_score"] = stats.get("score", "")
            row[f"{split}_success"] = int(stats.get("success") or 0)
            row[f"{split}_landed"] = int(stats.get("landed") or 0)
            row[f"{split}_total"] = int(stats.get("total") or 0)
            row[f"{split}_missing"] = int(stats.get("missing") or 0)
            total_success += int(stats.get("success") or 0)
            total_landed += int(stats.get("landed") or 0)
            total_total += int(stats.get("total") or 0)
        row["score_total"] = f"{total_success}/{total_total or '?'}"
        row["landed_total"] = f"{total_landed}/{total_total or '?'}"
        rows.append(row)
    return rows


def main() -> int:
    start_time = matrix.now()
    status = "finished"
    matrix.configure_modules()
    matrix.verify_inputs()
    matrix.QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    matrix.LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    matrix.DESIGN_ROOT.mkdir(parents=True, exist_ok=True)
    matrix.base.ensure_process_csv_header()
    matrix.base.refresh_process_registry()
    matrix.base.append_process_row(
        kind="experiment_master",
        name=matrix.MASTER_NAME,
        pid=os.getpid(),
        cwd=matrix.ROOT,
        run_dir=str(matrix.QUEUE_LOG_ROOT),
        command=" ".join([str(matrix.PYTHON), "-u", str(Path(__file__).resolve())]),
        log_path=matrix.MASTER_LOG,
        notes=(
            "9B BOOTV7 r1 fixed-skill boot-only completion; "
            f"source_prefix={r1fixed.SOURCE_PREFIX}; repeats={r1fixed.BOOT_FIXED_REPEATS}; "
            f"summary={matrix.SUMMARY_JSON}; report={matrix.REPORT_DOC}"
        ),
    )
    scheduler = threading.Thread(
        target=matrix.scheduler_loop,
        name="gaia-9b-bootv7-r1fixed-boot-completion-scheduler",
        daemon=True,
    )
    matrix.write_manifest(
        "starting",
        {
            "stage": "r1fixed_boot_completion_start",
            "source_prefix": r1fixed.SOURCE_PREFIX,
            "source_repeat": r1fixed.SOURCE_REPEAT,
            "boot_fixed_repeats": r1fixed.BOOT_FIXED_REPEATS,
            "expected_new_counted_eval_count": len(r1fixed.BOOT_FIXED_REPEATS) * 2,
            "source_skill": str(r1fixed.boot_skill_path()),
        },
    )
    try:
        source_skill = r1fixed.boot_skill_path()
        matrix.base.wait_endpoints()
        scheduler.start()
        r1fixed.enqueue_fixed_boot_evals(source_skill)
        matrix.wait_method_evals_tail_or_done("boot_fixed")
        stats = collect_fixed_boot_stats()
        if matrix.eval_failures:
            status = "finished_with_eval_failures"
        elif matrix.released_evals:
            status = "finished_with_longtail_live"
        summary = matrix.write_summary(status, start_time=start_time, end_time=matrix.now())
        summary["fixed_boot_completion_stats"] = stats
        summary["source_skill"] = str(source_skill)
        matrix.SUMMARY_JSON.write_text(matrix.json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        matrix.append_report(summary)
        matrix.write_manifest(status, {"fixed_boot_completion_stats": stats, "source_skill": str(source_skill)})
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        summary = matrix.write_summary(status, start_time=start_time, end_time=matrix.now())
        matrix.append_report(summary)
        matrix.write_manifest(status)
        raise
    except Exception:
        status = "dead"
        summary = matrix.write_summary(status, start_time=start_time, end_time=matrix.now())
        matrix.append_report(summary)
        matrix.write_manifest(status)
        raise
    finally:
        matrix.scheduler_stop.set()
        scheduler.join(timeout=60)
        matrix.base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
