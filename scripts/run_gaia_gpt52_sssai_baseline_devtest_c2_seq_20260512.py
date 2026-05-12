from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any

import run_gaia_gpt52_sssai_baseline_devtest_c10_20260507 as runner


CONFIRMATION_DOC = Path(
    os.environ.get(
        "GAIA_GPT52_SSSAI_BASELINE_CONFIRMATION_DOC",
        "/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.12_1811_GAIA_SSSAI_GPT52_baseline_c2顺序devtest.md",
    )
)
SLEEP_SECONDS = int(os.environ.get("GAIA_GPT52_SSSAI_BASELINE_MONITOR_INTERVAL_SECONDS", "60"))
TOOL_API_KEY = os.environ.get(
    "NLRL_TOOL_API_KEY",
    "sk-JhritIDG3G8QxS6pPJ1kIfqxWorzSAZgHgkLz4EA0RgFl9lQ",
)


_base_common_env = runner.common_env
_base_selected_env = runner.selected_env


def common_env() -> dict[str, str]:
    env = _base_common_env()
    env.update(
        {
            "NLRL_TOOL_API_KEY": TOOL_API_KEY,
            "NLRL_TOOL_TIMEOUT_SECONDS": os.environ.get("NLRL_TOOL_TIMEOUT_SECONDS", "1200"),
            "NLRL_TOOL_AUDIO_MODEL": os.environ.get("NLRL_TOOL_AUDIO_MODEL", "gpt-4o-mini-transcribe"),
            "NLRL_LLM_MAX_RETRIES": os.environ.get("NLRL_LLM_MAX_RETRIES", "7"),
            "NLRL_LLM_RETRY_DELAY_SECONDS": os.environ.get("NLRL_LLM_RETRY_DELAY_SECONDS", "8"),
            "NLRL_LLM_RETRY_BACKOFF_MULTIPLIER": os.environ.get("NLRL_LLM_RETRY_BACKOFF_MULTIPLIER", "1.7"),
            "NLRL_LLM_RETRY_MAX_DELAY_SECONDS": os.environ.get("NLRL_LLM_RETRY_MAX_DELAY_SECONDS", "90"),
            "GAIA_GPT52_SSSAI_BASELINE_SCHEDULE": "sequential_dev_then_test",
        }
    )
    return env


def selected_env(env: dict[str, str]) -> dict[str, str]:
    selected = _base_selected_env(env)
    for key in [
        "NLRL_TOOL_API_KEY",
        "NLRL_TOOL_TIMEOUT_SECONDS",
        "NLRL_TOOL_AUDIO_MODEL",
        "NLRL_LLM_MAX_RETRIES",
        "NLRL_LLM_RETRY_DELAY_SECONDS",
        "NLRL_LLM_RETRY_BACKOFF_MULTIPLIER",
        "NLRL_LLM_RETRY_MAX_DELAY_SECONDS",
        "GAIA_GPT52_SSSAI_BASELINE_PREFIX",
        "GAIA_GPT52_SSSAI_BASELINE_CONCURRENCY",
        "GAIA_GPT52_SSSAI_BASELINE_CONFIRMATION_DOC",
        "GAIA_GPT52_SSSAI_BASELINE_SCHEDULE",
    ]:
        if env.get(key) is not None:
            selected[key] = env[key]
    return selected


runner.common_env = common_env
runner.selected_env = selected_env


