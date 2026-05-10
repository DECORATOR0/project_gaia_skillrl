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
DAY_ROOT = RUN_ROOT / "2026/5/2026-5-10"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"

PREFIX = os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_PREFIX", "20260510_1230_9b_bootv4_b2_b3_recheck_r3")
FILL_NAME = f"{PREFIX}_fill_master"
FILL_LOG = Path(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_FILL_LOG", QUEUE_LOG_ROOT / f"{FILL_NAME}.log"))
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_fill_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_fill_summary.json"
ORIGINAL_MASTER_PID = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_ORIGINAL_MASTER_PID", "1050662"))

CONCURRENCY = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_POLL_SECONDS", "180"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_CODEX_TIMEOUT_SECONDS", "3600"))

BOOT_DEV_RUN_NAME = f"{PREFIX}_9b_bootv4_recheck_boot_dev_c{CONCURRENCY}"
BOOT_TEST_RUN_NAME = f"{PREFIX}_9b_bootv4_recheck_boot_test_c{CONCURRENCY}"
B2_DEV_RUN_NAME = f"{PREFIX}_9b_bootv4_recheck_B2_dev_eval_c{CONCURRENCY}"
B2_TEST_RUN_NAME = f"{PREFIX}_9b_bootv4_recheck_B2_test_eval_c{CONCURRENCY}"
B3_DEV_RUN_NAME = f"{PREFIX}_9b_bootv4_recheck_B3_dev_eval_c{CONCURRENCY}"
B3_TEST_RUN_NAME = f"{PREFIX}_9b_bootv4_recheck_B3_test_eval_c{CONCURRENCY}"

