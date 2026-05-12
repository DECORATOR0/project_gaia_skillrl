from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system_bootv6_long_rules.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"

PREFIX = os.environ.get(
    "GAIA_BOOTV6_LONGRULES_AFULL_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_9b_bootv6_longrules_afull"),
).strip()
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(
    os.environ.get(
        "GAIA_BOOTV6_LONGRULES_AFULL_MASTER_LOG",
        QUEUE_LOG_ROOT / f"{MASTER_NAME}.log",
    )
)
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"

REPEATS = int(os.environ.get("GAIA_BOOTV6_LONGRULES_AFULL_REPEATS", "1"))
CONCURRENCY = int(os.environ.get("GAIA_BOOTV6_LONGRULES_AFULL_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_BOOTV6_LONGRULES_AFULL_POLL_SECONDS", "180"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_BOOTV6_LONGRULES_AFULL_CODEX_TIMEOUT_SECONDS", "3600"))
PREPROCESS_NOTE_CHAR_CAP = os.environ.get("GAIA_BOOTV6_LONGRULES_AFULL_PREPROCESS_NOTE_CHAR_CAP", "700")
PARTIAL_STOP_MIN_STATES = int(os.environ.get("GAIA_BOOTV6_LONGRULES_AFULL_PARTIAL_STOP_MIN_STATES", "80"))
PARTIAL_STOP_STALL_SECONDS = int(
    os.environ.get("GAIA_BOOTV6_LONGRULES_AFULL_PARTIAL_STOP_STALL_SECONDS", "3600")
)

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
class LaunchedEval:
    label: str
    repeat_index: int
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
    last_done: int = -1
    last_progress_at: float = field(default_factory=time.time)
    partial_stopped: bool = False


launched: dict[str, LaunchedEval] = {}
offline_records: list[dict] = []
repeat_records: list[dict] = []


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
    env["NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP"] = PREPROCESS_NOTE_CHAR_CAP
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
    mode = os.environ.get("GAIA_BOOTV6_LONGRULES_AFULL_DEPLOY_MODE", "auto").strip().lower()
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


def bootstrap_skill_path(boot_dev_run_name: str) -> Path | None:
    run_dir = latest_run_dir(boot_dev_run_name)
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def rule_word_stats(skill_path: Path) -> dict[str, dict[str, int]]:
    text = skill_path.read_text(encoding="utf-8", errors="replace")
    stats: dict[str, dict[str, int]] = {}
    for part in re.split(r"(?m)^## Phase: ", text)[1:]:
        lines = part.splitlines()
        if not lines:
            continue
        name = lines[0].strip()
        match = re.search(
            r"(?ms)^Rules:\s*(.*?)(?=^Exit handoff:|^Available actions:|^Next:|^## Phase:|\Z)",
            part,
        )
        rules = match.group(1).strip() if match else ""
        stats[name] = {
            "words": len(re.findall(r"\b\w+\b", rules)),
            "chars": len(rules),
        }
    return stats


def write_manifest(status: str, extra: dict | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "repeats": REPEATS,
        "summary_json": str(SUMMARY_JSON),
        "lanes": [asdict(lane) for lane in prev.LANES],
        "repeat_records": repeat_records,
        "offline_records": offline_records,
        "evals": {
            label: {
                "label": item.label,
                "repeat_index": item.repeat_index,
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
                "partial_stopped": item.partial_stopped,
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
        "repeats": REPEATS,
        "repeat_records": repeat_records,
        "offline_records": offline_records,
        "evals": {
            label: {
                "run_name": item.run_name,
                "returncode": item.returncode,
                "lane": asdict(item.lane),
                "log_path": str(item.log_path),
                "run_dir": item.run_dir,
                "partial_stopped": item.partial_stopped,
                "stats": prev.stats_for_run(item.run_name),
            }
            for label, item in launched.items()
        },
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stop_eval(item: LaunchedEval, status: str) -> None:
    if item.returncode is not None:
        return
    try:
        os.killpg(item.process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            item.process.terminate()
        except ProcessLookupError:
            pass
    deadline = time.time() + 10
    while time.time() < deadline:
        code = item.process.poll()
        if code is not None:
            item.returncode = code
            break
        time.sleep(0.5)
    if item.returncode is None:
        try:
            os.killpg(item.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                item.process.kill()
            except ProcessLookupError:
                pass
        item.process.wait(timeout=5)
        item.returncode = item.process.returncode
    item.partial_stopped = True
    item.ended_at = now()
    run_dir = latest_run_dir(item.run_name)
    item.run_dir = str(run_dir) if run_dir else ""
    prev.base.update_process_row(item.process.pid, status, item.returncode)
    log(f"stopped {item.label} pid={item.process.pid} status={status} code={item.returncode}")
    write_manifest("running")


def maybe_stop_stalled(item: LaunchedEval) -> None:
    if item.returncode is not None:
        return
    done, total, _last_mtime = state_count(item.run_name)
    if done != item.last_done:
        item.last_done = done
        item.last_progress_at = time.time()
    stalled_for = time.time() - item.last_progress_at
    if done >= PARTIAL_STOP_MIN_STATES and stalled_for >= PARTIAL_STOP_STALL_SECONDS:
        log(
            f"partial stop threshold reached for {item.label}; "
            f"states={done}/{total or '?'} stalled_for={int(stalled_for)}s"
        )
        stop_eval(item, "stopped_partial_stalled")


def launch_eval(
    *,
    label: str,
    repeat_index: int,
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
            f"9B BOOT-V6 long-rules -> A_full repeat={repeat_index} {label}; "
            f"lane={lane.name}; endpoint={lane.base_url}; bootstrap={bootstrap}; "
            f"skill={skill_path or '(bootstrap)'}; {PREFIX}"
        ),
    )
    item = LaunchedEval(
        label=label,
        repeat_index=repeat_index,
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
        maybe_stop_stalled(item)
        if item.returncode is not None:
            changed = True
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
        live = [label for label, item in launched.items() if item.returncode is None]
        log(f"waiting for a free 254 9B lane; live={live}")
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


def wait_boot_source(boot_dev: LaunchedEval) -> Path:
    while True:
        poll_launched()
        run_dir = latest_run_dir(boot_dev.run_name)
        done, total, _last_mtime = state_count(boot_dev.run_name)
        if boot_dev.returncode is not None:
            if run_dir is None:
                raise RuntimeError(f"{boot_dev.label} ended but run_dir is missing: {boot_dev.run_name}")
            return run_dir
        stalled_for = time.time() - boot_dev.last_progress_at
        if run_dir is not None and done >= PARTIAL_STOP_MIN_STATES and stalled_for >= PARTIAL_STOP_STALL_SECONDS:
            stop_eval(boot_dev, "stopped_partial_boot_source")
            return run_dir
        log(
            f"waiting boot source for {boot_dev.label}; states={done}/{total or '?'} "
            f"stalled_for={int(stalled_for)}s partial_min={PARTIAL_STOP_MIN_STATES}"
        )
        time.sleep(POLL_SECONDS)


def run_offline_a(repeat_index: int, source_run: Path, source_skill: Path) -> Path:
    source = prev.base.BootSource(
        model_key="9b",
        boot_key=f"bootv6_longrules_s{repeat_index}",
        label=f"9B BOOT-V6 long-rules repeat {repeat_index} A_full source",
        boot_dev_run=source_run,
        boot_skill=source_skill,
        supplemental_runs=[],
        source_notes=(
            f"2026-05-12 BOOT-V6 long-rules repeat {repeat_index}: A_full from same fresh "
            "boot_dev states; strategy=full graph_policy=locked"
        ),
    )
    started = now()
    old_config = prev.base.CONFIG
    try:
        prev.base.CONFIG = CONFIG
        skill = prev.base.run_offline_variant(
            source,
            method_key="A_full",
            strategy="full",
            graph_policy="locked",
        )
    finally:
        prev.base.CONFIG = old_config
    record = {
        "repeat_index": repeat_index,
        "method_key": "A_full",
        "strategy": "full",
        "graph_policy": "locked",
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


def wait_labels(labels: list[str]) -> None:
    label_set = set(labels)
    while True:
        poll_launched()
        live = [label for label, item in launched.items() if label in label_set and item.returncode is None]
        write_manifest("running", {"waiting_labels": labels, "live": live})
        if not live:
            return
        log(f"waiting repeat evals; live={live}")
        time.sleep(POLL_SECONDS)


def run_repeat(repeat_index: int) -> None:
    repeat_tag = f"r{repeat_index}"
    record = {
        "repeat_index": repeat_index,
        "started_at": now(),
        "status": "running",
        "labels": [],
    }
    repeat_records.append(record)
    write_manifest("running", {"stage": f"{repeat_tag}_start"})

    boot_dev_label = f"{repeat_tag}_boot_dev"
    boot_test_label = f"{repeat_tag}_boot_test"
    a_dev_label = f"{repeat_tag}_A_full_dev"
    a_test_label = f"{repeat_tag}_A_full_test"
    record["labels"] = [boot_dev_label, boot_test_label, a_dev_label, a_test_label]

    boot_dev = launch_eval(
        label=boot_dev_label,
        repeat_index=repeat_index,
        run_name=f"{PREFIX}_{repeat_tag}_9b_bootv6_boot_dev_c{CONCURRENCY}",
        dataset=DEV_DATASET,
        lane=first_free_lane(0),
        bootstrap=True,
    )
    run_dir, snapshot = prev.read_config_snapshot(boot_dev.run_name, timeout=180)
    boot_dev.run_dir = run_dir
    record["first_config_snapshot"] = {"label": boot_dev.label, "run_dir": run_dir, "snapshot": snapshot}
    write_manifest("running", {"stage": f"{repeat_tag}_boot_config_checked"})

    boot_skill = wait_boot_skill(boot_dev)
    record["boot_skill"] = str(boot_skill)
    record["boot_skill_rule_word_stats"] = rule_word_stats(boot_skill)
    write_manifest("running", {"stage": f"{repeat_tag}_boot_skill_checked"})

    launch_eval(
        label=boot_test_label,
        repeat_index=repeat_index,
        run_name=f"{PREFIX}_{repeat_tag}_9b_bootv6_boot_test_c{CONCURRENCY}",
        dataset=TEST_DATASET,
        lane=first_free_lane(1),
        skill_path=boot_skill,
    )

    source_run = wait_boot_source(boot_dev)
    record["source_run"] = str(source_run)
    a_skill = run_offline_a(repeat_index, source_run, boot_skill)
    record["a_full_skill"] = str(a_skill)

    launch_eval(
        label=a_dev_label,
        repeat_index=repeat_index,
        run_name=f"{PREFIX}_{repeat_tag}_9b_bootv6_A_full_dev_eval_c{CONCURRENCY}",
        dataset=DEV_DATASET,
        lane=first_free_lane(0),
        skill_path=a_skill,
    )
    launch_eval(
        label=a_test_label,
        repeat_index=repeat_index,
        run_name=f"{PREFIX}_{repeat_tag}_9b_bootv6_A_full_test_eval_c{CONCURRENCY}",
        dataset=TEST_DATASET,
        lane=first_free_lane(1),
        skill_path=a_skill,
    )

    wait_labels(record["labels"])
    record["ended_at"] = now()
    failed = {label: launched[label].returncode for label in record["labels"] if launched[label].returncode not in {0, None}}
    record["status"] = "finished_with_eval_failures" if failed else "finished"
    record["failed"] = failed
    write_manifest("running", {"stage": f"{repeat_tag}_done"})


def main() -> int:
    start = now()
    status = "finished"
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    prev.base.ensure_process_csv_header()
    prev.base.refresh_process_registry()
    verify_inputs()
    master_command = os.environ.get(
        "GAIA_BOOTV6_LONGRULES_AFULL_MASTER_COMMAND",
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
            f"9B BOOT-V6 long-rules -> A_full repeats={REPEATS}; "
            f"counted evals={REPEATS * 4}; summary={SUMMARY_JSON}; manifest={MANIFEST_JSON}"
        ),
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        ensure_9b_services()
        write_manifest("running", {"started_at": start, "stage": "services_ready"})
        for repeat_index in range(1, REPEATS + 1):
            run_repeat(repeat_index)
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
