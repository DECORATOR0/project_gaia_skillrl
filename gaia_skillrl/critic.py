from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import CriticReward, EnvState, LLMMessage, SkillDetail
from .skill_graph import graph_signature
from .utils import write_json


class SkillCritic:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.critic)

    _PROTOCOL_OUTCOMES = {
        "invalid_answer_phase",
        "invalid_phase_transition",
        "tool_call_rejected",
        "unrecognized_action",
        "unknown_phase",
    }
    _MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".mp3", ".wav", ".mp4", ".pdf"}
    _LOCAL_SUFFIXES = {".xlsx", ".xls", ".csv", ".docx", ".pptx", ".zip", ".json", ".jsonld", ".txt"}
    _MEDIA_TOOLS = {"ocr_image", "image_qa", "audio_transcribe", "extract_pdf_text", "image_metadata"}
    _LOCAL_TOOLS = {"read_file", "read_table", "parse_docx", "parse_pptx", "extract_archive"}
    _WEB_TOOLS = {"web_search", "fetch_url", "html_extract"}

    @staticmethod
    def _phase_path(transitions: list[Any]) -> list[str]:
        if not transitions:
            return ["INIT"]
        path = [transitions[0].from_phase]
        for transition in transitions:
            if transition.to_phase != path[-1]:
                path.append(transition.to_phase)
        return path

    def _compact_row(self, state: EnvState) -> dict[str, Any]:
        action_trace = state.env_result.action_trace
        outcome_counts = Counter(record.outcome for record in action_trace if record.outcome)
        step_count = max((record.step_index for record in action_trace), default=0)
        tools: list[dict[str, Any]] = []
        for record in state.env_result.tool_trajectory:
            item: dict[str, Any] = {
                "step": record.step_index,
                "tool": record.tool_name,
                "success": record.success,
            }
            if not record.success and record.error:
                item["error"] = record.error[:200]
            tools.append(item)
        final_answer = state.env_result.final_answer or ""
        return {
            "task_id": state.task_id,
            "level": state.task_context.get("level"),
            "task_prompt": state.task_prompt,
            "file_list": state.task_context.get("file_list", []),
            "gold_answer": state.task_context.get("gold_answer", ""),
            "final_answer": final_answer,
            "success": state.env_result.evaluation.task_success,
            "step_count": step_count,
            "empty_answer": not final_answer.strip(),
            "max_step_hit": step_count >= self.config.runtime.max_executor_steps,
            "outcome_counts": dict(sorted(outcome_counts.items())),
            "path": self._phase_path(state.env_result.phase_transitions),
            "tools": tools,
        }

    def _serialize_batch_states(self, states: list[EnvState]) -> list[dict]:
        return [self._compact_row(state) for state in states]

    def _family_for_row(self, row: dict[str, Any]) -> str:
        if row.get("success"):
            return "success_anchor"
        outcome_counts = row.get("outcome_counts", {})
        if row.get("empty_answer") or row.get("max_step_hit"):
            return "no_answer_or_timeout"
        if any(int(outcome_counts.get(name, 0) or 0) > 0 for name in self._PROTOCOL_OUTCOMES):
            return "protocol_friction"
        suffixes = {Path(name).suffix.lower() for name in row.get("file_list", []) if name != "task.json"}
        tools = {item.get("tool") for item in row.get("tools", [])}
        if suffixes & self._MEDIA_SUFFIXES or tools & self._MEDIA_TOOLS:
            return "media_attachment_failure"
        if suffixes & self._LOCAL_SUFFIXES or tools & self._LOCAL_TOOLS:
            return "local_attachment_failure"
        if tools & self._WEB_TOOLS:
            return "web_retrieval_failure"
        if str(row.get("final_answer", "")).strip():
            return "answer_synthesis_or_format_failure"
        return "other_failure"

    def _make_shards(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            family = self._family_for_row(row)
            row = dict(row)
            row["family"] = family
            groups[family].append(row)
        family_order = [
            "success_anchor",
            "no_answer_or_timeout",
            "protocol_friction",
            "media_attachment_failure",
            "local_attachment_failure",
            "web_retrieval_failure",
            "answer_synthesis_or_format_failure",
            "other_failure",
        ]
        shard_size = max(1, self.config.runtime.critic_shard_size)
        shards: list[dict[str, Any]] = []
        shard_index = 1
        for family in family_order:
            items = groups.get(family, [])
            for start in range(0, len(items), shard_size):
                shard_rows = items[start:start + shard_size]
                if not shard_rows:
                    continue
                shards.append({
                    "shard_id": f"{shard_index:02d}_{family}",
                    "family": family,
                    "rows": shard_rows,
                })
                shard_index += 1
        return shards

    def _skill_payload(self, skill: SkillDetail) -> dict[str, Any]:
        return {
            "header": skill.header.__dict__,
            "body": skill.body,
            "phase_order": sorted(skill.phases.keys(), key=lambda n: skill.phases[n].order),
            "resources": skill.resources,
            "graph_signature": graph_signature(skill),
        }

    def _call_critic(
        self,
        *,
        mode: str,
        batch_rows: list[dict[str, Any]],
        skill: SkillDetail,
        history_poll: list[dict],
        log_dir: Path,
        log_name: str,
        shard_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system_prompt = render_prompt(self.config.prompt_root / "critic_system.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "critic_user.md",
            critic_mode=mode,
            batch_rows_json=json.dumps(batch_rows, ensure_ascii=False, indent=2, default=str),
            skill_json=json.dumps(self._skill_payload(skill), ensure_ascii=False, indent=2),
            graph_signature_json=json.dumps(graph_signature(skill), ensure_ascii=False, indent=2),
            history_poll_json=json.dumps(
                history_poll[-5:] if history_poll else [],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            shard_context_json=json.dumps(shard_context or {}, ensure_ascii=False, indent=2, default=str),
        )
        payload, llm_result = self.llm.chat_json([
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ])
        log_llm_call(log_dir, log_name, llm_result)
        write_json(log_dir / f"{log_name}_response.json", payload)
        return payload

    def _aggregate_shard_payloads(
        self,
        *,
        shard_payloads: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        skill: SkillDetail,
        history_poll: list[dict],
        log_dir: Path,
    ) -> dict[str, Any]:
        system_prompt = render_prompt(self.config.prompt_root / "critic_system.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "critic_aggregate_user.md",
            shard_findings_json=json.dumps(shard_payloads, ensure_ascii=False, indent=2, default=str),
            batch_overview_json=json.dumps(self._batch_overview(rows), ensure_ascii=False, indent=2, default=str),
            skill_json=json.dumps(self._skill_payload(skill), ensure_ascii=False, indent=2),
            graph_signature_json=json.dumps(graph_signature(skill), ensure_ascii=False, indent=2),
            history_poll_json=json.dumps(
                history_poll[-5:] if history_poll else [],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )
        payload, llm_result = self.llm.chat_json([
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ])
        log_llm_call(log_dir, "critic_sharded_aggregate", llm_result)
        write_json(log_dir / "critic_sharded_aggregate_response.json", payload)
        return payload

    @staticmethod
    def _batch_overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
        success_count = sum(1 for row in rows if row.get("success"))
        family_counts = Counter(row.get("family", "unassigned") for row in rows)
        outcome_counts: Counter[str] = Counter()
        tool_counts: Counter[str] = Counter()
        for row in rows:
            outcome_counts.update(row.get("outcome_counts", {}))
            for tool in row.get("tools", []):
                if tool.get("tool"):
                    tool_counts[str(tool["tool"])] += 1
        return {
            "task_count": len(rows),
            "success_count": success_count,
            "failure_count": len(rows) - success_count,
            "family_counts": dict(sorted(family_counts.items())),
            "outcome_counts": dict(outcome_counts.most_common()),
            "tool_counts": dict(tool_counts.most_common()),
        }

    def evaluate_batch(
        self,
        states: list[EnvState],
        skill: SkillDetail,
        history_poll: list[dict],
        log_dir: Path,
    ) -> CriticReward:
        rows = self._serialize_batch_states(states)
        strategy = self.config.runtime.critic_strategy.strip().lower()
        write_json(log_dir / "critic_compact_rows.json", rows)
        if strategy in {"sharded", "family_sharded", "family-sharded"}:
            shards = self._make_shards(rows)
            write_json(log_dir / "critic_shards.json", shards)
            shard_payloads: list[dict[str, Any]] = []
            for shard in shards:
                payload = self._call_critic(
                    mode="shard",
                    batch_rows=shard["rows"],
                    skill=skill,
                    history_poll=history_poll,
                    log_dir=log_dir,
                    log_name=f"critic_shard_{shard['shard_id']}",
                    shard_context={
                        "shard_id": shard["shard_id"],
                        "family": shard["family"],
                        "task_count": len(shard["rows"]),
                    },
                )
                payload = dict(payload)
                payload["shard_id"] = shard["shard_id"]
                payload["family"] = shard["family"]
                shard_payloads.append(payload)
            payload = self._aggregate_shard_payloads(
                shard_payloads=shard_payloads,
                rows=[row for shard in shards for row in shard["rows"]],
                skill=skill,
                history_poll=history_poll,
                log_dir=log_dir,
            )
        else:
            payload = self._call_critic(
                mode="full",
                batch_rows=rows,
                skill=skill,
                history_poll=history_poll,
                log_dir=log_dir,
                log_name="critic",
            )
        reward = CriticReward(
            natural_language_reward=str(payload.get("natural_language_reward", "")),
            reward_dimensions=payload.get("reward_dimensions", {}) if isinstance(payload.get("reward_dimensions", {}), dict) else {},
            experience_note=str(payload.get("experience_note", "")),
            summary=str(payload.get("summary", "")),
        )
        write_json(log_dir / "reward.json", reward.__dict__)
        return reward
