from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import DatasetTask, LLMMessage
from .tools import available_tool_names_for_profile
from .utils import ensure_dir, write_json


class SkillBootstrapper:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.actor)

    def _runtime_contract(self) -> dict[str, Any]:
        return {
            "executor_model": self.config.executor.model,
            "max_executor_steps": self.config.runtime.max_executor_steps,
            "answer_rule": "A non-empty <ANSWER>...</ANSWER> is accepted as the final answer by the runtime; empty answers are rejected.",
            "phase_header_format": "## Phase: NAME",
            "action_tags": {
                "tool_call": "<CALL>tool_name</CALL><ARGS>{...}</ARGS>",
                "phase_transition": "<NEXT>PHASE_NAME</NEXT>",
                "final_answer": "<ANSWER>short final answer</ANSWER>",
            },
            "tool_profile": self.config.runtime.tool_profile,
            "available_tools": available_tool_names_for_profile(self.config.runtime.tool_profile),
            "environment_limits": [
                "Browser automation is unavailable.",
                "Task workspaces contain task.json and any materialized attachments.",
            ],
            "downstream_consumption": [
                "The executor receives only the INIT phase at task start.",
                "After an accepted <NEXT> transition, the runtime injects only the target phase content.",
                "The runtime infers the phase graph from explicit Next: lines; missing Next: lines fall back to phase order.",
                "The runtime infers per-phase tool allowlists from Allowed tools: lines and concrete <CALL> examples.",
                "The current phase may emit exactly one action per step: <CALL>, <NEXT>, or <ANSWER>.",
                "Bootstrap skills should not create a dedicated CONCLUDE or answer-only finalization phase.",
                "An unsupported <NEXT> target is rejected by the runtime.",
            ],
        }

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
                    "gold_answer": task.gold_answer,
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

    def _cap_preprocess_note(self, note: str, question: str) -> str:
        configured_cap = max(80, int(self.config.runtime.bootstrap_preprocess_note_char_cap))
        question_cap = max(220, min(configured_cap, len(question.strip()) + 120))
        cap = min(configured_cap, question_cap)
        cleaned = " ".join(str(note).split())
        if len(cleaned) <= cap:
            return cleaned
        return cleaned[: max(0, cap - 3)].rstrip() + "..."

    def _preprocess_batch_payload(
        self,
        *,
        runtime_contract: dict[str, Any],
        batch_payload: dict[str, Any],
        log_dir: Path,
    ) -> dict[str, Any]:
        if not self.config.runtime.bootstrap_task_preprocess:
            return batch_payload

        write_json(log_dir / "bootstrap_raw_batch_input.json", batch_payload)
        note_char_cap = max(80, int(self.config.runtime.bootstrap_preprocess_note_char_cap))
        system_prompt = render_prompt(self.config.prompt_root / "bootstrap_task_preprocess_system.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "bootstrap_task_preprocess_user.md",
            runtime_contract_json=json.dumps(runtime_contract, ensure_ascii=False, indent=2),
            batch_tasks_json=json.dumps(batch_payload, ensure_ascii=False, indent=2),
            note_char_cap=str(note_char_cap),
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "bootstrap_task_preprocess", llm_result)
        write_json(log_dir / "bootstrap_task_preprocess_response.json", payload)

        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError("Bootstrap task preprocess response must include items as a list.")
        notes_by_task_id: dict[str, str] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "")).strip()
            note = str(item.get("preprocess_note", "")).strip()
            if task_id and note:
                notes_by_task_id[task_id] = note

        preprocessed = dict(batch_payload)
        rows: list[dict[str, Any]] = []
        missing: list[str] = []
        for row in batch_payload.get("tasks", []):
            if not isinstance(row, dict):
                continue
            task_id = str(row.get("task_id", "")).strip()
            updated = dict(row)
            note = notes_by_task_id.get(task_id, "")
            if note:
                updated["preprocess_note"] = self._cap_preprocess_note(
                    note,
                    str(row.get("question", "")),
                )
            else:
                missing.append(task_id)
            rows.append(updated)
        if missing:
            raise ValueError(
                "Bootstrap task preprocess response missed task_id values: "
                + ", ".join(missing[:10])
                + (" ..." if len(missing) > 10 else "")
            )
        preprocessed["tasks"] = rows
        preprocessed["preprocess"] = {
            "enabled": True,
            "note_char_cap": note_char_cap,
            "field": "preprocess_note",
            "missing_count": len(missing),
        }
        write_json(log_dir / "bootstrap_preprocessed_batch_input.json", preprocessed)
        return preprocessed

    def bootstrap(self, tasks: list[DatasetTask], log_dir: Path) -> dict[str, Any]:
        ensure_dir(log_dir)
        runtime_contract = self._runtime_contract()
        raw_batch_payload = self._batch_payload(tasks)
        batch_payload = self._preprocess_batch_payload(
            runtime_contract=runtime_contract,
            batch_payload=raw_batch_payload,
            log_dir=log_dir,
        )
        write_json(
            log_dir / "bootstrap_input.json",
            {
                "runtime_contract": runtime_contract,
                "batch_task_snapshot": batch_payload,
            },
        )
        write_json(log_dir / "bootstrap_batch_input.json", batch_payload)

        system_prompt = render_prompt(self.config.prompt_root / "bootstrap_skill_system.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "bootstrap_skill_user.md",
            runtime_contract_json=json.dumps(runtime_contract, ensure_ascii=False, indent=2),
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
