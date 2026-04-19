#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path("/data/xsy/project_gaia_skillrl").resolve()
DEFAULT_CONFIG = REPO_ROOT / "configs" / "system.json"
DEFAULT_DATASET = REPO_ROOT / "data" / "converted" / "gaia_validation_tasks.json"
DEFAULT_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
DEFAULT_RESCORER = REPO_ROOT / "scripts" / "rescore_gaia_runs_with_official.py"
LAUNCH_LOG_ROOT = REPO_ROOT / "runs" / "_launch_logs"
QUEUE_LOG_ROOT = REPO_ROOT / "runs" / "_queue_logs"


def timestamp_now() -> datetime:
    return datetime.now()


def second_prefix(dt: datetime | None = None) -> str:
    return (dt or timestamp_now()).strftime("%Y%m%d_%H%M%S")


def run_parent_for_prefix(prefix: str) -> Path:
    return REPO_ROOT / "runs" / str(int(prefix[0:4])) / str(int(prefix[4:6])) / f"{int(prefix[0:4])}-{int(prefix[4:6])}-{int(prefix[6:8])}"


@dataclass
class ExperimentSpec:
    key: str
    description: str
    run_name_suffix: str
    cli_args: list[str]
    env_overrides: dict[str, str]


def build_experiment_specs(
    *,
    dataset_path: Path,
    config_path: Path,
    task_concurrency: int,
    llm_concurrency: int,
    max_tasks: int,
) -> list[ExperimentSpec]:
    dataset_path_str = str(dataset_path)
    config_path_str = str(config_path)

    shared_env = {
        "NLRL_RUNTIME_TASK_CONCURRENCY": str(task_concurrency),
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(llm_concurrency),
    }

    sssai_executor_env = {
        **shared_env,
        "NLRL_EXECUTOR_MODEL": os.environ.get("GAIA_SSSAI_EXECUTOR_MODEL", "gpt-5.2"),
        "NLRL_EXECUTOR_BASE_URL": os.environ.get("GAIA_SSSAI_EXECUTOR_BASE_URL", "https://node-hk.sssaicode.com/api/v1/responses"),
        "NLRL_EXECUTOR_API_KEY": os.environ.get(
            "GAIA_SSSAI_EXECUTOR_API_KEY",
            "sk-sssaicode-9d340d3064511a95fb7f428f2356dfc7a08c724271a66aa1bdc72a3b2af85cb4",
        ),
        "NLRL_EXECUTOR_API_MODE": os.environ.get("GAIA_SSSAI_EXECUTOR_API_MODE", "responses_sse"),
        "NLRL_EXECUTOR_STREAM": os.environ.get("GAIA_SSSAI_EXECUTOR_STREAM", "1"),
        "NLRL_EXECUTOR_TIMEOUT_SECONDS": os.environ.get("GAIA_SSSAI_EXECUTOR_TIMEOUT_SECONDS", "420"),
    }

    direct_8b_env = {
        **shared_env,
        "NLRL_EXECUTOR_MODEL": os.environ.get("GAIA_8B_EXECUTOR_MODEL", "qwen3-8b"),
    }
    for key in (
        "GAIA_8B_EXECUTOR_BASE_URL",
        "GAIA_8B_EXECUTOR_API_KEY",
        "GAIA_8B_EXECUTOR_API_MODE",
        "GAIA_8B_EXECUTOR_STREAM",
        "GAIA_8B_EXECUTOR_TIMEOUT_SECONDS",
    ):
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        mapped_key = key.replace("GAIA_8B_", "NLRL_")
        direct_8b_env[mapped_key] = value

    train_8b_env = {
        **shared_env,
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "2",
        "NLRL_EXECUTOR_MODEL": os.environ.get("GAIA_8B_EXECUTOR_MODEL", "qwen3-8b"),
    }
    for key in (
        "GAIA_8B_EXECUTOR_BASE_URL",
        "GAIA_8B_EXECUTOR_API_KEY",
        "GAIA_8B_EXECUTOR_API_MODE",
        "GAIA_8B_EXECUTOR_STREAM",
        "GAIA_8B_EXECUTOR_TIMEOUT_SECONDS",
    ):
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        mapped_key = key.replace("GAIA_8B_", "NLRL_")
        train_8b_env[mapped_key] = value
    for key in (
        "GAIA_TRAIN_ACTOR_MODEL",
        "GAIA_TRAIN_ACTOR_BASE_URL",
        "GAIA_TRAIN_ACTOR_API_KEY",
        "GAIA_TRAIN_ACTOR_API_MODE",
        "GAIA_TRAIN_ACTOR_TIMEOUT_SECONDS",
        "GAIA_TRAIN_CRITIC_MODEL",
        "GAIA_TRAIN_CRITIC_BASE_URL",
        "GAIA_TRAIN_CRITIC_API_KEY",
        "GAIA_TRAIN_CRITIC_API_MODE",
        "GAIA_TRAIN_CRITIC_TIMEOUT_SECONDS",
    ):
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        direct_key = key.replace("GAIA_TRAIN_", "NLRL_")
        train_8b_env[direct_key] = value

    return [
        ExperimentSpec(
            key="direct_sssai_gpt52",
            description="Strong-model no-skill upper bound with SSSAI gpt-5.2 as executor.",
            run_name_suffix="gaia_validation53_level1_direct_gpt52_sssai_c20_extreme",
            cli_args=[
                str(DEFAULT_PYTHON),
                "-m",
                "gaia_skillrl.cli",
                "--config",
                config_path_str,
                "direct-eval-local",
                "--dataset-path",
                dataset_path_str,
                "--level",
                "1",
                "--max-tasks",
                str(max_tasks),
            ],
            env_overrides=sssai_executor_env,
        ),
        ExperimentSpec(
            key="direct_qwen3_8b",
            description="8B no-skill baseline with qwen3-8b as executor.",
            run_name_suffix="gaia_validation53_level1_direct_qwen3_8b_c20_extreme",
            cli_args=[
                str(DEFAULT_PYTHON),
                "-m",
                "gaia_skillrl.cli",
                "--config",
                config_path_str,
                "direct-eval-local",
                "--dataset-path",
                dataset_path_str,
                "--level",
                "1",
                "--max-tasks",
                str(max_tasks),
            ],
            env_overrides=direct_8b_env,
        ),
        ExperimentSpec(
            key="train2_qwen3_8b_bootstrap",
            description="Current method: 8B executor plus bootstrap skill generation and 2 training iterations.",
            run_name_suffix="gaia_validation53_level1_train2_qwen3_8b_gpt54_c20G_extreme",
            cli_args=[
                str(DEFAULT_PYTHON),
                "-m",
                "gaia_skillrl.cli",
                "--config",
                config_path_str,
                "train-local",
                "--dataset-path",
                dataset_path_str,
                "--level",
                "1",
                "--max-tasks",
                str(max_tasks),
                "--bootstrap-skill",
            ],
            env_overrides=train_8b_env,
        ),
    ]


