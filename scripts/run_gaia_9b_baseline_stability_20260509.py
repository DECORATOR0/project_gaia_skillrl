from __future__ import annotations

import csv
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

RUN_PREFIX = os.environ.get("GAIA_9B_BASELINE_STABILITY_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_9b_baseline_stability"
)
MASTER_NAME = f"{RUN_PREFIX}_master"
SUMMARY_PATH = QUEUE_LOG_ROOT / f"{MASTER_NAME}_summary.json"
CONCURRENCY = int(os.environ.get("GAIA_9B_BASELINE_STABILITY_CONCURRENCY", "20"))
REPEATS = int(os.environ.get("GAIA_9B_BASELINE_STABILITY_REPEATS", "2"))


@dataclass(frozen=True)
class Lane:
    name: str
    gpu: int
    port: int
    model_path: str = "/data/xsy/codes/checkpoints/Qwen3.5-9B"
    served_name: str = "Qwen3.5-9B-local"
    tokenizer_path: str = "/data/xsy/codes/checkpoints/Qwen3.5-9B"
    max_model_len: int = 49152

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


DEV_LANE = Lane("254_gpu0_9b", 0, 8610)
TEST_LANE = Lane("254_gpu1_9b", 1, 8611)
LANES = [DEV_LANE, TEST_LANE]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def ensure_process_csv_header() -> None:
    if PROCESS_CSV.exists() and PROCESS_CSV.stat().st_size > 0:
        return
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
                "recorded_at",
                "last_checked_at",
                "status",
                "kind",
                "name",
                "pid",
                "start_time",
                "ended_at",
                "cwd",
                "run_dir",
                "command",
                "exit_code",
                "notes",
                "log_path",
            ]
        )


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def refresh_process_registry() -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[2].strip() != "running":
            continue
        pid_text = row[5].strip()
        if not pid_text.isdigit():
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
    kind: str,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
) -> None:
    ensure_process_csv_header()
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
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
        )


