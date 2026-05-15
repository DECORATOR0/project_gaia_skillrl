from __future__ import annotations

import csv
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"
CONFIG_V4 = ROOT / "configs/system.json"
CONFIG_V5 = ROOT / "configs/system_bootv5_graph_free.json"
PROMPT_ROOT_V5 = ROOT / "prompts/boot_v5_graph_free"
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

PREFIX = os.environ.get(
    "GAIA_BOOTV5_GRAPH_FREE_STEPS_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_9b_bootv5_graphfree_steps"),
).strip()
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(
    os.environ.get(
        "GAIA_BOOTV5_GRAPH_FREE_STEPS_MASTER_LOG",
        QUEUE_LOG_ROOT / f"{MASTER_NAME}.log",
    )
)
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"

CONCURRENCY = int(os.environ.get("GAIA_BOOTV5_GRAPH_FREE_STEPS_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_BOOTV5_GRAPH_FREE_STEPS_POLL_SECONDS", "180"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_BOOTV5_GRAPH_FREE_STEPS_CODEX_TIMEOUT_SECONDS", "3600"))
PREPROCESS_NOTE_CHAR_CAP = os.environ.get("GAIA_BOOTV5_GRAPH_FREE_STEPS_PREPROCESS_NOTE_CHAR_CAP", "700")
LONGTAIL_FRACTION = float(os.environ.get("GAIA_BOOTV5_GRAPH_FREE_STEPS_LONGTAIL_FRACTION", "0.95"))
LONGTAIL_MAX_MISSING = int(os.environ.get("GAIA_BOOTV5_GRAPH_FREE_STEPS_LONGTAIL_MAX_MISSING", "2"))
LONGTAIL_STALLED_SECONDS = int(os.environ.get("GAIA_BOOTV5_GRAPH_FREE_STEPS_LONGTAIL_STALLED_SECONDS", "900"))
V5_STEPS = [24, 48]
FOLLOWUP_STEP = 48

os.environ["GAIA_B3_CONTINUE254_PREFIX"] = PREFIX
os.environ["GAIA_B3_CONTINUE254_MASTER_LOG"] = str(MASTER_LOG)
os.environ["GAIA_B3_CONTINUE254_CONCURRENCY"] = str(CONCURRENCY)
os.environ["GAIA_B3_CONTINUE254_CODEX_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
os.environ.setdefault("GAIA_B3_CONTINUE254_POLL_SECONDS", str(POLL_SECONDS))
os.environ.setdefault("GAIA_24EXP_PREFIX", PREFIX)
os.environ.setdefault("GAIA_24EXP_TASK_CONCURRENCY", str(CONCURRENCY))
os.environ.setdefault("GAIA_24EXP_CODEX_TIMEOUT_SECONDS", str(CODEX_TIMEOUT_SECONDS))
os.environ.setdefault("NLRL_CODEX_CLI_WORKDIR", str(ROOT))
os.environ.setdefault("GAIA_REMAIN20_ALLOW_PARTIAL_SOURCE_STATES", "1")
os.environ.setdefault("GAIA_REMAIN20_CRITIC_SHARD_CONCURRENCY", "99")
os.environ.setdefault("GAIA_SEARCH_RUNTIME_CONFIG", str(SEARCH_RUNTIME_CONFIG))

sys.path.insert(0, str(SCRIPTS))
import gaia_queue_guard as queue_guard  # noqa: E402
import run_gaia_9b_bootv4_b3_continue254_20260508 as prev  # noqa: E402


@dataclass
class LaunchedEval:
    label: str
    group: str
    run_name: str
    dataset: Path
    runner: str
    config_path: Path
    step_budget: int
    lane: prev.Lane
    process: subprocess.Popen[str]
    log_path: Path
    skill_path: Path | None = None
    bootstrap: bool = False
    started_at: str = ""
    returncode: int | None = None
    ended_at: str | None = None
    run_dir: str = ""


launched: dict[str, LaunchedEval] = {}
stage_records: list[dict[str, Any]] = []
offline_records: list[dict[str, Any]] = []
predecessors: list[dict[str, str]] = []


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    return prev.command_display(env, cmd)


def common_env(*, lane: prev.Lane, step_budget: int, bootstrap_preprocess: bool) -> dict[str, str]:
    env = prev.common_env(lane)
    env["GAIA_SEARCH_RUNTIME_CONFIG"] = str(SEARCH_RUNTIME_CONFIG)
    env["NLRL_RUNTIME_TASK_CONCURRENCY"] = str(CONCURRENCY)
    env["NLRL_LLM_MAX_CONCURRENT_REQUESTS"] = str(CONCURRENCY)
    env["NLRL_RUNTIME_MAX_EXECUTOR_STEPS"] = str(step_budget)
    env["NLRL_ACTOR_MODEL"] = "gpt-5.4"
    env["NLRL_ACTOR_BASE_URL"] = "codex-cli"
    env["NLRL_ACTOR_API_KEY"] = "EMPTY"
    env["NLRL_ACTOR_API_MODE"] = "codex_cli"
    env["NLRL_ACTOR_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    env["NLRL_CRITIC_MODEL"] = "gpt-5.4"
    env["NLRL_CRITIC_BASE_URL"] = "codex-cli"
    env["NLRL_CRITIC_API_KEY"] = "EMPTY"
    env["NLRL_CRITIC_API_MODE"] = "codex_cli"
    env["NLRL_CRITIC_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    env["NLRL_CODEX_CLI_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    if bootstrap_preprocess:
        env["NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS"] = "1"
        env["NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP"] = PREPROCESS_NOTE_CHAR_CAP
    else:
        env.pop("NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS", None)
        env.pop("NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP", None)
    return env


def endpoints_ready() -> bool:
    for lane in prev.LANES:
        ids = prev.endpoint_models(lane.base_url)
        if not any(item == lane.served_name or item.endswith("/Qwen3.5-9B") for item in ids):
            return False
    return True


def ensure_9b_services() -> None:
    mode = os.environ.get("GAIA_BOOTV5_GRAPH_FREE_STEPS_DEPLOY_MODE", "auto").strip().lower()
    if mode in {"skip", "reuse"}:
        os.environ["GAIA_B3_CONTINUE254_SKIP_DEPLOY"] = "1"
        prev.wait_9b_endpoints()
        return
    if mode == "restart":
        os.environ.pop("GAIA_B3_CONTINUE254_SKIP_DEPLOY", None)
        prev.deploy_local_9b_services()
        prev.wait_9b_endpoints()
        return
    if endpoints_ready():
        log("reusing existing 254 9B endpoints")
        prev.wait_9b_endpoints()
        return
    os.environ.pop("GAIA_B3_CONTINUE254_SKIP_DEPLOY", None)
    prev.deploy_local_9b_services()
    prev.wait_9b_endpoints()


def latest_run_dir(run_name: str) -> Path | None:
    return prev.latest_run_dir(run_name)


def latest_iteration_dir(run_dir: Path) -> Path | None:
    return prev.latest_iteration_dir(run_dir)


def state_count(run_name: str) -> tuple[int, int, float]:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return 0, 0, 0.0
    progress = queue_guard.state_progress(run_dir)
    return progress.landed, progress.total, progress.last_state_mtime


def bootstrap_skill_path(run_name: str) -> Path | None:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def verify_inputs() -> None:
    missing = [
        str(path)
        for path in [
            PYTHON,
            CONFIG_V4,
            CONFIG_V5,
            PROMPT_ROOT_V5 / "bootstrap_skill_system.md",
            PROMPT_ROOT_V5 / "bootstrap_skill_user.md",
            DEV_DATASET,
            TEST_DATASET,
            SEARCH_RUNTIME_CONFIG,
        ]
        if not path.exists()
    ]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_process_rows() -> list[dict[str, str]]:
    if not PROCESS_CSV.exists():
        return []
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def capture_predecessors() -> list[dict[str, str]]:
    rows = []
    for row in read_process_rows():
        if row.get("status", "").strip() != "running":
            continue
        kind = row.get("kind", "").strip()
        if kind != "experiment":
            continue
        name = row.get("name", "").strip()
        if not name or name.startswith(PREFIX):
            continue
        pid_text = row.get("pid", "").strip()
        if not pid_text.isdigit() or not pid_alive(int(pid_text)):
            continue
        rows.append(row)
    return rows


def predecessor_progress(row: dict[str, str]) -> dict[str, Any]:
    name = row.get("name", "")
    pid_text = row.get("pid", "").strip()
    live = pid_text.isdigit() and pid_alive(int(pid_text))
    progress = queue_guard.state_progress(latest_run_dir(name))
    payload = queue_guard.progress_payload(
        name,
        progress,
        live=live,
        fraction=LONGTAIL_FRACTION,
        max_missing=LONGTAIL_MAX_MISSING,
        stalled_seconds=LONGTAIL_STALLED_SECONDS,
    )
    payload.update({
        "name": name,
        "pid": pid_text,
        "done": progress.landed,
        "ready": payload["longtail_ready"],
    })
    return payload


def wait_predecessor_longtail() -> None:
    if not predecessors:
        log("no live predecessors captured")
        write_manifest("waiting_predecessors", {"predecessors": []})
        return
    while True:
        prev.base.refresh_process_registry()
        progress = [predecessor_progress(row) for row in predecessors]
        write_manifest("waiting_predecessors", {"predecessors": progress})
        if all(item["ready"] for item in progress):
            log("predecessors reached long-tail/ended: " + ", ".join(item["name"] for item in progress))
            return
        summary = "; ".join(
            f"{item['name']}={item['done']}/{item['total'] or '?'} live={item['live']} ready={item['ready']}"
            for item in progress
        )
        log(f"waiting predecessors long-tail: {summary}")
        time.sleep(POLL_SECONDS)


def write_manifest(status: str, extra: dict[str, Any] | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "summary_json": str(SUMMARY_JSON),
        "v5_steps": V5_STEPS,
        "followup_step": FOLLOWUP_STEP,
        "lanes": [asdict(lane) for lane in prev.LANES],
        "stage_records": stage_records,
        "offline_records": offline_records,
        "evals": {
            label: {
                "label": item.label,
                "group": item.group,
                "run_name": item.run_name,
                "dataset": str(item.dataset),
                "runner": item.runner,
                "config_path": str(item.config_path),
                "step_budget": item.step_budget,
                "lane": asdict(item.lane),
                "pid": item.process.pid,
                "log_path": str(item.log_path),
                "skill_path": str(item.skill_path) if item.skill_path else "",
                "bootstrap": item.bootstrap,
                "started_at": item.started_at,
                "returncode": item.returncode,
                "ended_at": item.ended_at,
                "run_dir": item.run_dir,
                "stats": prev.stats_for_run(item.run_name),
            }
            for label, item in launched.items()
        },
    }
    if extra:
        payload.update(extra)
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_summary(status: str) -> None:
    payload = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "manifest_json": str(MANIFEST_JSON),
        "stage_records": stage_records,
        "offline_records": offline_records,
        "evals": {
            label: {
                "run_name": item.run_name,
                "returncode": item.returncode,
                "group": item.group,
                "runner": item.runner,
                "step_budget": item.step_budget,
                "lane": asdict(item.lane),
                "log_path": str(item.log_path),
                "run_dir": item.run_dir,
                "stats": prev.stats_for_run(item.run_name),
            }
            for label, item in launched.items()
        },
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def launch_eval(
    *,
    label: str,
    group: str,
    run_name: str,
    dataset: Path,
    lane: prev.Lane,
    config_path: Path,
    step_budget: int,
    runner: str = "train-local",
    skill_path: Path | None = None,
    bootstrap: bool = False,
    bootstrap_preprocess: bool = False,
) -> LaunchedEval:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{short_ts()}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(config_path),
        runner,
        "--dataset-path",
        str(dataset),
        "--run-name",
        run_name,
    ]
    if bootstrap:
        cmd.append("--bootstrap-skill")
    env = common_env(lane=lane, step_budget=step_budget, bootstrap_preprocess=bootstrap_preprocess)
    if skill_path is not None:
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(skill_path)
    else:
        env.pop("NLRL_RUNTIME_INITIAL_SKILL_PATH", None)
    with log_path.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    prev.append_process_row(
        kind="experiment",
        name=run_name,
        pid=process.pid,
        cwd=str(ROOT),
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=str(log_path),
        notes=(
            f"9B BOOT-V5 graph-free queue {label}; group={group}; step={step_budget}; "
            f"runner={runner}; lane={lane.name}; endpoint={lane.base_url}; "
            f"bootstrap={bootstrap}; skill={skill_path or '(none/bootstrap)'}; {PREFIX}"
        ),
    )
    item = LaunchedEval(
        label=label,
        group=group,
        run_name=run_name,
        dataset=dataset,
        runner=runner,
        config_path=config_path,
        step_budget=step_budget,
        lane=lane,
        process=process,
        log_path=log_path,
        skill_path=skill_path,
        bootstrap=bootstrap,
        started_at=now(),
    )
    launched[label] = item
    log(f"started {label} pid={process.pid} lane={lane.name} step={step_budget} log={log_path}")
    write_manifest("running", {"stage": f"started_{label}"})
    return item


def poll_launched() -> None:
    changed = False
    for item in launched.values():
        if item.returncode is not None:
            continue
        code = item.process.poll()
        if code is None:
            continue
        item.returncode = code
        item.ended_at = now()
        run_dir = latest_run_dir(item.run_name)
        item.run_dir = str(run_dir) if run_dir else ""
        prev.base.update_process_row(item.process.pid, "finished" if code == 0 else "dead", code)
        log(f"eval exited {item.label} pid={item.process.pid} code={code}")
        changed = True
    if changed:
        write_manifest("running")


def item_progress(item: LaunchedEval) -> dict[str, Any]:
    live = item.returncode is None and item.process.poll() is None
    progress = queue_guard.state_progress(latest_run_dir(item.run_name))
    payload = queue_guard.progress_payload(
        item.label,
        progress,
        live=live,
        fraction=LONGTAIL_FRACTION,
        max_missing=LONGTAIL_MAX_MISSING,
        stalled_seconds=LONGTAIL_STALLED_SECONDS,
    )
    payload.update({
        "run_name": item.run_name,
        "done": progress.landed,
        "longtail": payload["longtail_ready"],
    })
    return payload


def item_blocks_lane(item: LaunchedEval) -> bool:
    progress = item_progress(item)
    return bool(progress["live"]) and not bool(progress["longtail"])


def first_free_lane(preferred_index: int = 0) -> prev.Lane:
    ordered = prev.LANES[preferred_index:] + prev.LANES[:preferred_index]
    while True:
        poll_launched()
        busy = {item.lane.name for item in launched.values() if item_blocks_lane(item)}
        for lane in ordered:
            if lane.name not in busy:
                return lane
        live = [item_progress(item) for item in launched.values() if item.returncode is None]
        log(f"waiting for a free or long-tail 254 9B lane; live={live}")
        time.sleep(30)


def wait_boot_skill(boot_dev: LaunchedEval) -> Path:
    while True:
        poll_launched()
        skill = bootstrap_skill_path(boot_dev.run_name)
        if skill is not None:
            log(f"bootstrap skill ready for {boot_dev.label}: {skill}")
            return skill
        if boot_dev.returncode is not None:
            raise RuntimeError(f"{boot_dev.label} exited before bootstrap skill was ready: code={boot_dev.returncode}")
        done, total, _last_mtime = state_count(boot_dev.run_name)
        log(f"waiting bootstrap skill for {boot_dev.label}; states={done}/{total or '?'}")
        time.sleep(min(60, POLL_SECONDS))


def wait_eval_done(item: LaunchedEval) -> None:
    while True:
        poll_launched()
        if item.returncode is not None:
            return
        done, total, _last_mtime = state_count(item.run_name)
        log(f"waiting {item.label}; states={done}/{total or '?'}")
        time.sleep(POLL_SECONDS)


def wait_labels(labels: list[str]) -> None:
    label_set = set(labels)
    while True:
        poll_launched()
        live = [label for label, item in launched.items() if label in label_set and item.returncode is None]
        write_manifest("running", {"waiting_labels": labels, "live": live})
        if not live:
            return
        log(f"waiting labels; live={live}")
        time.sleep(POLL_SECONDS)


def wait_labels_longtail(labels: list[str]) -> None:
    label_set = set(labels)
    while True:
        poll_launched()
        progress = [item_progress(item) for label, item in launched.items() if label in label_set]
        write_manifest("running", {"waiting_longtail_labels": labels, "progress": progress})
        if progress and all(item["longtail"] for item in progress):
            log("labels reached long-tail/ended: " + ", ".join(labels))
            return
        summary = "; ".join(
            f"{item['label']}={item['done']}/{item['total'] or '?'} live={item['live']} longtail={item['longtail']}"
            for item in progress
        )
        log(f"waiting labels long-tail: {summary}")
        time.sleep(POLL_SECONDS)


def wait_boot_source(boot_dev: LaunchedEval) -> Path:
    wait_eval_done(boot_dev)
    run_dir = latest_run_dir(boot_dev.run_name)
    if run_dir is None:
        raise RuntimeError(f"{boot_dev.label} ended but run_dir is missing: {boot_dev.run_name}")
    return run_dir


def inspect_boot_artifacts(run_name: str) -> dict[str, Any]:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return {"run_name": run_name, "run_dir": "", "status": "missing_run_dir"}
    bootstrap_dir = run_dir / "bootstrap"
    skill_path = bootstrap_skill_path(run_name)
    skill_text = skill_path.read_text(encoding="utf-8", errors="replace") if skill_path and skill_path.exists() else ""
    phase_count = skill_text.count("\n## Phase:")
    return {
        "run_name": run_name,
        "run_dir": str(run_dir),
        "config_snapshot": str(run_dir / "config_snapshot.json"),
        "bootstrap_input": str(bootstrap_dir / "bootstrap_input.json"),
        "bootstrap_preprocessed_input": str(bootstrap_dir / "bootstrap_preprocessed_batch_input.json"),
        "bootstrap_requests": [str(path) for path in sorted(bootstrap_dir.glob("*bootstrap_initial_skill_request.json"))],
        "skill_path": str(skill_path) if skill_path else "",
        "skill_exists": bool(skill_path and skill_path.exists()),
        "phase_count": phase_count,
        "contains_conclude": "CONCLUDE" in skill_text,
        "metadata_boot_v5": "boot-v5" in skill_text,
    }


def run_offline_a(
    *,
    boot_key: str,
    source_label: str,
    step_budget: int,
    source_run: Path,
    source_skill: Path,
    config_path: Path,
) -> Path:
    source = prev.base.BootSource(
        model_key="9b",
        boot_key=boot_key,
        label=source_label,
        boot_dev_run=source_run,
        boot_skill=source_skill,
        supplemental_runs=[],
        source_notes=(
            f"2026-05-11 queued step{step_budget}: A_full from same fresh boot_dev states; "
            "strategy=full graph_policy=locked"
        ),
    )
    old_step = os.environ.get("NLRL_RUNTIME_MAX_EXECUTOR_STEPS")
    old_config = prev.base.CONFIG
    os.environ["NLRL_RUNTIME_MAX_EXECUTOR_STEPS"] = str(step_budget)
    started = now()
    try:
        prev.base.CONFIG = config_path
        skill = prev.base.run_offline_variant(source, method_key="A_full", strategy="full", graph_policy="locked")
    finally:
        prev.base.CONFIG = old_config
        if old_step is None:
            os.environ.pop("NLRL_RUNTIME_MAX_EXECUTOR_STEPS", None)
        else:
            os.environ["NLRL_RUNTIME_MAX_EXECUTOR_STEPS"] = old_step
    record = {
        "method_key": "A_full",
        "boot_key": boot_key,
        "step_budget": step_budget,
        "strategy": "full",
        "graph_policy": "locked",
        "config_path": str(config_path),
        "started_at": started,
        "ended_at": now(),
        "source_run": str(source_run),
        "source_skill": str(source_skill),
        "skill_path": str(skill),
    }
    offline_records.append(record)
    write_manifest("running", {"stage": "offline_a_done"})
    return skill


def read_first_config_snapshot(item: LaunchedEval, timeout: int = 180) -> dict[str, Any]:
    run_dir, snapshot = prev.read_config_snapshot(item.run_name, timeout=timeout)
    item.run_dir = run_dir
    return {"label": item.label, "run_dir": run_dir, "snapshot": snapshot}


def run_v5_step_group(step_budget: int) -> dict[str, Any]:
    group = f"v5_step{step_budget}"
    record: dict[str, Any] = {"group": group, "step_budget": step_budget, "started_at": now(), "status": "running"}
    stage_records.append(record)
    write_manifest("running", {"stage": f"{group}_start"})

    boot_dev_label = f"{group}_boot_dev"
    boot_test_label = f"{group}_boot_test"
    labels = [boot_dev_label, boot_test_label]
    boot_dev = launch_eval(
        label=boot_dev_label,
        group=group,
        run_name=f"{PREFIX}_{group}_9b_bootv5_boot_dev_c{CONCURRENCY}",
        dataset=DEV_DATASET,
        lane=first_free_lane(0),
        config_path=CONFIG_V5,
        step_budget=step_budget,
        runner="train-local",
        bootstrap=True,
        bootstrap_preprocess=True,
    )
    record["first_config_snapshot"] = read_first_config_snapshot(boot_dev)
    write_manifest("running", {"stage": f"{group}_config_checked"})

    boot_skill = wait_boot_skill(boot_dev)
    record["boot_skill"] = str(boot_skill)
    record["boot_artifacts"] = inspect_boot_artifacts(boot_dev.run_name)
    write_manifest("running", {"stage": f"{group}_skill_ready"})

    launch_eval(
        label=boot_test_label,
        group=group,
        run_name=f"{PREFIX}_{group}_9b_bootv5_boot_test_c{CONCURRENCY}",
        dataset=TEST_DATASET,
        lane=first_free_lane(1),
        config_path=CONFIG_V5,
        step_budget=step_budget,
        runner="train-local",
        skill_path=boot_skill,
        bootstrap_preprocess=True,
    )
    record["labels"] = labels
    wait_labels_longtail(labels)
    record["status"] = "released_after_longtail"
    record["released_at"] = now()
    write_manifest("running", {"stage": f"{group}_released_after_longtail"})
    return record


def run_bootv4_step48_followup() -> None:
    group = "bootv4_Afull_step48_followup"
    step_budget = FOLLOWUP_STEP
    record: dict[str, Any] = {"group": group, "step_budget": step_budget, "started_at": now(), "status": "running"}
    stage_records.append(record)
    write_manifest("running", {"stage": f"{group}_start"})

    baseline_label = "step48_baseline_test"
    boot_dev_label = "step48_bootv4_boot_dev"
    boot_test_label = "step48_bootv4_boot_test"
    a_dev_label = "step48_bootv4_A_full_dev"
    a_test_label = "step48_bootv4_A_full_test"
    labels = [baseline_label, boot_dev_label, boot_test_label, a_dev_label, a_test_label]

    launch_eval(
        label=baseline_label,
        group=group,
        run_name=f"{PREFIX}_step48_9b_baseline_test_c{CONCURRENCY}",
        dataset=TEST_DATASET,
        lane=first_free_lane(1),
        config_path=CONFIG_V4,
        step_budget=step_budget,
        runner="direct-eval-local",
        bootstrap_preprocess=False,
    )
    boot_dev = launch_eval(
        label=boot_dev_label,
        group=group,
        run_name=f"{PREFIX}_step48_9b_bootv4_boot_dev_c{CONCURRENCY}",
        dataset=DEV_DATASET,
        lane=first_free_lane(0),
        config_path=CONFIG_V4,
        step_budget=step_budget,
        runner="train-local",
        bootstrap=True,
        bootstrap_preprocess=True,
    )
    record["first_config_snapshot"] = read_first_config_snapshot(boot_dev)
    write_manifest("running", {"stage": f"{group}_boot_config_checked"})

    boot_skill = wait_boot_skill(boot_dev)
    record["boot_skill"] = str(boot_skill)
    record["boot_artifacts"] = inspect_boot_artifacts(boot_dev.run_name)
    write_manifest("running", {"stage": f"{group}_boot_skill_ready"})

    launch_eval(
        label=boot_test_label,
        group=group,
        run_name=f"{PREFIX}_step48_9b_bootv4_boot_test_c{CONCURRENCY}",
        dataset=TEST_DATASET,
        lane=first_free_lane(1),
        config_path=CONFIG_V4,
        step_budget=step_budget,
        runner="train-local",
        skill_path=boot_skill,
        bootstrap_preprocess=True,
    )

    source_run = wait_boot_source(boot_dev)
    record["source_run"] = str(source_run)
    a_skill = run_offline_a(
        boot_key=f"bootv4_step{step_budget}",
        source_label=f"9B BOOT-V4 step{step_budget} A_full source",
        step_budget=step_budget,
        source_run=source_run,
        source_skill=boot_skill,
        config_path=CONFIG_V4,
    )
    record["a_full_skill"] = str(a_skill)

    launch_eval(
        label=a_dev_label,
        group=group,
        run_name=f"{PREFIX}_step48_9b_bootv4_A_full_dev_eval_c{CONCURRENCY}",
        dataset=DEV_DATASET,
        lane=first_free_lane(0),
        config_path=CONFIG_V4,
        step_budget=step_budget,
        runner="train-local",
        skill_path=a_skill,
        bootstrap_preprocess=True,
    )
    launch_eval(
        label=a_test_label,
        group=group,
        run_name=f"{PREFIX}_step48_9b_bootv4_A_full_test_eval_c{CONCURRENCY}",
        dataset=TEST_DATASET,
        lane=first_free_lane(1),
        config_path=CONFIG_V4,
        step_budget=step_budget,
        runner="train-local",
        skill_path=a_skill,
        bootstrap_preprocess=True,
    )

    wait_labels_longtail(labels)
    record["status"] = "released_after_longtail"
    record["released_at"] = now()
    write_manifest("running", {"stage": f"{group}_released_after_longtail"})


def run_v5_a_followups(v5_boot_records: list[dict[str, Any]]) -> None:
    for boot_record in v5_boot_records:
        step_budget = int(boot_record["step_budget"])
        group = f"v5_Afull_step{step_budget}"
        record: dict[str, Any] = {
            "group": group,
            "step_budget": step_budget,
            "started_at": now(),
            "status": "running",
            "source_boot_group": boot_record.get("group", ""),
        }
        stage_records.append(record)
        write_manifest("running", {"stage": f"{group}_start"})

        boot_dev_label = f"v5_step{step_budget}_boot_dev"
        boot_dev = launched[boot_dev_label]
        source_run = wait_boot_source(boot_dev)
        boot_skill = Path(str(boot_record["boot_skill"]))
        record["source_run"] = str(source_run)
        record["boot_skill"] = str(boot_skill)

        a_skill = run_offline_a(
            boot_key=f"bootv5_step{step_budget}",
            source_label=f"9B BOOT-V5 step{step_budget} A_full source",
            step_budget=step_budget,
            source_run=source_run,
            source_skill=boot_skill,
            config_path=CONFIG_V5,
        )
        record["a_full_skill"] = str(a_skill)

        a_dev_label = f"{group}_A_full_dev"
        a_test_label = f"{group}_A_full_test"
        labels = [a_dev_label, a_test_label]
        record["labels"] = labels

        launch_eval(
            label=a_dev_label,
            group=group,
            run_name=f"{PREFIX}_{group}_9b_bootv5_A_full_dev_eval_c{CONCURRENCY}",
            dataset=DEV_DATASET,
            lane=first_free_lane(0),
            config_path=CONFIG_V5,
            step_budget=step_budget,
            runner="train-local",
            skill_path=a_skill,
            bootstrap_preprocess=True,
        )
        launch_eval(
            label=a_test_label,
            group=group,
            run_name=f"{PREFIX}_{group}_9b_bootv5_A_full_test_eval_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane=first_free_lane(1),
            config_path=CONFIG_V5,
            step_budget=step_budget,
            runner="train-local",
            skill_path=a_skill,
            bootstrap_preprocess=True,
        )

        wait_labels_longtail(labels)
        record["status"] = "released_after_longtail"
        record["released_at"] = now()
        write_manifest("running", {"stage": f"{group}_released_after_longtail"})


def wait_all_evals_done() -> None:
    while True:
        poll_launched()
        progress = [
            item_progress(item)
            for item in launched.values()
            if item.returncode is None
        ]
        live = [item["label"] for item in progress if item["live"]]
        blocking = [item["label"] for item in progress if item["live"] and not item["longtail"]]
        write_manifest(
            "running",
            {"stage": "final_wait_tail_or_done", "live": live, "blocking": blocking, "progress": progress},
        )
        if not blocking:
            return
        log(f"final wait tail-or-done; blocking={blocking}")
        time.sleep(POLL_SECONDS)


def main() -> int:
    start = now()
    status = "finished"
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    prev.base.ensure_process_csv_header()
    prev.base.refresh_process_registry()
    verify_inputs()

    global predecessors
    predecessors = capture_predecessors()
    master_command = os.environ.get(
        "GAIA_BOOTV5_GRAPH_FREE_STEPS_MASTER_COMMAND",
        f"{PYTHON} -u {Path(__file__).resolve()}",
    )
    prev.append_process_row(
        kind="experiment_master",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=str(ROOT),
        run_dir=str(QUEUE_LOG_ROOT),
        command=master_command,
        log_path=str(MASTER_LOG),
        notes=(
            f"9B BOOT-V5 graph-free step24/48 + BOOT-V4/A_full step48 + BOOT-V5/A_full followup; "
            "13 evals, c20, gpu0/1 long-tail refill; "
            f"summary={SUMMARY_JSON}; manifest={MANIFEST_JSON}"
        ),
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))

    try:
        write_manifest("starting", {"started_at": start, "captured_predecessors": predecessors})
        wait_predecessor_longtail()
        ensure_9b_services()
        write_manifest("running", {"started_at": start, "stage": "services_ready"})
        v5_boot_records: list[dict[str, Any]] = []
        for step_budget in V5_STEPS:
            v5_boot_records.append(run_v5_step_group(step_budget))
        run_bootv4_step48_followup()
        run_v5_a_followups(v5_boot_records)
        wait_all_evals_done()
        failed = {label: item.returncode for label, item in launched.items() if item.returncode not in {0, None}}
        live_longtail = {
            label: item_progress(item)
            for label, item in launched.items()
            if item.returncode is None and item_progress(item)["longtail"]
        }
        if failed:
            status = "finished_with_eval_failures"
        elif live_longtail:
            status = "finished_with_longtail_live"
        else:
            status = "finished"
        write_manifest(status, {"started_at": start, "ended_at": now(), "failed": failed, "released_after_longtail": live_longtail})
        write_summary(status)
        return 0 if not failed else 1
    except KeyboardInterrupt:
        status = "stopped"
        write_manifest(status, {"started_at": start, "ended_at": now()})
        write_summary(status)
        return 130
    except Exception as exc:
        status = "dead"
        write_manifest(status, {"started_at": start, "ended_at": now(), "error": repr(exc)})
        write_summary(status)
        raise
    finally:
        prev.base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
