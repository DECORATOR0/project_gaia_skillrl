from __future__ import annotations

import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"

PREFIX = os.environ.get(
    "GAIA_8B_BOOTV6_R3BEST_B2B3_FIRST_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv6_r3best_b2b3_first_remote132"),
).strip()

os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_PREFIX"] = PREFIX
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_CONCURRENCY", "20")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_POLL_SECONDS", "60")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_BOOT_WORKERS", "1")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_BOOTSTRAP_WORKERS", "1")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_OFFLINE_WORKERS", "1")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_CRITIC_SHARD_CONCURRENCY", "4")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_PARTIAL_SOURCE_MIN_STATES", "80")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_LONGTAIL_MAX_MISSING", "2")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_LONGTAIL_STALLED_SECONDS", "900")

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_bootv5v6_full_matrix_remote132_20260513 as q  # noqa: E402


CODEX_EFFORT = os.environ.get("GAIA_8B_BOOTV6_R3BEST_B2B3_CODEX_EFFORT", "high").strip() or "high"
os.environ["NLRL_CODEX_CLI_EXTRA_ARGS"] = f"-c 'model_reasoning_effort=\"{CODEX_EFFORT}\"'"
os.environ["NLRL_ACTOR_REASONING_EFFORT"] = CODEX_EFFORT
os.environ["NLRL_CRITIC_REASONING_EFFORT"] = CODEX_EFFORT

SOURCE_RUN = (
    ROOT
    / "runs/2026/5/2026-5-13/20260513_151911_8b_bootv5v6_fullmatrix_remote132_retry_sem_8b_bootv6_r3_boot_dev_c20"
)
BASE_SKILL = SOURCE_RUN / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
METHODS = ["B2", "B3"]
REPEATS_TO_RUN = [1]
EXPECTED_COUNTED_EVALS = len(METHODS) * len(REPEATS_TO_RUN) * 2

q.BOOT_KEYS[:] = ["bootv6"]
q.METHOD_ORDER[:] = METHODS
q.DOWNSTREAM_BOOT_KEYS[:] = ["bootv6"]
q.EXPECTED_COUNTED_EVALS = EXPECTED_COUNTED_EVALS


def log(message: str) -> None:
    q.log(f"[bootv6-r3best-b2b3-first] {message}")


def downstream_run_name(method_key: str, repeat: int, split: str) -> str:
    return f"{PREFIX}_8b_bootv6_r3best_{method_key}_eval_r{repeat}_{split}_c{q.CONCURRENCY}"


def run_offline_method(source: q.remain20.BootSource, method_key: str) -> Path:
    strategy, graph_policy = q.METHOD_SPECS[method_key]
    started_at = q.now()
    try:
        with q.offline_config_lock:
            previous_config = q.remain20.CONFIG
            q.remain20.CONFIG = q.CONFIGS["bootv6"]
            try:
                skill = q.remain20.run_offline_variant(
                    source,
                    method_key=method_key,
                    strategy=strategy,
                    graph_policy=graph_policy,
                )
            finally:
                q.remain20.CONFIG = previous_config
    except Exception as exc:
        failure = {
            "source_id": source.boot_key,
            "method_key": method_key,
            "strategy": strategy,
            "graph_policy": graph_policy,
            "source_run": str(source.boot_dev_run),
            "source_skill": str(source.boot_skill),
            "error": repr(exc),
            "started_at": started_at,
            "ended_at": q.now(),
        }
        with q.status_lock:
            q.offline_failures.append(failure)
            q.write_manifest("running", {"stage": f"offline_failed_{method_key}"})
        raise
    record = {
        "source_id": source.boot_key,
        "method_key": method_key,
        "strategy": strategy,
        "graph_policy": graph_policy,
        "source_run": str(source.boot_dev_run),
        "source_skill": str(source.boot_skill),
        "skill_path": str(skill),
        "started_at": started_at,
        "ended_at": q.now(),
    }
    with q.status_lock:
        q.offline_records.append(record)
        q.write_manifest("running", {"stage": f"offline_done_{method_key}"})
    log(f"offline skill ready method={method_key}: {skill}")
    return skill


def enqueue_first_round(method_key: str, skill: Path) -> None:
    for repeat in REPEATS_TO_RUN:
        for split, dataset in [("dev", q.DEV_DATASET), ("test", q.TEST_DATASET)]:
            q.enqueue_eval(
                q.EvalSpec(
                    key=f"{method_key}_r{repeat}_{split}",
                    label=f"8B BOOTV6 r3best {method_key} repeat {repeat} {split}",
                    command="train-local",
                    config_path=q.CONFIGS["bootv6"],
                    dataset=dataset,
                    run_name=downstream_run_name(method_key, repeat, split),
                    counted=True,
                    boot_key="bootv6",
                    method_key=method_key,
                    repeat=repeat,
                    split=split,
                    skill_path=skill,
                    notes="first macro repeat only; downstream r2/r3 intentionally not enqueued",
                )
            )