def update_process_row(pid: int, status: str, *, exit_code: int | None = None, run_dir: str = "") -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[5].strip() != str(pid):
            continue
        while len(row) < 14:
            row.append("")
        row[1] = now()
        row[2] = status
        if status != "running" and not row[7].strip():
            row[7] = now()
        if run_dir:
            row[9] = run_dir
        if exit_code is not None:
            row[11] = str(exit_code)
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def load_search_runtime_env() -> dict[str, str]:
    try:
        data = json.loads(SEARCH_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    env: dict[str, str] = {}
    if data.get("http_proxy"):
        env["HTTP_PROXY"] = str(data["http_proxy"])
    if data.get("https_proxy"):
        env["HTTPS_PROXY"] = str(data["https_proxy"])
    no_proxy = str(data.get("no_proxy", "127.0.0.1,localhost,0.0.0.0"))
    parts = [part.strip() for part in no_proxy.split(",") if part.strip()]
    for item in ["127.0.0.1", "localhost", "0.0.0.0"]:
        if item not in parts:
            parts.append(item)
    env["NO_PROXY"] = ",".join(parts)
    return env


def endpoint_models(base_url: str) -> list[str]:
    try:
        request = Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    except Exception:
        return []


def wait_endpoints() -> None:
    deadline = time.time() + int(os.environ.get("GAIA_9B_BASELINE_STABILITY_ENDPOINT_WAIT_SECONDS", "120"))
    while True:
        missing = [lane for lane in LANES if lane.served_name not in endpoint_models(lane.base_url)]
        if not missing:
            log("selected 9B endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        if time.time() >= deadline:
            raise RuntimeError("9B endpoints not ready: " + ", ".join(lane.base_url for lane in missing))
        log("waiting 9B endpoints: " + ", ".join(f"{lane.name}:{lane.base_url}" for lane in missing))
        time.sleep(10)


def common_env(lane: Lane) -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "ALL_PROXY",
        "all_proxy",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_THINKING_BUDGET",
        "NLRL_LLM_THINKING_TOKEN_BUDGET",
        "NLRL_LLM_THINKING_BUDGET",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
    ]:
        env.pop(key, None)
    env.update(load_search_runtime_env())
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": "",
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "BLIS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "NUMBA_NUM_THREADS": "1",
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_RUNTIME_CONFIG),
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": "24",
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_EXECUTOR_MODEL": lane.served_name,
            "NLRL_EXECUTOR_BASE_URL": lane.base_url,
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_EXECUTOR_MAX_TOKENS": "12288",
            "NLRL_EXECUTOR_TOKENIZER_PATH": lane.tokenizer_path,
            "NLRL_EXECUTOR_MAX_MODEL_LEN": str(lane.max_model_len),
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": "512",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_BASE_URL": os.environ.get("NLRL_TOOL_BASE_URL", "http://35.220.164.252:3888/v1"),
            "NLRL_TOOL_API_KEY": os.environ.get(
                "NLRL_TOOL_API_KEY",
                "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            ),
            "NLRL_TOOL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_MODEL": os.environ.get("NLRL_TOOL_MODEL", "gpt-4o-mini"),
            "NLRL_TOOL_AUDIO_MODEL": os.environ.get("NLRL_TOOL_AUDIO_MODEL", "gpt-4o-mini-transcribe"),
        }
    )
    return env


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "GAIA_9B_BASELINE_STABILITY_PREFIX",
        "GAIA_9B_BASELINE_STABILITY_CONCURRENCY",
        "GAIA_9B_BASELINE_STABILITY_REPEATS",
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "CUDA_VISIBLE_DEVICES",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_API_MODE",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_MODEL",
    ]
    return {key: env[key] for key in keys if env.get(key) is not None}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def start_eval(label: str, dataset_path: Path, lane: Lane, repeat_index: int) -> subprocess.Popen[str]:
    run_name = f"{RUN_PREFIX}_r{repeat_index}_baseline_{label}_{lane.name}_c{CONCURRENCY}"
    log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "direct-eval-local",
        "--dataset-path",
        str(dataset_path),
        "--run-name",
        run_name,
    ]
    env = common_env(lane)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
        kind="experiment",
        name=run_name,
        pid=proc.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes=(
            f"9B direct baseline stability repeat {repeat_index} {label}; "
            f"no bootstrap; no initial skill; lane={lane.name}; endpoint={lane.base_url}; c{CONCURRENCY}."
        ),
    )
    log(f"started r{repeat_index} baseline {label} pid={proc.pid} lane={lane.name} log={log_path}")
    return proc


def find_run_dir(run_name: str) -> str:
    matches = list(RUN_ROOT.glob(f"2026/5/*/{run_name}"))
    if matches:
        return str(matches[-1])
    matches = list(RUN_ROOT.glob(f"2026/5/*/*_{run_name}"))
    if matches:
        return str(matches[-1])
    matches = list(RUN_ROOT.glob(f"**/{run_name}"))
    if matches:
        return str(matches[-1])
    matches = list(RUN_ROOT.glob(f"**/*_{run_name}"))
    return str(matches[-1]) if matches else ""


def dataset_total(dataset_path: Path) -> int:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    return len(payload.get("tasks", []))


def run_stats(run_dir: str, total: int) -> dict[str, object]:
    if not run_dir:
        return {"score": f"0/{total}", "success": 0, "landed": 0, "total": total, "missing": total}
    state_paths = sorted(Path(run_dir).glob("iteration_01/*/state.json"))
    success = 0
    forced = 0
    max_step_like = 0
    for path in state_paths:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        env_result = state.get("env_result") or {}
        evaluation = env_result.get("evaluation") or {}
        if evaluation.get("task_success"):
            success += 1
        action_trace = env_result.get("action_trace") or []
        if any((action.get("outcome") or "") == "forced_final_answer_after_max_steps" for action in action_trace):
            forced += 1
        if len(action_trace) >= 24 or any("max_steps" in str(action.get("outcome") or "") for action in action_trace):
            max_step_like += 1
    landed = len(state_paths)
    return {
        "score": f"{success}/{total}",
        "success": success,
        "landed": landed,
        "total": total,
        "missing": max(total - landed, 0),
        "forced": forced,
        "max_step_like": max_step_like,
    }


