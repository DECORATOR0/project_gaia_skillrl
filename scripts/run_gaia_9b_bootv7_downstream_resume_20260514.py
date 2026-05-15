from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


os.environ.setdefault(
    "GAIA_8B_BOOTV7V8_V3V6_MATRIX_PREFIX",
    "20260514_1329_9b_bootv7_downstream_resume_from_1221_gpu2_4",
)
os.environ.setdefault("GAIA_BOOT_MATRIX_EXECUTOR_LABEL", "9B")
os.environ.setdefault("GAIA_BOOT_MATRIX_RUN_MODEL_TOKEN", "9b")
os.environ.setdefault("GAIA_BOOT_MATRIX_MODEL_PATH", "/data/xsy/codes/checkpoints/Qwen3.5-9B")
os.environ.setdefault("GAIA_BOOT_MATRIX_SERVED_NAME", "Qwen3.5-9B-local")
os.environ.setdefault("GAIA_BOOT_MATRIX_TOKENIZER_PATH", "/data/xsy/codes/checkpoints/Qwen3.5-9B")
os.environ.setdefault("GAIA_BOOT_MATRIX_MAX_MODEL_LEN", "49152")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_BOOT_KEYS", "")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_BOOT_STAGES", "")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_DOWNSTREAM_BOOT_KEYS", "bootv7")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_METHOD_ORDER", "A_full,B1")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_GPU_IDS", "2,3,4")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CONCURRENCY", "20")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_POLL_SECONDS", "120")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_MODEL", "gpt-5.2")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_REASONING_EFFORT", "high")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_TIMEOUT_SECONDS", "3600")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_OFFLINE_WORKERS", "3")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CRITIC_SHARD_CONCURRENCY", "4")
os.environ.setdefault("GAIA_8B_BASELINE_VLLM_PYTHON", "/data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python")

import run_gaia_8b_bootv7v8_v3v6_matrix_remote132_20260514 as matrix  # noqa: E402


SOURCE_PREFIX = os.environ.get(
    "GAIA_9B_BOOTV7_RESUME_SOURCE_PREFIX",
    "20260514_1221_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5",
).strip()
SOURCE_MIN_STATES = int(os.environ.get("GAIA_9B_BOOTV7_RESUME_MIN_STATES", "79"))
SOURCE_REPEATS = [
    int(item)
    for item in os.environ.get("GAIA_9B_BOOTV7_RESUME_REPEATS", "1,2,3").split(",")
    if item.strip()
]


def source_run_name(repeat: int) -> str:
    return f"{SOURCE_PREFIX}_9b_bootv7_r{repeat}_boot_dev_c20"


def build_sources() -> list[matrix.remain20.BootSource]:
    sources: list[matrix.remain20.BootSource] = []
    for repeat in SOURCE_REPEATS:
        run_name = source_run_name(repeat)
        run_dir = matrix.latest_run_dir(run_name)
        if run_dir is None:
            raise RuntimeError(f"missing source run dir for {run_name}")
        done, total, _ = matrix.state_count(run_name)
        if done < SOURCE_MIN_STATES:
            raise RuntimeError(f"source {run_name} has too few states: {done}/{total or '?'}")
        skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
        if not skill.exists():
            raise RuntimeError(f"missing boot skill for {run_name}: {skill}")
        sources.append(
            matrix.remain20.BootSource(
                model_key=matrix.RUN_MODEL_TOKEN,
                boot_key=f"bootv7_r{repeat}",
                label=f"9B BOOTV7 repeat {repeat} resume from {SOURCE_PREFIX}",
                boot_dev_run=run_dir,
                boot_skill=skill,
                supplemental_runs=[],
                source_notes=(
                    f"resume downstream from {SOURCE_PREFIX}; "
                    f"source_states={done}/{total or '?'}; min={SOURCE_MIN_STATES}"
                ),
            )
        )
    return sources


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
            f"9B BOOTV7 downstream resume from {SOURCE_PREFIX}; "
            f"source_min_states={SOURCE_MIN_STATES}; methods={','.join(matrix.METHOD_ORDER)}; "
            f"summary={matrix.SUMMARY_JSON}; report={matrix.REPORT_DOC}"
        ),
    )
    scheduler = threading.Thread(
        target=matrix.scheduler_loop,
        name="gaia-9b-bootv7-downstream-resume-scheduler",
        daemon=True,
    )
    matrix.write_manifest(
        "starting",
        {
            "stage": "resume_start",
            "source_prefix": SOURCE_PREFIX,
            "source_min_states": SOURCE_MIN_STATES,
            "source_repeats": SOURCE_REPEATS,
        },
    )
    try:
        matrix.base.wait_endpoints()
        sources = build_sources()
        matrix.write_manifest(
            "running",
            {
                "stage": "resume_sources_ready",
                "source_prefix": SOURCE_PREFIX,
                "sources": [
                    {
                        "boot_key": source.boot_key,
                        "boot_dev_run": str(source.boot_dev_run),
                        "boot_skill": str(source.boot_skill),
                    }
                    for source in sources
                ],
            },
        )
        scheduler.start()
        with ThreadPoolExecutor(
            max_workers=max(1, matrix.OFFLINE_WORKERS),
            thread_name_prefix="9b-v7-resume-offline",
        ) as offline_pool:
            matrix.run_downstream_for_sources(offline_pool, sources, stage_index=1)
        matrix.wait_counted_evals_tail_or_done()
        if matrix.offline_failures and matrix.eval_failures:
            status = "finished_with_offline_and_eval_failures"
        elif matrix.offline_failures:
            status = "finished_with_offline_failures"
        elif matrix.eval_failures:
            status = "finished_with_eval_failures"
        elif matrix.released_evals:
            status = "finished_with_longtail_live"
        summary = matrix.write_summary(status, start_time=start_time, end_time=matrix.now())
        matrix.append_report(summary)
        matrix.write_manifest(status)
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
