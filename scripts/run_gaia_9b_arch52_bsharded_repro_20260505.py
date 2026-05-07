#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import shlex
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
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

OLD_B_SHARDED_SKILL = Path(
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-4/"
    "20260504_161115_9b_p04_token_guard_bootab_fresh_bootv3_B_sharded_offline_actor_iter1/"
    "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)
SOURCE_INDEX_DOC = ROOT / "实验设计与迭代/26.5.05_1855_GAIA_迭代分数整合索引.md"
SOURCE_RUN_DOC = ROOT / "实验设计与迭代/26.5.04_1611_GAIA_9B_p04_token_guard强制answer重跑记录.md"

RUN_PREFIX = os.environ.get("GAIA_ARCH52_B_REPRO_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_9b_arch52_bsharded_repro"
)
CONCURRENCY = int(os.environ.get("GAIA_ARCH52_B_REPRO_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_ARCH52_B_REPRO_POLL_SECONDS", "60"))
TAIL_GRACE_POLLS = int(os.environ.get("GAIA_ARCH52_B_REPRO_TAIL_GRACE_POLLS", "3"))
TAIL_POLICY = os.environ.get("GAIA_ARCH52_B_REPRO_TAIL_POLICY", "old_partial").strip().lower()
MASTER_NAME = f"{RUN_PREFIX}_master"
SUMMARY_PATH = QUEUE_LOG_ROOT / f"{MASTER_NAME}_summary.json"


@dataclass(frozen=True)
class EvalSpec:
    label: str
    dataset_path: Path
    total_tasks: int
    gpu: int
    port: int
    old_partial_stop_completed: int

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def run_name(self) -> str:
        return f"{RUN_PREFIX}_{self.label}_eval_c{CONCURRENCY}"

    @property
    def log_path(self) -> Path:
        return LAUNCH_LOG_ROOT / f"{self.run_name}.log"

    @property
    def run_dir(self) -> Path:
        year = self.run_name[:4]
        month = str(int(self.run_name[4:6]))
        day = str(int(self.run_name[6:8]))
        return RUN_ROOT / year / month / f"{year}-{month}-{day}" / self.run_name


DEFAULT_SPECS = [
    EvalSpec("B_dev", DEV_DATASET, 83, gpu=2, port=8112, old_partial_stop_completed=79),
    EvalSpec("B_test", TEST_DATASET, 82, gpu=3, port=8103, old_partial_stop_completed=80),
]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def build_specs() -> list[EvalSpec]:
    only = os.environ.get("GAIA_ARCH52_B_REPRO_ONLY", "").strip()
    if only in {"B_dev", "dev"}:
        return [
            EvalSpec(
                "B_dev",
                DEV_DATASET,
                83,
                gpu=_env_int("GAIA_ARCH52_B_REPRO_DEV_GPU", 2),
                port=_env_int("GAIA_ARCH52_B_REPRO_DEV_PORT", 8112),
                old_partial_stop_completed=_env_int("GAIA_ARCH52_B_REPRO_DEV_OLD_PARTIAL", 79),
            )
        ]
    if only in {"B_test", "test"}:
        return [
            EvalSpec(
                "B_test",
                TEST_DATASET,
                82,
                gpu=_env_int("GAIA_ARCH52_B_REPRO_TEST_GPU", 3),
                port=_env_int("GAIA_ARCH52_B_REPRO_TEST_PORT", 8103),
                old_partial_stop_completed=_env_int("GAIA_ARCH52_B_REPRO_TEST_OLD_PARTIAL", 80),
            )
        ]
    return DEFAULT_SPECS


SPECS = build_specs()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def refresh_process_csv() -> None:
    if not PROCESS_CSV.exists():
        return
    rows = list(csv.reader(PROCESS_CSV.open("r", encoding="utf-8-sig", newline="")))
    changed = False
    for row in rows:
        if len(row) < 8 or row[2] != "running" or not row[5].strip().isdigit():
            continue
        if pid_alive(int(row[5])):
            row[1] = now()
            changed = True
            continue
        row[1] = now()
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
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(row)


def update_process_row(pid: int, status: str) -> None:
    if not PROCESS_CSV.exists():
        return
    rows = list(csv.reader(PROCESS_CSV.open("r", encoding="utf-8-sig", newline="")))
    changed = False
    for row in rows:
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


def endpoint_models(base_url: str) -> list[str]:
    try:
        request = Request(base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer EMPTY"})
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
    except Exception:
        return []


def common_env(base_url: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "ALL_PROXY",
        "all_proxy",
        "NLRL_EXECUTOR_THINKING_TOKEN_BUDGET",
        "NLRL_EXECUTOR_THINKING_BUDGET",
        "NLRL_LLM_THINKING_TOKEN_BUDGET",
        "NLRL_LLM_THINKING_BUDGET",
    ]:
        env.pop(key, None)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "GAIA_SEARCH_RUNTIME_CONFIG": str(SEARCH_RUNTIME_CONFIG),
            "HTTP_PROXY": "http://127.0.0.1:17890",
            "HTTPS_PROXY": "http://127.0.0.1:17890",
            "NO_PROXY": "127.0.0.1,localhost,0.0.0.0",
            "NLRL_RUNTIME_MAX_CONTEXT_CHARS": "0",
            "NLRL_RUNTIME_MAX_EXECUTOR_STEPS": "24",
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_INITIAL_SKILL_PATH": str(OLD_B_SHARDED_SKILL),
            "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL": "0",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "0",
            "NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS": "0",
            "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
            "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
            "NLRL_RUNTIME_CRITIC_STRATEGY": "full",
            "NLRL_RUNTIME_CRITIC_SHARD_SIZE": "12",
            "NLRL_RUNTIME_ACTOR_GRAPH_EDIT_POLICY": "locked",
            "NLRL_EXECUTOR_MODEL": "Qwen3.5-9B-local",
            "NLRL_EXECUTOR_BASE_URL": base_url,
            "NLRL_EXECUTOR_API_KEY": "EMPTY",
            "NLRL_EXECUTOR_API_MODE": "chat_completions",
            "NLRL_EXECUTOR_STREAM": "1",
            "NLRL_EXECUTOR_ENABLE_THINKING": "1",
            "NLRL_EXECUTOR_TEMPERATURE": "0.1",
            "NLRL_EXECUTOR_TIMEOUT_SECONDS": "1200",
            "NLRL_EXECUTOR_STREAM_INCLUDE_USAGE": "1",
            "NLRL_EXECUTOR_MAX_TOKENS": "12288",
            "NLRL_EXECUTOR_TOKENIZER_PATH": "/data/xsy/codes/checkpoints/Qwen3.5-9B",
            "NLRL_EXECUTOR_MAX_MODEL_LEN": "49152",
            "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN": "512",
            "NLRL_LLM_STREAM_WALL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_BASE_URL": "http://35.220.164.252:3888/v1",
            "NLRL_TOOL_API_KEY": "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
            "NLRL_TOOL_TIMEOUT_SECONDS": "1200",
            "NLRL_TOOL_MODEL": "gpt-4o-mini",
            "NLRL_TOOL_AUDIO_MODEL": "gpt-4o-mini-transcribe",
            "NLRL_ACTOR_MODEL": "gpt-5.4",
            "NLRL_ACTOR_BASE_URL": "codex-cli",
            "NLRL_ACTOR_API_KEY": "EMPTY",
            "NLRL_ACTOR_API_MODE": "codex_cli",
            "NLRL_ACTOR_ENABLE_THINKING": "1",
            "NLRL_ACTOR_TEMPERATURE": "0.2",
            "NLRL_ACTOR_TIMEOUT_SECONDS": "1800",
            "NLRL_CRITIC_MODEL": "gpt-5.4",
            "NLRL_CRITIC_BASE_URL": "codex-cli",
            "NLRL_CRITIC_API_KEY": "EMPTY",
            "NLRL_CRITIC_API_MODE": "codex_cli",
            "NLRL_CRITIC_ENABLE_THINKING": "1",
            "NLRL_CRITIC_TEMPERATURE": "0.2",
            "NLRL_CRITIC_TIMEOUT_SECONDS": "1800",
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": "1800",
        }
    )
    return env


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "GAIA_SEARCH_RUNTIME_CONFIG",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_EXECUTOR_TIMEOUT_SECONDS",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_API_KEY",
        "NLRL_TOOL_MODEL",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_BASE_URL",
        "NLRL_ACTOR_API_MODE",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_BASE_URL",
        "NLRL_CRITIC_API_MODE",
    ]
    return {key: env[key] for key in keys if env.get(key)}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def write_summary(status: str, **extra: Any) -> None:
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "updated_at": now(),
        "run_prefix": RUN_PREFIX,
        "master": MASTER_NAME,
        "tail_policy": TAIL_POLICY,
        "tail_grace_polls": TAIL_GRACE_POLLS,
        "poll_seconds": POLL_SECONDS,
        "source_index_doc": str(SOURCE_INDEX_DOC),
        "source_run_doc": str(SOURCE_RUN_DOC),
        "old_b_sharded_skill": str(OLD_B_SHARDED_SKILL),
        "config_intent": {
            "arch": "ARCH5.2",
            "flow": "20260504_1611 token_guard + forced answer + BOOT-V3 B_sharded eval-only repro",
            "executor": "Qwen3.5-9B-local",
            "max_model_len": 49152,
            "max_tokens": 12288,
            "token_guard_safety_margin": 512,
            "max_executor_steps": 24,
            "forced_answer_after_max_steps": True,
            "answer_acceptance_policy": "any_phase",
            "iterations_per_batch": 0,
            "hide_stale_phase_prompts": False,
        },
        "specs": [
            {
                **{
                    key: (str(value) if isinstance(value, Path) else value)
                    for key, value in asdict(spec).items()
                },
                "base_url": spec.base_url,
                "run_name": spec.run_name,
                "run_dir": str(spec.run_dir),
                "log_path": str(spec.log_path),
            }
            for spec in SPECS
        ],
        **extra,
    }
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def start_eval(spec: EvalSpec) -> subprocess.Popen[str]:
    env = common_env(spec.base_url)
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "train-local",
        "--dataset-path",
        str(spec.dataset_path),
        "--run-name",
        spec.run_name,
    ]
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    with spec.log_path.open("w", encoding="utf-8") as handle:
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
        name=spec.run_name,
        pid=proc.pid,
        cwd=ROOT,
        run_dir=str(spec.run_dir),
        command=command_display(env, cmd),
        log_path=spec.log_path,
        notes=(
            f"ARCH5.2 B_sharded repro; label={spec.label}; lane=gpu{spec.gpu}/p{spec.port}; "
            f"old_partial_stop_completed={spec.old_partial_stop_completed}/{spec.total_tasks}; "
            f"skill={OLD_B_SHARDED_SKILL}"
        ),
    )
    log(f"started {spec.label} pid={proc.pid} endpoint={spec.base_url} log={spec.log_path}")
    return proc


