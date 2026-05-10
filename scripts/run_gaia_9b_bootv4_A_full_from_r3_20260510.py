from __future__ import annotations

import json
import os
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

PREFIX = os.environ.get("GAIA_BOOTV4_A_FULL_PREFIX", "20260510_2132_9b_bootv4_A_full_from_r3")
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(os.environ.get("GAIA_BOOTV4_A_FULL_MASTER_LOG", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"))
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"

CONCURRENCY = int(os.environ.get("GAIA_BOOTV4_A_FULL_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_BOOTV4_A_FULL_POLL_SECONDS", "180"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_BOOTV4_A_FULL_CODEX_TIMEOUT_SECONDS", "3600"))

BOOT_DEV_RUN = DAY_ROOT / "20260510_123058_9b_bootv4_b2_b3_recheck_r3_9b_bootv4_recheck_boot_dev_c20"
BOOT_SKILL = BOOT_DEV_RUN / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"

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

sys.path.insert(0, str(SCRIPTS))
import run_gaia_9b_bootv4_b3_continue254_20260508 as prev  # noqa: E402


@dataclass
class EvalItem:
    label: str
    run_name: str
    dataset: Path
    lane: prev.Lane
    pid: int
    log_path: Path
    skill_path: Path
    started_at: str
    returncode: int | None = None
    ended_at: str | None = None


evals: dict[str, EvalItem] = {}
offline_record: dict = {}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    return prev.command_display(env, cmd)


def run_name_suffix(run_name: str) -> str:
    parts = run_name.split("_", 2)
    return parts[2] if len(parts) == 3 else run_name


def latest_run_dir(run_name: str) -> Path | None:
    suffix = run_name_suffix(run_name)
    matches = [path for path in DAY_ROOT.glob(f"*{suffix}") if path.is_dir()]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def stats_for_run(run_name: str) -> dict:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return {"run_name": run_name, "status": "missing_run_dir"}
    selected = run_dir / "selected_tasks.json"
    total = 0
    if selected.exists():
        try:
            total = len(json.loads(selected.read_text(encoding="utf-8")).get("task_ids", []))
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


def common_env(lane: prev.Lane) -> dict[str, str]:
    env = prev.common_env(lane)
    env["NLRL_RUNTIME_TASK_CONCURRENCY"] = str(CONCURRENCY)
    env["NLRL_LLM_MAX_CONCURRENT_REQUESTS"] = str(CONCURRENCY)
    env["NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS"] = "1"
    env["NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP"] = os.environ.get(
        "GAIA_BOOTV4_A_FULL_PREPROCESS_NOTE_CHAR_CAP",
        "700",
    )
    env["NLRL_ACTOR_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    env["NLRL_CRITIC_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    env["NLRL_CODEX_CLI_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
    return env


def write_manifest(status: str, extra: dict | None = None) -> None:
    payload = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "summary_json": str(SUMMARY_JSON),
        "source": {
            "boot_dev_run": str(BOOT_DEV_RUN),
            "boot_skill": str(BOOT_SKILL),
            "source_mode": "r3_partial_boot_dev_states",
        },
        "lanes": [asdict(lane) for lane in prev.LANES],
        "offline_record": offline_record,
        "evals": {
            label: {
                "label": item.label,
                "run_name": item.run_name,
                "dataset": str(item.dataset),
                "lane": asdict(item.lane),
                "pid": item.pid,
                "log_path": str(item.log_path),
                "skill_path": str(item.skill_path),
                "started_at": item.started_at,
                "returncode": item.returncode,
                "ended_at": item.ended_at,
                "stats": stats_for_run(item.run_name),
            }
            for label, item in evals.items()
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
        "offline_record": offline_record,
        "evals": {
            label: {
                "run_name": item.run_name,
                "returncode": item.returncode,
                "log_path": str(item.log_path),
                "stats": stats_for_run(item.run_name),
            }
            for label, item in evals.items()
        },
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_inputs() -> None:
    missing = [str(path) for path in [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET, BOOT_DEV_RUN, BOOT_SKILL] if not path.exists()]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))


def run_offline_a() -> Path:
    source = prev.base.BootSource(
        model_key="9b",
        boot_key="bootv4",
        label="9B BOOT-V4 r3 A_full source",
        boot_dev_run=BOOT_DEV_RUN,
        boot_skill=BOOT_SKILL,
        supplemental_runs=[],
        source_notes="2026-05-10 A_full from r3 fresh BOOT-V4 partial boot_dev states",
    )
    started = now()
    skill = prev.base.run_offline_variant(
        source,
        method_key="A_full",
        strategy="full",
        graph_policy="locked",
    )
    offline_record.update(
        {
            "method_key": "A_full",
            "strategy": "full",
            "graph_policy": "locked",
            "started_at": started,
            "ended_at": now(),
            "source_run": str(BOOT_DEV_RUN),
            "source_skill": str(BOOT_SKILL),
            "skill_path": str(skill),
        }
    )
    write_manifest("running")
    return skill


def launch_eval(label: str, run_name: str, dataset: Path, lane: prev.Lane, skill_path: Path) -> EvalItem:
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
            f"9B BOOT-V4 A_full from r3 {label}; lane={lane.name}; "
            f"endpoint={lane.base_url}; skill={skill_path}; {PREFIX}"
        ),
    )
    item = EvalItem(label, run_name, dataset, lane, process.pid, log_path, skill_path, now())
    item.process = process  # type: ignore[attr-defined]
    evals[label] = item
    log(f"started {label} pid={process.pid} lane={lane.name} endpoint={lane.base_url} log={log_path}")
    write_manifest("running")
    return item


def poll_evals() -> None:
    changed = False
    for item in evals.values():
        if item.returncode is not None:
            continue
        process = getattr(item, "process", None)
        if process is None:
            continue
        code = process.poll()
        if code is None:
            continue
        item.returncode = code
        item.ended_at = now()
        prev.base.update_process_row(item.pid, "finished" if code == 0 else "dead", code)
        log(f"eval exited {item.label} pid={item.pid} code={code}")
        changed = True
    if changed:
        write_manifest("running")


def wait_all() -> None:
    while True:
        poll_evals()
        live = [label for label, item in evals.items() if item.returncode is None]
        write_manifest("running", {"live": live})
        if not live:
            return
        log(f"waiting A evals; live={live}")
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
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=str(ROOT),
        run_dir=str(QUEUE_LOG_ROOT),
        command=os.environ.get("GAIA_BOOTV4_A_FULL_MASTER_COMMAND", f"{PYTHON} -u {Path(__file__).resolve()}"),
        log_path=str(MASTER_LOG),
        notes=f"9B BOOT-V4 A_full from r3; dev/test on 254 GPU0/GPU1; manifest={MANIFEST_JSON}; summary={SUMMARY_JSON}",
    )
    try:
        validate_inputs()
        prev.wait_9b_endpoints()
        write_manifest("running", {"started_at": start, "stage": "endpoints_ready"})
        skill = run_offline_a()
        launch_eval(
            "9b_bootv4_A_full_dev",
            f"{PREFIX}_9b_bootv4_A_full_dev_eval_c{CONCURRENCY}",
            DEV_DATASET,
            prev.LANES[0],
            skill,
        )
        launch_eval(
            "9b_bootv4_A_full_test",
            f"{PREFIX}_9b_bootv4_A_full_test_eval_c{CONCURRENCY}",
            TEST_DATASET,
            prev.LANES[1],
            skill,
        )
        wait_all()
        failed = {label: item.returncode for label, item in evals.items() if item.returncode not in {0, None}}
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
