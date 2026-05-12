from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import run_gaia_8b_baseline_254_gpu01_20260507 as base  # noqa: E402


RUN_PREFIX = os.environ.get("GAIA_8B_REMOTE132_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_8b_remote132_baseline_c20_c40_gpus0123"
)
MASTER_NAME = f"{RUN_PREFIX}_master"
SUMMARY_PATH = base.QUEUE_LOG_ROOT / f"{MASTER_NAME}_summary.json"

LANES = [
    base.Lane("132_gpu0_8b_c20_dev", 0, 8128),
    base.Lane("132_gpu1_8b_c20_test", 1, 8129),
    base.Lane("132_gpu2_8b_c40_dev", 2, 8130),
    base.Lane("132_gpu3_8b_c40_test", 3, 8131),
]

JOBS = [
    {"key": "dev_c20", "label": "dev_c20", "dataset": base.DEV_DATASET, "lane": LANES[0], "concurrency": 20},
    {"key": "test_c20", "label": "test_c20", "dataset": base.TEST_DATASET, "lane": LANES[1], "concurrency": 20},
    {"key": "dev_c40", "label": "dev_c40", "dataset": base.DEV_DATASET, "lane": LANES[2], "concurrency": 40},
    {"key": "test_c40", "label": "test_c40", "dataset": base.TEST_DATASET, "lane": LANES[3], "concurrency": 40},
]


def configure_base() -> None:
    base.HOST_TAG = "132"
    base.RUN_PREFIX = RUN_PREFIX
    base.MASTER_NAME = MASTER_NAME
    base.SUMMARY_PATH = SUMMARY_PATH
    base.LANES = LANES
    base.CONFLICTING_VLLM_PORTS = [8128, 8129, 8130, 8131]
    base.VLLM_MAX_NUM_SEQS = os.environ.get(
        "GAIA_8B_REMOTE132_VLLM_MAX_NUM_SEQS",
        os.environ.get("GAIA_8B_BASELINE_VLLM_MAX_NUM_SEQS", "48"),
    )
    base.VLLM_GPU_MEMORY_UTILIZATION = os.environ.get(
        "GAIA_8B_REMOTE132_VLLM_GPU_MEMORY_UTILIZATION",
        os.environ.get("GAIA_8B_BASELINE_VLLM_GPU_MEMORY_UTILIZATION", "0.90"),
    )


def write_summary(status: str, **extra: object) -> None:
    base.QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": MASTER_NAME,
        "run_prefix": RUN_PREFIX,
        "updated_at": base.now(),
        "status": status,
        "lanes": [asdict(lane) for lane in LANES],
        "jobs": [
            {
                "key": str(job["key"]),
                "label": str(job["label"]),
                "dataset": str(job["dataset"]),
                "lane": asdict(job["lane"]),
                "concurrency": int(job["concurrency"]),
            }
            for job in JOBS
        ],
        "config": {
            "model": "Qwen3-8B-local",
            "model_path": "/data/xsy/codes/checkpoints/Qwen3-8B",
            "tokenizer_path": "/data/xsy/codes/checkpoints/Qwen3-8B",
            "max_model_len": 40960,
            "max_tokens": 12288,
            "token_guard_safety_margin": 512,
            "max_context_chars": 0,
            "thinking_token_budget": None,
            "enable_thinking": True,
            "max_executor_steps": 24,
            "task_concurrency_by_job": {str(job["key"]): int(job["concurrency"]) for job in JOBS},
            "vllm_gpu_memory_utilization": base.VLLM_GPU_MEMORY_UTILIZATION,
            "vllm_max_num_seqs": base.VLLM_MAX_NUM_SEQS,
            "tool_profile": "atomic_v2",
            "answer_acceptance_policy": "any_phase",
            "runner": "direct-eval-local",
            "bootstrap": "disabled",
            "initial_skill": None,
        },
        **extra,
    }
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def start_eval_job(job: dict[str, object]):
    base.CONCURRENCY = int(job["concurrency"])
    return base.start_eval(str(job["label"]), Path(job["dataset"]), job["lane"])


def main() -> int:
    configure_base()
    base.QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    base.LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    master_log = base.QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"
    base.append_process_row(
        kind="experiment_queue",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=base.ROOT,
        run_dir="(8B remote132 four-lane baseline c20/c40 master)",
        command=base.command_display(os.environ, [str(base.PYTHON), *sys.argv]),
        log_path=master_log,
        notes=f"8B remote132 baseline master; GPU0 dev c20, GPU1 test c20, GPU2 dev c40, GPU3 test c40; summary={SUMMARY_PATH}.",
    )
    status = "finished"
    write_summary("starting")
    try:
        base.refresh_process_registry()
        vllm_pids = [base.start_or_reuse_vllm(lane) for lane in LANES]
        base.wait_endpoints()
        write_summary("vllm_ready", vllm_pids=vllm_pids)
        processes = {str(job["key"]): start_eval_job(job) for job in JOBS}
        write_summary(
            "evals_running",
            vllm_pids=vllm_pids,
            eval_pids={label: proc.pid for label, proc in processes.items()},
        )
        base.wait_processes(processes)
        write_summary(
            "finished",
            vllm_pids=vllm_pids,
            eval_pids={label: proc.pid for label, proc in processes.items()},
        )
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        write_summary(status)
        raise
    except Exception as exc:
        status = "dead"
        write_summary(status, error=str(exc))
        raise
    finally:
        base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
