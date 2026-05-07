from __future__ import annotations

import csv
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


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
REPORT_PATH = ROOT / "实验设计与迭代/26.5.07_0123_GAIA_8B9B_ARCH53_BOOTV3V4_24实验启动与结果.md"

RUN_PREFIX = os.environ.get("GAIA_GPT52_SSSAI_BASELINE_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_gpt52_sssai_baseline"
)
MASTER_NAME = f"{RUN_PREFIX}_master"
SUMMARY_PATH = QUEUE_LOG_ROOT / f"{MASTER_NAME}_summary.json"
CONCURRENCY = int(os.environ.get("GAIA_GPT52_SSSAI_BASELINE_CONCURRENCY", "10"))

SSSAI_MODEL = os.environ.get("GAIA_GPT52_SSSAI_MODEL", "gpt-5.2")
SSSAI_BASE_URL = os.environ.get("GAIA_GPT52_SSSAI_BASE_URL", "https://node-hk.sssaicode.com/api/v1/responses")
SSSAI_API_KEY = os.environ.get(
    "GAIA_GPT52_SSSAI_API_KEY",
    "sk-sssaicode-9d340d3064511a95fb7f428f2356dfc7a08c724271a66aa1bdc72a3b2af85cb4",
)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


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


def update_process_row(pid: int, status: str, *, exit_code: int | None = None, notes_suffix: str = "") -> None:
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
        if exit_code is not None:
            row[11] = str(exit_code)
        if notes_suffix:
            row[12] = (row[12] + "; " if row[12].strip() else "") + notes_suffix
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


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


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def load_search_runtime() -> dict[str, Any]:
    try:
        return json.loads(SEARCH_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_search_runtime_env() -> dict[str, str]:
    data = load_search_runtime()
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
    env["NLRL_WEB_SEARCH_PROVIDER"] = str(data.get("provider") or "serper")
    env["NLRL_WEB_SEARCH_FALLBACK_PROVIDER"] = str(data.get("fallback_provider") or "ddg")
    if data.get("serper_api_key"):
        env["NLRL_SERPER_API_KEY"] = str(data["serper_api_key"])
    keys = data.get("serper_api_keys")
    if isinstance(keys, list) and keys:
        env["NLRL_SERPER_API_KEYS"] = ",".join(str(key) for key in keys if str(key).strip())
    if data.get("serper_search_endpoint"):
        env["NLRL_SERPER_SEARCH_ENDPOINT"] = str(data["serper_search_endpoint"])
    if data.get("serper_search_timeout_seconds"):
        env["NLRL_SERPER_SEARCH_TIMEOUT_SECONDS"] = str(data["serper_search_timeout_seconds"])
    return env


def common_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "ALL_PROXY",
        "all_proxy",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_THINKING_BUDGET",
        "NLRL_LLM_THINKING_TOKEN_BUDGET",
        "NLRL_LLM_THINKING_BUDGET",
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
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_CRITIC_SHARD_CONCURRENCY": "1",
            "NLRL_EXECUTOR_MODEL": SSSAI_MODEL,
            "NLRL_EXECUTOR_BASE_URL": SSSAI_BASE_URL,
            "NLRL_EXECUTOR_API_KEY": SSSAI_API_KEY,
            "NLRL_EXECUTOR_API_MODE": "responses_sse",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_REASONING_EFFORT": "xhigh",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_MAX_TOKENS": "12288",
            "NLRL_TOOL_BASE_URL": "http://35.220.164.252:3888/v1",
            "NLRL_TOOL_MODEL": "gpt-4o-mini",
        }
    )
    return env


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "CUDA_VISIBLE_DEVICES",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "NLRL_WEB_SEARCH_PROVIDER",
        "NLRL_SERPER_API_KEY",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_API_KEY",
        "NLRL_EXECUTOR_API_MODE",
        "NLRL_EXECUTOR_STREAM",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_EXECUTOR_REASONING_EFFORT",
        "NLRL_EXECUTOR_TEMPERATURE",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_MODEL",
    ]
    return {key: env[key] for key in keys if env.get(key) is not None}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def run_name_for(label: str) -> str:
    total = 83 if label == "dev" else 82
    return f"{RUN_PREFIX}_gpt52_sssai_baseline_{label}{total}_c{CONCURRENCY}"


