from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from pathlib import Path
import shutil
from typing import Any

from .actor import SkillActor
from .config import SystemConfig, clone_system_config
from .critic import SkillCritic
from .dataset import build_gaia_converted_dataset, load_converted_dataset, select_tasks
from .environment import DirectEnvironment, SkillEnvironment
from .schemas import DatasetTask, SkillDetail, to_dict
from .skills import (
    discover_skills,
    load_skill_detail,
    reset_experience_buffer,
    reset_skill_library,
    write_skill_bundle,
)
from .utils import append_jsonl, ensure_dir, prepare_dated_run_dir, utc_timestamp, write_json

_DEFAULT_SKILL_FILES = {
    "SKILL.md": """---
name: gaia-general-skill
description: Phase-based GAIA execution skill for local attachments, web retrieval, and short-answer synthesis.
allowed-tools:
  - list_dir
  - read_file
  - read_json_file
  - extract_pdf_text
  - read_table
  - image_metadata
  - web_search
  - fetch_url
  - run_python
metadata:
  benchmark: GAIA
  version: "0.1"
---

## Phase: INIT

Goal: inspect the task directory and task manifest before doing anything else.

Instructions:
- Always call `list_dir` on `.` and inspect `task.json`.
- If an attachment exists, note its exact filename.
- Do not answer in this phase.
- Do not jump directly to `ANALYZE` or `CONCLUDE`.
- After you understand the local task layout, move to `GATHER`.

## Phase: GATHER

Goal: collect evidence from the task attachment first, then from the web only if needed.

Instructions:
- Prefer local files before web search.
- Use `extract_pdf_text` for PDFs, `read_table` for CSV/XLSX, `read_file` for txt/json/html, and `image_metadata` only to inspect image basics.
- Use `web_search` and `fetch_url` only when the question clearly needs external information.
- Do not guess filenames or URLs.
- Do not answer in this phase.
- Move to `ANALYZE` once the relevant evidence has been collected.

## Phase: ANALYZE

Goal: convert evidence into the exact short answer.

Instructions:
- Use `run_python` for arithmetic, counting, sorting, and normalization.
- Keep track of the exact answer string you plan to output.
- Explicitly identify the requested final units, scale, rounding, and format before finalizing.
- If the prompt asks for scaled units such as thousand-hours, millions, percentages, nearest buckets, or date formatting, convert your intermediate value into that requested representation before treating it as final.
- Do not answer in this phase.
- If the evidence is insufficient, gather more evidence instead of guessing.
- Move to `CONCLUDE` only when you can state the final short answer exactly.

## Phase: CONCLUDE

Goal: return the final answer only.

Instructions:
- Do one final check that the answer matches the prompt's requested units, scale, rounding, separators, and exact output format.
- Output `<ANSWER>your final answer</ANSWER>`.
- The answer should be short and exact.
- Do not add explanation inside the answer tag.
""",
    "references/GAIA_EXECUTION_NOTES.md": """# GAIA Execution Notes

- Read `task.json` before opening attachments.
- Prefer attachment evidence before external search.
- Use `run_python` whenever arithmetic or deterministic parsing is involved.
- Return a short final answer string without extra formatting.
""",
}


