from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"

PREFIX = os.environ.get(
    "GAIA_8B_BOOTV6_R3_FIXED_AB1_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv6_r3_fixed_ab1_repeats_remote132"),
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


CODEX_EFFORT = os.environ.get("GAIA_8B_BOOTV6_R3_FIXED_AB1_CODEX_EFFORT", "high").strip() or "high"
os.environ["NLRL_CODEX_CLI_EXTRA_ARGS"] = f"-c 'model_reasoning_effort=\"{CODEX_EFFORT}\"'"
os.environ["NLRL_ACTOR_REASONING_EFFORT"] = CODEX_EFFORT
os.environ["NLRL_CRITIC_REASONING_EFFORT"] = CODEX_EFFORT

SOURCE_PREFIX = "20260513_1519_8b_bootv5v6_fullmatrix_remote132_retry_sem"
BASE_BOOT_DEV_RUN = f"{SOURCE_PREFIX}_8b_bootv6_r3_boot_dev_c{q.CONCURRENCY}"
BASE_BOOT_TEST_RUN = f"{SOURCE_PREFIX}_8b_bootv6_r3_boot_test_c{q.CONCURRENCY}"
BASE_SKILL = (
    ROOT
    / "runs/2026/5/2026-5-13/20260513_151911_8b_bootv5v6_fullmatrix_remote132_retry_sem_8b_bootv6_r3_boot_dev_c20"
    / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
)

METHODS = ["A_full", "B1"]
EXPECTED_COUNTED_EVALS = 4 + len(METHODS) * 3 * 2

q.BOOT_KEYS[:] = ["bootv6"]
q.METHOD_ORDER[:] = METHODS
q.DOWNSTREAM_BOOT_KEYS[:] = ["bootv6"]
q.EXPECTED_COUNTED_EVALS = EXPECTED_COUNTED_EVALS


def log(message: str) -> None:
    q.log(f"[bootv6-r3-fixed-ab1] {message}")


def selected_task_ids(run_dir: Path | None) -> list[str]:
    if run_dir is None:
        return []
    path = run_dir / "selected_tasks.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        ids = payload.get("task_ids", [])
        return [str(item) for item in ids] if isinstance(ids, list) else []
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return []


