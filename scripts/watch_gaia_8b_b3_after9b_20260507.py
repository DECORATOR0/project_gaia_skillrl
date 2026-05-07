from __future__ import annotations

import csv
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
RUN_ROOT = ROOT / "runs"
DAY_ROOT = RUN_ROOT / "2026/5/2026-5-7"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

PREFIX = "20260507_1148_8b_b3_after9b"
WATCH_STATUS_JSONL = QUEUE_LOG_ROOT / "20260507_1038_rebalance_8b9b_live_status.jsonl"
STATUS_JSONL = QUEUE_LOG_ROOT / f"{PREFIX}_watch_status.jsonl"
MANIFEST_JSON = QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"

DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"

REMOTE_HOST = "124.115.123.132"
REMOTE_PORT = "22219"
REMOTE_USER = "xsy"
REMOTE_PASSWORD = "xsy945@j8ca0"

POLL_SECONDS = int(os.environ.get("GAIA_8B_B3_WATCH_POLL_SECONDS", "180"))
TRIGGER_REBALANCE_9B_MISSING = int(os.environ.get("GAIA_8B_B3_TRIGGER_REBALANCE_9B_MISSING", "40"))
TRIGGER_LIVE_9B_COUNT = int(os.environ.get("GAIA_8B_B3_TRIGGER_LIVE_9B_COUNT", "16"))
TARGET_EXTRA_8B_PORTS = int(os.environ.get("GAIA_8B_B3_TARGET_EXTRA_8B_PORTS", "3"))
FORCE_TRIGGER = os.environ.get("GAIA_8B_B3_FORCE_TRIGGER", "0").strip().lower() in {"1", "true", "yes", "on"}

SOURCE_8B_BOOTV3_DEV = DAY_ROOT / "20260507_012303_8b9b_archv53_bootv3v4_24exp_8b_bootv3_boot_dev_c20"
SOURCE_8B_BOOTV3_SUPPLEMENT = DAY_ROOT / "20260507_092250_missing_tail_fill_8b_bootv3_boot_dev_missing1_c1"
SOURCE_8B_BOOTV4_DEV = DAY_ROOT / "20260507_012303_8b9b_archv53_bootv3v4_24exp_8b_bootv4_boot_dev_c20"


os.environ["GAIA_24EXP_PREFIX"] = PREFIX
os.environ.setdefault("GAIA_REMAIN20_ALLOW_PARTIAL_SOURCE_STATES", "1")
os.environ.setdefault("GAIA_REMAIN20_OFFLINE_WORKERS", "2")
os.environ.setdefault("GAIA_REMAIN20_CRITIC_SHARD_CONCURRENCY", "99")
os.environ.setdefault("NLRL_CODEX_CLI_WORKDIR", str(ROOT))

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b9b_remain20_fast_20260507 as base  # noqa: E402


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def short_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def write_status(payload: dict) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"checked_at": now(), **payload}
    with STATUS_JSONL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_manifest(payload: dict) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_inputs() -> None:
    required = [
        WATCH_STATUS_JSONL,
        DEV_DATASET,
        TEST_DATASET,
        SOURCE_8B_BOOTV3_DEV / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md",
        SOURCE_8B_BOOTV3_SUPPLEMENT,
        SOURCE_8B_BOOTV4_DEV / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))


def load_latest_watch_status() -> dict:
    lines = [line for line in WATCH_STATUS_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"empty status jsonl: {WATCH_STATUS_JSONL}")
    return json.loads(lines[-1])


def local_port_from_base_url(base_url: str) -> int | None:
    for port in range(18120, 18128):
        if f":{port}/" in base_url or base_url.endswith(f":{port}/v1") or base_url.endswith(f":{port}"):
            return port
    return None


def summarize_watch_status(status: dict) -> dict:
    records = status.get("records", [])
    live_9b = [item for item in records if item.get("model_family") == "9b"]
    rebalance_9b = [item for item in live_9b if item.get("family") == "rebalance"]
    old_9b = [item for item in live_9b if item.get("family") != "rebalance"]
    by_port: dict[int, dict] = {}
    for item in live_9b:
        port = local_port_from_base_url(str(item.get("base_url") or ""))
        if port is None:
            continue
        slot = by_port.setdefault(port, {"records": 0, "missing": 0, "landed": 0, "total": 0, "pids": []})
        slot["records"] += 1
        slot["missing"] += int(item.get("missing") or 0)
        slot["landed"] += int(item.get("landed") or 0)
        slot["total"] += int(item.get("total") or 0)
        slot["pids"].append(item.get("pid"))
    return {
        "source_checked_at": status.get("checked_at"),
        "live_9b_count": len(live_9b),
        "rebalance_9b_count": len(rebalance_9b),
        "old_9b_count": len(old_9b),
        "rebalance_9b_missing": sum(int(item.get("missing") or 0) for item in rebalance_9b),
        "rebalance_9b_landed": sum(int(item.get("landed") or 0) for item in rebalance_9b),
        "rebalance_9b_total": sum(int(item.get("total") or 0) for item in rebalance_9b),
        "old_9b_missing": sum(int(item.get("missing") or 0) for item in old_9b),
        "ports": by_port,
    }


