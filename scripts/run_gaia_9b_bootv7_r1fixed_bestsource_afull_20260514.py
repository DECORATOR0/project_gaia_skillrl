from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any


PREFIX_DEFAULT = "20260514_1635_9b_bootv7_r1fixed_bestsource_afull_gpu0_5"

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
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_GPU_IDS", "0,1,2,3,4,5")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CONCURRENCY", "20")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_POLL_SECONDS", "120")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_MODEL", "gpt-5.2")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_REASONING_EFFORT", "high")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_TIMEOUT_SECONDS", "3600")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_OFFLINE_WORKERS", "1")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CRITIC_SHARD_CONCURRENCY", "4")
os.environ.setdefault("GAIA_8B_BASELINE_VLLM_PYTHON", "/data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python")
os.environ.setdefault(
    "GAIA_8B_BOOTV7V8_V3V6_MATRIX_REPORT_DOC",
    str(
        Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
        / "实验设计与迭代/26.5.14_1635_GAIA_9B_BOOTV7_R1固定skill补跑执行记录.md"
    ),
)

import run_gaia_8b_bootv7v8_v3v6_matrix_remote132_20260514 as matrix  # noqa: E402


SOURCE_PREFIX = os.environ.get(
    "GAIA_9B_BOOTV7_R1FIXED_SOURCE_PREFIX",
    "20260514_1221_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5",
).strip()
SOURCE_REPEAT = int(os.environ.get("GAIA_9B_BOOTV7_R1FIXED_SOURCE_REPEAT", "1"))
BOOT_FIXED_REPEATS = [
    int(item)
    for item in os.environ.get("GAIA_9B_BOOTV7_R1FIXED_BOOT_REPEATS", "2,3").split(",")
    if item.strip()
]
AFULL_REPEATS = [
    int(item)
    for item in os.environ.get("GAIA_9B_BOOTV7_R1FIXED_AFULL_REPEATS", "1,2,3").split(",")
    if item.strip()
]


def source_run_name(split: str) -> str:
    return f"{SOURCE_PREFIX}_9b_bootv7_r{SOURCE_REPEAT}_boot_{split}_c20"


def fixed_boot_run_name(repeat: int, split: str) -> str:
    return f"{matrix.PREFIX}_9b_bootv7_r1skill_bootrep{repeat}_{split}_c{matrix.CONCURRENCY}"


def afull_run_name(repeat: int, split: str) -> str:
    return f"{matrix.PREFIX}_9b_bootv7_r1skill_A_full_rep{repeat}_{split}_c{matrix.CONCURRENCY}"


def boot_skill_path() -> Path:
    run_dir = matrix.latest_run_dir(source_run_name("dev"))
    if run_dir is None:
        raise RuntimeError(f"missing source r{SOURCE_REPEAT} boot dev run: {source_run_name('dev')}")
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    if not skill.exists():
        raise RuntimeError(f"missing source boot skill: {skill}")
    return skill


def stats_for(run_name: str, *, label: str, method_key: str, repeat: int, split: str) -> dict[str, Any]:
    return matrix.run_stats(
        matrix.EvalSpec(
            key=f"stats_{method_key}_{repeat}_{split}",
            label=label,
            command="train-local",
            config_path=matrix.CONFIGS["bootv7"],
            dataset=matrix.DEV_DATASET if split == "dev" else matrix.TEST_DATASET,
            run_name=run_name,
            counted=True,
            boot_key="bootv7",
            method_key=method_key,
            repeat=repeat,
            split=split,
        )
    )


def candidate_stats(label: str, repeat: int, dev_run: str, test_run: str) -> dict[str, Any]:
    dev = stats_for(dev_run, label=f"{label} dev", method_key="boot_fixed", repeat=repeat, split="dev")
    test = stats_for(test_run, label=f"{label} test", method_key="boot_fixed", repeat=repeat, split="test")
    return {
        "label": label,
        "repeat": repeat,
        "dev_run_name": dev_run,
        "test_run_name": test_run,
        "dev_run_dir": dev.get("run_dir", ""),
        "test_run_dir": test.get("run_dir", ""),
        "dev_success": int(dev.get("success") or 0),
        "test_success": int(test.get("success") or 0),
        "dev_total": int(dev.get("total") or 0),
        "test_total": int(test.get("total") or 0),
        "dev_landed": int(dev.get("landed") or 0),
        "test_landed": int(test.get("landed") or 0),
        "dev_missing": int(dev.get("missing") or 0),
        "test_missing": int(test.get("missing") or 0),
        "score_total": int(dev.get("success") or 0) + int(test.get("success") or 0),
        "landed_total": int(dev.get("landed") or 0) + int(test.get("landed") or 0),
    }


def select_best_source(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        candidates,
        key=lambda item: (
            item["score_total"],
            item["dev_success"],
            item["test_success"],
            item["landed_total"],
        ),
    )


def enqueue_fixed_boot_evals(skill: Path) -> None:
    for repeat in BOOT_FIXED_REPEATS:
        for split, dataset in [("dev", matrix.DEV_DATASET), ("test", matrix.TEST_DATASET)]:
            matrix.enqueue_eval(
                matrix.EvalSpec(
                    key=f"r1skill_bootrep{repeat}_{split}",
                    label=f"9B BOOTV7 r1-skill boot repeat {repeat} {split}",
                    command="train-local",
                    config_path=matrix.CONFIGS["bootv7"],
                    dataset=dataset,
                    run_name=fixed_boot_run_name(repeat, split),
                    counted=True,
                    boot_key="bootv7",
                    method_key="boot_fixed",
                    repeat=repeat,
                    split=split,
                    skill_path=skill,
                    notes=(
                        f"fixed source skill from {source_run_name('dev')}; "
                        "no bootstrap, repeat measures executor/eval variance"
                    ),
                )
            )