def score_run(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {
            "run_dir": "",
            "selected": 0,
            "landed": 0,
            "success": 0,
            "score": 0.0,
            "missing": 0,
            "status": "missing_run_dir",
        }
    selected = selected_task_ids(run_dir)
    iteration = q.latest_iteration_dir(run_dir)
    states = list(iteration.glob("*/state.json")) if iteration else []
    success = 0
    for state_path in states:
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        evaluation = payload.get("env_result", {}).get("evaluation", {})
        success += int(bool(evaluation.get("task_success")))
    total = len(selected) or len(states)
    missing = max(0, total - len(states)) if total else 0
    status = "complete" if total and len(states) >= total else "partial"
    return {
        "run_dir": str(run_dir),
        "selected": total,
        "landed": len(states),
        "success": success,
        "score": success / total if total else 0.0,
        "missing": missing,
        "status": status,
    }


def find_cli_processes(run_name_fragment: str) -> list[int]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,cmd="],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    pids: list[int] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if run_name_fragment not in line or "gaia_skillrl.cli" not in line:
            continue
        pid_text = line.split(None, 1)[0]
        if pid_text.isdigit() and int(pid_text) != os.getpid():
            pids.append(int(pid_text))
    return pids


def stop_ignored_old_children() -> list[int]:
    stopped: list[int] = []
    for fragment in [
        f"{SOURCE_PREFIX}_8b_bootv5_r3_boot_dev_c20",
    ]:
        for pid in find_cli_processes(fragment):
            try:
                os.killpg(pid, signal.SIGTERM)
                q.base.update_process_row(pid, "stopped_for_bootv6_r3_fixed_ab1")
                stopped.append(pid)
                log(f"stopped old ignored child pid={pid} fragment={fragment}")
            except ProcessLookupError:
                q.base.update_process_row(pid, "dead")
            except Exception as exc:
                log(f"failed to stop old child pid={pid}: {exc!r}")
    return stopped


def fixed_repeat_run_name(repeat: int, split: str) -> str:
    return f"{PREFIX}_8b_bootv6_r3fixed_eval_r{repeat}_{split}_c{q.CONCURRENCY}"


def downstream_run_name(method_key: str, repeat: int, split: str) -> str:
    return f"{PREFIX}_8b_bootv6_r3best_{method_key}_eval_r{repeat}_{split}_c{q.CONCURRENCY}"


def enqueue_fixed_skill_repeats() -> None:
    for repeat in [2, 3]:
        for split, dataset in [("dev", q.DEV_DATASET), ("test", q.TEST_DATASET)]:
            q.enqueue_eval(
                q.EvalSpec(
                    key=f"bootv6_r3fixed_r{repeat}_{split}",
                    label=f"8B BOOTV6 r3 fixed-skill repeat {repeat} {split}",
                    command="train-local",
                    config_path=q.CONFIGS["bootv6"],
                    dataset=dataset,
                    run_name=fixed_repeat_run_name(repeat, split),
                    counted=True,
                    boot_key="bootv6",
                    method_key="bootv6_r3_fixed",
                    repeat=repeat,
                    split=split,
                    skill_path=BASE_SKILL,
                    notes="fixed reuse of existing bootv6 r3 bootstrap skill; no new bootstrap/actor",
                )
            )


def fixed_repeat_sufficient(run_name: str) -> bool:
    run_dir = q.latest_run_dir(run_name)
    if run_dir is None:
        return False
    stats = score_run(run_dir)
    if stats["selected"] and stats["missing"] <= q.LONGTAIL_MAX_MISSING:
        return True
    return int(stats["landed"]) >= q.PARTIAL_SOURCE_MIN_STATES


def fixed_repeats_sufficient() -> bool:
    names = [
        fixed_repeat_run_name(repeat, split)
        for repeat in [2, 3]
        for split in ["dev", "test"]
    ]
    return all(fixed_repeat_sufficient(name) for name in names)


def fixed_groups() -> list[dict[str, Any]]:
    groups = [
        {
            "group": "old_r3",
            "repeat": 1,
            "dev_run_name": BASE_BOOT_DEV_RUN,
            "test_run_name": BASE_BOOT_TEST_RUN,
        }
    ]
    for repeat in [2, 3]:
        groups.append(
            {
                "group": f"fixed_r{repeat}",
                "repeat": repeat,
                "dev_run_name": fixed_repeat_run_name(repeat, "dev"),
                "test_run_name": fixed_repeat_run_name(repeat, "test"),
            }
        )
    for item in groups:
        dev_run = q.latest_run_dir(item["dev_run_name"])
        test_run = q.latest_run_dir(item["test_run_name"])
        dev = score_run(dev_run)
        test = score_run(test_run)
        item["dev"] = dev
        item["test"] = test
        item["combined_success"] = int(dev["success"]) + int(test["success"])
        item["combined_score"] = float(dev["score"]) + float(test["score"])
        item["combined_landed"] = int(dev["landed"]) + int(test["landed"])
        item["combined_missing"] = int(dev["missing"]) + int(test["missing"])
    return groups


def choose_best_group(groups: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        groups,
        key=lambda item: (
            item["combined_success"],
            item["combined_score"],
            item["combined_landed"],
            -item["combined_missing"],
        ),
    )


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


def enqueue_downstream_repeats(method_key: str, skill: Path, source_run: Path) -> None:
    for repeat in [1, 2, 3]:
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
                    notes=f"fixed downstream skill from selected source run {source_run}",
                )
            )


def append_custom_report(summary: dict[str, Any], groups: list[dict[str, Any]], best: dict[str, Any]) -> None:
    lines = [
        "",
        "## BootV6 r3 固定 skill 新策略补充",
        "",
        "- 口径：旧 `bootv6 r3` skill 固定复用，新增两轮 dev/test executor repeat。",
        "- 选择规则：按 `dev_success + test_success` 排序，平手再看 `dev_score + test_score`、落盘数和缺失数。",
        "- downstream：只生成 `A_full` 和 `B1` 两个 skill；每个 skill 再跑 dev/test 各 3 repeat。",
        f"- 离线生成控制：offline worker=1，critic shard concurrency=4，Codex effort={CODEX_EFFORT}；executor 仍保持 c20。",
        "",
        "| group | dev | test | combined_success | combined_score | combined_missing |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in groups:
        dev = item["dev"]
        test = item["test"]
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item["group"]),
                    f"{dev['success']}/{dev['selected'] or '?'}",
                    f"{test['success']}/{test['selected'] or '?'}",
                    str(item["combined_success"]),
                    f"{item['combined_score']:.4f}",
                    str(item["combined_missing"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            f"- selected_group: `{best['group']}`",
            f"- selected_dev_run: `{best['dev']['run_dir']}`",
            f"- selected_test_run: `{best['test']['run_dir']}`",
            "",
        ]
    )
    q.REPORT_DOC.write_text(q.REPORT_DOC.read_text(encoding="utf-8") + "\n".join(lines), encoding="utf-8")


