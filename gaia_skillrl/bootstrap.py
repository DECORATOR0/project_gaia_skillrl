from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import DatasetTask, LLMMessage
from .utils import ensure_dir, write_json


class SkillBootstrapper:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.actor)

    @staticmethod
    def _task_attachments(task: DatasetTask) -> list[dict[str, str]]:
        attachments: list[dict[str, str]] = []
        for name in task.file_list:
            if name == "task.json":
                continue
            suffix = Path(name).suffix.lower()
            attachments.append(
                {
                    "name": name,
                    "suffix": suffix or "(none)",
                }
            )
        return attachments

    def _batch_payload(self, tasks: list[DatasetTask]) -> dict[str, Any]:
        attachment_histogram: Counter[str] = Counter()
        task_rows: list[dict[str, Any]] = []
        for task in tasks:
            attachments = self._task_attachments(task)
            for item in attachments:
                attachment_histogram[item["suffix"]] += 1
            task_rows.append(
                {
                    "task_id": task.task_id,
                    "level": task.metadata.get("level"),
                    "split": task.metadata.get("split") or task.source_type,
                    "question": task.prompt,
                    "attachments": attachments,
                    "file_list": task.file_list,
                }
            )
        return {
            "task_count": len(tasks),
            "levels": sorted({task.metadata.get("level") for task in tasks}),
            "attachment_suffix_histogram": dict(sorted(attachment_histogram.items())),
            "tasks": task_rows,
        }

    def bootstrap(self, tasks: list[DatasetTask], log_dir: Path) -> dict[str, Any]:
        ensure_dir(log_dir)
        batch_payload = self._batch_payload(tasks)
        write_json(log_dir / "bootstrap_batch_input.json", batch_payload)

        system_prompt = render_prompt(self.config.prompt_root / "bootstrap_skill_system.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "bootstrap_skill_user.md",
            current_skills_json="[]",
            batch_tasks_json=json.dumps(batch_payload, ensure_ascii=False, indent=2),
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "bootstrap_initial_skill", llm_result)
        write_json(log_dir / "bootstrap_skill_response.json", payload)

        nested_skill = payload.get("skill", {}) if isinstance(payload.get("skill", {}), dict) else {}
        target_skill_name = str(
            nested_skill.get("target_skill_name")
            or payload.get("target_skill_name")
            or "gaia-general-skill"
        ).strip() or "gaia-general-skill"
        raw_files = nested_skill.get("files_to_write", payload.get("files_to_write", {}))
        if not isinstance(raw_files, dict):
            raise ValueError("Bootstrap skill response must include files_to_write as an object.")
        files_to_write = {str(path): str(content) for path, content in raw_files.items()}
        if not str(files_to_write.get("SKILL.md", "")).strip():
            raise ValueError("Bootstrap skill response must include a non-empty SKILL.md.")
        forbidden_paths = sorted(path for path in files_to_write if path.startswith("scripts/"))
        if forbidden_paths:
            raise ValueError(
                f"Bootstrap skill attempted to create unsupported scripts: {', '.join(forbidden_paths)}"
            )

        decision = {
            "summary": str(nested_skill.get("summary") or payload.get("summary") or "").strip(),
            "target_skill_name": target_skill_name,
            "files_to_write": files_to_write,
            "task_count": len(tasks),
        }
        write_json(log_dir / "bootstrap_skill_decision.json", decision)
        return decision