def state_success(state: dict[str, Any]) -> bool:
    return bool((state.get("env_result") or {}).get("evaluation", {}).get("task_success"))


def run_stats(spec: EvalSpec) -> dict[str, Any]:
    state_paths = sorted((spec.run_dir / "iteration_01").glob("*/state.json"))
    success_count = 0
    empty_final = 0
    max_step_hit = 0
    forced = 0
    for path in state_paths:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        success_count += int(state_success(state))
        env = state.get("env_result") or {}
        answer = str(env.get("final_answer") or env.get("final_choice_label") or "").strip()
        empty_final += int(not answer)
        trace = env.get("action_trace") or []
        max_step_hit += int(any(item.get("step_index") == 24 for item in trace if isinstance(item, dict)))
        forced += int(
            any(
                str(item.get("outcome") or "") == "forced_final_answer_after_max_steps"
                for item in trace
                if isinstance(item, dict)
            )
        )
    landed = len(state_paths)
    return {
        "label": spec.label,
        "run_name": spec.run_name,
        "run_dir": str(spec.run_dir),
        "log_path": str(spec.log_path),
        "landed": landed,
        "total": spec.total_tasks,
        "missing": max(0, spec.total_tasks - landed),
        "success_count": success_count,
        "score": f"{success_count}/{spec.total_tasks}",
        "empty_final": empty_final,
        "max_step_hit": max_step_hit,
        "forced_answer": forced,
    }