def trigger_ready(summary: dict) -> tuple[bool, str]:
    if FORCE_TRIGGER:
        return True, "force trigger requested"
    if summary["rebalance_9b_missing"] <= TRIGGER_REBALANCE_9B_MISSING:
        return True, f"rebalance_9b_missing<={TRIGGER_REBALANCE_9B_MISSING}"
    if summary["live_9b_count"] <= TRIGGER_LIVE_9B_COUNT:
        return True, f"live_9b_count<={TRIGGER_LIVE_9B_COUNT}"
    return False, (
        f"waiting: rebalance_9b_missing={summary['rebalance_9b_missing']} "
        f"live_9b_count={summary['live_9b_count']}"
    )


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_local_pid_group(pid: int, status: str) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
        base.update_process_row(pid, status)
        log(f"sent SIGTERM to pid group {pid}: {status}")
    except ProcessLookupError:
        base.update_process_row(pid, "dead")
        return
    deadline = time.time() + 35
    while time.time() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(1)
    try:
        os.killpg(pid, signal.SIGKILL)
        base.update_process_row(pid, f"{status}_sigkill")
        log(f"sent SIGKILL to pid group {pid}: {status}")
    except ProcessLookupError:
        base.update_process_row(pid, status)


def select_ports_to_reclaim(summary: dict) -> list[int]:
    candidates = []
    for port, info in summary["ports"].items():
        if port == 18122:
            continue
        if port < 18123 or port > 18127:
            continue
        candidates.append((int(info["missing"]), int(info["records"]), port))
    candidates.sort()
    selected = [port for _missing, _records, port in candidates[:TARGET_EXTRA_8B_PORTS]]
    for port in range(18123, 18128):
        if len(selected) >= TARGET_EXTRA_8B_PORTS:
            break
        if port not in selected:
            selected.append(port)
    return [18122, *selected]


def records_for_ports(status: dict, ports: list[int]) -> list[dict]:
    selected = []
    for item in status.get("records", []):
        port = local_port_from_base_url(str(item.get("base_url") or ""))
        if port in ports:
            selected.append(item)
    return selected


def run_remote(command: str, timeout: int = 180) -> str:
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
    idx = child.expect(["password:", "yes/no", pexpect.EOF, pexpect.TIMEOUT])
    chunks.append(child.before)
    if idx == 0:
        child.sendline("xsy945@j8ca0")
    elif idx == 1:
        child.sendline("yes")
    elif idx == 2:
        break
    else:
        raise TimeoutError("ssh command timed out")
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


def deploy_remote_8b_services(local_ports: list[int]) -> str:
    remote_specs = []
    for local_port in local_ports:
        remote_port = local_port - 10000
        gpu = remote_port - 8120
        remote_specs.append(f"{gpu}:{remote_port}")
    specs_text = " ".join(remote_specs)
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
  if pids=$(ps -eo pid,args | awk -v p="$port" '$0 ~ /vllm.entrypoints.openai.api_server/ && index($0, "--port " p) {{print $1}}'); [ -n "$pids" ]; then
    echo "port $port still busy: $pids"
    continue
  fi
  log=/data/xsy/project_gaia_skillrl/runs/_launch_logs/{PREFIX}_remote132_qwen3_8b_gpu${{gpu}}_p${{port}}.log
  CUDA_VISIBLE_DEVICES=$gpu nohup /data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python -u -m vllm.entrypoints.openai.api_server \\
    --model /data/xsy/codes/checkpoints/Qwen3-8B \\
    --served-model-name Qwen3-8B-local \\
    --host 127.0.0.1 \\
    --port $port \\
    --tensor-parallel-size 1 \\
    --gpu-memory-utilization 0.90 \\
    --dtype bfloat16 \\
    --trust-remote-code \\
    --enable-prefix-caching \\
    --reasoning-parser qwen3 \\
    --reasoning-config '{{"reasoning_start_str":"<think>","reasoning_end_str":"</think>"}}' \\
    --max-model-len 40960 \\
    --max-num-seqs 128 > "$log" 2>&1 < /dev/null &
  echo "started gpu=$gpu port=$port pid=$! log=$log"