def write_summary(status: str, *, waves: list[dict[str, object]], failures: dict[str, int] | None = None) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": MASTER_NAME,
        "run_prefix": RUN_PREFIX,
        "updated_at": now(),
        "status": status,
        "summary_json": str(SUMMARY_PATH),
        "lanes": [asdict(lane) for lane in LANES],
        "config": {
            "runner": "direct-eval-local",
            "bootstrap": "disabled",
            "initial_skill": None,
            "model": "Qwen3.5-9B-local",
            "tokenizer_path": "/data/xsy/codes/checkpoints/Qwen3.5-9B",
            "max_model_len": 49152,
            "max_tokens": 12288,
            "token_guard_safety_margin": 512,
            "max_context_chars": 0,
            "thinking_token_budget": None,
            "max_executor_steps": 24,
            "task_concurrency": CONCURRENCY,
            "tool_profile": "atomic_v2",
            "answer_acceptance_policy": "any_phase",
        },
        "waves": waves,
        "failures": failures or {},
    }
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    master_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"))
    refresh_process_registry()
    append_process_row(
        kind="experiment_queue",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(9B baseline stability master)",
        command=command_display(os.environ, [str(PYTHON), *sys.argv]),
        log_path=master_log,
        notes=f"9B baseline stability rerun; repeats={REPEATS}; summary={SUMMARY_PATH}",
    )
    waves: list[dict[str, object]] = []
    failures: dict[str, int] = {}
    status = "finished"
    write_summary("starting", waves=waves)
    try:
        wait_endpoints()
        dev_total = dataset_total(DEV_DATASET)
        test_total = dataset_total(TEST_DATASET)
        for repeat_index in range(1, REPEATS + 1):
            log(f"starting wave r{repeat_index}: dev on {DEV_LANE.base_url}, test on {TEST_LANE.base_url}")
            procs = {
                "dev": start_eval("dev", DEV_DATASET, DEV_LANE, repeat_index),
                "test": start_eval("test", TEST_DATASET, TEST_LANE, repeat_index),
            }
            wave: dict[str, object] = {"repeat": repeat_index, "started_at": now(), "runs": {}}
            for label, proc in procs.items():
                code = proc.wait()
                run_name = f"{RUN_PREFIX}_r{repeat_index}_baseline_{label}_{DEV_LANE.name if label == 'dev' else TEST_LANE.name}_c{CONCURRENCY}"
                run_dir = find_run_dir(run_name)
                update_process_row(proc.pid, "finished" if code == 0 else "dead", exit_code=code, run_dir=run_dir)
                if code != 0:
                    failures[run_name] = code
                total = dev_total if label == "dev" else test_total
                wave["runs"][label] = {
                    "run_name": run_name,
                    "returncode": code,
                    "run_dir": run_dir,
                    "stats": run_stats(run_dir, total),
                }
                log(f"finished r{repeat_index} {label}: rc={code} run_dir={run_dir}")
            wave["ended_at"] = now()
            waves.append(wave)
            write_summary("running" if repeat_index < REPEATS else "finished", waves=waves, failures=failures)
        if failures:
            status = "finished_with_eval_failures"
            write_summary(status, waves=waves, failures=failures)
            return 1
        write_summary("finished", waves=waves)
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        write_summary(status, waves=waves, failures=failures)
        raise
    except Exception as exc:
        status = "dead"
        failures["master"] = -1
        write_summary(status, waves=waves, failures={**failures, "error": str(exc)})
        raise
    finally:
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
