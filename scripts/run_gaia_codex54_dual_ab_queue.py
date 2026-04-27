from __future__ import annotations

import csv
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
QUEUE_SCRIPT = ROOT / "scripts/run_gaia_fresh_bootstrap_ab_tail_queue.py"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

VLLM_PYTHON = Path(os.environ.get("GAIA_CODEX54_VLLM_PYTHON", "/data/xsy/miniconda3/envs/vllm_env/bin/python"))
QWEN_MODEL = Path(os.environ.get("GAIA_CODEX54_QWEN_MODEL", "/data/xsy/codes/checkpoints/Qwen3-8B"))
CODEX_BIN = os.environ.get(
    "NLRL_CODEX_CLI_PATH",
    "/data/xsy/.vscode-server/extensions/openai.chatgpt-26.422.30944/bin/linux-x86_64/codex",
)

CONCURRENCY = int(os.environ.get("GAIA_CODEX54_CONCURRENCY", "20"))
TAIL_THRESHOLD = int(os.environ.get("GAIA_CODEX54_TAIL_THRESHOLD", "3"))
POLL_SECONDS = int(os.environ.get("GAIA_CODEX54_POLL_SECONDS", "60"))
STAMP = os.environ.get("GAIA_CODEX54_PREFIX", "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
KEEP_GPUS = {int(item) for item in os.environ.get("GAIA_CODEX54_KEEP_GPUS", "0,1").split(",") if item.strip()}
STOP_GPUS = {int(item) for item in os.environ.get("GAIA_CODEX54_STOP_GPUS", "2,3").split(",") if item.strip()}

RESUME_BOOT_DEV_RUN_NAME = os.environ.get(
    "GAIA_CODEX54_RESUME_BOOT_DEV_RUN_NAME",
    "20260426_205027_rollback_prompt_fresh_bootv3_boot_dev_c20",
)
RESUME_BOOT_SKILL_PATH = os.environ.get(
    "GAIA_CODEX54_RESUME_BOOT_SKILL_PATH",
    "/data/xsy/project_gaia_skillrl/runs/2026/4/2026-4-26/"
    "20260426_205027_rollback_prompt_fresh_bootv3_boot_dev_c20/"
    "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md",
)

LANES = {
    0: "http://127.0.0.1:8100/v1",
    1: "http://127.0.0.1:8101/v1",
    2: "http://127.0.0.1:8102/v1",
    3: "http://127.0.0.1:8103/v1",
}

started_model_pids: dict[int, int] = {}
current_queue: subprocess.Popen[str] | None = None


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        stat = stat_path.read_text(encoding="utf-8", errors="replace").split()
        return len(stat) > 2 and stat[2] != "Z"
    except FileNotFoundError:
        return False
    except Exception:
        return True


def refresh_process_registry() -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return
    changed = False
    for row in rows[1:]:
        if len(row) < 8:
            continue
        status = row[2].strip().lower()
        pid_text = row[5].strip()
        if status != "running" or not pid_text.isdigit():
            continue
        row[1] = now()
        if not pid_alive(int(pid_text)):
            row[2] = "dead"
            if not row[7].strip():
                row[7] = now()
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def append_process_row(
    *,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
    kind: str,
) -> None:
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    row = [
        now(),
        now(),
        "running",
        kind,
        name,
        str(pid),
        now(),
        "",
        str(cwd),
        run_dir,
        command,
        "",
        notes,
        str(log_path),
    ]
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(row)


def update_process_row(pid: int, status: str) -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[5].strip() != str(pid):
            continue
        row[1] = now()
        row[2] = status
        if status != "running" and not row[7].strip():
            row[7] = now()
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    keys = [
        "CUDA_VISIBLE_DEVICES",
        "GAIA_FRESH_QUEUE_PREFIX",
        "GAIA_FRESH_QUEUE_CONCURRENCY",
        "GAIA_FRESH_QUEUE_TAIL_THRESHOLD",
        "GAIA_FRESH_QUEUE_ALLOW_PARTIAL_TAIL",
        "GAIA_FRESH_QUEUE_TAIL_GRACE_POLLS",
        "GAIA_FRESH_QUEUE_RESUME_BOOT_DEV_RUN_NAME",
        "GAIA_FRESH_QUEUE_RESUME_BOOT_SKILL_PATH",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_BASE_URL",
        "NLRL_ACTOR_API_KEY",
        "NLRL_ACTOR_API_MODE",
        "NLRL_ACTOR_TIMEOUT_SECONDS",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_BASE_URL",
        "NLRL_CRITIC_API_KEY",
        "NLRL_CRITIC_API_MODE",
        "NLRL_CRITIC_TIMEOUT_SECONDS",
        "NLRL_CODEX_CLI_PATH",
        "NLRL_CODEX_CLI_WORKDIR",
        "NLRL_CODEX_CLI_TIMEOUT_SECONDS",
        "NLRL_CODEX_CLI_EXTRA_ARGS",
    ]
    env_parts = [f"{key}={shlex.quote(env[key])}" for key in keys if env.get(key)]
    cmd_parts = [shlex.quote(part) for part in cmd]
    return "env " + " ".join([*env_parts, *cmd_parts])


def vllm_cmd(gpu: int) -> list[str]:
    port = 8100 + gpu
    return [
        str(VLLM_PYTHON),
        "-u",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(QWEN_MODEL),
        "--served-model-name",
        "Qwen3-8B-local",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--tensor-parallel-size",
        "1",
        "--gpu-memory-utilization",
        "0.9",
        "--dtype",
        "bfloat16",
        "--trust-remote-code",
        "--enable-prefix-caching",
        "--reasoning-parser",
        "qwen3",
        "--max-model-len",
        "40960",
        "--max-num-seqs",
        "128",
    ]


def endpoint_ready(base_url: str) -> bool:
    try:
        with urlopen(base_url.rstrip("/") + "/models", timeout=5) as response:
            return 200 <= response.status < 500
    except (URLError, TimeoutError, OSError):
        return False


def find_vllm_pid(port: int) -> int | None:
    result = subprocess.run(
        ["ps", "-eo", "pid,cmd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    marker = f"--port {port}"
    for line in result.stdout.splitlines():
        if "vllm.entrypoints.openai.api_server" not in line:
            continue
        if marker not in line:
            continue
        parts = line.strip().split(maxsplit=1)
        if parts and parts[0].isdigit():
            return int(parts[0])
    return None


def start_vllm(gpu: int) -> None:
    base_url = LANES[gpu]
    port = 8100 + gpu
    existing_pid = find_vllm_pid(port)
    if endpoint_ready(base_url):
        log(f"reuse healthy vLLM gpu={gpu} port={port} pid={existing_pid or 'unknown'}")
        if existing_pid is not None:
            started_model_pids[gpu] = existing_pid
        return
    if existing_pid is not None and pid_alive(existing_pid):
        stop_pid(existing_pid, f"stopped_unhealthy_before_codex54_queue_gpu{gpu}")
        time.sleep(5)

    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"qwen3_8b_local_vllm_gpu{gpu}_p{port}_{STAMP}.log"
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    cmd = vllm_cmd(gpu)
    handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            cmd,
            cwd="/data/xsy",
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    finally:
        handle.close()
    started_model_pids[gpu] = process.pid
    append_process_row(
        name=f"qwen3_8b_local_vllm_gpu{gpu}_p{port}_{STAMP}",
        pid=process.pid,
        cwd=Path("/data/xsy"),
        run_dir=str(LAUNCH_LOG_ROOT),
        command=command_display(env, cmd),
        log_path=log_path,
        notes=f"Local Qwen3-8B vLLM service for Codex CLI 5.4 dual AB queue; GPU={gpu}; port={port}.",
        kind="model_server",
    )
    log(f"started vLLM gpu={gpu} pid={process.pid} port={port} log={log_path}")


def wait_vllm_ready() -> None:
    deadline = time.monotonic() + int(os.environ.get("GAIA_CODEX54_VLLM_READY_TIMEOUT", "900"))
    pending = set(LANES)
    while pending:
        for gpu in list(pending):
            if endpoint_ready(LANES[gpu]):
                pending.remove(gpu)
                log(f"vLLM ready gpu={gpu} endpoint={LANES[gpu]}")
        if not pending:
            return
        for gpu, pid in list(started_model_pids.items()):
            if gpu in pending and not pid_alive(pid):
                update_process_row(pid, "dead")
                raise RuntimeError(f"vLLM gpu={gpu} pid={pid} exited before readiness")
        if time.monotonic() > deadline:
            raise TimeoutError(f"vLLM readiness timeout, pending={sorted(pending)}")
        log(f"waiting for vLLM readiness: pending={sorted(pending)}")
        time.sleep(15)


def stop_pid(pid: int, status: str) -> None:
    if not pid_alive(pid):
        update_process_row(pid, "dead")
        return
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        update_process_row(pid, status)
        log(f"sent SIGTERM pid={pid} pgid={pgid} status={status}")
    except ProcessLookupError:
        update_process_row(pid, "dead")
        return
    except Exception as exc:
        log(f"failed to SIGTERM pid={pid}: {exc}")
        return
    time.sleep(8)
    if pid_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            update_process_row(pid, status + "_sigkill")
            log(f"sent SIGKILL pid={pid}")
        except ProcessLookupError:
            update_process_row(pid, status)
        except Exception as exc:
            log(f"failed to SIGKILL pid={pid}: {exc}")


def cleanup_stop_gpus() -> None:
    for gpu in sorted(STOP_GPUS):
        port = 8100 + gpu
        pid = started_model_pids.get(gpu) or find_vllm_pid(port)
        if pid is None:
            log(f"no vLLM pid found for cleanup gpu={gpu} port={port}")
            continue
        stop_pid(pid, f"stopped_after_codex54_dual_ab_queue_gpu{gpu}")
    for gpu in sorted(KEEP_GPUS):
        pid = started_model_pids.get(gpu) or find_vllm_pid(8100 + gpu)
        log(f"kept vLLM gpu={gpu} pid={pid or 'unknown'} endpoint={LANES[gpu]}")


def codex_role_env() -> dict[str, str]:
    return {
        "NLRL_ACTOR_MODEL": "gpt-5.4",
        "NLRL_ACTOR_BASE_URL": "codex-cli",
        "NLRL_ACTOR_API_KEY": "EMPTY",
        "NLRL_ACTOR_API_MODE": "codex_cli",
        "NLRL_ACTOR_TIMEOUT_SECONDS": os.environ.get("NLRL_ACTOR_TIMEOUT_SECONDS", "1800"),
        "NLRL_CRITIC_MODEL": "gpt-5.4",
        "NLRL_CRITIC_BASE_URL": "codex-cli",
        "NLRL_CRITIC_API_KEY": "EMPTY",
        "NLRL_CRITIC_API_MODE": "codex_cli",
        "NLRL_CRITIC_TIMEOUT_SECONDS": os.environ.get("NLRL_CRITIC_TIMEOUT_SECONDS", "1800"),
        "NLRL_CODEX_CLI_PATH": CODEX_BIN,
        "NLRL_CODEX_CLI_WORKDIR": os.environ.get("NLRL_CODEX_CLI_WORKDIR", "/tmp"),
        "NLRL_CODEX_CLI_TIMEOUT_SECONDS": os.environ.get("NLRL_CODEX_CLI_TIMEOUT_SECONDS", "1800"),
    }


def run_queue(prefix: str, *, resume: bool) -> None:
    global current_queue
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(codex_role_env())
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "GAIA_FRESH_QUEUE_PREFIX": prefix,
            "GAIA_FRESH_QUEUE_CONCURRENCY": str(CONCURRENCY),
            "GAIA_FRESH_QUEUE_TAIL_THRESHOLD": str(TAIL_THRESHOLD),
            "GAIA_FRESH_QUEUE_ALLOW_PARTIAL_TAIL": "1",
            "GAIA_FRESH_QUEUE_TAIL_GRACE_POLLS": "3",
            "GAIA_FRESH_QUEUE_POLL_SECONDS": str(POLL_SECONDS),
            "GAIA_QUEUE_CLEANUP_SERVER_PIDS": "",
        }
    )
    if resume:
        env["GAIA_FRESH_QUEUE_RESUME_BOOT_DEV_RUN_NAME"] = RESUME_BOOT_DEV_RUN_NAME
        env["GAIA_FRESH_QUEUE_RESUME_BOOT_SKILL_PATH"] = RESUME_BOOT_SKILL_PATH
    else:
        env.pop("GAIA_FRESH_QUEUE_RESUME_BOOT_DEV_RUN_NAME", None)
        env.pop("GAIA_FRESH_QUEUE_RESUME_BOOT_SKILL_PATH", None)

    log_path = LAUNCH_LOG_ROOT / f"{prefix}_fresh_bootstrap_ab_tail_queue_c{CONCURRENCY}.log"
    env["NLRL_QUEUE_LOG_PATH"] = str(log_path)
    cmd = [str(PYTHON), str(QUEUE_SCRIPT)]
    handle = log_path.open("w", encoding="utf-8")
    try:
        current_queue = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    finally:
        handle.close()
    log(f"started child queue prefix={prefix} pid={current_queue.pid} resume={resume} log={log_path}")
    code = current_queue.wait()
    update_process_row(current_queue.pid, "finished" if code == 0 else "dead")
    current_queue = None
    if code != 0:
        raise RuntimeError(f"child queue {prefix} failed with returncode={code}; log={log_path}")
    log(f"finished child queue prefix={prefix}")


def stop_current_queue() -> None:
    if current_queue is None or current_queue.poll() is not None:
        return
    stop_pid(current_queue.pid, "stopped_by_codex54_dual_ab_queue")


def preflight() -> None:
    required = [PYTHON, QUEUE_SCRIPT, VLLM_PYTHON, QWEN_MODEL, Path(RESUME_BOOT_SKILL_PATH), Path(CODEX_BIN)]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required paths: {missing}")


def main() -> int:
    refresh_process_registry()
    preflight()
    append_process_row(
        name=f"{STAMP}_codex54_dual_ab_outer_queue",
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(outer queue manager)",
        command=command_display(os.environ, [str(PYTHON), *sys.argv]),
        log_path=Path(os.environ.get("NLRL_OUTER_QUEUE_LOG_PATH", "")) if os.environ.get("NLRL_OUTER_QUEUE_LOG_PATH") else LAUNCH_LOG_ROOT / f"{STAMP}_codex54_dual_ab_outer_queue.log",
        notes=(
            "Run two Codex CLI GPT-5.4 ROLE candidate queues: "
            "AB on existing GPT-5.2 bootstrap skill, then fresh Codex CLI bootstrap plus AB. "
            "Keep GPU0/GPU1 vLLM after completion and stop GPU2/GPU3."
        ),
        kind="experiment_queue",
    )
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        for gpu in sorted(LANES):
            start_vllm(gpu)
        wait_vllm_ready()
        run_queue(f"{STAMP}_codex54_ab_on_gpt52_boot", resume=True)
        run_queue(f"{STAMP}_codex54_fresh_bootstrap_ab", resume=False)
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        stop_current_queue()
        return 130
    except Exception:
        status = "dead"
        stop_current_queue()
        raise
    finally:
        cleanup_stop_gpus()
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())

