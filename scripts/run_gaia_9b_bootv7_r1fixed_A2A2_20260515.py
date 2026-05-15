from __future__ import annotations

import os
import signal
import threading
from pathlib import Path
from typing import Any


PREFIX_DEFAULT = "20260515_0052_9b_bootv7_r1fixed_A2A2_gpu0_1"

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
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_GPU_IDS", "0,1")
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
        / "实验设计与迭代/26.5.15_0052_GAIA_9B_BOOTV7_A2A2两卡执行记录.md"
    ),
)

import run_gaia_8b_bootv7v8_v3v6_matrix_remote132_20260514 as matrix  # noqa: E402


ROOT = matrix.ROOT
A2_CONFIG = ROOT / "configs/system_bootv7_pseudocomplete_a2_phase.json"
BOOT_SOURCE_PREFIX = os.environ.get(
    "GAIA_9B_BOOTV7_A2A2_BOOT_SOURCE_PREFIX",
    "20260514_1221_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5",
).strip()
BOOT_SOURCE_REPEAT = int(os.environ.get("GAIA_9B_BOOTV7_A2A2_BOOT_SOURCE_REPEAT", "1"))
FIXED_BOOT_PREFIX = os.environ.get(
    "GAIA_9B_BOOTV7_A2A2_FIXED_BOOT_PREFIX",
    "20260514_1635_9b_bootv7_r1fixed_bestsource_afull_gpu0_5",
).strip()
FIXED_BOOT_REPEATS = [
    int(item)
    for item in os.environ.get("GAIA_9B_BOOTV7_A2A2_FIXED_BOOT_REPEATS", "2,3").split(",")
    if item.strip()
]
A2_REPEATS = [
    int(item)
    for item in os.environ.get("GAIA_9B_BOOTV7_A2A2_A2_REPEATS", "1,2,3").split(",")
    if item.strip()
]
A2A2_REPEATS = [
    int(item)
    for item in os.environ.get("GAIA_9B_BOOTV7_A2A2_A2A2_REPEATS", "1,2,3").split(",")
    if item.strip()
]
BASELINE_REPEATS = [
    int(item)
    for item in os.environ.get("GAIA_9B_BOOTV7_A2A2_BASELINE_REPEATS", "1,2,3").split(",")
    if item.strip()
]

matrix.CONFIGS["bootv7_a2"] = A2_CONFIG
matrix.EXPECTED_COUNTED_EVALS = (len(A2_REPEATS) + len(A2A2_REPEATS) + len(BASELINE_REPEATS)) * 2


def boot_source_run_name(split: str) -> str:
    return f"{BOOT_SOURCE_PREFIX}_9b_bootv7_r{BOOT_SOURCE_REPEAT}_boot_{split}_c20"


def fixed_boot_run_name(repeat: int, split: str) -> str:
    return f"{FIXED_BOOT_PREFIX}_9b_bootv7_r1skill_bootrep{repeat}_{split}_c20"


def a2_eval_run_name(method_key: str, repeat: int, split: str) -> str:
    return f"{matrix.PREFIX}_{matrix.RUN_MODEL_TOKEN}_bootv7_r1fixed_{method_key}_rep{repeat}_{split}_c{matrix.CONCURRENCY}"


def baseline_run_name(repeat: int, split: str) -> str:
    return f"{matrix.PREFIX}_{matrix.RUN_MODEL_TOKEN}_baseline_rep{repeat}_{split}_c{matrix.CONCURRENCY}"


def boot_skill_path() -> Path:
    run_dir = matrix.latest_run_dir(boot_source_run_name("dev"))
    if run_dir is None:
        raise RuntimeError(f"missing boot source dev run: {boot_source_run_name('dev')}")
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    if not skill.exists():
        raise RuntimeError(f"missing fixed boot skill: {skill}")
    return skill


