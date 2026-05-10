from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"

PREFIX = os.environ.get(
    "GAIA_BOOTV4_B2B3_RECHECK_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_9b_bootv4_b2_b3_recheck"),
).strip()
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_MASTER_LOG", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"))
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"

CONCURRENCY = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_POLL_SECONDS", "180"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_CODEX_TIMEOUT_SECONDS", "3600"))
PARTIAL_SOURCE_MIN_STATES = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_PARTIAL_SOURCE_MIN_STATES", "80"))
PARTIAL_SOURCE_STALL_SECONDS = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_PARTIAL_SOURCE_STALL_SECONDS", "3600"))

os.environ["GAIA_B3_CONTINUE254_PREFIX"] = PREFIX
os.environ["GAIA_B3_CONTINUE254_MASTER_LOG"] = str(MASTER_LOG)
os.environ["GAIA_B3_CONTINUE254_CONCURRENCY"] = str(CONCURRENCY)
os.environ["GAIA_B3_CONTINUE254_CODEX_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
os.environ.setdefault("GAIA_B3_CONTINUE254_POLL_SECONDS", str(POLL_SECONDS))
os.environ.setdefault("NLRL_CODEX_CLI_WORKDIR", str(ROOT))
os.environ.setdefault("GAIA_REMAIN20_ALLOW_PARTIAL_SOURCE_STATES", "1")
os.environ.setdefault("GAIA_REMAIN20_CRITIC_SHARD_CONCURRENCY", "99")

sys.path.insert(0, str(SCRIPTS))
import run_gaia_9b_bootv4_b3_continue254_20260508 as prev  # noqa: E402


@dataclass
class LaunchedEval:
    label: str
    run_name: str
    dataset: Path
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
offline_records: list[dict] = []
source_record: dict = {}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    return prev.command_display(env, cmd)


def common_env(lane: prev.Lane) -> dict[str, str]:
    env = prev.common_env(lane)
    env["NLRL_RUNTIME_TASK_CONCURRENCY"] = str(CONCURRENCY)
    env["NLRL_LLM_MAX_CONCURRENT_REQUESTS"] = str(CONCURRENCY)
    env["NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS"] = "1"
    env["NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP"] = os.environ.get(
        "GAIA_BOOTV4_B2B3_RECHECK_PREPROCESS_NOTE_CHAR_CAP",
        "700",
    )
    env["NLRL_ACTOR_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    env["NLRL_CRITIC_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    env["NLRL_CODEX_CLI_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    return env


def endpoints_ready() -> bool:
    for lane in prev.LANES:
        ids = prev.endpoint_models(lane.base_url)
        if not any(item == lane.served_name or item.endswith("/Qwen3.5-9B") for item in ids):
            return False
    return True


def ensure_9b_services() -> None:
    mode = os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_DEPLOY_MODE", "auto").strip().lower()
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
    selected = run_dir / "selected_tasks.json"
    total = 0
    if selected.exists():
        try:
            total = len(json.loads(selected.read_text(encoding="utf-8")).get("task_ids", []))
        except Exception:
            total = 0
    iteration = latest_iteration_dir(run_dir)
    states = list(iteration.glob("*/state.json")) if iteration else []
    last_mtime = max((path.stat().st_mtime for path in states), default=0.0)
    return len(states), total, last_mtime


def bootstrap_skill_path() -> Path | None:
    run_dir = latest_run_dir(f"{PREFIX}_9b_bootv4_recheck_boot_dev_c{CONCURRENCY}")
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def write_manifest(status: str, extra: dict | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "summary_json": str(SUMMARY_JSON),
        "lanes": [asdict(lane) for lane in prev.LANES],
        "source": source_record,
        "offline_records": offline_records,
        "evals": {
            label: {
                "label": item.label,
                "run_name": item.run_name,
                "dataset": str(item.dataset),
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
        "source": source_record,
        "offline_records": offline_records,
        "evals": {
            label: {
                "run_name": item.run_name,
                "returncode": item.returncode,
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
    run_name: str,
    dataset: Path,
    lane: prev.Lane,
    skill_path: Path | None = None,
    bootstrap: bool = False,
) -> LaunchedEval:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{short_ts()}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "train-local",
        "--dataset-path",
        str(dataset),
        "--run-name",
        run_name,
    ]
    if bootstrap:
        cmd.append("--bootstrap-skill")
    env = common_env(lane)
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
            f"9B BOOT-V4 B2/B3 recheck {label}; lane={lane.name}; "
            f"endpoint={lane.base_url}; bootstrap={bootstrap}; skill={skill_path or '(bootstrap)'}; {PREFIX}"
        ),
    )
    item = LaunchedEval(
        label=label,
        run_name=run_name,
        dataset=dataset,
        lane=lane,
        process=process,
        log_path=log_path,
        skill_path=skill_path,
        bootstrap=bootstrap,
        started_at=now(),
    )
    launched[label] = item
    log(f"started {label} pid={process.pid} lane={lane.name} endpoint={lane.base_url} log={log_path}")
    write_manifest("running")
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


def first_free_lane(preferred_index: int = 0) -> prev.Lane:
    ordered = prev.LANES[preferred_index:] + prev.LANES[:preferred_index]
    while True:
        poll_launched()
        busy = {item.lane.name for item in launched.values() if item.returncode is None and item.process.poll() is None}
        for lane in ordered:
            if lane.name not in busy:
                return lane
        log("waiting for a free 254 9B lane")
        time.sleep(30)


def wait_boot_skill(boot_dev: LaunchedEval) -> Path:
    while True:
        poll_launched()
        skill = bootstrap_skill_path()
        if skill is not None:
            log(f"bootstrap skill ready: {skill}")
            return skill
        if boot_dev.returncode is not None:
            raise RuntimeError(f"boot_dev exited before bootstrap skill was ready: code={boot_dev.returncode}")
        done, total, _last_mtime = state_count(boot_dev.run_name)
        log(f"waiting bootstrap skill; boot_dev states={done}/{total or '?'}")
        time.sleep(min(60, POLL_SECONDS))


def wait_boot_source(boot_dev: LaunchedEval) -> Path:
    last_done = -1
    last_change = time.time()
    while True:
        poll_launched()
        run_dir = latest_run_dir(boot_dev.run_name)
        done, total, _last_mtime = state_count(boot_dev.run_name)
        if done != last_done:
            last_done = done
            last_change = time.time()
        if boot_dev.returncode is not None:
            if run_dir is None:
                raise RuntimeError(f"boot_dev ended but run_dir is missing: {boot_dev.run_name}")
            source_record.update(
                {
                    "boot_dev_run": str(run_dir),
                    "source_mode": "boot_dev_exited",
                    "source_states": done,
                    "source_total": total,
                    "boot_dev_returncode": boot_dev.returncode,
                }
            )
            write_manifest("running")
            return run_dir
        stalled_for = time.time() - last_change
        if run_dir is not None and done >= PARTIAL_SOURCE_MIN_STATES and stalled_for >= PARTIAL_SOURCE_STALL_SECONDS:
            source_record.update(
                {
                    "boot_dev_run": str(run_dir),
                    "source_mode": "partial_stalled",
                    "source_states": done,
                    "source_total": total,
                    "stalled_seconds": int(stalled_for),
                }
            )
            write_manifest("running")
            return run_dir
        log(
            f"waiting boot_dev source; states={done}/{total or '?'} "
            f"stalled_for={int(stalled_for)}s partial_min={PARTIAL_SOURCE_MIN_STATES}"
        )
        time.sleep(POLL_SECONDS)


def run_offline(method_key: str, source_run: Path, source_skill: Path) -> Path:
    strategy = "graph_b2" if method_key == "B2" else "graph_v3"
    graph_policy = "graph_unlocked" if method_key == "B2" else "graph_v3"
    source = prev.base.BootSource(
        model_key="9b",
        boot_key="bootv4",
        label=f"9B BOOT-V4 recheck {method_key} source",
        boot_dev_run=source_run,
        boot_skill=source_skill,
        supplemental_runs=[],
        source_notes=f"2026-05-10 recheck source; {method_key} from same fresh BOOT-V4 boot_dev states",
    )
    started = now()
    skill = prev.base.run_offline_variant(
        source,
        method_key=method_key,
        strategy=strategy,
        graph_policy=graph_policy,
    )
    record = {
        "method_key": method_key,
        "strategy": strategy,
        "graph_policy": graph_policy,
        "started_at": started,
        "ended_at": now(),
        "source_run": str(source_run),
        "source_skill": str(source_skill),
        "skill_path": str(skill),
    }
    offline_records.append(record)
    write_manifest("running")
    return skill


def verify_inputs() -> None:
    missing = [str(path) for path in [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET] if not path.exists()]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))


def wait_all() -> None:
    while True:
        poll_launched()
        live = [label for label, item in launched.items() if item.returncode is None]
        if not live:
            return
        log(f"waiting all evals; live={live}")
        time.sleep(POLL_SECONDS)


def main() -> int:
    start = now()
    status = "finished"
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    prev.base.ensure_process_csv_header()
    prev.base.refresh_process_registry()
    verify_inputs()
    master_command = os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_MASTER_COMMAND", f"{PYTHON} -u {Path(__file__).resolve()}")
    prev.append_process_row(
        kind="experiment_master",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=str(ROOT),
        run_dir=str(QUEUE_LOG_ROOT),
        command=master_command,
        log_path=str(MASTER_LOG),
        notes=f"9B BOOT-V4 B2/B3 recheck; 6 counted evals; summary={SUMMARY_JSON}; manifest={MANIFEST_JSON}",
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        ensure_9b_services()
        write_manifest("running", {"started_at": start, "stage": "services_ready"})
        boot_dev = launch_eval(
            label="9b_bootv4_recheck_boot_dev",
            run_name=f"{PREFIX}_9b_bootv4_recheck_boot_dev_c{CONCURRENCY}",
            dataset=DEV_DATASET,
            lane=prev.LANES[0],
            bootstrap=True,
        )
        run_dir, snapshot = prev.read_config_snapshot(boot_dev.run_name, timeout=180)
        boot_dev.run_dir = run_dir
        write_manifest(
            "running",
            {
                "first_config_snapshot_checked": {
                    "label": boot_dev.label,
                    "run_dir": run_dir,
                    "snapshot": snapshot,
                }
            },
        )

        boot_skill = wait_boot_skill(boot_dev)
        launch_eval(
            label="9b_bootv4_recheck_boot_test",
            run_name=f"{PREFIX}_9b_bootv4_recheck_boot_test_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane=prev.LANES[1],
            skill_path=boot_skill,
        )

        source_run = wait_boot_source(boot_dev)
        b2_skill = run_offline("B2", source_run, boot_skill)
        launch_eval(
            label="9b_bootv4_recheck_B2_dev",
            run_name=f"{PREFIX}_9b_bootv4_recheck_B2_dev_eval_c{CONCURRENCY}",
            dataset=DEV_DATASET,
            lane=first_free_lane(0),
            skill_path=b2_skill,
        )
        launch_eval(
            label="9b_bootv4_recheck_B2_test",
            run_name=f"{PREFIX}_9b_bootv4_recheck_B2_test_eval_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane=first_free_lane(1),
            skill_path=b2_skill,
        )

        b3_skill = run_offline("B3", source_run, boot_skill)
        launch_eval(
            label="9b_bootv4_recheck_B3_dev",
            run_name=f"{PREFIX}_9b_bootv4_recheck_B3_dev_eval_c{CONCURRENCY}",
            dataset=DEV_DATASET,
            lane=first_free_lane(0),
            skill_path=b3_skill,
        )
        launch_eval(
            label="9b_bootv4_recheck_B3_test",
            run_name=f"{PREFIX}_9b_bootv4_recheck_B3_test_eval_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane=first_free_lane(1),
            skill_path=b3_skill,
        )

        wait_all()
        failed = {label: item.returncode for label, item in launched.items() if item.returncode not in {0, None}}
        status = "finished_with_eval_failures" if failed else "finished"
        write_manifest(status, {"started_at": start, "ended_at": now(), "failed": failed})
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