def append_custom_report(summary: dict[str, Any], method_skills: dict[str, str]) -> None:
    lines = [
        "",
        "## BootV6 r3best B2/B3 首轮补充",
        "",
        "- 口径：复用 `bootv6 r3best` source，只生成 `B2` 和 `B3` 两个 downstream skill。",
        f"- Codex effort: `{CODEX_EFFORT}`；critic shard concurrency=4；executor c20。",
        "- 当前只发宏观 repeat 1 的 dev/test；r2/r3 暂不连续排队。",
        "",
        "| method | skill |",
        "| --- | --- |",
    ]
    for method_key, skill in method_skills.items():
        lines.append(f"| {method_key} | `{skill}` |")
    lines.append("")
    q.REPORT_DOC.write_text(q.REPORT_DOC.read_text(encoding="utf-8") + "\n".join(lines), encoding="utf-8")


def main() -> int:
    start_time = q.now()
    status = "finished"
    q.configure_modules()
    q.verify_inputs()
    if not SOURCE_RUN.exists():
        raise FileNotFoundError(f"missing source run: {SOURCE_RUN}")
    if not BASE_SKILL.exists():
        raise FileNotFoundError(f"missing base skill: {BASE_SKILL}")

    q.QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    q.LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    q.DESIGN_ROOT.mkdir(parents=True, exist_ok=True)
    q.base.ensure_process_csv_header()
    q.base.refresh_process_registry()
    q.base.append_process_row(
        kind="experiment_master",
        name=q.MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir=str(q.QUEUE_LOG_ROOT),
        command=os.environ.get(
            "GAIA_8B_BOOTV6_R3BEST_B2B3_FIRST_MASTER_COMMAND",
            q.shell_join([str(PYTHON), "-u", str(Path(__file__).resolve())]),
        ),
        log_path=q.MASTER_LOG,
        notes=(
            f"8B bootv6 r3best B2/B3 first macro repeat; expected_counted_eval_count={EXPECTED_COUNTED_EVALS}; "
            f"source_run={SOURCE_RUN}; base_skill={BASE_SKILL}; codex_effort={CODEX_EFFORT}; "
            f"summary={q.SUMMARY_JSON}; report={q.REPORT_DOC}"
        ),
    )
    scheduler = q.threading.Thread(target=q.scheduler_loop, name="gaia-8b-bootv6-r3best-b2b3-scheduler", daemon=True)
    q.write_manifest("starting", {"strategy": "bootv6_r3best_b2b3_first", "expected_counted_eval_count": EXPECTED_COUNTED_EVALS})
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
        vllm_pids = [q.base.start_or_reuse_vllm(lane) for lane in q.LANES]
        q.base.wait_endpoints()
        q.write_manifest("vllm_ready", {"vllm_pids": vllm_pids})
        scheduler.start()

        source = q.remain20.BootSource(
            model_key="8b",
            boot_key="bootv6_r3best",
            label="8B BOOTV6 r3best B2/B3 first macro repeat",
            boot_dev_run=SOURCE_RUN,
            boot_skill=BASE_SKILL,
            supplemental_runs=[],
            source_notes=f"{PREFIX}: selected old bootv6 r3 source; first macro repeat only",
        )
        with q.status_lock:
            q.pipeline_records["bootv6_r3best"] = {
                "status": "selected",
                "base_skill": str(BASE_SKILL),
                "selected_dev_run": str(SOURCE_RUN),
                "repeats_to_run": REPEATS_TO_RUN,
            }
            q.write_manifest("running", {"stage": "selected_bootv6_r3best"})

        method_skills: dict[str, str] = {}
        for method_key in METHODS:
            skill = run_offline_method(source, method_key)
            method_skills[method_key] = str(skill)
            enqueue_first_round(method_key, skill)

        q.wait_counted_evals_tail_or_done()
        if q.offline_failures and q.eval_failures:
            status = "finished_with_offline_and_eval_failures"
        elif q.offline_failures:
            status = "finished_with_offline_failures"
        elif q.eval_failures:
            status = "finished_with_eval_failures"
        elif q.released_evals:
            status = "finished_with_longtail_live"
        summary = q.write_summary(status, start_time=start_time, end_time=q.now())
        summary["custom_strategy"] = {
            "base_skill": str(BASE_SKILL),
            "selected_dev_run": str(SOURCE_RUN),
            "methods": METHODS,
            "repeats_to_run": REPEATS_TO_RUN,
            "method_skills": method_skills,
            "codex_effort": CODEX_EFFORT,
        }
        q.SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        q.append_report(summary)
        append_custom_report(summary, method_skills)
        q.write_manifest(status, {"method_skills": method_skills})
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        summary = q.write_summary(status, start_time=start_time, end_time=q.now())
        q.append_report(summary)
        q.write_manifest(status)
        raise
    except Exception:
        status = "failed"
        summary = q.write_summary(status, start_time=start_time, end_time=q.now())
        q.append_report(summary)
        q.write_manifest(status)
        raise
    finally:
        q.scheduler_stop.set()
        q.base.refresh_process_registry()


if __name__ == "__main__":
    raise SystemExit(main())