BOOT_DEV_RUN = DAY_ROOT / "20260510_123058_9b_bootv4_b2_b3_recheck_r3_9b_bootv4_recheck_boot_dev_c20"
BOOT_SKILL = BOOT_DEV_RUN / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
B2_SKILL = (
    DAY_ROOT
    / "20260510_123032_9b_bootv4_b2_b3_recheck_r3_9b_bootv4_B2_offline_actor_iter1"
    / "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)

os.environ["GAIA_B3_CONTINUE254_PREFIX"] = PREFIX
os.environ["GAIA_B3_CONTINUE254_MASTER_LOG"] = str(FILL_LOG)
os.environ["GAIA_B3_CONTINUE254_CONCURRENCY"] = str(CONCURRENCY)
os.environ["GAIA_B3_CONTINUE254_CODEX_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
os.environ.setdefault("GAIA_B3_CONTINUE254_POLL_SECONDS", str(POLL_SECONDS))
os.environ.setdefault("NLRL_CODEX_CLI_WORKDIR", str(ROOT))
os.environ.setdefault("GAIA_REMAIN20_ALLOW_PARTIAL_SOURCE_STATES", "1")
os.environ.setdefault("GAIA_REMAIN20_CRITIC_SHARD_CONCURRENCY", "99")

sys.path.insert(0, str(SCRIPTS))
import run_gaia_9b_bootv4_b3_continue254_20260508 as prev  # noqa: E402


@dataclass
class EvalRecord:
    label: str
    run_name: str
    dataset: Path
    lane: prev.Lane
    pid: int | None
    log_path: Path | None
    skill_path: Path | None
    started_at: str
    returncode: int | None = None
    ended_at: str | None = None
    launched_by_fill: bool = False
    process: subprocess.Popen[str] | None = None


records: dict[str, EvalRecord] = {}
offline_records: list[dict] = []


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    return prev.command_display(env, cmd)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def find_pid_for_run(run_name: str) -> int | None:
    result = subprocess.run(
        ["ps", "-eo", "pid,args"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or "gaia_skillrl.cli" not in stripped or run_name not in stripped:
            continue
        first = stripped.split(maxsplit=1)[0]
        if first.isdigit():
            return int(first)
    return None


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


def run_name_suffix(run_name: str) -> str:
    parts = run_name.split("_", 2)
    return parts[2] if len(parts) == 3 else run_name


def latest_run_dir(run_name: str) -> Path | None:
    suffix = run_name_suffix(run_name)
    matches = [path for path in DAY_ROOT.glob(f"*{suffix}") if path.is_dir()]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def fast_stats_for_run(run_name: str) -> dict:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return {"run_name": run_name, "status": "missing_run_dir"}
    selected_path = run_dir / "selected_tasks.json"
    total = 0
    if selected_path.exists():
        try:
            total = len(json.loads(selected_path.read_text(encoding="utf-8")).get("task_ids", []))
        except Exception:
            total = 0
    iterations = [path for path in run_dir.glob("iteration_*") if path.is_dir()]
    iteration = max(iterations, key=lambda path: path.name) if iterations else None
    states = list(iteration.glob("*/state.json")) if iteration else []
    success = 0
    for state_path in states:
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            success += int(bool(data.get("env_result", {}).get("evaluation", {}).get("task_success")))
        except Exception:
            pass
    total = total or len(states)
    return {
        "run_name": run_name,
        "run_dir": str(run_dir),
        "score": f"{success}/{total or '?'}",
        "success": success,
        "landed": len(states),
        "total": total,
        "missing": max(0, total - len(states)),
    }


def register_existing(label: str, run_name: str, dataset: Path, lane: prev.Lane, skill_path: Path | None) -> None:
    pid = find_pid_for_run(run_name)
    records[label] = EvalRecord(
        label=label,
        run_name=run_name,
        dataset=dataset,
        lane=lane,
        pid=pid,
        log_path=None,
        skill_path=skill_path,
        started_at="",
        launched_by_fill=False,
    )


def write_manifest(status: str, extra: dict | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "prefix": PREFIX,
        "master": FILL_NAME,
        "status": status,
        "updated_at": now(),
        "summary_json": str(SUMMARY_JSON),
        "original_master_pid": ORIGINAL_MASTER_PID,
        "lanes": [asdict(lane) for lane in prev.LANES],
        "offline_records": offline_records,
        "evals": {
            label: {
                "label": item.label,
                "run_name": item.run_name,
                "dataset": str(item.dataset),
                "lane": asdict(item.lane),
                "pid": item.pid,
                "log_path": str(item.log_path) if item.log_path else "",
                "skill_path": str(item.skill_path) if item.skill_path else "",
                "started_at": item.started_at,
                "returncode": item.returncode,
                "ended_at": item.ended_at,
                "launched_by_fill": item.launched_by_fill,
                "stats": fast_stats_for_run(item.run_name),
            }
            for label, item in records.items()
        },
    }
    if extra:
        payload.update(extra)
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_summary(status: str) -> None:
    payload = {
        "prefix": PREFIX,
        "master": FILL_NAME,
        "status": status,
        "updated_at": now(),
        "manifest_json": str(MANIFEST_JSON),
        "offline_records": offline_records,
        "evals": {
            label: {
                "run_name": item.run_name,
                "returncode": item.returncode,
                "pid": item.pid,
                "launched_by_fill": item.launched_by_fill,
                "stats": fast_stats_for_run(item.run_name),
                "log_path": str(item.log_path) if item.log_path else "",
            }
            for label, item in records.items()
        },
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stop_original_master() -> None:
    if ORIGINAL_MASTER_PID <= 0 or ORIGINAL_MASTER_PID == os.getpid():
        return
    if not pid_alive(ORIGINAL_MASTER_PID):
        prev.base.update_process_row(ORIGINAL_MASTER_PID, "dead_before_fill")
        return
    log(f"stopping original master pid={ORIGINAL_MASTER_PID}; child evals stay alive")
    os.kill(ORIGINAL_MASTER_PID, signal.SIGTERM)
    deadline = time.time() + 20
    while time.time() < deadline and pid_alive(ORIGINAL_MASTER_PID):
        time.sleep(0.5)
    if pid_alive(ORIGINAL_MASTER_PID):
        os.kill(ORIGINAL_MASTER_PID, signal.SIGKILL)
        time.sleep(0.5)
    prev.base.update_process_row(ORIGINAL_MASTER_PID, "superseded_by_fill_no_tail_block")


def launch_eval(label: str, run_name: str, dataset: Path, lane: prev.Lane, skill_path: Path) -> EvalRecord:
    existing_pid = find_pid_for_run(run_name)
    if existing_pid is not None:
        log(f"reuse existing {label} pid={existing_pid}")
        item = EvalRecord(label, run_name, dataset, lane, existing_pid, None, skill_path, "", launched_by_fill=False)
        records[label] = item
        write_manifest("running")
        return item

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
    env = common_env(lane)
    env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(skill_path)
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
            f"9B BOOT-V4 B2/B3 recheck fill {label}; lane={lane.name}; "
            f"endpoint={lane.base_url}; skill={skill_path}; {PREFIX}"
        ),
    )
    item = EvalRecord(
        label=label,
        run_name=run_name,
        dataset=dataset,
        lane=lane,
        pid=process.pid,
        log_path=log_path,
        skill_path=skill_path,
        started_at=now(),
        launched_by_fill=True,
        process=process,
    )
    records[label] = item
    log(f"started {label} pid={process.pid} lane={lane.name} endpoint={lane.base_url} log={log_path}")
    write_manifest("running")
    return item


def poll_records() -> None:
    changed = False
    for item in records.values():
        if item.returncode is not None:
            continue
        if item.process is not None:
            code = item.process.poll()
            if code is None:
                continue
            item.returncode = code
            item.ended_at = now()
            prev.base.update_process_row(item.pid or 0, "finished" if code == 0 else "dead", code)
            log(f"eval exited {item.label} pid={item.pid} code={code}")
            changed = True
            continue
        if item.pid is not None and not pid_alive(item.pid):
            item.returncode = 0
            item.ended_at = now()
            changed = True
    if changed:
        write_manifest("running")


def find_existing_b3_skill() -> Path | None:
    matches = sorted(
        DAY_ROOT.glob(f"*{PREFIX}*B3_offline_actor_iter1/offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"),
        key=lambda path: path.stat().st_mtime,
    )
    return matches[-1] if matches else None


def run_b3_offline() -> Path:
    existing = find_existing_b3_skill()
    if existing is not None:
        log(f"reuse existing B3 skill: {existing}")
        return existing
    source = prev.base.BootSource(
        model_key="9b",
        boot_key="bootv4",
        label="9B BOOT-V4 recheck B3 source",
        boot_dev_run=BOOT_DEV_RUN,
        boot_skill=BOOT_SKILL,
        supplemental_runs=[],
        source_notes="2026-05-10 fill: B3 from same fresh BOOT-V4 partial boot_dev states",
    )
    started = now()
    skill = prev.base.run_offline_variant(
        source,
        method_key="B3",
        strategy="graph_v3",
        graph_policy="graph_v3",
    )
    record = {
        "method_key": "B3",
        "strategy": "graph_v3",
        "graph_policy": "graph_v3",
        "started_at": started,
        "ended_at": now(),
        "source_run": str(BOOT_DEV_RUN),
        "source_skill": str(BOOT_SKILL),
        "skill_path": str(skill),
    }
    offline_records.append(record)
    write_manifest("running")
    return skill


def validate_inputs() -> None:
    missing = [str(path) for path in [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET, BOOT_DEV_RUN, BOOT_SKILL, B2_SKILL] if not path.exists()]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))


def wait_until_done() -> None:
    while True:
        poll_records()
        live = []
        for label, item in records.items():
            if item.returncode is not None:
                continue
            if item.process is not None and item.process.poll() is None:
                live.append(label)
            elif item.process is None and item.pid is not None and pid_alive(item.pid):
                live.append(label)
        write_manifest("running", {"live": live})
        if not live:
            return
        log(f"waiting fill/evals; live={live}")
        time.sleep(POLL_SECONDS)


def main() -> int:
    start = now()
    status = "finished"
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    prev.base.ensure_process_csv_header()
    prev.base.refresh_process_registry()
    prev.append_process_row(
        kind="experiment_master",
        name=FILL_NAME,
        pid=os.getpid(),
        cwd=str(ROOT),
        run_dir=str(QUEUE_LOG_ROOT),
        command=os.environ.get("GAIA_BOOTV4_B2B3_RECHECK_FILL_COMMAND", f"{PYTHON} -u {Path(__file__).resolve()}"),
        log_path=str(FILL_LOG),
        notes=f"fill pending B2_test/B3 for {PREFIX}; manifest={MANIFEST_JSON}; summary={SUMMARY_JSON}",
    )
    try:
        validate_inputs()
        prev.wait_9b_endpoints()
        register_existing("9b_bootv4_recheck_boot_dev", BOOT_DEV_RUN_NAME, DEV_DATASET, prev.LANES[0], None)
        register_existing("9b_bootv4_recheck_boot_test", BOOT_TEST_RUN_NAME, TEST_DATASET, prev.LANES[1], BOOT_SKILL)
        register_existing("9b_bootv4_recheck_B2_dev", B2_DEV_RUN_NAME, DEV_DATASET, prev.LANES[1], B2_SKILL)
        write_manifest("running", {"started_at": start, "stage": "registered_existing"})
        stop_original_master()

        launch_eval("9b_bootv4_recheck_B2_test", B2_TEST_RUN_NAME, TEST_DATASET, prev.LANES[0], B2_SKILL)
        b3_skill = run_b3_offline()
        launch_eval("9b_bootv4_recheck_B3_dev", B3_DEV_RUN_NAME, DEV_DATASET, prev.LANES[1], b3_skill)
        launch_eval("9b_bootv4_recheck_B3_test", B3_TEST_RUN_NAME, TEST_DATASET, prev.LANES[0], b3_skill)

        wait_until_done()
        failed = {label: item.returncode for label, item in records.items() if item.returncode not in {0, None}}
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