class GaiaSkillTrainer:
    def __init__(self, config: SystemConfig):
        self.config = config

    def prepare_run_dir(self, run_name: str | None = None, *, mode: str = "gaia-single-skill") -> Path:
        run_dir = prepare_dated_run_dir(self.config.run_root, run_name=run_name)
        write_json(
            run_dir / "config_snapshot.json",
            {
                "mode": mode,
                "base_config": to_dict(self.config),
            },
        )
        return run_dir

    def _build_logger(self, run_dir: Path) -> logging.Logger:
        logger = logging.getLogger(f"gaia_skillrl.{run_dir.name}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        file_handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        return logger

    def _batch_state_config(self, run_dir: Path) -> SystemConfig:
        state_root = ensure_dir(run_dir / "batch_state")
        return clone_system_config(
            self.config,
            paths={
                "run_root": state_root / "runs",
                "skill_library_root": state_root / "skill_library",
                "experience_buffer_path": state_root / "experience_buffer.jsonl",
            },
        )

    def _write_default_skill(self, config: SystemConfig) -> Path:
        reset_skill_library(config.skill_library_root)
        return write_skill_bundle(config.skill_library_root, "gaia-general-skill", _DEFAULT_SKILL_FILES)

    def _load_active_skill(self, skill_library_root: Path) -> SkillDetail:
        headers = discover_skills(skill_library_root)
        if not headers:
            raise RuntimeError("No active skill found in skill library.")
        return load_skill_detail(headers[0])

    @staticmethod
    def _snapshot_active_skill(skill: SkillDetail, target_root: Path) -> Path:
        source_dir = Path(skill.header.skill_dir)
        target_dir = target_root / skill.header.name
        ensure_dir(target_root)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
        return target_dir

    def _select_local_tasks(
        self,
        *,
        dataset_path: Path,
        max_tasks: int | None,
        task_ids: list[str] | None,
        level: int | None,
    ) -> list[DatasetTask]:
        tasks = load_converted_dataset(dataset_path)
        selected_tasks = select_tasks(tasks, task_ids=task_ids, count=max_tasks, level=level)
        if not selected_tasks:
            raise RuntimeError("No GAIA tasks selected from the local converted dataset.")
        return selected_tasks

    @staticmethod
    def _write_selected_tasks_summary(
        run_dir: Path,
        *,
        source: dict[str, Any],
        selected_tasks: list[DatasetTask],
    ) -> None:
        write_json(
            run_dir / "selected_tasks.json",
            {
                **source,
                "task_ids": [task.task_id for task in selected_tasks],
                "levels": [task.metadata.get("level") for task in selected_tasks],
            },
        )

    def _run_tasks(
        self,
        *,
        selected_tasks: list[DatasetTask],
        iteration_dir: Path,
        task_concurrency: int,
        logger: logging.Logger,
        iteration_label: str,
        run_one: Any,
    ) -> list[Any]:
        total = len(selected_tasks)
        concurrency = max(1, min(task_concurrency, total))
        states: list[Any] = [None] * total

        def _job(index: int, task: DatasetTask) -> tuple[int, Any]:
            task_dir = ensure_dir(iteration_dir / task.task_id)
            return index, run_one(task, task_dir)

        if concurrency == 1:
            for index, task in enumerate(selected_tasks):
                _, state = _job(index, task)
                states[index] = state
                logger.info(
                    "%s progress %d/%d task=%s success=%s",
                    iteration_label,
                    index + 1,
                    total,
                    task.task_id,
                    state.env_result.evaluation.task_success,
                )
            return states

        logger.info("%s task concurrency=%d", iteration_label, concurrency)
        completed = 0
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="gaia-task") as pool:
            futures = {
                pool.submit(_job, index, task): (index, task.task_id)
                for index, task in enumerate(selected_tasks)
            }
            for future in as_completed(futures):
                index, task_id = futures[future]
                _, state = future.result()
                states[index] = state
                completed += 1
                logger.info(
                    "%s progress %d/%d task=%s success=%s",
                    iteration_label,
                    completed,
                    total,
                    task_id,
                    state.env_result.evaluation.task_success,
                )
        return states

    @staticmethod
    def _history_entry(iteration_index: int, states: list[Any], reward_summary: str) -> dict[str, Any]:
        succeeded = [state.task_id for state in states if state.env_result.evaluation.task_success]
        failed = [state.task_id for state in states if not state.env_result.evaluation.task_success]
        return {
            "iteration_index": iteration_index,
            "succeeded_task_ids": succeeded,
            "failed_task_ids": failed,
            "summary": reward_summary,
        }

    def _run_training_loop(
        self,
        *,
        run_dir: Path,
        selected_tasks: list[DatasetTask],
    ) -> Path:
        logger = self._build_logger(run_dir)
        state_config = self._batch_state_config(run_dir)
        reset_experience_buffer(state_config.experience_buffer_path)
        task_concurrency = max(1, state_config.runtime.task_concurrency)

        self._write_default_skill(state_config)
        actor = SkillActor(state_config)
        critic = SkillCritic(state_config)
        history_poll: list[dict[str, Any]] = []

        for iteration_index in range(1, state_config.runtime.iterations_per_batch + 1):
            logger.info("Iteration %02d: executor -> critic -> actor", iteration_index)
            active_skill = self._load_active_skill(state_config.skill_library_root)
            iteration_dir = ensure_dir(run_dir / f"iteration_{iteration_index:02d}")
            self._snapshot_active_skill(active_skill, iteration_dir / "skill_before_actor")
            states = self._run_tasks(
                selected_tasks=selected_tasks,
                iteration_dir=iteration_dir,
                task_concurrency=task_concurrency,
                logger=logger,
                iteration_label=f"Iteration {iteration_index:02d}",
                run_one=lambda task, task_dir: SkillEnvironment(state_config).run(task, active_skill, task_dir),
            )
            reward = critic.evaluate_batch(states, active_skill, history_poll, iteration_dir)
            decision = actor.act(reward, active_skill, history_poll, iteration_dir)
            actor.apply(decision)
            updated_skill = self._load_active_skill(state_config.skill_library_root)
            self._snapshot_active_skill(updated_skill, iteration_dir / "skill_after_actor")

            entry = self._history_entry(iteration_index, states, reward.summary)
            history_poll.append(entry)
            append_jsonl(run_dir / "history_poll.jsonl", entry)
            write_json(
                iteration_dir / "iteration_summary.json",
                {
                    "iteration_index": iteration_index,
                    "states": [to_dict(state) for state in states],
                    "reward": to_dict(reward),
                    "actor_decision": to_dict(decision),
                },
            )

        logger.info("Running final validation with the latest skill")
        final_skill = self._load_active_skill(state_config.skill_library_root)
        final_iteration_index = state_config.runtime.iterations_per_batch + 1
        final_iteration_dir = ensure_dir(run_dir / f"iteration_{final_iteration_index:02d}")
        self._snapshot_active_skill(final_skill, final_iteration_dir / "skill_for_eval")
        final_states = self._run_tasks(
            selected_tasks=selected_tasks,
            iteration_dir=final_iteration_dir,
            task_concurrency=task_concurrency,
            logger=logger,
            iteration_label=f"Iteration {final_iteration_index:02d}",
            run_one=lambda task, task_dir: SkillEnvironment(state_config).run(task, final_skill, task_dir),
        )

        final_summary = {
            "run_name": run_dir.name,
            "generated_at": utc_timestamp(),
            "task_count": len(final_states),
            "success_count": sum(1 for state in final_states if state.env_result.evaluation.task_success),
            "tasks": [to_dict(state) for state in final_states],
            "active_skill": to_dict(final_skill),
        }
        write_json(run_dir / "run_summary.json", final_summary)
        return run_dir

    def smoke_train(
        self,
        *,
        hf_token: str,
        config_name: str = "2023_level1",
        split: str = "validation",
        max_tasks: int = 1,
        task_ids: list[str] | None = None,
        level: int | None = None,
        run_name: str | None = None,
    ) -> Path:
        run_dir = self.prepare_run_dir(run_name, mode="gaia-single-skill")

        logger = self._build_logger(run_dir)
        logger.info("Building GAIA converted dataset: config=%s split=%s", config_name, split)
        build_gaia_converted_dataset(
            self.config,
            hf_token=hf_token,
            config_name=config_name,
            split=split,
        )
        tasks = load_converted_dataset(self.config.converted_dataset_path)
        selected_tasks = select_tasks(tasks, task_ids=task_ids, count=max_tasks, level=level)
        if not selected_tasks:
            raise RuntimeError("No GAIA tasks selected for smoke run.")

        self._write_selected_tasks_summary(
            run_dir,
            source={
                "source_mode": "hf_build",
                "config_name": config_name,
                "split": split,
            },
            selected_tasks=selected_tasks,
        )
        return self._run_training_loop(run_dir=run_dir, selected_tasks=selected_tasks)

    def train_local(
        self,
        *,
        dataset_path: Path | None = None,
        max_tasks: int | None = None,
        task_ids: list[str] | None = None,
        level: int | None = None,
        run_name: str | None = None,
    ) -> Path:
        resolved_dataset_path = (dataset_path or self.config.converted_dataset_path).resolve()
        selected_tasks = self._select_local_tasks(
            dataset_path=resolved_dataset_path,
            max_tasks=max_tasks,
            task_ids=task_ids,
            level=level,
        )
        run_dir = self.prepare_run_dir(run_name, mode="gaia-single-skill-local")
        self._write_selected_tasks_summary(
            run_dir,
            source={
                "source_mode": "local_dataset",
                "dataset_path": str(resolved_dataset_path),
            },
            selected_tasks=selected_tasks,
        )
        return self._run_training_loop(run_dir=run_dir, selected_tasks=selected_tasks)

    def direct_eval_local(
        self,
        *,
        dataset_path: Path | None = None,
        max_tasks: int | None = None,
        task_ids: list[str] | None = None,
        level: int | None = None,
        run_name: str | None = None,
    ) -> Path:
        resolved_dataset_path = (dataset_path or self.config.converted_dataset_path).resolve()
        selected_tasks = self._select_local_tasks(
            dataset_path=resolved_dataset_path,
            max_tasks=max_tasks,
            task_ids=task_ids,
            level=level,
        )
        run_dir = self.prepare_run_dir(run_name, mode="gaia-direct-baseline")
        logger = self._build_logger(run_dir)
        self._write_selected_tasks_summary(
            run_dir,
            source={
                "source_mode": "local_dataset",
                "dataset_path": str(resolved_dataset_path),
                "runner": "direct-baseline",
            },
            selected_tasks=selected_tasks,
        )

        logger.info("Direct baseline evaluation: executor only, no activated skill")
        iteration_dir = ensure_dir(run_dir / "iteration_01")
        task_concurrency = max(1, self.config.runtime.task_concurrency)
        states = self._run_tasks(
            selected_tasks=selected_tasks,
            iteration_dir=iteration_dir,
            task_concurrency=task_concurrency,
            logger=logger,
            iteration_label="Iteration 01",
            run_one=lambda task, task_dir: DirectEnvironment(self.config).run(task, task_dir),
        )

        summary = {
            "run_name": run_dir.name,
            "generated_at": utc_timestamp(),
            "task_count": len(states),
            "success_count": sum(1 for state in states if state.env_result.evaluation.task_success),
            "tasks": [to_dict(state) for state in states],
            "mode": "direct-baseline",
        }
        write_json(run_dir / "run_summary.json", summary)
        return run_dir