def find_run_dir(run_name: str) -> Path | None:
    matches = sorted(RUN_ROOT.glob(f"2026/*/*/{run_name}"))
    if matches:
        return matches[-1]
    minute_match = re.match(r"^(20\d{6})_([0-2]\d[0-5]\d)_(.+)$", run_name)
    if minute_match:
        date, hour_minute, rest = minute_match.groups()
        pattern = f"{date}_{hour_minute}??_{rest}"
        matches = sorted(RUN_ROOT.glob(f"2026/*/*/{pattern}"))
        if matches:
            return matches[-1]
    matches = sorted(RUN_ROOT.rglob(run_name))
    return matches[-1] if matches else None


def start_eval(label: str, dataset_path: Path) -> subprocess.Popen[str]:
    run_name = run_name_for(label)
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
    env = common_env()
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
            f"SSSAI gpt-5.2 direct baseline {label}; no bootstrap; no initial skill; "
            f"api_mode=responses_sse; c{CONCURRENCY}; provider-native context; Serper."
        ),
    )
    log(f"started {label} pid={proc.pid} run_name={run_name} log={log_path}")
    return proc


def stats_for_run(run_name: str, total_hint: int) -> dict[str, Any]:
    run_dir = find_run_dir(run_name)
    stats: dict[str, Any] = {
        "run_name": run_name,
        "run_dir": str(run_dir) if run_dir else "",
        "total": total_hint,
        "landed": 0,
        "missing": total_hint,
        "success": 0,
        "avg_steps": 0.0,
        "web_calls": 0,
        "summary_exists": False,
    }
    if not run_dir:
        return stats
    summary_path = run_dir / "run_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            tasks = summary.get("tasks") or []
            landed = len(tasks)
            success = int(summary.get("success_count") or 0)
            step_counts: list[int] = []
            web_calls = 0
            for task in tasks:
                env_result = task.get("env_result") or {}
                action_trace = env_result.get("action_trace") or []
                step_counts.append(len(action_trace))
                for item in action_trace:
                    tool_name = str(item.get("tool_name") or item.get("tool") or "")
                    if "web" in tool_name.lower() or "search" in tool_name.lower():
                        web_calls += 1
            stats.update(
                {
                    "total": int(summary.get("task_count") or total_hint),
                    "landed": landed,
                    "missing": max(0, int(summary.get("task_count") or total_hint) - landed),
                    "success": success,
                    "avg_steps": (sum(step_counts) / len(step_counts)) if step_counts else 0.0,
                    "web_calls": web_calls,
                    "summary_exists": True,
                }
            )
            return stats
        except Exception as exc:
            stats["summary_error"] = str(exc)
    state_paths = sorted(run_dir.glob("iteration_01/*/state.json"))
    stats["landed"] = len(state_paths)
    stats["missing"] = max(0, total_hint - len(state_paths))
    success = 0
    step_counts = []
    web_calls = 0
    for state_path in state_paths:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        env_result = state.get("env_result") or {}
        evaluation = env_result.get("evaluation") or {}
        success += int(bool(evaluation.get("task_success")))
        action_trace = env_result.get("action_trace") or state.get("action_trace") or []
        step_counts.append(len(action_trace))
        for item in action_trace:
            tool_name = str(item.get("tool_name") or item.get("tool") or "")
            if "web" in tool_name.lower() or "search" in tool_name.lower():
                web_calls += 1
    stats["success"] = success
    stats["avg_steps"] = (sum(step_counts) / len(step_counts)) if step_counts else 0.0
    stats["web_calls"] = web_calls
    return stats


def collect_stats() -> dict[str, dict[str, Any]]:
    return {
        "dev": stats_for_run(run_name_for("dev"), 83),
        "test": stats_for_run(run_name_for("test"), 82),
    }


