from __future__ import annotations

import csv
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"
VLLM = Path(os.environ.get("GAIA_B3_CONTINUE254_VLLM_BIN", "/data/xsy/miniconda3/envs/vllm_budget/bin/vllm"))
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
DAY_ROOT = RUN_ROOT / "2026/5/2026-5-7"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

PREFIX = os.environ.get("GAIA_B3_CONTINUE254_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M_9b_bootv4_b3_continue254"
)
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(os.environ.get("GAIA_B3_CONTINUE254_MASTER_LOG", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"))
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"

CONCURRENCY = int(os.environ.get("GAIA_B3_CONTINUE254_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_B3_CONTINUE254_POLL_SECONDS", "180"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_B3_CONTINUE254_CODEX_TIMEOUT_SECONDS", "3600"))
VLLM_GPU_MEMORY_UTILIZATION = os.environ.get("GAIA_B3_CONTINUE254_VLLM_GPU_MEMORY_UTILIZATION", "0.90")
VLLM_MAX_NUM_SEQS = os.environ.get("GAIA_B3_CONTINUE254_VLLM_MAX_NUM_SEQS", "32")

B3_SKILL = DAY_ROOT / (
    "20260507_084814_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv4_B3_offline_actor_iter1/"
    "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)
B3_DEV_PRIMARY = DAY_ROOT / "20260507_084813_8b9b_archv53_bootv3v4_remain20_fast_9b_bootv4_B3_dev_eval_c20_oversub_refill_c20"
B3_DEV_SUPPLEMENT = DAY_ROOT / "20260507_103807_rebalance_8b9b_remain20_refill_9b_bootv4_B3_dev_gpu3_9b_rebalanced_c20"

os.environ.setdefault("GAIA_24EXP_PREFIX", PREFIX)
os.environ.setdefault("GAIA_24EXP_TASK_CONCURRENCY", str(CONCURRENCY))
os.environ.setdefault("GAIA_24EXP_CODEX_TIMEOUT_SECONDS", str(CODEX_TIMEOUT_SECONDS))
os.environ.setdefault("GAIA_REMAIN20_ALLOW_PARTIAL_SOURCE_STATES", "1")
os.environ.setdefault("GAIA_REMAIN20_CRITIC_SHARD_CONCURRENCY", "99")
os.environ.setdefault("NLRL_CODEX_CLI_WORKDIR", str(ROOT))

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b9b_remain20_fast_20260507 as base  # noqa: E402


@dataclass(frozen=True)
class Lane:
    name: str
    model_key: str
    base_url: str
    served_name: str
    tokenizer_path: str
    max_model_len: int
    gpu: int
    port: int


@dataclass
class LaunchedEval:
    label: str
    run_name: str
    dataset: Path
    runner: str
    lane: Lane
    skill_path: Path | None
    process: subprocess.Popen[str]
    log_path: Path
    started_at: str
    returncode: int | None = None
    ended_at: str | None = None
    run_dir: str = ""


LANES = [
    Lane(
        name="254_gpu0_9b",
        model_key="9b",
        base_url="http://127.0.0.1:8610/v1",
        served_name="Qwen3.5-9B-local",
        tokenizer_path="/data/xsy/codes/checkpoints/Qwen3.5-9B",
        max_model_len=49152,
        gpu=0,
        port=8610,
    ),
    Lane(
        name="254_gpu1_9b",
        model_key="9b",
        base_url="http://127.0.0.1:8611/v1",
        served_name="Qwen3.5-9B-local",
        tokenizer_path="/data/xsy/codes/checkpoints/Qwen3.5-9B",
        max_model_len=49152,
        gpu=1,
        port=8611,
    ),
]

launched: dict[str, LaunchedEval] = {}
vllm_processes: list[dict] = []
offline_records: list[dict] = []


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def endpoint_models(base_url: str) -> list[str]:
    try:
        request = Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    except Exception:
        return []


def append_process_row(
    *,
    kind: str,
    name: str,
    pid: int,
    cwd: str,
    run_dir: str,
    command: str,
    log_path: str,
    notes: str,
) -> None:
    base.ensure_process_csv_header()
    row = [now(), now(), "running", kind, name, str(pid), now(), "", cwd, run_dir, command, "", notes, log_path]
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(row)


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    return base.command_display(env, cmd)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_port(port: int) -> None:
    result = subprocess.run(["ps", "-eo", "pid,args"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    pids = []
    marker = f"--port {port}"
    for line in result.stdout.splitlines():
        if "vllm" in line and marker in line:
            first = line.strip().split(maxsplit=1)[0]
            if first.isdigit():
                pids.append(int(first))
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            os.kill(pid, signal.SIGTERM)
    time.sleep(3)
    for pid in pids:
        if pid_alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                os.kill(pid, signal.SIGKILL)


def deploy_local_9b_services() -> None:
    if os.environ.get("GAIA_B3_CONTINUE254_SKIP_DEPLOY", "").strip().lower() in {"1", "true", "yes", "on"}:
        log("local 254 deploy skipped by GAIA_B3_CONTINUE254_SKIP_DEPLOY=1")
        return
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    for lane in LANES:
        stop_port(lane.port)
    for lane in LANES:
        log_path = LAUNCH_LOG_ROOT / f"{PREFIX}_{lane.name}_vllm_p{lane.port}.log"
        cmd = [
            str(VLLM),
            "serve",
            "/data/xsy/codes/checkpoints/Qwen3.5-9B",
            "--host",
            "127.0.0.1",
            "--port",
            str(lane.port),
            "--served-model-name",
            lane.served_name,
            "--trust-remote-code",
            "--max-model-len",
            str(lane.max_model_len),
            "--max-num-seqs",
            VLLM_MAX_NUM_SEQS,
            "--gpu-memory-utilization",
            VLLM_GPU_MEMORY_UTILIZATION,
            "--dtype",
            "bfloat16",
            "--enable-prefix-caching",
            "--reasoning-parser",
            "qwen3",
            "--reasoning-config",
            '{"reasoning_start_str":"<think>","reasoning_end_str":"</think>"}',
        ]
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(lane.gpu)
        env["PYTHONUNBUFFERED"] = "1"
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
        append_process_row(
            kind="local_vllm",
            name=f"{PREFIX}_{lane.name}_qwen35_9b_p{lane.port}",
            pid=proc.pid,
            cwd=str(ROOT),
            run_dir=str(LAUNCH_LOG_ROOT),
            command="CUDA_VISIBLE_DEVICES=" + str(lane.gpu) + " " + " ".join(shlex.quote(x) for x in cmd),
            log_path=str(log_path),
            notes=f"254 local Qwen3.5-9B service; lane={lane.name}; base_url={lane.base_url}; max_model_len={lane.max_model_len}; max_num_seqs={VLLM_MAX_NUM_SEQS}",
        )
        vllm_processes.append({"lane": asdict(lane), "pid": proc.pid, "log_path": str(log_path)})
        log(f"started local 9B vLLM {lane.name} pid={proc.pid} port={lane.port} log={log_path}")


def wait_9b_endpoints() -> None:
    deadline = time.time() + 600
    while time.time() < deadline:
        missing = []
        for lane in LANES:
            ids = endpoint_models(lane.base_url)
            if not any(item == lane.served_name or item.endswith("/Qwen3.5-9B") for item in ids):
                missing.append(lane)
        if not missing:
            log("254 9B endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        for lane in missing:
            proc = next((item for item in vllm_processes if item["lane"]["name"] == lane.name), None)
            if proc and not pid_alive(int(proc["pid"])):
                log_path = Path(proc["log_path"])
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if log_path.exists() else ""
                raise RuntimeError(f"local vLLM exited before ready: {lane.name}; log_tail={tail}")
        log("waiting 254 9B endpoints: " + ", ".join(f"{lane.name}:{lane.base_url}" for lane in missing))
        time.sleep(15)
    raise RuntimeError("254 9B endpoints did not become ready")


def common_env(lane: Lane) -> dict[str, str]:
    compat_lane = base.Lane(
        lane.name,
        lane.model_key,
        lane.base_url,
        lane.served_name,
        lane.tokenizer_path,
        lane.max_model_len,
        lane.gpu,
        lane.port,
    )
    env = base.common_env(compat_lane)
    env["NLRL_RUNTIME_TASK_CONCURRENCY"] = str(CONCURRENCY)
    env["NLRL_LLM_MAX_CONCURRENT_REQUESTS"] = str(CONCURRENCY)
    return env


def run_name_suffix_for_match(run_name: str) -> str:
    match = re.match(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_(.+)$", run_name)
    return "_" + match.group(1) if match else run_name


def latest_run_dir(run_name: str) -> Path | None:
    suffix = run_name_suffix_for_match(run_name)
    matches = [
        path
        for pattern in (f"**/{run_name}", f"**/*{suffix}")
        for path in RUN_ROOT.glob(pattern)
        if path.is_dir()
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def latest_iteration_dir(run_dir: Path) -> Path | None:
    iterations = [path for path in run_dir.glob("iteration_*") if path.is_dir()]
    return max(iterations, key=lambda path: path.name) if iterations else None


def stats_for_run(run_name: str) -> dict:
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
    iteration = latest_iteration_dir(run_dir)
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


def read_config_snapshot(run_name: str, timeout: int = 180) -> tuple[str, dict | None]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run_dir = latest_run_dir(run_name)
        if run_dir is not None:
            cfg_path = run_dir / "config_snapshot.json"
            if cfg_path.exists():
                try:
                    return str(run_dir), json.loads(cfg_path.read_text(encoding="utf-8"))
                except Exception:
                    return str(run_dir), None
        time.sleep(5)
    return "", None


def write_manifest(status: str, extra: dict | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "lanes": [asdict(lane) for lane in LANES],
        "vllm_processes": vllm_processes,
        "offline_records": offline_records,
        "launched": {
            name: {
                "label": item.label,
                "run_name": item.run_name,
                "dataset": str(item.dataset),
                "runner": item.runner,
                "lane": asdict(item.lane),
                "skill_path": str(item.skill_path) if item.skill_path else "",
                "pid": item.process.pid,
                "log_path": str(item.log_path),
                "started_at": item.started_at,
                "returncode": item.returncode,
                "ended_at": item.ended_at,
                "run_dir": item.run_dir,
                "stats": stats_for_run(item.run_name),
            }
            for name, item in launched.items()
        },
    }
    if extra:
        payload.update(extra)
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def launch_eval(
    *,
    label: str,
    run_name: str,
    dataset: Path,
    lane: Lane,
    runner: str,
    skill_path: Path | None = None,
) -> LaunchedEval:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{short_ts()}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        runner,
        "--dataset-path",
        str(dataset),
        "--run-name",
        run_name,
    ]
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
    append_process_row(
        kind="experiment",
        name=run_name,
        pid=process.pid,
        cwd=str(ROOT),
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=str(log_path),
        notes=(
            f"9B BOOT-V4 B3 continuation on 254 {label}; runner={runner}; "
            f"lane={lane.name}; endpoint={lane.base_url}; skill={skill_path or 'direct baseline'}; {PREFIX}"
        ),
    )
    item = LaunchedEval(label, run_name, dataset, runner, lane, skill_path, process, log_path, now())
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
        base.update_process_row(item.process.pid, "finished" if code == 0 else "dead", code)
        log(f"eval exited {item.label} pid={item.process.pid} code={code}")
        changed = True
    if changed:
        write_manifest("running")


def wait_label(label: str) -> LaunchedEval:
    while True:
        poll_launched()
        item = launched[label]
        if item.returncode is not None:
            return item
        live = [name for name, record in launched.items() if record.returncode is None]
        log(f"waiting {label}; live evals={live}")
        time.sleep(POLL_SECONDS)


def wait_all() -> None:
    while True:
        poll_launched()
        live = [name for name, item in launched.items() if item.returncode is None]
        if not live:
            return
        log(f"waiting all evals; live={live}")
        time.sleep(POLL_SECONDS)


def first_free_lane(preferred_index: int = 0) -> Lane:
    ordered = LANES[preferred_index:] + LANES[:preferred_index]
    while True:
        poll_launched()
        busy = {item.lane.name for item in launched.values() if item.returncode is None and item.process.poll() is None}
        for lane in ordered:
            if lane.name not in busy:
                return lane
        log("waiting for a free 254 9B lane")
        time.sleep(30)


def run_offline(tag: str, source_run: Path, source_skill: Path, supplemental_runs: list[Path]) -> Path:
    source = base.BootSource(
        model_key="9b",
        boot_key="bootv4",
        label=f"9B BOOT-V4 {tag} continuation source on 254",
        boot_dev_run=source_run,
        boot_skill=source_skill,
        supplemental_runs=supplemental_runs,
        source_notes=f"{tag} is same B3 prompt-version continuation; partial source states accepted",
    )
    started = now()
    skill = base.run_offline_variant(source, method_key=tag.replace("-", "_"), strategy="graph_v3", graph_policy="graph_v3")
    record = {
        "tag": tag,
        "started_at": started,
        "ended_at": now(),
        "source_run": str(source_run),
        "source_skill": str(source_skill),
        "supplemental_runs": [str(path) for path in supplemental_runs],
        "skill_path": str(skill),
    }
    offline_records.append(record)
    write_manifest("running")
    return skill


def write_summary(status: str) -> None:
    payload = {
        "prefix": PREFIX,
        "master": MASTER_NAME,
        "status": status,
        "updated_at": now(),
        "manifest_json": str(MANIFEST_JSON),
        "vllm_processes": vllm_processes,
        "offline_records": offline_records,
        "evals": {
            label: {
                "run_name": item.run_name,
                "returncode": item.returncode,
                "lane": asdict(item.lane),
                "log_path": str(item.log_path),
                "run_dir": item.run_dir,
                "stats": stats_for_run(item.run_name),
            }
            for label, item in launched.items()
        },
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_inputs() -> None:
    required = [PYTHON, VLLM, CONFIG, DEV_DATASET, TEST_DATASET, B3_SKILL, B3_DEV_PRIMARY, B3_DEV_SUPPLEMENT]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))


def main() -> int:
    start = now()
    status = "finished"
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    base.ensure_process_csv_header()
    base.refresh_process_registry()
    verify_inputs()
    master_command = os.environ.get("GAIA_B3_CONTINUE254_MASTER_COMMAND", f"{PYTHON} -u {Path(__file__).resolve()}")
    append_process_row(
        kind="experiment_master",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=str(ROOT),
        run_dir=str(QUEUE_LOG_ROOT),
        command=master_command,
        log_path=str(MASTER_LOG),
        notes=f"9B BOOT-V4 B3 continuation on 254; B3-2 then B3-3; summary={SUMMARY_JSON}; manifest={MANIFEST_JSON}",
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        deploy_local_9b_services()
        wait_9b_endpoints()
        baseline = launch_eval(
            label="9b_baseline_dev",
            run_name=f"{PREFIX}_9b_baseline_dev_c{CONCURRENCY}",
            dataset=DEV_DATASET,
            lane=first_free_lane(0),
            runner="direct-eval-local",
        )
        run_dir, snapshot = read_config_snapshot(baseline.run_name, timeout=180)
        baseline.run_dir = run_dir
        log(f"baseline config snapshot checked run_dir={run_dir}")
        write_manifest("running", {"first_config_snapshot_checked": {"label": baseline.label, "run_dir": run_dir, "snapshot": snapshot}})

        b3_2_skill = run_offline("B3-2", B3_DEV_PRIMARY, B3_SKILL, [B3_DEV_SUPPLEMENT])
        b3_2_dev = launch_eval(
            label="9b_bootv4_B3-2_dev",
            run_name=f"{PREFIX}_9b_bootv4_B3_2_dev_eval_c{CONCURRENCY}",
            dataset=DEV_DATASET,
            lane=first_free_lane(1),
            runner="train-local",
            skill_path=b3_2_skill,
        )
        launch_eval(
            label="9b_bootv4_B3-2_test",
            run_name=f"{PREFIX}_9b_bootv4_B3_2_test_eval_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane=first_free_lane(0),
            runner="train-local",
            skill_path=b3_2_skill,
        )

        wait_label(b3_2_dev.label)
        b3_2_dev_dir = latest_run_dir(b3_2_dev.run_name)
        if b3_2_dev_dir is None:
            raise RuntimeError(f"B3-2 dev run_dir missing after eval exit: {b3_2_dev.run_name}")
        b3_3_skill = run_offline("B3-3", b3_2_dev_dir, b3_2_skill, [])
        launch_eval(
            label="9b_bootv4_B3-3_dev",
            run_name=f"{PREFIX}_9b_bootv4_B3_3_dev_eval_c{CONCURRENCY}",
            dataset=DEV_DATASET,
            lane=first_free_lane(1),
            runner="train-local",
            skill_path=b3_3_skill,
        )
        launch_eval(
            label="9b_bootv4_B3-3_test",
            run_name=f"{PREFIX}_9b_bootv4_B3_3_test_eval_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane=first_free_lane(0),
            runner="train-local",
            skill_path=b3_3_skill,
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
        base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