def enqueue_afull_evals(skill: Path, best: dict[str, Any]) -> None:
    for repeat in AFULL_REPEATS:
        for split, dataset in [("dev", matrix.DEV_DATASET), ("test", matrix.TEST_DATASET)]:
            matrix.enqueue_eval(
                matrix.EvalSpec(
                    key=f"r1skill_A_full_rep{repeat}_{split}",
                    label=f"9B BOOTV7 r1-fixed A_full repeat {repeat} {split}",
                    command="train-local",
                    config_path=matrix.CONFIGS["bootv7"],
                    dataset=dataset,
                    run_name=afull_run_name(repeat, split),
                    counted=True,
                    boot_key="bootv7",
                    method_key="A_full_fixed",
                    repeat=repeat,
                    split=split,
                    skill_path=skill,
                    notes=(
                        f"single A_full skill from best boot candidate {best['label']} "
                        f"source={best['dev_run_name']}"
                    ),
                )
            )


def run_single_afull_creator(source_dev_run: Path, source_skill: Path, best: dict[str, Any]) -> Path:
    source = matrix.remain20.BootSource(
        model_key=matrix.RUN_MODEL_TOKEN,
        boot_key="bootv7_r1fixed_best",
        label=f"9B BOOTV7 r1-fixed best source {best['label']}",
        boot_dev_run=source_dev_run,
        boot_skill=source_skill,
        supplemental_runs=[],
        source_notes=(
            f"best selected among source r{SOURCE_REPEAT} and fixed-skill boot repeats; "
            f"score_total={best['score_total']}/165"
        ),
    )
    started_at = matrix.now()
    with matrix.offline_config_lock:
        previous_config = matrix.remain20.CONFIG
        matrix.remain20.CONFIG = matrix.CONFIGS["bootv7"]
        try:
            skill = matrix.remain20.run_offline_variant(
                source,
                method_key="A_full",
                strategy="full",
                graph_policy="locked",
            )
        finally:
            matrix.remain20.CONFIG = previous_config
    matrix.offline_records.append(
        {
            "source_id": source.boot_key,
            "method_key": "A_full_fixed",
            "strategy": "full",
            "graph_policy": "locked",
            "config_path": str(matrix.CONFIGS["bootv7"]),
            "source_run": str(source_dev_run),
            "source_skill": str(source_skill),
            "skill_path": str(skill),
            "selected_best": best,
            "started_at": started_at,
            "ended_at": matrix.now(),
        }
    )
    matrix.write_manifest("running", {"stage": "afull_creator_done", "skill_path": str(skill), "selected_best": best})
    return skill


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
            "9B BOOTV7 r1 fixed-skill supplement; "
            f"source_prefix={SOURCE_PREFIX}; boot_repeats={BOOT_FIXED_REPEATS}; "
            f"afull_repeats={AFULL_REPEATS}; summary={matrix.SUMMARY_JSON}; report={matrix.REPORT_DOC}"
        ),
    )
    scheduler = threading.Thread(
        target=matrix.scheduler_loop,
        name="gaia-9b-bootv7-r1fixed-scheduler",
        daemon=True,
    )
    matrix.write_manifest(
        "starting",
        {
            "stage": "r1fixed_start",
            "source_prefix": SOURCE_PREFIX,
            "source_repeat": SOURCE_REPEAT,
            "boot_fixed_repeats": BOOT_FIXED_REPEATS,
            "afull_repeats": AFULL_REPEATS,
            "expected_new_counted_eval_count": len(BOOT_FIXED_REPEATS) * 2 + len(AFULL_REPEATS) * 2,
        },
    )
    try:
        source_skill = boot_skill_path()
        matrix.base.wait_endpoints()
        scheduler.start()

        enqueue_fixed_boot_evals(source_skill)
        matrix.wait_method_evals_tail_or_done("boot_fixed")

        candidates = [
            candidate_stats(
                f"source_r{SOURCE_REPEAT}",
                1,
                source_run_name("dev"),
                source_run_name("test"),
            )
        ]
        for repeat in BOOT_FIXED_REPEATS:
            candidates.append(
                candidate_stats(
                    f"fixed_boot_rep{repeat}",
                    repeat,
                    fixed_boot_run_name(repeat, "dev"),
                    fixed_boot_run_name(repeat, "test"),
                )
            )
        best = select_best_source(candidates)
        best_dev_run = Path(str(best["dev_run_dir"]))
        if not best_dev_run.exists():
            raise RuntimeError(f"best dev run dir missing: {best_dev_run}")
        matrix.write_manifest("running", {"stage": "best_boot_source_selected", "candidates": candidates, "best": best})
        matrix.log(f"best boot source selected: {best}")

        afull_skill = run_single_afull_creator(best_dev_run, source_skill, best)
        enqueue_afull_evals(afull_skill, best)
        matrix.wait_method_evals_tail_or_done("A_full_fixed")

        if matrix.offline_failures and matrix.eval_failures:
            status = "finished_with_offline_and_eval_failures"
        elif matrix.offline_failures:
            status = "finished_with_offline_failures"
        elif matrix.eval_failures:
            status = "finished_with_eval_failures"
        elif matrix.released_evals:
            status = "finished_with_longtail_live"
        summary = matrix.write_summary(status, start_time=start_time, end_time=matrix.now())
        summary["fixed_skill_candidates"] = candidates
        summary["selected_best_boot_source"] = best
        matrix.SUMMARY_JSON.write_text(matrix.json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        matrix.append_report(summary)
        matrix.write_manifest(status, {"selected_best_boot_source": best})
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
