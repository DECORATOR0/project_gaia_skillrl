from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_system_config
from .dataset import build_gaia_converted_dataset
from .trainer import GaiaSkillTrainer
from .utils import ensure_preferred_proxy_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GAIA single-skill smoke framework.")
    parser.add_argument("--config", required=True, help="Path to system config JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download-gaia", help="Download and materialize GAIA metadata into the converted dataset.")
    download.add_argument("--hf-token", required=True)
    download.add_argument("--config-name", default="2023_level1")
    download.add_argument("--split", default="validation")
    download.add_argument("--max-tasks", type=int, default=None)

    smoke = subparsers.add_parser("smoke-train", help="Run a low-cost GAIA smoke training loop.")
    smoke.add_argument("--hf-token", required=True)
    smoke.add_argument("--config-name", default="2023_level1")
    smoke.add_argument("--split", default="validation")
    smoke.add_argument("--max-tasks", type=int, default=1)
    smoke.add_argument("--task-id", action="append", default=[])
    smoke.add_argument("--level", type=int, default=None)
    smoke.add_argument("--run-name", default=None)

    train_local = subparsers.add_parser("train-local", help="Run the skill-training loop on an existing local converted dataset.")
    train_local.add_argument("--dataset-path", default=None)
    train_local.add_argument("--max-tasks", type=int, default=None)
    train_local.add_argument("--task-id", action="append", default=[])
    train_local.add_argument("--level", type=int, default=None)
    train_local.add_argument("--run-name", default=None)

    direct_eval = subparsers.add_parser("direct-eval-local", help="Run a no-skill direct executor baseline on an existing local converted dataset.")
    direct_eval.add_argument("--dataset-path", default=None)
    direct_eval.add_argument("--max-tasks", type=int, default=None)
    direct_eval.add_argument("--task-id", action="append", default=[])
    direct_eval.add_argument("--level", type=int, default=None)
    direct_eval.add_argument("--run-name", default=None)

    return parser


def main() -> None:
    ensure_preferred_proxy_env()
    parser = build_parser()
    args = parser.parse_args()
    config = load_system_config(args.config)

    if args.command == "download-gaia":
        path = build_gaia_converted_dataset(
            config,
            hf_token=args.hf_token,
            config_name=args.config_name,
            split=args.split,
            max_tasks=args.max_tasks,
        )
        print(path)
        return

    if args.command == "smoke-train":
        trainer = GaiaSkillTrainer(config)
        run_dir = trainer.smoke_train(
            hf_token=args.hf_token,
            config_name=args.config_name,
            split=args.split,
            max_tasks=args.max_tasks,
            task_ids=args.task_id or None,
            level=args.level,
            run_name=args.run_name,
        )
        print(run_dir)
        return

    if args.command == "train-local":
        trainer = GaiaSkillTrainer(config)
        run_dir = trainer.train_local(
            dataset_path=Path(args.dataset_path) if args.dataset_path else None,
            max_tasks=args.max_tasks,
            task_ids=args.task_id or None,
            level=args.level,
            run_name=args.run_name,
        )
        print(run_dir)
        return

    if args.command == "direct-eval-local":
        trainer = GaiaSkillTrainer(config)
        run_dir = trainer.direct_eval_local(
            dataset_path=Path(args.dataset_path) if args.dataset_path else None,
            max_tasks=args.max_tasks,
            task_ids=args.task_id or None,
            level=args.level,
            run_name=args.run_name,
        )
        print(run_dir)
        return

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