def write_summary(status: str, **extra: Any) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": MASTER_NAME,
        "run_prefix": RUN_PREFIX,
        "updated_at": now(),
        "status": status,
        "config": {
            "runner": "direct-eval-local",
            "bootstrap": "disabled",
            "initial_skill": None,
            "executor_model": SSSAI_MODEL,
            "executor_base_url": SSSAI_BASE_URL,
            "executor_api_mode": "responses_sse",
            "executor_stream": True,
            "executor_reasoning_effort": "xhigh",
            "executor_max_output_tokens": 12288,
            "executor_temperature": 0.1,
            "executor_timeout_seconds": 1200,
            "context_policy": "provider-native responses_sse; no local tokenizer token guard",
            "runtime_max_context_chars": 0,
            "max_executor_steps": 24,
            "task_concurrency_per_split": CONCURRENCY,
            "answer_acceptance_policy": "any_phase",
            "tool_profile": "atomic_v2",
            "search_provider": "serper",
            "search_runtime_config": str(SEARCH_RUNTIME_CONFIG),
        },
        **extra,
    }
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_final_report(status: str, stats: dict[str, dict[str, Any]], eval_pids: dict[str, int]) -> None:
    lines = [
        "",
        f"### 2026-05-07 SSSAI GPT-5.2 baseline c{CONCURRENCY} 结束记录",
        "",
        f"- prefix：`{RUN_PREFIX}`",
        f"- master：`{MASTER_NAME}`，status `{status}`，summary `{SUMMARY_PATH}`",
        f"- eval PIDs：dev `{eval_pids.get('dev')}`，test `{eval_pids.get('test')}`",
        "- executor：`gpt-5.2` via SSSAI Responses SSE，`stream=True`，`reasoning_effort=xhigh`，`max_output_tokens=12288`。",
        "- context：provider-native；本次未使用 8B/9B tokenizer token guard。",
        "",
        "| label | score | landed | missing | avg_steps | web_calls | run_dir |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for label in ("dev", "test"):
        item = stats[label]
        total = item.get("total") or (83 if label == "dev" else 82)
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    f"{item.get('success', 0)}/{total}",
                    str(item.get("landed", 0)),
                    str(item.get("missing", 0)),
                    f"{float(item.get('avg_steps') or 0):.4f}",
                    str(item.get("web_calls", 0)),
                    f"`{item.get('run_dir', '')}`",
                ]
            )
            + " |"
        )
    REPORT_PATH.write_text(REPORT_PATH.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8")


def wait_processes(processes: dict[str, subprocess.Popen[str]]) -> tuple[str, dict[str, int]]:
    failures: dict[str, int] = {}
    for label, proc in processes.items():
        code = proc.wait()
        update_process_row(proc.pid, "finished" if code == 0 else "dead", exit_code=code)
        if code != 0:
            failures[label] = code
    if failures:
        return "dead", failures
    return "finished", {}


def main() -> int:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    master_log = QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"
    append_process_row(
        kind="experiment_queue",
        name=MASTER_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(SSSAI gpt-5.2 direct baseline dev/test master)",
        command=command_display(os.environ, [str(PYTHON), *sys.argv]),
        log_path=master_log,
        notes=f"SSSAI gpt-5.2 baseline master; dev/test concurrent; c{CONCURRENCY}; summary={SUMMARY_PATH}.",
    )
    status = "finished"
    eval_pids: dict[str, int] = {}
    write_summary("starting")
    try:
        refresh_process_registry()
        processes = {
            "dev": start_eval("dev", DEV_DATASET),
            "test": start_eval("test", TEST_DATASET),
        }
        eval_pids = {label: proc.pid for label, proc in processes.items()}
        write_summary("evals_running", eval_pids=eval_pids, stats=collect_stats())
        status, failures = wait_processes(processes)
        stats = collect_stats()
        write_summary(status, eval_pids=eval_pids, failures=failures, stats=stats)
        append_final_report(status, stats, eval_pids)
        if failures:
            raise RuntimeError(f"baseline failures: {failures}")
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        stats = collect_stats()
        write_summary(status, eval_pids=eval_pids, stats=stats)
        append_final_report(status, stats, eval_pids)
        raise
    except Exception as exc:
        status = "dead"
        stats = collect_stats()
        write_summary(status, eval_pids=eval_pids, error=str(exc), stats=stats)
        append_final_report(status, stats, eval_pids)
        raise
    finally:
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