done
"""
    return run_remote(f"bash -lc {shlex.quote(remote_script)}", timeout=180)


def endpoint_models(base_url: str) -> list[str]:
    try:
        req = Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urlopen(req, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    except Exception:
        return []


def wait_8b_endpoints(local_ports: list[int]) -> None:
    deadline = time.time() + 540
    while time.time() < deadline:
        missing = []
        for port in local_ports:
            url = f"http://127.0.0.1:{port}/v1"
            if "Qwen3-8B-local" not in endpoint_models(url):
                missing.append(port)
        if not missing:
            log("8B endpoints ready: " + ", ".join(str(port) for port in local_ports))
            return
        log("waiting 8B endpoints: " + ", ".join(str(port) for port in missing))
        time.sleep(15)
    raise RuntimeError("8B endpoints not ready: " + ", ".join(str(port) for port in local_ports))


def offline_b3_skills() -> dict[str, str]:
    cache = QUEUE_LOG_ROOT / f"{PREFIX}_offline_skills.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if all(Path(path).exists() for path in data.values()):
            log(f"reuse cached offline B3 skills: {data}")
            return {str(key): str(value) for key, value in data.items()}

    bootv3_skill = SOURCE_8B_BOOTV3_DEV / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    bootv4_skill = SOURCE_8B_BOOTV4_DEV / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    sources = [
        base.BootSource(
            "8b",
            "bootv3",
            "8B BOOT-V3 ARCH5.3",
            SOURCE_8B_BOOTV3_DEV,
            bootv3_skill,
            supplemental_runs=[SOURCE_8B_BOOTV3_SUPPLEMENT],
            source_notes="B3 add-on; bootv3 dev uses 09:22 missing-tail supplement.",
        ),
        base.BootSource(
            "8b",
            "bootv4",
            "8B BOOT-V4 ARCH5.3 preprocess",
            SOURCE_8B_BOOTV4_DEV,
            bootv4_skill,
            source_notes="B3 add-on.",
        ),
    ]
    results: dict[str, str] = {}
    log("starting 8B B3 offline skill generation for bootv3/bootv4")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="8b-b3-offline") as pool:
        futures: dict[Future[Path], str] = {
            pool.submit(base.run_offline_variant, source, method_key="B3", strategy="graph_v3", graph_policy="graph_v3"): source.boot_key
            for source in sources
        }
        for future, boot_key in list(futures.items()):
            skill = future.result()
            results[boot_key] = str(skill)
            log(f"offline B3 skill ready for {boot_key}: {skill}")
    cache.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def lane_for_port(local_port: int) -> base.Lane:
    remote_port = local_port - 10000
    for lane in base.LANES:
        if lane.model_key == "8b" and lane.remote_port == remote_port:
            return lane
    raise KeyError(local_port)


def launch_eval(run_name: str, dataset: Path, skill_path: str, lane: base.Lane) -> subprocess.Popen[str]:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{short_ts()}.log"
    cmd = [
        str(base.PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(base.CONFIG),
        "train-local",
        "--dataset-path",
        str(dataset),
        "--run-name",
        run_name,
    ]
    env = base.common_env(lane)
    env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = skill_path
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
        notes=f"8B B3 add-on after 9B tail cleanup; lane={lane.name}; endpoint={lane.base_url}; skill={skill_path}",
    )
    log(f"started {run_name} pid={proc.pid} lane={lane.name} endpoint={lane.base_url} log={log_path}")
    return proc


def launch_four_evals(skills: dict[str, str], local_ports: list[int]) -> tuple[list[dict], dict[int, subprocess.Popen[str]]]:
    lanes = [lane_for_port(port) for port in local_ports]
    specs = [
        ("bootv3", "dev", DEV_DATASET),
        ("bootv3", "test", TEST_DATASET),
        ("bootv4", "dev", DEV_DATASET),
        ("bootv4", "test", TEST_DATASET),
    ]
    launched = []
    procs: dict[int, subprocess.Popen[str]] = {}
    for idx, (boot_key, split, dataset) in enumerate(specs):
        lane = lanes[idx % len(lanes)]
        run_name = f"{PREFIX}_8b_{boot_key}_B3_{split}_eval_c20"
        proc = launch_eval(run_name, dataset, skills[boot_key], lane)
        procs[proc.pid] = proc
        launched.append(
            {
                "run_name": run_name,
                "pid": proc.pid,
                "boot_key": boot_key,
                "split": split,
                "dataset": str(dataset),
                "lane": base.asdict(lane),
                "skill_path": skills[boot_key],
            }
        )
    return launched, procs


def monitor_launched(launched: list[dict], procs: dict[int, subprocess.Popen[str]]) -> None:
    pid_to_name = {int(item["pid"]): str(item["run_name"]) for item in launched}
    while procs:
        remaining = {}
        for pid, proc in procs.items():
            name = pid_to_name.get(pid, str(pid))
            code = proc.poll()
            if code is None:
                remaining[pid] = name
                continue
            status = "finished" if code == 0 else "dead"
            base.update_process_row(pid, status, code)
            log(f"launched eval exited: pid={pid} name={name} code={code}")
        pid_to_name = remaining
        procs = {pid: proc for pid, proc in procs.items() if pid in remaining}
        write_status({"phase": "running_8b_b3", "running": pid_to_name, "launched": launched})
        if procs:
            time.sleep(POLL_SECONDS)


def main() -> int:
    ensure_inputs()
    base.ensure_process_csv_header()
    base.refresh_process_registry()
    base.append_process_row(
        kind="experiment_watcher",
        name=f"{PREFIX}_watcher",
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(wait for 9B tail, then launch 8B B3 add-on)",
        command=base.command_display(os.environ, [str(Path(sys.executable)), "-u", __file__]),
        log_path=QUEUE_LOG_ROOT / f"{PREFIX}.log",
        notes=(
            f"waits for 9B near-finish; trigger rebalance_9b_missing<={TRIGGER_REBALANCE_9B_MISSING} "
            f"or live_9b_count<={TRIGGER_LIVE_9B_COUNT}; then reclaims 18122 plus "
            f"{TARGET_EXTRA_8B_PORTS} least-missing 9B endpoints for 8B B3."
        ),
    )

    manifest: dict = {
        "prefix": PREFIX,
        "started_at": now(),
        "watch_status_jsonl": str(STATUS_JSONL),
        "source_watch_status_jsonl": str(WATCH_STATUS_JSONL),
        "trigger": {
            "rebalance_9b_missing": TRIGGER_REBALANCE_9B_MISSING,
            "live_9b_count": TRIGGER_LIVE_9B_COUNT,
            "force": FORCE_TRIGGER,
        },
        "offline_skills": {},
        "reclaimed_ports": [],
        "stopped_records": [],
        "launched": [],
    }
    write_manifest(manifest)

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="8b-b3-skill-prep") as pool:
        offline_future = pool.submit(offline_b3_skills)
        while True:
            status = load_latest_watch_status()
            summary = summarize_watch_status(status)
            ready, reason = trigger_ready(summary)
            write_status({"phase": "waiting_9b_tail", "ready": ready, "reason": reason, "summary": summary})
            log(reason)
            if ready:
                break
            time.sleep(POLL_SECONDS)
        skills = offline_future.result()

    status = load_latest_watch_status()
    summary = summarize_watch_status(status)
    local_ports = select_ports_to_reclaim(summary)
    stop_records = records_for_ports(status, local_ports)
    manifest["offline_skills"] = skills
    manifest["triggered_at"] = now()
    manifest["trigger_summary"] = summary
    manifest["reclaimed_ports"] = local_ports
    manifest["stopped_records"] = stop_records
    write_manifest(manifest)
    write_status({"phase": "triggered", "summary": summary, "local_ports": local_ports, "stopped_records": stop_records})

    for record in stop_records:
        pid = record.get("pid")
        if isinstance(pid, int):
            stop_local_pid_group(pid, f"stopped_for_{PREFIX}_8b_b3")

    remote_output = deploy_remote_8b_services(local_ports)
    manifest["remote_redeploy_output"] = remote_output
    write_manifest(manifest)
    log(remote_output.strip())
    wait_8b_endpoints(local_ports)

    launched, procs = launch_four_evals(skills, local_ports)
    manifest["launched"] = launched
    manifest["launched_at"] = now()
    write_manifest(manifest)
    write_status({"phase": "launched_8b_b3", "local_ports": local_ports, "launched": launched})

    monitor_launched(launched, procs)
    manifest["ended_at"] = now()
    manifest["status"] = "launched_evals_exited_or_dead"
    write_manifest(manifest)
    write_status({"phase": "done", "manifest": str(MANIFEST_JSON)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
