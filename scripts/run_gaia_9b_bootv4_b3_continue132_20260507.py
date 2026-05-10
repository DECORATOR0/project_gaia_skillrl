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
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
DAY_ROOT = RUN_ROOT / "2026/5/2026-5-7"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

PREFIX = os.environ.get("GAIA_B3_CONTINUE_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M_9b_bootv4_b3_continue132"
)
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = Path(os.environ.get("GAIA_B3_CONTINUE_MASTER_LOG", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"))
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"
SUMMARY_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"

REMOTE_HOST = "124.115.123.132"
REMOTE_PORT = "22219"
REMOTE_USER = "xsy"
REMOTE_PASSWORD = "xsy945@j8ca0"

CONCURRENCY = int(os.environ.get("GAIA_B3_CONTINUE_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_B3_CONTINUE_POLL_SECONDS", "180"))
CODEX_TIMEOUT_SECONDS = int(os.environ.get("GAIA_B3_CONTINUE_CODEX_TIMEOUT_SECONDS", "3600"))

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
    remote_gpu: int
    remote_port: int


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
    config_snapshot: dict | None = None


LANES = [
    Lane(
        name=f"remote132_gpu{gpu}_9b",
        model_key="9b",
        base_url=f"http://127.0.0.1:{18120 + gpu}/v1",
        served_name="Qwen3.5-9B-local",
        tokenizer_path="/data/xsy/codes/checkpoints/Qwen3.5-9B",
        max_model_len=49152,
        remote_gpu=gpu,
        remote_port=8120 + gpu,
    )
    for gpu in range(8)
]

launched: dict[str, LaunchedEval] = {}
remote_vllm_records: list[dict] = []
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


def run_remote(command: str, timeout: int = 240) -> str:
    helper = r'''
import pexpect
import sys

command = sys.argv[1]
timeout = int(sys.argv[2])
child = pexpect.spawn(
    "ssh",
    [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=/data/xsy/.ssh/known_hosts",
        "-p", "22219",
        "xsy@124.115.123.132",
        command,
    ],
    timeout=timeout,
    encoding="utf-8",
)
chunks = []
while True:
    index = child.expect(["password:", "yes/no", pexpect.EOF, pexpect.TIMEOUT])
    chunks.append(child.before)
    if index == 0:
        child.sendline("xsy945@j8ca0")
    elif index == 1:
        child.sendline("yes")
    elif index == 2:
        break
    else:
        raise TimeoutError("remote ssh command timed out")
child.close()
if child.exitstatus not in (0, None):
    print("".join(chunks), end="")
    raise SystemExit(child.exitstatus)
if child.signalstatus:
    print("".join(chunks), end="")
    raise SystemExit(128 + child.signalstatus)
print("".join(chunks), end="")
'''
    completed = subprocess.run(
        ["python3", "-c", helper, command, str(timeout)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout + 30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"remote command failed rc={completed.returncode}: {completed.stderr[-2000:]}")
    return completed.stdout


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
    row = [
        now(),
        now(),
        "running",
        kind,
        name,
        str(pid),
        now(),
        "",
        cwd,
        run_dir,
        command,
        "",
        notes,
        log_path,
    ]
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(row)


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    return base.command_display(env, cmd)


def deploy_remote_9b_services() -> None:
    specs_text = " ".join(f"{lane.remote_gpu}:{lane.remote_port}" for lane in LANES)
    remote_script = f"""
set -u
mkdir -p /data/xsy/project_gaia_skillrl/runs/_launch_logs
for spec in {specs_text}; do
  port=${{spec##*:}}
  pids=$(ps -eo pid,args | awk -v p="$port" '$0 ~ /vllm.entrypoints.openai.api_server/ && index($0, "--port " p) {{print $1}}')
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
done
sleep 8
for spec in {specs_text}; do
  port=${{spec##*:}}
  pids=$(ps -eo pid,args | awk -v p="$port" '$0 ~ /vllm.entrypoints.openai.api_server/ && index($0, "--port " p) {{print $1}}')
  for pid in $pids; do kill -9 "$pid" 2>/dev/null || true; done
done
for spec in {specs_text}; do
  gpu=${{spec%%:*}}
  port=${{spec##*:}}
  log=/data/xsy/project_gaia_skillrl/runs/_launch_logs/{PREFIX}_remote132_qwen35_9b_gpu${{gpu}}_p${{port}}.log
  CUDA_VISIBLE_DEVICES=$gpu nohup /data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python -u -m vllm.entrypoints.openai.api_server \\
    --model /data/xsy/codes/checkpoints/Qwen3.5-9B \\
    --served-model-name Qwen3.5-9B-local \\
    --host 127.0.0.1 \\
    --port $port \\
    --tensor-parallel-size 1 \\
    --gpu-memory-utilization 0.90 \\
    --dtype bfloat16 \\
    --trust-remote-code \\
    --enable-prefix-caching \\
    --reasoning-parser qwen3 \\
    --reasoning-config '{{"reasoning_start_str":"<think>","reasoning_end_str":"</think>"}}' \\
    --max-model-len 49152 \\
    --max-num-seqs 128 > "$log" 2>&1 < /dev/null &
  echo "started gpu=$gpu port=$port pid=$! log=$log"
done
"""
    log("redeploying remote132 GPU0-7 as Qwen3.5-9B-local")
    output = run_remote(f"bash -lc {shlex.quote(remote_script)}", timeout=180)
    log(output.strip())
    if output.count("started gpu=") < len(LANES):
        raise RuntimeError(f"remote132 9B deploy incomplete: expected {len(LANES)} starts, got {output.count('started gpu=')}; output={output[-2000:]}")
    pattern = re.compile(r"started gpu=(\d+) port=(\d+) pid=(\d+) log=(.+)")
    for line in output.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        gpu, port, pid, log_path = match.groups()
        command = (
            f"CUDA_VISIBLE_DEVICES={gpu} nohup /data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python -u "
            "-m vllm.entrypoints.openai.api_server --model /data/xsy/codes/checkpoints/Qwen3.5-9B "
            "--served-model-name Qwen3.5-9B-local --host 127.0.0.1 "
            f"--port {port} --tensor-parallel-size 1 --gpu-memory-utilization 0.90 --dtype bfloat16 "
            "--trust-remote-code --enable-prefix-caching --reasoning-parser qwen3 "
            "--reasoning-config '{\"reasoning_start_str\":\"<think>\",\"reasoning_end_str\":\"</think>\"}' "
            "--max-model-len 49152 --max-num-seqs 128"
        )
        name = f"{PREFIX}_remote132_qwen35_9b_gpu{gpu}_p{port}"
        append_process_row(
            kind="remote_vllm",
            name=name,
            pid=int(pid),
            cwd=f"{REMOTE_HOST}:/data/xsy",
            run_dir=f"{REMOTE_HOST}:/data/xsy/project_gaia_skillrl/runs/_launch_logs",
            command=command,
            log_path=f"{REMOTE_HOST}:{log_path}",
            notes=f"remote host {REMOTE_HOST}:{REMOTE_PORT}; GPU{gpu}; local tunnel {18120 + int(gpu)} -> remote {port}; {PREFIX}",
        )
        remote_vllm_records.append({"gpu": int(gpu), "port": int(port), "pid": int(pid), "log_path": log_path})


def wait_9b_endpoints() -> None:
    deadline = time.time() + 600
    while time.time() < deadline:
        missing = []
        for lane in LANES:
            model_ids = endpoint_models(lane.base_url)
            ready = any(item == lane.served_name or item.endswith("/Qwen3.5-9B") for item in model_ids)
            if not ready:
                missing.append(lane)
        if not missing:
            log("remote132 9B endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        log("waiting 9B endpoints: " + ", ".join(f"{lane.name}:{lane.base_url}" for lane in missing))
        time.sleep(15)
    raise RuntimeError("remote132 9B endpoints did not become ready")


def common_env(lane: Lane) -> dict[str, str]:
    compat_lane = base.Lane(
        lane.name,
        lane.model_key,
        lane.base_url,
        lane.served_name,
        lane.tokenizer_path,
        lane.max_model_len,
        lane.remote_gpu,
        lane.remote_port,
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
        "remote_vllm_records": remote_vllm_records,
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
            f"9B BOOT-V4 B3 continuation {label}; runner={runner}; "
            f"lane={lane.name}; endpoint={lane.base_url}; skill={skill_path or 'direct baseline'}; {PREFIX}"
        ),
    )
    item = LaunchedEval(
        label=label,
        run_name=run_name,
        dataset=dataset,
        runner=runner,
        lane=lane,
        skill_path=skill_path,
        process=process,
        log_path=log_path,
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
    busy = {item.lane.name for item in launched.values() if item.returncode is None and item.process.poll() is None}
    ordered = LANES[preferred_index:] + LANES[:preferred_index]
    for lane in ordered:
        if lane.name not in busy:
            return lane
    while True:
        poll_launched()
        busy = {item.lane.name for item in launched.values() if item.returncode is None and item.process.poll() is None}
        for lane in ordered:
            if lane.name not in busy:
                return lane
        log("waiting for a free 9B lane")
        time.sleep(30)


def run_offline(tag: str, source_run: Path, source_skill: Path, supplemental_runs: list[Path]) -> Path:
    source = base.BootSource(
        model_key="9b",
        boot_key="bootv4",
        label=f"9B BOOT-V4 {tag} continuation source",
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
        "remote_vllm_records": remote_vllm_records,
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
    required = [PYTHON, CONFIG, DEV_DATASET, TEST_DATASET, B3_SKILL, B3_DEV_PRIMARY, B3_DEV_SUPPLEMENT]
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
    master_command = os.environ.get("GAIA_B3_CONTINUE_MASTER_COMMAND", f"{PYTHON} -u {Path(__file__).resolve()}")
    append_process_row(
        kind="experiment_master",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=str(ROOT),
        run_dir=str(QUEUE_LOG_ROOT),
        command=master_command,
        log_path=str(MASTER_LOG),
        notes=f"9B BOOT-V4 B3 continuation on remote132; B3-2 then B3-3; summary={SUMMARY_JSON}; manifest={MANIFEST_JSON}",
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        if os.environ.get("GAIA_B3_CONTINUE_SKIP_DEPLOY", "").strip().lower() in {"1", "true", "yes", "on"}:
            log("remote132 deploy skipped by GAIA_B3_CONTINUE_SKIP_DEPLOY=1")
        else:
            deploy_remote_9b_services()
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
        baseline.config_snapshot = snapshot
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
            lane=first_free_lane(2),
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
            lane=first_free_lane(3),
            runner="train-local",
            skill_path=b3_3_skill,
        )
        launch_eval(
            label="9b_bootv4_B3-3_test",
            run_name=f"{PREFIX}_9b_bootv4_B3_3_test_eval_c{CONCURRENCY}",
            dataset=TEST_DATASET,
            lane=first_free_lane(4),
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