def shell_env_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def write_summary(status: str, **extra: Any) -> None:
    runner.QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "master": runner.MASTER_NAME,
        "run_prefix": runner.RUN_PREFIX,
        "updated_at": runner.now(),
        "status": status,
        "schedule": "sequential_dev_then_test",
        "total_sssai_api_concurrency": runner.CONCURRENCY,
        "confirmation_doc": str(CONFIRMATION_DOC),
        "config": {
            "runner": "direct-eval-local",
            "bootstrap": "disabled",
            "initial_skill": None,
            "executor_model": runner.SSSAI_MODEL,
            "executor_base_url": runner.SSSAI_BASE_URL,
            "executor_api_mode": "responses_sse",
            "executor_stream": True,
            "executor_enable_thinking": True,
            "executor_reasoning_effort": "xhigh",
            "executor_max_output_tokens": 12288,
            "executor_temperature": 0.1,
            "executor_timeout_seconds": 1200,
            "context_policy": "provider-native responses_sse; no local tokenizer token guard",
            "runtime_max_context_chars": 0,
            "max_executor_steps": 24,
            "task_concurrency_per_active_split": runner.CONCURRENCY,
            "answer_acceptance_policy": "any_phase",
            "tool_profile": "atomic_v2",
            "tool_base_url": "http://35.220.164.252:3888/v1",
            "tool_model": "gpt-4o-mini",
            "tool_audio_model": "gpt-4o-mini-transcribe",
            "search_provider": "serper",
            "search_runtime_config": str(runner.SEARCH_RUNTIME_CONFIG),
        },
        **extra,
    }
    runner.SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_report_heading(master_pid: int, master_log: Path) -> None:
    lines = [
        "",
        "## 2026-05-12 18:11 启动记录",
        "",
        f"- prefix：`{runner.RUN_PREFIX}`",
        f"- master：`{runner.MASTER_NAME}`，PID `{master_pid}`",
        f"- 调度：dev83 完成后再启动 test82；任一时刻 SSSAI executor API 并发上限 `{runner.CONCURRENCY}`。",
        f"- summary：`{runner.SUMMARY_PATH}`",
        f"- master log：`{master_log}`",
        "- split 日志会写入 `/data/xsy/project_gaia_skillrl/runs/_launch_logs/`，文件名包含 run name。",
    ]
    CONFIRMATION_DOC.write_text(CONFIRMATION_DOC.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8")


def append_final_report(status: str, stats: dict[str, dict[str, Any]], eval_pids: dict[str, int]) -> None:
    lines = [
        "",
        f"## 2026-05-12 SSSAI GPT-5.2 baseline c{runner.CONCURRENCY} 顺序 dev/test 结束记录",
        "",
        f"- status：`{status}`",
        f"- prefix：`{runner.RUN_PREFIX}`",
        f"- master：`{runner.MASTER_NAME}`，summary `{runner.SUMMARY_PATH}`",
        f"- eval PIDs：dev `{eval_pids.get('dev')}`，test `{eval_pids.get('test')}`",
        "- executor：`gpt-5.2` via SSSAI Responses SSE，`stream=True`，`reasoning_effort=xhigh`，`max_output_tokens=12288`。",
        "- 调度：顺序 dev -> test；总 SSSAI API 并发上限为 `2`。",
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
    CONFIRMATION_DOC.write_text(CONFIRMATION_DOC.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8")


def wait_one(label: str, proc: Any, eval_pids: dict[str, int], failures: dict[str, int]) -> int:
    while True:
        code = proc.poll()
        if code is not None:
            status = "finished" if code == 0 else "dead"
            runner.update_process_row(proc.pid, status, exit_code=code)
            if code != 0:
                failures[label] = code
            write_summary(
                f"{label}_{status}",
                active_split=None,
                eval_pids=eval_pids,
                failures=failures,
                stats=runner.collect_stats(),
            )
            runner.log(f"{label} exited code={code}")
            return code
        write_summary(f"{label}_running", active_split=label, eval_pids=eval_pids, failures=failures, stats=runner.collect_stats())
        time.sleep(SLEEP_SECONDS)


def main() -> int:
    runner.QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    runner.LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    runner.refresh_process_registry()
    master_log = runner.QUEUE_LOG_ROOT / f"{runner.MASTER_NAME}.log"
    master_cmd = shell_env_display(os.environ, [str(runner.PYTHON), *sys.argv])
    runner.append_process_row(
        kind="experiment_queue",
        name=runner.MASTER_NAME,
        pid=os.getpid(),
        cwd=runner.ROOT,
        run_dir=str(runner.QUEUE_LOG_ROOT),
        command=master_cmd,
        log_path=master_log,
        notes=(
            "SSSAI gpt-5.2 direct baseline master; sequential dev then test; "
            f"total SSSAI api concurrency c{runner.CONCURRENCY}; summary={runner.SUMMARY_PATH}."
        ),
    )
    append_report_heading(os.getpid(), master_log)
    status = "finished"
    eval_pids: dict[str, int] = {}
    failures: dict[str, int] = {}
    write_summary("starting", eval_pids=eval_pids, failures=failures, stats=runner.collect_stats())
    try:
        dev_proc = runner.start_eval("dev", runner.DEV_DATASET)
        eval_pids["dev"] = dev_proc.pid
        write_summary("dev_started", active_split="dev", eval_pids=eval_pids, failures=failures, stats=runner.collect_stats())
        wait_one("dev", dev_proc, eval_pids, failures)

        test_proc = runner.start_eval("test", runner.TEST_DATASET)
        eval_pids["test"] = test_proc.pid
        write_summary("test_started", active_split="test", eval_pids=eval_pids, failures=failures, stats=runner.collect_stats())
        wait_one("test", test_proc, eval_pids, failures)

        stats = runner.collect_stats()
        status = "dead" if failures else "finished"
        write_summary(status, eval_pids=eval_pids, failures=failures, stats=stats)
        append_final_report(status, stats, eval_pids)
        return 1 if failures else 0
    except KeyboardInterrupt:
        status = "stopped"
        stats = runner.collect_stats()
        write_summary(status, eval_pids=eval_pids, failures=failures, stats=stats)
        append_final_report(status, stats, eval_pids)
        raise
    except Exception as exc:
        status = "dead"
        stats = runner.collect_stats()
        write_summary(status, eval_pids=eval_pids, failures=failures, error=str(exc), stats=stats)
        append_final_report(status, stats, eval_pids)
        raise
    finally:
        runner.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
