from __future__ import annotations

import json
from pathlib import Path

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import ActorDecision, CriticReward, ExperienceEntry, LLMMessage, SkillDetail
from .skill_graph import graph_signature, graph_signature_from_phases
from .skills import (
    append_experience,
    load_experience_buffer,
    parse_skill_phases,
    retrieve_similar_experiences,
    write_skill_bundle,
)
from .utils import utc_timestamp, write_json


class SkillActor:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.actor)

    def act(
        self,
        reward: CriticReward,
        skill: SkillDetail,
        history_poll: list[dict],
        log_dir: Path,
    ) -> ActorDecision:
        experience_buffer = load_experience_buffer(self.config.experience_buffer_path)
        similar = retrieve_similar_experiences(experience_buffer, reward.experience_note or reward.natural_language_reward)

        graph_policy = self.config.runtime.actor_graph_edit_policy.strip().lower()
        use_graph_actor = graph_policy not in {"", "locked", "fixed", "fixed_graph"}
        use_graph_v3_actor = graph_policy in {"graph_v3", "graph-v3", "flow_batch_v3", "flow-batch-v3", "b3_graph", "b3-graph"}
        system_prompt_name = "actor_system.md"
        user_prompt_name = "actor_modify_skill.md"
        if use_graph_v3_actor:
            system_prompt_name = "actor_system_graph_v3.md"
            user_prompt_name = "actor_modify_skill_graph_v3.md"
        elif use_graph_actor:
            system_prompt_name = "actor_system_graph_b2.md"
            user_prompt_name = "actor_modify_skill_graph_b2.md"
        system_prompt = render_prompt(
            self.config.prompt_root / system_prompt_name
        )
        user_prompt = render_prompt(
            self.config.prompt_root / user_prompt_name,
            reward_json=json.dumps(reward.__dict__, ensure_ascii=False, indent=2),
            skill_json=json.dumps(
                {
                    "header": skill.header.__dict__,
                    "body": skill.body,
                    "phase_order": sorted(skill.phases.keys(), key=lambda n: skill.phases[n].order),
                    "resources": skill.resources,
                    "graph_signature": graph_signature(skill),
                },
                ensure_ascii=False,
                indent=2,
            ),
            graph_signature_json=json.dumps(graph_signature(skill), ensure_ascii=False, indent=2),
            history_poll_json=json.dumps(
                history_poll[-5:] if history_poll else [],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            experiences_json=json.dumps(
                [item.__dict__ for item in similar],
                ensure_ascii=False,
                indent=2,
            ),
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "actor_modify_skill", llm_result)

        target_name = str(payload.get("target_skill_name", skill.header.name))
        if target_name != skill.header.name:
            raise ValueError(
                f"Actor attempted to change skill name from {skill.header.name!r} to {target_name!r}."
            )
        files_to_write = {str(k): str(v) for k, v in payload.get("files_to_write", {}).items()}
        self._validate_graph_policy(skill, files_to_write, log_dir)
        entry_payload = payload.get("experience_entry", {}) if isinstance(payload.get("experience_entry", {}), dict) else {}
        experience_entry = ExperienceEntry(
            task_id="batch",
            failure_signature=str(entry_payload.get("failure_signature", reward.experience_note or reward.summary)),
            action_type="modify_skill",
            affected_skills=[target_name],
            modification_summary=str(entry_payload.get("modification_summary", payload.get("summary", ""))),
            reward_excerpt=reward.natural_language_reward[:500],
            created_at=utc_timestamp(),
        )

        decision = ActorDecision(
            action_type="modify_skill",
            summary=str(payload.get("summary", "")),
            target_skill_name=target_name,
            files_to_write=files_to_write,
            files_to_delete=[str(item) for item in payload.get("files_to_delete", [])],
            experience_entry=experience_entry,
            raw_model_output=json.dumps(payload, ensure_ascii=False, indent=2),
        )
        write_json(log_dir / "actor_decision.json", decision.__dict__ | {"experience_entry": experience_entry.__dict__})
        return decision

    def _validate_graph_policy(self, skill: SkillDetail, files_to_write: dict[str, str], log_dir: Path) -> None:
        skill_md = files_to_write.get("SKILL.md", "")
        if not skill_md.strip():
            raise ValueError("Actor must write a non-empty SKILL.md.")
        old_signature = graph_signature(skill)
        new_phases = parse_skill_phases(skill_md)
        if not new_phases:
            raise ValueError("Actor output SKILL.md must contain at least one phase.")
        new_signature = graph_signature_from_phases(new_phases)
        graph_policy = self.config.runtime.actor_graph_edit_policy.strip().lower() or "locked"
        graph_locked = graph_policy in {"locked", "fixed", "fixed_graph"}
        report = {
            "old_graph_signature": old_signature,
            "new_graph_signature": new_signature,
            "graph_unchanged": old_signature == new_signature,
            "graph_edit_policy": graph_policy,
            "graph_lock_enforced": graph_locked,
        }
        write_json(log_dir / "actor_graph_lock_check.json", report)
        if graph_locked and old_signature != new_signature:
            raise ValueError(
                "Actor graph lock violation: phase order or Next target set changed. "
                "The actor may only modify common text and node-local instructions."
            )

    def apply(self, decision: ActorDecision) -> None:
        if not decision.target_skill_name:
            raise ValueError("modify_skill requires target_skill_name")
        write_skill_bundle(self.config.skill_library_root, decision.target_skill_name, decision.files_to_write)
        if decision.experience_entry is not None:
            append_experience(self.config.experience_buffer_path, decision.experience_entry)