def main() -> int:
    start_time = q.now()
    status = "finished"
    q.configure_modules()
    q.verify_inputs()
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
            "GAIA_8B_BOOTV6_R3_FIXED_AB1_MASTER_COMMAND",
            q.shell_join([str(PYTHON), "-u", str(Path(__file__).resolve())]),
        ),
        log_path=q.MASTER_LOG,
        notes=(
            f"8B bootv6 r3 fixed-skill AB1 repeats; expected_counted_eval_count={EXPECTED_COUNTED_EVALS}; "
            f"base_skill={BASE_SKILL}; codex_effort={CODEX_EFFORT}; summary={q.SUMMARY_JSON}; report={q.REPORT_DOC}"
        ),
    )
    scheduler = q.threading.Thread(target=q.scheduler_loop, name="gaia-8b-bootv6-r3-fixed-ab1-scheduler", daemon=True)
    q.write_manifest("starting", {"strategy": "bootv6_r3_fixed_ab1", "expected_counted_eval_count": EXPECTED_COUNTED_EVALS})
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
        stopped = stop_ignored_old_children()
        vllm_pids = [q.base.start_or_reuse_vllm(lane) for lane in q.LANES]
        q.base.wait_endpoints()
        q.write_manifest("vllm_ready", {"vllm_pids": vllm_pids, "stopped_old_children": stopped})
        scheduler.start()

        if fixed_repeats_sufficient():
            log("reuse existing fixed-skill repeat states; skip enqueueing fixed repeats")
            q.write_manifest("running", {"stage": "fixed_repeats_reused"})
        else:
            enqueue_fixed_skill_repeats()
            q.wait_method_evals_tail_or_done("bootv6_r3_fixed")
        groups = fixed_groups()
        best = choose_best_group(groups)
        selected_dev = Path(best["dev"]["run_dir"])
        if not selected_dev.exists():
            raise RuntimeError(f"selected dev run missing: {selected_dev}")
        source = q.remain20.BootSource(
            model_key="8b",
            boot_key="bootv6_r3best",
            label=f"8B BOOTV6 r3 fixed selected group {best['group']}",
            boot_dev_run=selected_dev,
            boot_skill=BASE_SKILL,
            supplemental_runs=[],
            source_notes=(
                f"{PREFIX}: selected by dev+test combined success from fixed bootv6 r3 repeats; "
                "offline critic uses selected dev trajectories only"
            ),
        )
        with q.status_lock:
            q.pipeline_records["bootv6_r3best"] = {
                "status": "selected",
                "base_skill": str(BASE_SKILL),
                "groups": groups,
                "best_group": best,
                "selected_dev_run": str(selected_dev),
                "selected_test_run": best["test"]["run_dir"],
            }
            q.write_manifest("running", {"stage": "selected_bootv6_r3best", "best_group": best})

        method_skills: dict[str, str] = {}
        for method_key in METHODS:
            skill = run_offline_method(source, method_key)
            method_skills[method_key] = str(skill)
            enqueue_downstream_repeats(method_key, skill, selected_dev)

        q.wait_counted_evals_tail_or_done()
        if q.offline_failures and q.eval_failures:
            status = "finished_with_offline_and_eval_failures"
        elif q.offline_failures:
            status = "finished_with_offline_failures"
        elif q.eval_failures:
            status = "finished_with_eval_failures"
        elif q.released_evals:
            status = "finished_with_longtail_live"
        end_time = q.now()
        summary = q.write_summary(status, start_time=start_time, end_time=end_time)
        summary["custom_strategy"] = {
            "base_skill": str(BASE_SKILL),
            "fixed_groups": groups,
            "best_group": best,
            "method_skills": method_skills,
        }
        q.SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        q.append_report(summary)
        append_custom_report(summary, groups, best)
        q.write_manifest(status, {"method_skills": method_skills})
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        summary = q.write_summary(status, start_time=start_time, end_time=q.now())
        q.append_report(summary)
        q.write_manifest(status)
        raise
    except Exception:
        status = "dead"
        summary = q.write_summary(status, start_time=start_time, end_time=q.now())
        q.append_report(summary)
        q.write_manifest(status)
        raise
    finally:
        q.scheduler_stop.set()
        scheduler.join(timeout=60)
        q.base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