def stop_process_group(proc: subprocess.Popen[str], status: str) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        update_process_row(proc.pid, status)
        return
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=30)
    update_process_row(proc.pid, status)


def main() -> int:
    refresh_process_csv()
    if not OLD_B_SHARDED_SKILL.exists():
        raise FileNotFoundError(f"missing old B_sharded skill: {OLD_B_SHARDED_SKILL}")
    for spec in SPECS:
        models = endpoint_models(spec.base_url)
        if "Qwen3.5-9B-local" not in models:
            raise RuntimeError(f"{spec.base_url} is not serving Qwen3.5-9B-local; models={models}")
    write_summary("starting")
    procs = {spec.label: start_eval(spec) for spec in SPECS}
    target_seen: dict[str, int] = {spec.label: 0 for spec in SPECS}
    statuses: dict[str, str] = {spec.label: "running" for spec in SPECS}
    specs_by_label = {spec.label: spec for spec in SPECS}

    while True:
        all_done = True
        stats = {label: run_stats(specs_by_label[label]) for label in specs_by_label}
        for label, proc in procs.items():
            spec = specs_by_label[label]
            if proc.poll() is not None:
                if statuses[label] == "running":
                    statuses[label] = "finished" if proc.returncode == 0 else f"dead_returncode_{proc.returncode}"
                    update_process_row(proc.pid, statuses[label])
                continue
            all_done = False
            if TAIL_POLICY == "old_partial" and stats[label]["landed"] >= spec.old_partial_stop_completed:
                target_seen[label] += 1
                log(
                    f"{label} reached old partial target "
                    f"{stats[label]['landed']}/{spec.total_tasks}; grace={target_seen[label]}/{TAIL_GRACE_POLLS}"
                )
                if target_seen[label] >= TAIL_GRACE_POLLS:
                    stop_process_group(proc, "stopped_old_partial_tail_repro")
                    statuses[label] = "stopped_old_partial_tail_repro"
            else:
                target_seen[label] = 0
        write_summary("running", statuses=statuses, results=list(stats.values()))
        if all_done:
            break
        time.sleep(POLL_SECONDS)

    final_stats = [run_stats(spec) for spec in SPECS]
    any_failed = any(status.startswith("dead") for status in statuses.values())
    final_status = "failed" if any_failed else "finished"
    write_summary(final_status, statuses=statuses, results=final_stats)
    log(f"{final_status}: " + json.dumps(final_stats, ensure_ascii=False))
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
