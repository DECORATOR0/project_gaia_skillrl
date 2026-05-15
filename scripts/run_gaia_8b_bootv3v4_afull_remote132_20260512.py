from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
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
    "GAIA_8B_BOOTV3V4_AFULL_REMOTE132_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv3v4_afull_remote132"),
).strip()
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(
    os.environ.get(
        "GAIA_8B_BOOTV3V4_AFULL_REMOTE132_MASTER_LOG",
        QUEUE_LOG_ROOT / f"{MASTER_NAME}.log",
    )
)
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"

CONCURRENCY = int(os.environ.get("GAIA_8B_BOOTV3V4_AFULL_REMOTE132_CONCURRENCY", "20"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_8B_BOOTV3V4_AFULL_REMOTE132_CODEX_TIMEOUT_SECONDS", "3600"))
POLL_SECONDS = int(os.environ.get("GAIA_8B_BOOTV3V4_AFULL_REMOTE132_POLL_SECONDS", "120"))
PARTIAL_SOURCE_MIN_STATES = int(os.environ.get("GAIA_8B_BOOTV3V4_AFULL_REMOTE132_PARTIAL_SOURCE_MIN_STATES", "80"))
PARTIAL_SOURCE_STALL_SECONDS = int(
    os.environ.get("GAIA_8B_BOOTV3V4_AFULL_REMOTE132_PARTIAL_SOURCE_STALL_SECONDS", "3600")
)
FORCE_CLEAR_GPUS = os.environ.get("GAIA_8B_BOOTV3V4_AFULL_REMOTE132_FORCE_CLEAR", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

CODEX_BIN = os.environ.get(
    "NLRL_CODEX_CLI_PATH",
    "/data/xsy/.vscode-server/extensions/openai.chatgpt-26.429.30905-linux-x64/bin/linux-x86_64/codex",
)
CODEX_PROXY = os.environ.get("GAIA_8B_BOOTV3V4_AFULL_CODEX_PROXY", "http://127.0.0.1:17890")

os.environ["GAIA_24EXP_PREFIX"] = PREFIX
os.environ["GAIA_24EXP_TASK_CONCURRENCY"] = str(CONCURRENCY)
os.environ["GAIA_24EXP_CODEX_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
os.environ["GAIA_REMAIN20_ALLOW_PARTIAL_SOURCE_STATES"] = "1"
os.environ["GAIA_REMAIN20_CRITIC_SHARD_CONCURRENCY"] = "99"
os.environ["NLRL_CODEX_CLI_PATH"] = CODEX_BIN
os.environ["NLRL_CODEX_CLI_WORKDIR"] = str(ROOT)
os.environ["NLRL_CODEX_CLI_TIMEOUT_SECONDS"] = str(CODEX_TIMEOUT_SECONDS)
os.environ["NLRL_CODEX_CLI_EXTRA_ARGS"] = "-c 'model_reasoning_effort=\"xhigh\"'"
os.environ["HTTP_PROXY"] = CODEX_PROXY
os.environ["HTTPS_PROXY"] = CODEX_PROXY
os.environ["http_proxy"] = CODEX_PROXY
os.environ["https_proxy"] = CODEX_PROXY
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_baseline_254_gpu01_20260507 as base  # noqa: E402
import run_gaia_8b9b_remain20_fast_20260507 as remain20  # noqa: E402


LANES = [
    base.Lane("132_gpu0_8b", 0, 8128),
    base.Lane("132_gpu1_8b", 1, 8129),
    base.Lane("132_gpu2_8b", 2, 8130),
    base.Lane("132_gpu3_8b", 3, 8131),
]


@dataclass
class EvalItem:
    label: str
    boot_key: str
    run_name: str
    dataset: Path
    lane: base.Lane
    process: subprocess.Popen[str]
    log_path: Path
    bootstrap: bool = False
    skill_path: Path | None = None
    started_at: str = ""
    ended_at: str | None = None
    returncode: int | None = None
    run_dir: str = ""
    partial_stopped: bool = False


launched: dict[str, EvalItem] = {}
pipeline_records: dict[str, dict[str, object]] = {}
offline_records: list[dict[str, object]] = []
state_progress: dict[str, tuple[int, float]] = {}
pipeline_errors: list[dict[str, str]] = []
lock = threading.RLock()
offline_lock = threading.Lock()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def configure_base() -> None:
    base.HOST_TAG = "132"
    base.RUN_PREFIX = PREFIX
    base.MASTER_NAME = MASTER_NAME
    base.SUMMARY_PATH = SUMMARY_JSON
    base.LANES = LANES
    base.CONFLICTING_VLLM_PORTS = [lane.port for lane in LANES]
    base.VLLM_GPU_MEMORY_UTILIZATION = os.environ.get(
        "GAIA_8B_BOOTV3V4_AFULL_REMOTE132_VLLM_GPU_MEMORY_UTILIZATION",
        "0.90",
    )
    base.VLLM_MAX_NUM_SEQS = os.environ.get(
        "GAIA_8B_BOOTV3V4_AFULL_REMOTE132_VLLM_MAX_NUM_SEQS",
        "48",
    )


def latest_run_dir(run_name: str) -> Path | None:
    return remain20.latest_run_dir(run_name)


def latest_iteration_dir(run_dir: Path) -> Path | None:
    return remain20.latest_iteration_dir(run_dir)


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


def stats_for_run(run_name: str) -> dict[str, object]:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return {"run_name": run_name, "status": "missing_run_dir"}
    done, total, _last_mtime = state_count(run_name)
    iteration = latest_iteration_dir(run_dir)
    success = 0
    nonempty_answer = 0
    max_step_like = 0
    if iteration is not None:
        for state_path in iteration.glob("*/state.json"):
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            env = data.get("env_result", {})
            success += int(bool(env.get("evaluation", {}).get("task_success")))
            nonempty_answer += int(bool(str(env.get("final_answer", "")).strip()))
            max_step_like += int(len(env.get("action_trace", [])) >= 24)
    total = total or done
    return {
        "run_name": run_name,
        "run_dir": str(run_dir),
        "success": success,
        "landed": done,
        "total": total,
        "missing": max(0, total - done),
        "score": f"{success}/{total or '?'}",
        "nonempty_answer": nonempty_answer,
        "max_step_like": max_step_like,
    }


def bootstrap_skill_path(run_name: str) -> Path | None:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def write_manifest(status: str, extra: dict[str, object] | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "summary_json": str(SUMMARY_JSON),
        "lanes": [asdict(lane) for lane in LANES],
        "config": {
            "executor_model": "Qwen3-8B-local",
            "max_model_len": 40960,
            "max_tokens": 12288,
            "max_executor_steps": 24,
            "task_concurrency": CONCURRENCY,
            "vllm_gpu_memory_utilization": base.VLLM_GPU_MEMORY_UTILIZATION,
            "vllm_max_num_seqs": base.VLLM_MAX_NUM_SEQS,
            "actor_model": "gpt-5.4",
            "critic_model": "gpt-5.4",
            "codex_reasoning_effort": "xhigh",
            "codex_cli_path": CODEX_BIN,
            "codex_proxy": CODEX_PROXY,
            "allow_partial_source_states": True,
            "partial_source_min_states": PARTIAL_SOURCE_MIN_STATES,
        },
        "pipelines": pipeline_records,
        "offline_records": offline_records,
        "evals": {
            label: {
                "label": item.label,
                "boot_key": item.boot_key,
                "run_name": item.run_name,
                "dataset": str(item.dataset),
                "lane": asdict(item.lane),
                "pid": item.process.pid,
                "log_path": str(item.log_path),
                "bootstrap": item.bootstrap,
                "skill_path": str(item.skill_path) if item.skill_path else "",
                "started_at": item.started_at,
                "ended_at": item.ended_at,
                "returncode": item.returncode,
                "run_dir": item.run_dir,
                "partial_stopped": item.partial_stopped,
                "state_count": state_count(item.run_name)[:2],
                "stats": stats_for_run(item.run_name),
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
        "pipelines": pipeline_records,
        "offline_records": offline_records,
        "evals": {
            label: {
                "run_name": item.run_name,
                "returncode": item.returncode,
                "lane": asdict(item.lane),
                "log_path": str(item.log_path),
                "run_dir": item.run_dir,
                "partial_stopped": item.partial_stopped,
                "stats": stats_for_run(item.run_name),
            }
            for label, item in launched.items()
        },
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_inputs() -> None:
    missing = [str(path) for path in [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET, Path(CODEX_BIN)] if not path.exists()]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))


def pmon_gpu_pids(gpus: set[int]) -> list[int]:
    result = subprocess.run(
        ["nvidia-smi", "pmon", "-c", "1", "-s", "um"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        gpu = int(parts[0])
        pid = int(parts[1])
        if gpu in gpus and pid > 0:
            pids.append(pid)
    return sorted(set(pids))


def terminate_pid(pid: int, reason: str) -> None:
    if pid == os.getpid():
        return
    try:
        os.killpg(pid, signal.SIGTERM)
        log(f"sent SIGTERM to pid group {pid}: {reason}")
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, signal.SIGTERM)
            log(f"sent SIGTERM to pid {pid}: {reason}")
        except Exception as exc:
            log(f"failed to terminate pid {pid}: {exc!r}")
            return
    deadline = time.time() + 30
    while time.time() < deadline:
        if not base.pid_alive(pid):
            return
        time.sleep(1)
    try:
        os.killpg(pid, signal.SIGKILL)
        log(f"sent SIGKILL to pid group {pid}: {reason}")
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def force_clear_gpu0123() -> None:
    if not FORCE_CLEAR_GPUS:
        log("force clear skipped by GAIA_8B_BOOTV3V4_AFULL_REMOTE132_FORCE_CLEAR=0")
        return
    targets: set[int] = set()
    for lane in LANES:
        pid = base.find_vllm_pid(lane.port)
        if pid is not None:
            targets.add(pid)
    targets.update(pmon_gpu_pids({0, 1, 2, 3}))
    for pid in sorted(targets):
        terminate_pid(pid, f"clear remote132 GPU0-3 before {PREFIX}")
    if targets:
        time.sleep(5)
    log(f"clear remote132 GPU0-3 done; pids={sorted(targets)}")


def common_env(lane: base.Lane, *, bootstrap_preprocess: bool = False, skill_path: Path | None = None) -> dict[str, str]:
    env = base.common_env(lane)
    env.update(
        {
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_ACTOR_MODEL": "gpt-5.4",
            "NLRL_CRITIC_MODEL": "gpt-5.4",
            "NLRL_ACTOR_API_MODE": "codex_cli",
            "NLRL_CRITIC_API_MODE": "codex_cli",
            "NLRL_ACTOR_REASONING_EFFORT": "xhigh",
            "NLRL_CRITIC_REASONING_EFFORT": "xhigh",
            "NLRL_CODEX_CLI_PATH": CODEX_BIN,
            "NLRL_CODEX_CLI_WORKDIR": str(ROOT),
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": str(CODEX_TIMEOUT_SECONDS),
            "NLRL_CODEX_CLI_EXTRA_ARGS": "-c 'model_reasoning_effort=\"xhigh\"'",
            "HTTP_PROXY": CODEX_PROXY,
            "HTTPS_PROXY": CODEX_PROXY,
            "http_proxy": CODEX_PROXY,
            "https_proxy": CODEX_PROXY,
        }
    )
    env.pop("ALL_PROXY", None)
    env.pop("all_proxy", None)
    if bootstrap_preprocess:
        env["NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS"] = "1"
        env["NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP"] = "700"
    else:
        env.pop("NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS", None)
    if skill_path is not None:
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(skill_path)
    return env


def poll_launched() -> None:
    with lock:
        for item in launched.values():
            if item.returncode is not None:
                continue
            code = item.process.poll()
            done, _total, last_mtime = state_count(item.run_name)
            old_done, old_at = state_progress.get(item.label, (-1, time.time()))
            if done > old_done or last_mtime > 0 and last_mtime > old_at:
                state_progress[item.label] = (done, time.time())
            elif item.label not in state_progress:
                state_progress[item.label] = (done, time.time())
            if code is None:
                continue
            item.returncode = code
            item.ended_at = now()
            run_dir = latest_run_dir(item.run_name)
            item.run_dir = str(run_dir) if run_dir else ""
            base.update_process_row(item.process.pid, "finished" if code == 0 else "dead", exit_code=code)
            log(f"eval exited {item.label} run={item.run_name} code={code}")
        write_manifest("running")


def lane_busy(lane: base.Lane) -> bool:
    return any(item.lane == lane and item.returncode is None for item in launched.values())


def wait_free_lane(preferred: list[base.Lane] | None = None) -> base.Lane:
    candidates = preferred or LANES
    while True:
        poll_launched()
        with lock:
            for lane in candidates:
                if not lane_busy(lane):
                    return lane
        log("waiting free lane among " + ",".join(lane.name for lane in candidates))
        time.sleep(30)


def launch_eval(
    *,
    label: str,
    boot_key: str,
    run_name: str,
    dataset: Path,
    lane: base.Lane,
    bootstrap: bool = False,
    bootstrap_preprocess: bool = False,
    skill_path: Path | None = None,
) -> EvalItem:
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
    env = common_env(lane, bootstrap_preprocess=bootstrap_preprocess, skill_path=skill_path)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{short_ts()}.log"
    with log_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    base.append_process_row(
        kind="experiment",
        name=run_name,
        pid=proc.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=base.command_display(env, cmd),
        log_path=log_path,
        notes=(
            f"8B {boot_key} {label}; lane={lane.name}; endpoint={lane.base_url}; "
            f"c{CONCURRENCY}; bootstrap={bootstrap}; skill={skill_path or ''}; "
            "Codex=gpt-5.4/xhigh."
        ),
    )
    item = EvalItem(
        label=label,
        boot_key=boot_key,
        run_name=run_name,
        dataset=dataset,
        lane=lane,
        process=proc,
        log_path=log_path,
        bootstrap=bootstrap,
        skill_path=skill_path,
        started_at=now(),
    )
    with lock:
        launched[label] = item
        state_progress[label] = (-1, time.time())
        write_manifest("running", {"stage": f"launched_{label}"})
    log(f"started {label} pid={proc.pid} lane={lane.name} log={log_path}")
    return item


def wait_boot_skill(item: EvalItem) -> Path:
    while True:
        poll_launched()
        skill = bootstrap_skill_path(item.run_name)
        if skill is not None:
            log(f"bootstrap skill ready for {item.label}: {skill}")
            return skill
        if item.returncode is not None:
            raise RuntimeError(f"{item.label} ended before bootstrap skill was ready: code={item.returncode}")
        done, total, _last_mtime = state_count(item.run_name)
        log(f"waiting bootstrap skill for {item.label}; states={done}/{total or '?'}")
        time.sleep(30)


def stop_eval_partial(item: EvalItem, reason: str) -> None:
    if item.returncode is not None:
        return
    item.partial_stopped = True
    try:
        os.killpg(item.process.pid, signal.SIGTERM)
        log(f"sent SIGTERM to {item.label} pid={item.process.pid}: {reason}")
    except ProcessLookupError:
        pass
    deadline = time.time() + 45
    while time.time() < deadline:
        poll_launched()
        if item.returncode is not None:
            return
        time.sleep(1)
    try:
        os.killpg(item.process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    poll_launched()


def wait_boot_source(item: EvalItem) -> Path:
    while True:
        poll_launched()
        run_dir = latest_run_dir(item.run_name)
        done, total, _last_mtime = state_count(item.run_name)
        if run_dir is not None and total and done >= total:
            return run_dir
        if item.returncode is not None:
            if run_dir is None:
                raise RuntimeError(f"{item.label} ended but run_dir is missing")
            if done < PARTIAL_SOURCE_MIN_STATES:
                raise RuntimeError(
                    f"{item.label} ended with too few source states: {done}/{total or '?'} "
                    f"min={PARTIAL_SOURCE_MIN_STATES}"
                )
            log(f"accept partial source for {item.label}: {done}/{total or '?'}")
            return run_dir
        _old_done, last_progress_at = state_progress.get(item.label, (done, time.time()))
        stalled_for = time.time() - last_progress_at
        if run_dir is not None and done >= PARTIAL_SOURCE_MIN_STATES and stalled_for >= PARTIAL_SOURCE_STALL_SECONDS:
            stop_eval_partial(item, "partial source stalled")
            log(f"accept stalled partial source for {item.label}: {done}/{total or '?'}")
            return run_dir
        log(
            f"waiting boot source for {item.label}; states={done}/{total or '?'} "
            f"stalled_for={int(stalled_for)}s partial_min={PARTIAL_SOURCE_MIN_STATES}"
        )
        time.sleep(POLL_SECONDS)


def run_offline_a(boot_key: str, source_run: Path, source_skill: Path) -> Path:
    with offline_lock:
        started_at = now()
        source = remain20.BootSource(
            model_key="8b",
            boot_key=boot_key,
            label=f"8B {boot_key.upper()} A_full source",
            boot_dev_run=source_run,
            boot_skill=source_skill,
            supplemental_runs=[],
            source_notes=f"{PREFIX}: A_full from same boot_dev source; Codex=gpt-5.4/xhigh",
        )
        skill = remain20.run_offline_variant(
            source,
            method_key="A_full",
            strategy="full",
            graph_policy="locked",
        )
        record = {
            "boot_key": boot_key,
            "method_key": "A_full",
            "strategy": "full",
            "graph_policy": "locked",
            "source_run": str(source_run),
            "source_skill": str(source_skill),
            "skill_path": str(skill),
            "started_at": started_at,
            "ended_at": now(),
            "codex_model": "gpt-5.4",
            "codex_reasoning_effort": "xhigh",
        }
        offline_records.append(record)
        write_manifest("running", {"stage": f"{boot_key}_offline_done"})
        return skill


def wait_labels(labels: list[str]) -> None:
    wanted = set(labels)
    while True:
        poll_launched()
        with lock:
            live = [label for label, item in launched.items() if label in wanted and item.returncode is None]
        if not live:
            return
        log(f"waiting labels live={live}")
        time.sleep(POLL_SECONDS)


def pipeline(boot_key: str, *, boot_dev_lane: base.Lane, boot_test_lane: base.Lane, bootstrap_preprocess: bool) -> None:
    record: dict[str, object] = {
        "boot_key": boot_key,
        "status": "running",
        "started_at": now(),
        "bootstrap_preprocess": bootstrap_preprocess,
    }
    with lock:
        pipeline_records[boot_key] = record
        write_manifest("running", {"stage": f"{boot_key}_start"})

    boot_dev_label = f"{boot_key}_boot_dev"
    boot_test_label = f"{boot_key}_boot_test"
    a_dev_label = f"{boot_key}_A_full_dev"
    a_test_label = f"{boot_key}_A_full_test"
    labels = [boot_dev_label, boot_test_label, a_dev_label, a_test_label]
    record["labels"] = labels

    boot_dev = launch_eval(
        label=boot_dev_label,
        boot_key=boot_key,
        run_name=f"{PREFIX}_8b_{boot_key}_boot_dev_c{CONCURRENCY}",
        dataset=DEV_DATASET,
        lane=boot_dev_lane,
        bootstrap=True,
        bootstrap_preprocess=bootstrap_preprocess,
    )
    boot_skill = wait_boot_skill(boot_dev)
    record["boot_skill"] = str(boot_skill)
    write_manifest("running", {"stage": f"{boot_key}_boot_skill_ready"})

    launch_eval(
        label=boot_test_label,
        boot_key=boot_key,
        run_name=f"{PREFIX}_8b_{boot_key}_boot_test_c{CONCURRENCY}",
        dataset=TEST_DATASET,
        lane=boot_test_lane,
        skill_path=boot_skill,
    )

    source_run = wait_boot_source(boot_dev)
    record["source_run"] = str(source_run)
    a_skill = run_offline_a(boot_key, source_run, boot_skill)
    record["a_full_skill"] = str(a_skill)

    a_dev_lane = wait_free_lane()
    launch_eval(
        label=a_dev_label,
        boot_key=boot_key,
        run_name=f"{PREFIX}_8b_{boot_key}_A_full_dev_eval_c{CONCURRENCY}",
        dataset=DEV_DATASET,
        lane=a_dev_lane,
        skill_path=a_skill,
    )
    a_test_lane = wait_free_lane()
    launch_eval(
        label=a_test_label,
        boot_key=boot_key,
        run_name=f"{PREFIX}_8b_{boot_key}_A_full_test_eval_c{CONCURRENCY}",
        dataset=TEST_DATASET,
        lane=a_test_lane,
        skill_path=a_skill,
    )

    wait_labels(labels)
    failed = {label: launched[label].returncode for label in labels if launched[label].returncode not in {0, None}}
    record["status"] = "finished_with_eval_failures" if failed else "finished"
    record["failed"] = failed
    record["ended_at"] = now()
    write_manifest("running", {"stage": f"{boot_key}_done"})


def pipeline_entry(**kwargs: object) -> None:
    boot_key = str(kwargs.get("boot_key", "unknown"))
    try:
        pipeline(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:
        with lock:
            pipeline_errors.append({"boot_key": boot_key, "error": repr(exc)})
            pipeline_records.setdefault(boot_key, {})["status"] = "dead"
            pipeline_records.setdefault(boot_key, {})["error"] = repr(exc)
            write_manifest("running", {"stage": f"{boot_key}_error", "error": repr(exc)})
        log(f"pipeline failed {boot_key}: {exc!r}")


def main() -> int:
    status = "finished"
    configure_base()
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    verify_inputs()
    base.ensure_process_csv_header()
    base.refresh_process_registry()
    master_command = os.environ.get(
        "GAIA_8B_BOOTV3V4_AFULL_REMOTE132_MASTER_COMMAND",
        f"{PYTHON} -u {Path(__file__).resolve()}",
    )
    base.append_process_row(
        kind="experiment_master",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir=str(QUEUE_LOG_ROOT),
        command=master_command,
        log_path=MASTER_LOG,
        notes=(
            f"8B BOOT-V3/V4 -> A_full remote132 GPU0-3; c{CONCURRENCY}; "
            f"mem={base.VLLM_GPU_MEMORY_UTILIZATION}; Codex=gpt-5.4/xhigh; "
            f"summary={SUMMARY_JSON}; manifest={MANIFEST_JSON}"
        ),
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    write_manifest("starting")
    try:
        force_clear_gpu0123()
        vllm_pids = [base.start_or_reuse_vllm(lane) for lane in LANES]
        base.wait_endpoints()
        write_manifest("vllm_ready", {"vllm_pids": vllm_pids})

        threads = [
            threading.Thread(
                target=pipeline_entry,
                kwargs={
                    "boot_key": "bootv4",
                    "boot_dev_lane": LANES[0],
                    "boot_test_lane": LANES[1],
                    "bootstrap_preprocess": True,
                },
                name="bootv4-pipeline",
            ),
            threading.Thread(
                target=pipeline_entry,
                kwargs={
                    "boot_key": "bootv3",
                    "boot_dev_lane": LANES[2],
                    "boot_test_lane": LANES[3],
                    "bootstrap_preprocess": False,
                },
                name="bootv3-pipeline",
            ),
        ]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            poll_launched()
            for thread in threads:
                thread.join(timeout=30)
        poll_launched()
        if pipeline_errors:
            raise RuntimeError(f"pipeline errors: {pipeline_errors}")
        write_summary("finished")
        write_manifest("finished")
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        write_summary(status)
        write_manifest(status)
        raise
    except Exception as exc:
        status = "dead"
        write_summary(status)
        write_manifest(status, {"error": repr(exc)})
        raise
    finally:
        base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