def shell_join(parts: list[str]) -> str:
    escaped: list[str] = []
    for part in parts:
        if not part or any(ch in part for ch in " \t\n\"'`$&|;()[]{}<>"):
            escaped.append("'" + part.replace("'", "'\"'\"'") + "'")
        else:
            escaped.append(part)
    return " ".join(escaped)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_subprocess(
    *,
    cmd: list[str],
    env: dict[str, str],
    cwd: Path,
    log_path: Path,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return int(process.returncode)


def queue_manifest_path(queue_name: str) -> Path:
    return QUEUE_LOG_ROOT / f"{queue_name}.json"


def queue_stdout_log_path(queue_name: str) -> Path:
    return QUEUE_LOG_ROOT / f"{queue_name}.log"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run three GAIA level1 extreme experiments sequentially."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--dataset-path", default=str(DEFAULT_DATASET))
    parser.add_argument("--task-concurrency", type=int, default=20)
    parser.add_argument("--llm-max-concurrent-requests", type=int, default=20)
    parser.add_argument("--max-tasks", type=int, default=53)
    parser.add_argument(
        "--only",
        nargs="*",
        choices=["direct_sssai_gpt52", "direct_qwen3_8b", "train2_qwen3_8b_bootstrap"],
        default=None,
    )
    parser.add_argument("--queue-name", default=None)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--skip-rescore", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    config_path = Path(args.config).resolve()
    dataset_path = Path(args.dataset_path).resolve()
    queue_started_at = timestamp_now()
    queue_name = args.queue_name or f"{second_prefix(queue_started_at)}_gaia_level1_extreme_queue"

    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")
    if not DEFAULT_PYTHON.exists():
        raise FileNotFoundError(f"Python executable not found: {DEFAULT_PYTHON}")

    specs = build_experiment_specs(
        dataset_path=dataset_path,
        config_path=config_path,
        task_concurrency=args.task_concurrency,
        llm_concurrency=args.llm_max_concurrent_requests,
        max_tasks=args.max_tasks,
    )
    if args.only:
        wanted = set(args.only)
        specs = [spec for spec in specs if spec.key in wanted]
    if not specs:
        raise ValueError("No experiments selected.")

    manifest: dict[str, Any] = {
        "queue_name": queue_name,
        "started_at": queue_started_at.isoformat(),
        "repo_root": str(REPO_ROOT),
        "config_path": str(config_path),
        "dataset_path": str(dataset_path),
        "task_concurrency": args.task_concurrency,
        "llm_max_concurrent_requests": args.llm_max_concurrent_requests,
        "max_tasks": args.max_tasks,
        "dry_run": args.dry_run,
        "continue_on_error": args.continue_on_error,
        "skip_rescore": args.skip_rescore,
        "experiments": [],
    }

    for index, spec in enumerate(specs, start=1):
        started_at = timestamp_now()
        run_prefix = second_prefix(started_at)
        run_name = f"{run_prefix}_{spec.run_name_suffix}"
        run_dir = run_parent_for_prefix(run_prefix) / run_name
        launch_log_path = LAUNCH_LOG_ROOT / f"{run_name}.log"
        env = os.environ.copy()
        env.update(spec.env_overrides)
        env.setdefault("HTTP_PROXY", "http://127.0.0.1:17890")
        env.setdefault("HTTPS_PROXY", "http://127.0.0.1:17890")
        env.setdefault("ALL_PROXY", "socks5://127.0.0.1:17891")

        cmd = list(spec.cli_args) + ["--run-name", run_name]
        command_text = " ".join(f'{key}=\"{value}\"' for key, value in spec.env_overrides.items()) + " " + shell_join(cmd)

        record: dict[str, Any] = {
            "index": index,
            "key": spec.key,
            "description": spec.description,
            "run_name": run_name,
            "run_dir": str(run_dir),
            "launch_log_path": str(launch_log_path),
            "command": command_text.strip(),
            "env_overrides": spec.env_overrides,
            "status": "planned",
            "started_at": started_at.isoformat(),
        }
        manifest["experiments"].append(record)
        write_json(queue_manifest_path(queue_name), manifest)

        print(f"[{index}/{len(specs)}] {spec.key}")
        print(f"run_name={run_name}")
        print(f"log_path={launch_log_path}")
        print(command_text)

        if args.dry_run:
            record["status"] = "dry_run"
            continue

        record["status"] = "running"
        write_json(queue_manifest_path(queue_name), manifest)
        return_code = run_subprocess(
            cmd=cmd,
            env=env,
            cwd=REPO_ROOT,
            log_path=launch_log_path,
        )
        record["return_code"] = return_code
        record["ended_at"] = timestamp_now().isoformat()

        if return_code != 0:
            record["status"] = "failed"
            write_json(queue_manifest_path(queue_name), manifest)
            print(f"experiment failed: {spec.key} return_code={return_code}", file=sys.stderr)
            if not args.continue_on_error:
                manifest["ended_at"] = timestamp_now().isoformat()
                manifest["status"] = "failed"
                write_json(queue_manifest_path(queue_name), manifest)
                return return_code
            continue

        record["status"] = "completed"

        if not args.skip_rescore:
            rescore_cmd = [str(DEFAULT_PYTHON), str(DEFAULT_RESCORER), str(run_dir)]
            rescore_log_path = LAUNCH_LOG_ROOT / f"{run_name}.rescore.log"
            record["rescore_log_path"] = str(rescore_log_path)
            rescore_return_code = run_subprocess(
                cmd=rescore_cmd,
                env=os.environ.copy(),
                cwd=REPO_ROOT,
                log_path=rescore_log_path,
            )
            record["rescore_return_code"] = rescore_return_code
            record["official_rescore_path"] = str(run_dir / "official_gaia_rescore.json")
            if rescore_return_code == 0:
                record["rescore_status"] = "completed"
            else:
                record["rescore_status"] = "failed"

        write_json(queue_manifest_path(queue_name), manifest)

    manifest["ended_at"] = timestamp_now().isoformat()
    manifest["status"] = "completed"
    write_json(queue_manifest_path(queue_name), manifest)
    print(f"queue completed: {queue_name}")
    print(f"manifest={queue_manifest_path(queue_name)}")
    print(f"queue_log_hint={queue_stdout_log_path(queue_name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