def stats_for(run_name: str, *, label: str, method_key: str, repeat: int, split: str) -> dict[str, Any]:
    return matrix.run_stats(
        matrix.EvalSpec(
            key=f"stats_{method_key}_{repeat}_{split}",
            label=label,
            command="train-local",
            config_path=A2_CONFIG,
            dataset=matrix.DEV_DATASET if split == "dev" else matrix.TEST_DATASET,
            run_name=run_name,
            counted=True,
            boot_key="bootv7",
            method_key=method_key,
            repeat=repeat,
            split=split,
        )
    )


def candidate_stats(label: str, repeat: int, dev_run: str, test_run: str, *, method_key: str) -> dict[str, Any]:
    dev = stats_for(dev_run, label=f"{label} dev", method_key=method_key, repeat=repeat, split="dev")
    test = stats_for(test_run, label=f"{label} test", method_key=method_key, repeat=repeat, split="test")
    return {
        "label": label,
        "repeat": repeat,
        "method_key": method_key,
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


def select_best(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        candidates,
        key=lambda item: (
            item["score_total"],
            item["dev_success"],
            item["test_success"],
            item["landed_total"],
        ),
    )


def fixed_boot_candidates() -> list[dict[str, Any]]:
    candidates = [
        candidate_stats(
            f"source_r{BOOT_SOURCE_REPEAT}",
            BOOT_SOURCE_REPEAT,
            boot_source_run_name("dev"),
            boot_source_run_name("test"),
            method_key="fixed_boot_source",
        )
    ]
    for repeat in FIXED_BOOT_REPEATS:
        candidates.append(
            candidate_stats(
                f"fixed_boot_rep{repeat}",
                repeat,
                fixed_boot_run_name(repeat, "dev"),
                fixed_boot_run_name(repeat, "test"),
                method_key="fixed_boot_source",
            )
        )
    return candidates


def run_offline_creator(
    *,
    method_key: str,
    source_dev_run: Path,
    input_skill: Path,
    source_key: str,
    label: str,
    source_notes: str,
) -> Path:
    source = matrix.remain20.BootSource(
        model_key=matrix.RUN_MODEL_TOKEN,
        boot_key=source_key,
        label=label,
        boot_dev_run=source_dev_run,
        boot_skill=input_skill,
        supplemental_runs=[],
        source_notes=source_notes,
    )
    started_at = matrix.now()
    with matrix.offline_config_lock:
        previous_config = matrix.remain20.CONFIG
        matrix.remain20.CONFIG = A2_CONFIG
        try:
            skill = matrix.remain20.run_offline_variant(
                source,
                method_key=method_key,
                strategy="full",
                graph_policy="locked",
            )
        finally:
            matrix.remain20.CONFIG = previous_config
    with matrix.status_lock:
        matrix.offline_records.append(
            {
                "source_id": source.boot_key,
                "method_key": method_key,
                "strategy": "full",
                "graph_policy": "locked",
                "config_path": str(A2_CONFIG),
                "source_run": str(source_dev_run),
                "source_skill": str(input_skill),
                "skill_path": str(skill),
                "source_notes": source_notes,
                "started_at": started_at,
                "ended_at": matrix.now(),
            }
        )
        matrix.write_manifest("running", {"stage": f"{method_key}_offline_done", "skill_path": str(skill)})
    return skill


def enqueue_skill_repeats(method_key: str, skill: Path, repeats: list[int], *, notes: str) -> None:
    for repeat in repeats:
        for split, dataset in [("dev", matrix.DEV_DATASET), ("test", matrix.TEST_DATASET)]:
            matrix.enqueue_eval(
                matrix.EvalSpec(
                    key=f"{method_key}_rep{repeat}_{split}",
                    label=f"9B BOOTV7 r1-fixed {method_key} repeat {repeat} {split}",
                    command="train-local",
                    config_path=A2_CONFIG,
                    dataset=dataset,
                    run_name=a2_eval_run_name(method_key, repeat, split),
                    counted=True,
                    boot_key="bootv7",
                    method_key=method_key,
                    repeat=repeat,
                    split=split,
                    skill_path=skill,
                    notes=notes,
                )
            )


def enqueue_baseline_repeats() -> None:
    for repeat in BASELINE_REPEATS:
        for split, dataset in [("dev", matrix.DEV_DATASET), ("test", matrix.TEST_DATASET)]:
            matrix.enqueue_eval(
                matrix.EvalSpec(
                    key=f"baseline_rep{repeat}_{split}",
                    label=f"9B baseline repeat {repeat} {split}",
                    command="direct-eval-local",
                    config_path=A2_CONFIG,
                    dataset=dataset,
                    run_name=baseline_run_name(repeat, split),
                    counted=True,
                    boot_key="baseline",
                    method_key="baseline",
                    repeat=repeat,
                    split=split,
                    notes="low-priority baseline repeat appended after A2A2",
                )
            )


def a2_candidates() -> list[dict[str, Any]]:
    return [
        candidate_stats(
            f"A2_rep{repeat}",
            repeat,
            a2_eval_run_name("A2", repeat, "dev"),
            a2_eval_run_name("A2", repeat, "test"),
            method_key="A2",
        )
        for repeat in A2_REPEATS
    ]


def append_custom_report(summary: dict[str, Any], *, fixed_best: dict[str, Any], a2_best: dict[str, Any]) -> None:
    lines = [
        "",
        "## Startup Confirmation",
        "",
        "- flow: fixed BOOTV7 best source -> A2 -> A2 eval x3 -> best A2 -> A2A2 -> A2A2 eval x3 -> baseline eval x3.",
        "- executor: Qwen3.5-9B-local vLLM with thinking enabled; max_model_len=49152; max_tokens=12288.",
        "- strong model: gpt-5.2 responses_sse high for offline critic and actor.",
        "- resources: GPU 0,1 only; ports 8128,8129 only; two-lane queue.",
        "- tail: 95% or missing<=2 after stalled threshold releases the lane and leaves long-tail children alive.",
        "",
        "## A2A2 Custom Chain",
        "",
        f"- selected_fixed_boot_source: `{fixed_best['label']}` score_total=`{fixed_best['score_total']}`",
        f"- selected_fixed_boot_dev_run: `{fixed_best['dev_run_name']}`",
        f"- selected_A2_repeat: `{a2_best['label']}` score_total=`{a2_best['score_total']}`",
        f"- selected_A2_dev_run: `{a2_best['dev_run_name']}`",
        f"- counted evals: `{summary.get('actual_counted_eval_specs')}`",
        f"- lanes: `{','.join(str(lane.gpu) for lane in matrix.LANES)}`",
        "",
    ]
    with matrix.REPORT_DOC.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main() -> int:
    start_time = matrix.now()
    status = "finished"
    matrix.configure_modules()
    matrix.verify_inputs()
    if not A2_CONFIG.exists():
        raise RuntimeError(f"missing A2 config: {A2_CONFIG}")
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
        command=matrix.shell_join([str(matrix.PYTHON), "-u", str(Path(__file__).resolve())]),
        log_path=matrix.MASTER_LOG,
        notes=(
            "9B BOOTV7 r1 fixed A2->A2 plus baseline repeats; "
            f"gpu_ids={matrix.GPU_IDS}; expected_counted_eval_count={matrix.EXPECTED_COUNTED_EVALS}; "
            f"a2_config={A2_CONFIG}; summary={matrix.SUMMARY_JSON}; report={matrix.REPORT_DOC}"
        ),
    )
    scheduler = threading.Thread(
        target=matrix.scheduler_loop,
        name="gaia-9b-bootv7-a2a2-scheduler",
        daemon=True,
    )
    matrix.write_manifest(
        "starting",
        {
            "stage": "a2a2_start",
            "a2_config": str(A2_CONFIG),
            "gpu_ids": matrix.GPU_IDS,
            "a2_repeats": A2_REPEATS,
            "a2a2_repeats": A2A2_REPEATS,
            "baseline_repeats": BASELINE_REPEATS,
        },
    )
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
        source_skill = boot_skill_path()
        vllm_pids = [matrix.base.start_or_reuse_vllm(lane) for lane in matrix.LANES]
        matrix.base.wait_endpoints()
        matrix.write_manifest("vllm_ready", {"vllm_pids": vllm_pids})
        scheduler.start()

        fixed_candidates = fixed_boot_candidates()
        fixed_best = select_best(fixed_candidates)
        fixed_best_dev_run = Path(str(fixed_best["dev_run_dir"]))
        if not fixed_best_dev_run.exists():
            raise RuntimeError(f"selected fixed boot dev run missing: {fixed_best_dev_run}")
        matrix.write_manifest(
            "running",
            {
                "stage": "fixed_boot_source_selected",
                "fixed_boot_candidates": fixed_candidates,
                "selected_fixed_boot_source": fixed_best,
            },
        )
        matrix.log(f"selected fixed boot source for A2: {fixed_best}")

        a2_skill = run_offline_creator(
            method_key="A2",
            source_dev_run=fixed_best_dev_run,
            input_skill=source_skill,
            source_key="bootv7_r1fixed_best",
            label=f"9B BOOTV7 r1-fixed A2 source {fixed_best['label']}",
            source_notes=f"selected fixed boot source score_total={fixed_best['score_total']}",
        )
        enqueue_skill_repeats("A2", a2_skill, A2_REPEATS, notes=f"A2 skill from {fixed_best['label']}")
        matrix.wait_method_evals_tail_or_done("A2")

        a2_stats = a2_candidates()
        a2_best = select_best(a2_stats)
        a2_best_dev_run = Path(str(a2_best["dev_run_dir"]))
        if not a2_best_dev_run.exists():
            raise RuntimeError(f"selected A2 dev run missing: {a2_best_dev_run}")
        matrix.write_manifest(
            "running",
            {"stage": "A2_best_selected", "a2_candidates": a2_stats, "selected_a2_source": a2_best},
        )
        matrix.log(f"selected A2 source for A2A2: {a2_best}")

        a2a2_skill = run_offline_creator(
            method_key="A2A2",
            source_dev_run=a2_best_dev_run,
            input_skill=a2_skill,
            source_key=f"bootv7_A2best_r{a2_best['repeat']}",
            label=f"9B BOOTV7 A2A2 source {a2_best['label']}",
            source_notes=f"selected A2 repeat score_total={a2_best['score_total']}",
        )
        enqueue_skill_repeats("A2A2", a2a2_skill, A2A2_REPEATS, notes=f"A2A2 skill from {a2_best['label']}")
        matrix.wait_method_evals_tail_or_done("A2A2")

        enqueue_baseline_repeats()
        matrix.wait_method_evals_tail_or_done("baseline")

        if matrix.offline_failures and matrix.eval_failures:
            status = "finished_with_offline_and_eval_failures"
        elif matrix.offline_failures:
            status = "finished_with_offline_failures"
        elif matrix.eval_failures:
            status = "finished_with_eval_failures"
        elif matrix.released_evals:
            status = "finished_with_longtail_live"
        summary = matrix.write_summary(status, start_time=start_time, end_time=matrix.now())
        summary["fixed_boot_candidates"] = fixed_candidates
        summary["selected_fixed_boot_source"] = fixed_best
        summary["a2_skill"] = str(a2_skill)
        summary["a2_candidates"] = a2_stats
        summary["selected_a2_source"] = a2_best
        summary["a2a2_skill"] = str(a2a2_skill)
        matrix.SUMMARY_JSON.write_text(matrix.json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        matrix.append_report(summary)
        append_custom_report(summary, fixed_best=fixed_best, a2_best=a2_best)
        matrix.write_manifest(
            status,
            {
                "selected_fixed_boot_source": fixed_best,
                "selected_a2_source": a2_best,
                "a2_skill": str(a2_skill),
                "a2a2_skill": str(a2a2_skill),
            },
        )
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
        if scheduler.ident is not None:
            scheduler.join(timeout=60)
        matrix.base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
