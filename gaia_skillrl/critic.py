from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    _COMPUTE_TOOLS = {"run_python"}
    _REWARD_WEIGHTS = {
        "task_success": 1.0,
        "nonempty_answer": 0.25,
        "no_protocol_error": 0.15,
        "before_max_step": 0.10,
        "step_count": -0.01,
        "tool_rejected": -0.05,
        "invalid_transition": -0.08,
        "repeated_edge_extra": -0.10,
    }

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

    def _enhanced_row(self, state: EnvState) -> dict[str, Any]:
        row = self._compact_row(state)
        action_trace = state.env_result.action_trace
        step_phase = {record.step_index: record.phase_before for record in action_trace}
        phase_dwell = Counter(record.phase_before for record in action_trace if record.phase_before)
        phase_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
        tools_by_phase: dict[str, Counter[str]] = defaultdict(Counter)
        tool_failures_by_phase: dict[str, Counter[str]] = defaultdict(Counter)
        invalid_next_by_phase: Counter[str] = Counter()
        invalid_next_targets_by_phase: dict[str, Counter[str]] = defaultdict(Counter)
        invalid_tool_by_phase: Counter[str] = Counter()
        answer_events: list[dict[str, Any]] = []
        last_events: list[str] = []

        for record in action_trace:
            phase = record.phase_before or "UNKNOWN"
            if record.outcome:
                phase_outcomes[phase][record.outcome] += 1
            if record.tool_name:
                tools_by_phase[phase][record.tool_name] += 1
                if not record.success:
                    tool_failures_by_phase[phase][record.tool_name] += 1
            if record.outcome in {"invalid_phase_transition", "unknown_phase"}:
                invalid_next_by_phase[phase] += 1
                if record.next_phase:
                    invalid_next_targets_by_phase[phase][record.next_phase] += 1
            if record.outcome == "tool_call_rejected":
                invalid_tool_by_phase[phase] += 1
            if record.action_type == "answer" or str(record.outcome).startswith("final_answer"):
                answer_events.append(
                    {
                        "step": record.step_index,
                        "phase": phase,
                        "accepted": record.accepted,
                        "outcome": record.outcome,
                    }
                )

        for record in action_trace[-6:]:
            action = record.action_type or "none"
            target = record.tool_name or record.next_phase or ("answer" if record.answer else "")
            outcome = record.outcome or ("accepted" if record.accepted else "")
            last_events.append(
                f"{record.step_index}:{record.phase_before}->{record.phase_after} {action} {target} {outcome}".strip()
            )

        edge_counts: Counter[str] = Counter()
        edge_first_steps: dict[str, int] = {}
        for transition in state.env_result.phase_transitions:
            edge = f"{transition.from_phase}->{transition.to_phase}"
            edge_counts[edge] += 1
            edge_first_steps.setdefault(edge, transition.step_index)

        row["graph_features"] = {
            "phase_dwell": dict(sorted(phase_dwell.items())),
            "edge_counts": dict(sorted(edge_counts.items())),
            "edge_first_steps": dict(sorted(edge_first_steps.items())),
            "repeated_edges": {edge: count for edge, count in sorted(edge_counts.items()) if count > 1},
            "tools_by_phase": {
                phase: dict(counter.most_common())
                for phase, counter in sorted(tools_by_phase.items())
            },
            "tool_failures_by_phase": {
                phase: dict(counter.most_common())
                for phase, counter in sorted(tool_failures_by_phase.items())
                if counter
            },
            "phase_outcomes": {
                phase: dict(counter.most_common())
                for phase, counter in sorted(phase_outcomes.items())
                if counter
            },
            "invalid_next_by_phase": dict(sorted(invalid_next_by_phase.items())),
            "invalid_next_targets_by_phase": {
                phase: dict(counter.most_common())
                for phase, counter in sorted(invalid_next_targets_by_phase.items())
                if counter
            },
            "invalid_tool_by_phase": dict(sorted(invalid_tool_by_phase.items())),
            "answer_events": answer_events,
            "forced_answer_candidate": bool(row.get("max_step_hit") and str(row.get("final_answer", "")).strip()),
            "last_events": last_events,
        }
        return row

    def _serialize_batch_states(self, states: list[EnvState]) -> list[dict]:
        return [self._compact_row(state) for state in states]

    def _serialize_enhanced_batch_states(self, states: list[EnvState]) -> list[dict]:
        return [self._enhanced_row(state) for state in states]

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

    @staticmethod
    def _mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    @staticmethod
    def _support_label(support: int) -> str:
        if support < 3:
            return "low_support"
        if support < 8:
            return "weak_signal"
        return "usable_signal"

    @staticmethod
    def _section_value(content: str, section: str) -> str:
        pattern = re.compile(rf"^\s*{re.escape(section)}:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
        match = pattern.search(content)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _split_tools(raw: str) -> list[str]:
        if not raw:
            return []
        return [item.strip().strip("`") for item in raw.split(",") if item.strip()]

    @staticmethod
    def _phase_without_rules(content: str) -> str:
        section_re = re.compile(r"^\s*(Goal|Allowed tools|Rules|Exit handoff|Available actions|Next):", re.IGNORECASE)
        kept: list[str] = []
        skipping_rules = False
        for line in content.splitlines():
            section_match = section_re.match(line)
            if section_match and section_match.group(1).lower() == "rules":
                skipping_rules = True
                continue
            if skipping_rules and section_match:
                skipping_rules = False
            if not skipping_rules:
                kept.append(line.rstrip())
        return "\n".join(kept).strip()

    def _skill_graph_view(self, skill: SkillDetail) -> dict[str, Any]:
        signature = graph_signature(skill)
        phase_order = signature.get("phase_order", [])
        phases = []
        for name in phase_order:
            phase = skill.phases.get(name)
            if phase is None:
                continue
            content_without_rules = self._phase_without_rules(phase.content)
            purpose = self._section_value(phase.content, "Goal")
            if not purpose:
                purpose = next(
                    (
                        line.strip()
                        for line in content_without_rules.splitlines()
                        if line.strip()
                        and not re.match(r"^\s*(Allowed tools|Exit handoff|Available actions|Next):", line, re.IGNORECASE)
                    ),
                    "",
                )
            phases.append({
                "name": name,
                "order": phase.order,
                "purpose_digest": purpose[:500],
                "allowed_tools": self._split_tools(self._section_value(phase.content, "Allowed tools")),
                "next": signature.get("graph", {}).get(name, []),
                "content_without_rules": content_without_rules,
            })
        common_preamble = skill.body.split("## Phase:", 1)[0].strip()
        skill_markdown_without_rules = "\n\n".join(
            [common_preamble] if common_preamble else []
        )
        phase_blocks = [
            f"## Phase: {item['name']}\n{item['content_without_rules']}".strip()
            for item in phases
        ]
        if phase_blocks:
            skill_markdown_without_rules = (
                skill_markdown_without_rules + "\n\n" if skill_markdown_without_rules else ""
            ) + "\n\n".join(phase_blocks)
        return {
            "skill_header": skill.header.__dict__,
            "global_protocol": [
                "Each executor step emits exactly one CALL, NEXT, or non-empty ANSWER.",
                "NEXT must target a phase listed in the current phase Next set.",
                "A non-empty ANSWER is accepted from any phase under the current answer policy.",
                "The skill has no CONCLUDE phase.",
                "The final answer must be a short non-empty string inside <ANSWER>...</ANSWER>.",
            ],
            "phase_order": phase_order,
            "phases": phases,
            "edges": signature.get("edges", []),
            "graph": signature.get("graph", {}),
            "skill_markdown_without_rules": skill_markdown_without_rules,
        }

    def _trajectory_reward(self, row: dict[str, Any]) -> dict[str, Any]:
        graph_features = row.get("graph_features", {}) if isinstance(row.get("graph_features", {}), dict) else {}
        outcome_counts = row.get("outcome_counts", {}) if isinstance(row.get("outcome_counts", {}), dict) else {}
        repeated_extra = sum(max(0, int(count or 0) - 1) for count in graph_features.get("repeated_edges", {}).values())
        invalid_transition_count = (
            int(outcome_counts.get("invalid_phase_transition", 0) or 0)
            + int(outcome_counts.get("unknown_phase", 0) or 0)
        )
        tool_rejected_count = int(outcome_counts.get("tool_call_rejected", 0) or 0)
        step_count = int(row.get("step_count", 0) or 0)
        components = {
            "task_success": int(bool(row.get("success"))),
            "nonempty_answer": int(not row.get("empty_answer")),
            "no_protocol_error": int(not any(int(outcome_counts.get(name, 0) or 0) for name in self._PROTOCOL_OUTCOMES)),
            "before_max_step": int(not row.get("max_step_hit")),
            "step_count": step_count,
            "tool_rejected": tool_rejected_count,
            "invalid_transition": invalid_transition_count,
            "repeated_edge_extra": repeated_extra,
        }
        reward = (
            self._REWARD_WEIGHTS["task_success"] * components["task_success"]
            + self._REWARD_WEIGHTS["nonempty_answer"] * components["nonempty_answer"]
            + self._REWARD_WEIGHTS["no_protocol_error"] * components["no_protocol_error"]
            + self._REWARD_WEIGHTS["before_max_step"] * components["before_max_step"]
            + self._REWARD_WEIGHTS["step_count"] * components["step_count"]
            + self._REWARD_WEIGHTS["tool_rejected"] * components["tool_rejected"]
            + self._REWARD_WEIGHTS["invalid_transition"] * components["invalid_transition"]
            + self._REWARD_WEIGHTS["repeated_edge_extra"] * components["repeated_edge_extra"]
        )
        return {
            "reward": round(reward, 4),
            "trajectory_success": bool(
                components["nonempty_answer"]
                and components["no_protocol_error"]
                and components["before_max_step"]
            ),
            "components": components,
        }

    def _task_tags(self, row: dict[str, Any]) -> list[str]:
        graph_features = row.get("graph_features", {}) if isinstance(row.get("graph_features", {}), dict) else {}
        suffixes = {Path(name).suffix.lower() for name in row.get("file_list", []) if name != "task.json"}
        tools = {item.get("tool") for item in row.get("tools", []) if item.get("tool")}
        path = row.get("path", [])
        tags: set[str] = set()
        if suffixes:
            tags.add("has_attachment")
        if suffixes & self._MEDIA_SUFFIXES or tools & self._MEDIA_TOOLS:
            tags.add("needs_media")
        if suffixes & self._LOCAL_SUFFIXES or tools & self._LOCAL_TOOLS:
            tags.add("needs_local_file")
        if tools & self._WEB_TOOLS or "WEB_EVIDENCE" in path:
            tags.add("needs_web")
        if tools & self._COMPUTE_TOOLS or "COMPUTE" in path:
            tags.add("needs_compute")
        if not tags:
            tags.add("prompt_only_or_unknown")
        if len(set(path)) >= 4:
            tags.add("multi_phase")
        if graph_features.get("repeated_edges"):
            tags.add("loop_or_revisit")
        if row.get("max_step_hit"):
            tags.add("max_step_hit")
        if row.get("empty_answer"):
            tags.add("empty_answer")
        return sorted(tags)

    def _task_unit_table(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        units: list[dict[str, Any]] = []
        for row in rows:
            graph_features = row.get("graph_features", {}) if isinstance(row.get("graph_features", {}), dict) else {}
            reward = self._trajectory_reward(row)
            outcome_counts = row.get("outcome_counts", {}) if isinstance(row.get("outcome_counts", {}), dict) else {}
            major_failure_flags = []
            if row.get("max_step_hit"):
                major_failure_flags.append("max_step_hit")
            if row.get("empty_answer"):
                major_failure_flags.append("empty_answer")
            if graph_features.get("repeated_edges"):
                major_failure_flags.append("loop_or_revisit")
            if any(int(outcome_counts.get(name, 0) or 0) for name in self._PROTOCOL_OUTCOMES):
                major_failure_flags.append("protocol_friction")
            file_types = sorted(
                {
                    Path(name).suffix.lower()
                    for name in row.get("file_list", [])
                    if name != "task.json" and Path(name).suffix
                }
            )
            units.append({
                "task_id": row.get("task_id"),
                "level": row.get("level"),
                "family": row.get("family", self._family_for_row(row)),
                "tags": self._task_tags(row),
                "file_types": file_types,
                "task_success": bool(row.get("success")),
                "trajectory_success": reward["trajectory_success"],
                "trajectory_reward": reward["reward"],
                "final_answer_empty": bool(row.get("empty_answer")),
                "max_step_hit": bool(row.get("max_step_hit")),
                "step_count": int(row.get("step_count", 0) or 0),
                "path": row.get("path", []),
                "edge_repeats": graph_features.get("repeated_edges", {}),
                "phase_dwell": graph_features.get("phase_dwell", {}),
                "major_failure_flags": major_failure_flags,
            })
        return units

    def _graph_v3_value_profiles(self, rows: list[dict[str, Any]], skill: SkillDetail) -> dict[str, Any]:
        signature = graph_signature(skill)
        row_rewards = [self._trajectory_reward(row) for row in rows]
        reward_values = [float(item["reward"]) for item in row_rewards]
        all_indices = set(range(len(rows)))
        phase_rows: dict[str, set[int]] = defaultdict(set)
        phase_dwell: dict[str, int] = defaultdict(int)
        edge_rows: dict[str, set[int]] = defaultdict(set)
        edge_uses: Counter[str] = Counter()
        edge_repeats: Counter[str] = Counter()
        edge_remaining_steps: dict[str, list[int]] = defaultdict(list)
        missing_targets: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        path_stats: dict[str, dict[str, Any]] = {}

        for index, row in enumerate(rows):
            path = [str(item) for item in row.get("path", [])]
            path_key = "->".join(path) if path else "EMPTY_PATH"
            path_item = path_stats.setdefault(path_key, {
                "path": path,
                "support": 0,
                "success_count": 0,
                "max_step_count": 0,
                "reward_values": [],
                "task_ids": [],
            })
            path_item["support"] += 1
            path_item["success_count"] += int(bool(row.get("success")))
            path_item["max_step_count"] += int(bool(row.get("max_step_hit")))
            path_item["reward_values"].append(reward_values[index])
            path_item["task_ids"].append(row.get("task_id"))

            graph_features = row.get("graph_features", {}) if isinstance(row.get("graph_features", {}), dict) else {}
            for phase in set(path):
                phase_rows[phase].add(index)
            for phase, dwell in graph_features.get("phase_dwell", {}).items():
                phase_dwell[str(phase)] += int(dwell or 0)
            for edge, count in graph_features.get("edge_counts", {}).items():
                count_int = int(count or 0)
                if count_int <= 0:
                    continue
                edge = str(edge)
                edge_rows[edge].add(index)
                edge_uses[edge] += count_int
            for edge, count in graph_features.get("repeated_edges", {}).items():
                edge_repeats[str(edge)] += max(0, int(count or 0) - 1)
            for edge, first_step in graph_features.get("edge_first_steps", {}).items():
                try:
                    remaining = max(0, int(row.get("step_count", 0) or 0) - int(first_step or 0))
                    edge_remaining_steps[str(edge)].append(remaining)
                except Exception:
                    continue
            for from_phase, targets in graph_features.get("invalid_next_targets_by_phase", {}).items():
                if not isinstance(targets, dict):
                    continue
                for target, count in targets.items():
                    missing_targets[(str(from_phase), str(target))][str(row.get("task_id"))] += int(count or 0)

        existing_edges = {f"{item['from']}->{item['to']}" for item in signature.get("edges", [])}
        edge_value_profile: list[dict[str, Any]] = []
        for edge in sorted(existing_edges | set(edge_rows)):
            source = edge.split("->", 1)[0] if "->" in edge else ""
            rows_with_edge = edge_rows.get(edge, set())
            source_rows = phase_rows.get(source, set())
            sibling_rows = source_rows - rows_with_edge
            edge_reward_values = [reward_values[index] for index in rows_with_edge]
            source_reward_values = [reward_values[index] for index in source_rows]
            sibling_reward_values = [reward_values[index] for index in sibling_rows]
            support = len(rows_with_edge)
            process_reward_mean = self._mean(edge_reward_values)
            sibling_baseline = self._mean(sibling_reward_values)
            source_baseline = self._mean(source_reward_values)
            baseline = sibling_baseline if sibling_reward_values else source_baseline
            edge_value_profile.append({
                "edge": edge,
                "support": support,
                "uses": edge_uses.get(edge, 0),
                "task_success_rate": round(
                    sum(1 for index in rows_with_edge if rows[index].get("success")) / support,
                    4,
                ) if support else 0.0,
                "trajectory_reward_mean": process_reward_mean,
                "source_baseline_reward": source_baseline,
                "sibling_baseline_reward": sibling_baseline,
                "advantage": round(process_reward_mean - baseline, 4) if support else 0.0,
                "max_step_rate": round(
                    sum(1 for index in rows_with_edge if rows[index].get("max_step_hit")) / support,
                    4,
                ) if support else 0.0,
                "repeat_extra_count": edge_repeats.get(edge, 0),
                "repeat_rate": round(edge_repeats.get(edge, 0) / max(1, edge_uses.get(edge, 0)), 4),
                "avg_remaining_steps_after_first_edge": self._mean([float(item) for item in edge_remaining_steps.get(edge, [])]),
                "confidence": self._support_label(support),
                "example_task_ids": [rows[index].get("task_id") for index in sorted(rows_with_edge)[:6]],
            })

        phase_value_profile: list[dict[str, Any]] = []
        for phase in sorted(set(signature.get("phase_order", [])) | set(phase_rows)):
            rows_with_phase = phase_rows.get(phase, set())
            rows_without_phase = all_indices - rows_with_phase
            support = len(rows_with_phase)
            phase_reward = self._mean([reward_values[index] for index in rows_with_phase])
            baseline = self._mean([reward_values[index] for index in rows_without_phase])
            phase_value_profile.append({
                "phase": phase,
                "visited_rows": support,
                "task_success_rate_when_visited": round(
                    sum(1 for index in rows_with_phase if rows[index].get("success")) / support,
                    4,
                ) if support else 0.0,
                "trajectory_reward_mean": phase_reward,
                "not_visited_baseline_reward": baseline,
                "advantage": round(phase_reward - baseline, 4) if support else 0.0,
                "avg_dwell_steps_when_visited": round(phase_dwell.get(phase, 0) / support, 2) if support else 0.0,
                "max_step_rate_when_visited": round(
                    sum(1 for index in rows_with_phase if rows[index].get("max_step_hit")) / support,
                    4,
                ) if support else 0.0,
                "confidence": self._support_label(support),
                "example_task_ids": [rows[index].get("task_id") for index in sorted(rows_with_phase)[:6]],
            })

        loop_profile = []
        for edge, repeat_count in edge_repeats.most_common():
            rows_with_edge = edge_rows.get(edge, set())
            support = len(rows_with_edge)
            loop_profile.append({
                "edge": edge,
                "support": support,
                "repeat_extra_count": repeat_count,
                "task_success_rate": round(
                    sum(1 for index in rows_with_edge if rows[index].get("success")) / support,
                    4,
                ) if support else 0.0,
                "trajectory_reward_mean": self._mean([reward_values[index] for index in rows_with_edge]),
                "max_step_rate": round(
                    sum(1 for index in rows_with_edge if rows[index].get("max_step_hit")) / support,
                    4,
                ) if support else 0.0,
                "example_task_ids": [rows[index].get("task_id") for index in sorted(rows_with_edge)[:6]],
            })

        missing_transition_profile = [
            {
                "from_phase": from_phase,
                "attempted_to": attempted_to,
                "invalid_count": sum(counter.values()),
                "example_task_ids": list(counter.keys())[:6],
                "confidence": self._support_label(len(counter)),
            }
            for (from_phase, attempted_to), counter in sorted(
                missing_targets.items(),
                key=lambda item: sum(item[1].values()),
                reverse=True,
            )
        ]

        path_profile = []
        for item in sorted(path_stats.values(), key=lambda value: value["support"], reverse=True):
            support = int(item["support"])
            path_profile.append({
                "path": item["path"],
                "support": support,
                "task_success_rate": round(item["success_count"] / support, 4) if support else 0.0,
                "max_step_rate": round(item["max_step_count"] / support, 4) if support else 0.0,
                "trajectory_reward_mean": self._mean([float(value) for value in item["reward_values"]]),
                "example_task_ids": item["task_ids"][:8],
            })

        return {
            "reward_spec": {
                "description": "Heuristic process reward used only to rank trajectory, phase, and edge signals for the critic.",
                "weights": self._REWARD_WEIGHTS,
                "support_labels": {
                    "low_support": "support < 3",
                    "weak_signal": "3 <= support < 8",
                    "usable_signal": "support >= 8",
                },
            },
            "global_score": {
                "task_count": len(rows),
                "task_success_count": sum(1 for row in rows if row.get("success")),
                "trajectory_success_count": sum(1 for item in row_rewards if item["trajectory_success"]),
                "max_step_hit_count": sum(1 for row in rows if row.get("max_step_hit")),
                "empty_answer_count": sum(1 for row in rows if row.get("empty_answer")),
                "avg_steps": self._mean([float(row.get("step_count", 0) or 0) for row in rows]),
                "avg_trajectory_reward": self._mean(reward_values),
            },
            "edge_value_profile": sorted(
                edge_value_profile,
                key=lambda item: (item["confidence"] != "usable_signal", item["advantage"], -item["support"]),
            ),
            "phase_value_profile": sorted(phase_value_profile, key=lambda item: item["phase"]),
            "loop_profile": loop_profile,
            "missing_transition_profile": missing_transition_profile,
            "path_profile": path_profile,
        }

    @staticmethod
    def _graph_v3_representative_cases(value_profiles: dict[str, Any]) -> dict[str, Any]:
        edges = value_profiles.get("edge_value_profile", [])
        phases = value_profiles.get("phase_value_profile", [])
        return {
            "top_negative_edges": [
                {
                    "edge": item.get("edge"),
                    "advantage": item.get("advantage"),
                    "support": item.get("support"),
                    "max_step_rate": item.get("max_step_rate"),
                    "why_selected": "negative or weak edge value signal",
                    "task_ids": item.get("example_task_ids", []),
                }
                for item in sorted(edges, key=lambda value: (value.get("advantage", 0), -value.get("support", 0)))[:8]
                if item.get("support", 0) > 0
            ],
            "top_positive_edges": [
                {
                    "edge": item.get("edge"),
                    "advantage": item.get("advantage"),
                    "support": item.get("support"),
                    "task_success_rate": item.get("task_success_rate"),
                    "why_selected": "positive edge value / success anchor candidate",
                    "task_ids": item.get("example_task_ids", []),
                }
                for item in sorted(edges, key=lambda value: (value.get("advantage", 0), value.get("support", 0)), reverse=True)[:8]
                if item.get("support", 0) > 0
            ],
            "high_cost_phases": [
                {
                    "phase": item.get("phase"),
                    "visited_rows": item.get("visited_rows"),
                    "advantage": item.get("advantage"),
                    "avg_dwell_steps_when_visited": item.get("avg_dwell_steps_when_visited"),
                    "max_step_rate_when_visited": item.get("max_step_rate_when_visited"),
                    "task_ids": item.get("example_task_ids", []),
                }
                for item in sorted(
                    phases,
                    key=lambda value: (
                        value.get("max_step_rate_when_visited", 0),
                        value.get("avg_dwell_steps_when_visited", 0),
                        -value.get("advantage", 0),
                    ),
                    reverse=True,
                )[:8]
                if item.get("visited_rows", 0) > 0
            ],
        }

    @staticmethod
    def _graph_profile(rows: list[dict[str, Any]], skill: SkillDetail) -> dict[str, Any]:
        signature = graph_signature(skill)
        node_visits: dict[str, int] = defaultdict(int)
        node_successes: dict[str, int] = defaultdict(int)
        node_max_steps: dict[str, int] = defaultdict(int)
        node_dwell: dict[str, int] = defaultdict(int)
        node_tools: dict[str, Counter[str]] = defaultdict(Counter)
        node_tool_failures: dict[str, Counter[str]] = defaultdict(Counter)
        node_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
        edge_uses: Counter[str] = Counter()
        edge_rows: Counter[str] = Counter()
        edge_successes: Counter[str] = Counter()
        edge_max_steps: Counter[str] = Counter()
        edge_repeats: Counter[str] = Counter()
        answer_phase_counts: Counter[str] = Counter()

        for row in rows:
            graph_features = row.get("graph_features", {}) if isinstance(row.get("graph_features", {}), dict) else {}
            phases = set(row.get("path", []))
            for phase in phases:
                node_visits[phase] += 1
                node_successes[phase] += int(bool(row.get("success")))
                node_max_steps[phase] += int(bool(row.get("max_step_hit")))
            for phase, dwell in graph_features.get("phase_dwell", {}).items():
                node_dwell[phase] += int(dwell or 0)
            for phase, tools in graph_features.get("tools_by_phase", {}).items():
                node_tools[phase].update({tool: int(count or 0) for tool, count in tools.items()})
            for phase, tools in graph_features.get("tool_failures_by_phase", {}).items():
                node_tool_failures[phase].update({tool: int(count or 0) for tool, count in tools.items()})
            for phase, outcomes in graph_features.get("phase_outcomes", {}).items():
                node_outcomes[phase].update({name: int(count or 0) for name, count in outcomes.items()})
            for event in graph_features.get("answer_events", []):
                if isinstance(event, dict):
                    answer_phase_counts[str(event.get("phase", "UNKNOWN"))] += 1
            for edge, count in graph_features.get("edge_counts", {}).items():
                count_int = int(count or 0)
                edge_uses[edge] += count_int
                edge_rows[edge] += 1
                edge_successes[edge] += int(bool(row.get("success")))
                edge_max_steps[edge] += int(bool(row.get("max_step_hit")))
                if count_int > 1:
                    edge_repeats[edge] += count_int - 1

        node_stats = {}
        for phase in signature.get("phase_order", []):
            visits = node_visits.get(phase, 0)
            node_stats[phase] = {
                "visited_rows": visits,
                "success_rate_when_visited": round(node_successes.get(phase, 0) / visits, 4) if visits else 0.0,
                "max_step_rate_when_visited": round(node_max_steps.get(phase, 0) / visits, 4) if visits else 0.0,
                "avg_dwell_steps_when_visited": round(node_dwell.get(phase, 0) / visits, 2) if visits else 0.0,
                "tools": dict(node_tools.get(phase, Counter()).most_common(12)),
                "tool_failures": dict(node_tool_failures.get(phase, Counter()).most_common(12)),
                "outcomes": dict(node_outcomes.get(phase, Counter()).most_common(12)),
            }

        edge_stats = {}
        for edge in sorted(set(edge_uses) | {f"{item['from']}->{item['to']}" for item in signature.get("edges", [])}):
            rows_with_edge = edge_rows.get(edge, 0)
            edge_stats[edge] = {
                "uses": edge_uses.get(edge, 0),
                "rows_with_edge": rows_with_edge,
                "success_rate_when_used": round(edge_successes.get(edge, 0) / rows_with_edge, 4) if rows_with_edge else 0.0,
                "max_step_rate_when_used": round(edge_max_steps.get(edge, 0) / rows_with_edge, 4) if rows_with_edge else 0.0,
                "repeat_extra_count": edge_repeats.get(edge, 0),
            }

        return {
            "graph_signature": signature,
            "task_count": len(rows),
            "success_count": sum(1 for row in rows if row.get("success")),
            "failure_slices": {
                "empty_answer": sum(1 for row in rows if row.get("empty_answer")),
                "max_step_hit": sum(1 for row in rows if row.get("max_step_hit")),
                "forced_answer_candidate": sum(
                    1 for row in rows
                    if row.get("graph_features", {}).get("forced_answer_candidate")
                ),
                "answer_phase_counts": dict(answer_phase_counts.most_common()),
            },
            "node_stats": node_stats,
            "edge_stats": edge_stats,
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

    def _call_graph_b2_shard_critic(
        self,
        *,
        mode: str,
        batch_rows: list[dict[str, Any]],
        graph_profile: dict[str, Any],
        skill: SkillDetail,
        history_poll: list[dict],
        log_dir: Path,
        log_name: str,
        shard_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system_prompt = render_prompt(self.config.prompt_root / "critic_system_graph_b2.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "critic_user_graph_b2_shard.md",
            critic_mode=mode,
            batch_rows_json=json.dumps(batch_rows, ensure_ascii=False, indent=2, default=str),
            skill_json=json.dumps(self._skill_payload(skill), ensure_ascii=False, indent=2),
            graph_signature_json=json.dumps(graph_signature(skill), ensure_ascii=False, indent=2),
            graph_profile_json=json.dumps(graph_profile, ensure_ascii=False, indent=2, default=str),
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

    def _aggregate_graph_b2_payloads(
        self,
        *,
        shard_payloads: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        graph_profile: dict[str, Any],
        skill: SkillDetail,
        history_poll: list[dict],
        log_dir: Path,
    ) -> dict[str, Any]:
        system_prompt = render_prompt(self.config.prompt_root / "critic_system_graph_b2.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "critic_aggregate_graph_b2_user.md",
            shard_findings_json=json.dumps(shard_payloads, ensure_ascii=False, indent=2, default=str),
            batch_overview_json=json.dumps(self._batch_overview(rows), ensure_ascii=False, indent=2, default=str),
            graph_profile_json=json.dumps(graph_profile, ensure_ascii=False, indent=2, default=str),
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
        log_llm_call(log_dir, "critic_graph_b2_aggregate", llm_result)
        write_json(log_dir / "critic_graph_b2_aggregate_response.json", payload)
        return payload

    def _call_graph_v3_critic(
        self,
        *,
        skill_graph_view: dict[str, Any],
        value_profiles: dict[str, Any],
        task_units: list[dict[str, Any]],
        representative_cases: dict[str, Any],
        history_poll: list[dict],
        log_dir: Path,
    ) -> dict[str, Any]:
        system_prompt = render_prompt(self.config.prompt_root / "critic_system_graph_v3.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "critic_user_graph_v3.md",
            skill_graph_view_json=json.dumps(skill_graph_view, ensure_ascii=False, indent=2, default=str),
            value_profiles_json=json.dumps(value_profiles, ensure_ascii=False, indent=2, default=str),
            task_units_json=json.dumps(task_units, ensure_ascii=False, indent=2, default=str),
            representative_cases_json=json.dumps(representative_cases, ensure_ascii=False, indent=2, default=str),
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
        log_llm_call(log_dir, "critic_graph_v3", llm_result)
        write_json(log_dir / "critic_graph_v3_response.json", payload)
        return payload

    def _aggregate_graph_v3_payloads(
        self,
        *,
        graph_payload: dict[str, Any],
        rules_payloads: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        skill_graph_view: dict[str, Any],
        value_profiles: dict[str, Any],
        representative_cases: dict[str, Any],
        skill: SkillDetail,
        history_poll: list[dict],
        log_dir: Path,
    ) -> dict[str, Any]:
        system_prompt = render_prompt(self.config.prompt_root / "critic_system_graph_v3.md")
        user_prompt = render_prompt(
            self.config.prompt_root / "critic_aggregate_graph_v3_user.md",
            graph_findings_json=json.dumps(graph_payload, ensure_ascii=False, indent=2, default=str),
            rules_findings_json=json.dumps(rules_payloads, ensure_ascii=False, indent=2, default=str),
            batch_overview_json=json.dumps(self._batch_overview(rows), ensure_ascii=False, indent=2, default=str),
            skill_graph_view_json=json.dumps(skill_graph_view, ensure_ascii=False, indent=2, default=str),
            value_profiles_json=json.dumps(value_profiles, ensure_ascii=False, indent=2, default=str),
            representative_cases_json=json.dumps(representative_cases, ensure_ascii=False, indent=2, default=str),
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
        log_llm_call(log_dir, "critic_graph_v3_aggregate", llm_result)
        write_json(log_dir / "critic_graph_v3_aggregate_response.json", payload)
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
        if strategy in {"graph_v3", "graph-v3", "b3_graph", "b3-graph", "flow_batch_v3", "flow-batch-v3"}:
            enhanced_rows = self._serialize_enhanced_batch_states(states)
            for row in enhanced_rows:
                row["family"] = self._family_for_row(row)
            skill_graph_view = self._skill_graph_view(skill)
            value_profiles = self._graph_v3_value_profiles(enhanced_rows, skill)
            task_units = self._task_unit_table(enhanced_rows)
            representative_cases = self._graph_v3_representative_cases(value_profiles)
            write_json(log_dir / "critic_graph_v3_rows.json", enhanced_rows)
            write_json(log_dir / "critic_graph_v3_skill_graph_view.json", skill_graph_view)
            write_json(log_dir / "critic_graph_v3_value_profiles.json", value_profiles)
            write_json(log_dir / "critic_graph_v3_task_units.json", task_units)
            write_json(log_dir / "critic_graph_v3_representative_cases.json", representative_cases)

            graph_payload = self._call_graph_v3_critic(
                skill_graph_view=skill_graph_view,
                value_profiles=value_profiles,
                task_units=task_units,
                representative_cases=representative_cases,
                history_poll=history_poll,
                log_dir=log_dir,
            )

            shards = self._make_shards(rows)
            write_json(log_dir / "critic_graph_v3_rules_shards.json", shards)
            rules_payloads: list[dict[str, Any] | None] = [None] * len(shards)

            def call_v3_rules_shard(index: int, shard: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                payload = self._call_critic(
                    mode="graph_v3_rules_list_shard",
                    batch_rows=shard["rows"],
                    skill=skill,
                    history_poll=history_poll,
                    log_dir=log_dir,
                    log_name=f"critic_graph_v3_rules_shard_{shard['shard_id']}",
                    shard_context={
                        "shard_id": shard["shard_id"],
                        "family": shard["family"],
                        "task_count": len(shard["rows"]),
                        "v3_focus": (
                            "Rules/List critic: propose common text, phase-local rules, "
                            "allowlist, exit-handoff, and existing-edge wording edits only. "
                            "Graph structure is handled by the dedicated Graph Critic."
                        ),
                    },
                )
                payload = dict(payload)
                payload["shard_id"] = shard["shard_id"]
                payload["family"] = shard["family"]
                payload["critic_role"] = "rules_list"
                return index, payload

            shard_concurrency = 1
            try:
                import os

                env_concurrency = os.environ.get("NLRL_CRITIC_SHARD_CONCURRENCY", "").strip()
                if env_concurrency:
                    shard_concurrency = int(env_concurrency)
                elif str(getattr(self.config.critic, "api_mode", "")).strip().lower() in {
                    "codex_cli",
                    "codex-exec",
                    "codex_exec",
                }:
                    shard_concurrency = len(shards)
            except Exception:
                shard_concurrency = 1
            shard_concurrency = max(1, min(shard_concurrency, len(shards) or 1))
            if shard_concurrency <= 1:
                for index, shard in enumerate(shards):
                    payload_index, payload = call_v3_rules_shard(index, shard)
                    rules_payloads[payload_index] = payload
            else:
                with ThreadPoolExecutor(max_workers=shard_concurrency, thread_name_prefix="critic-graph-v3-rules-shard") as pool:
                    futures = {
                        pool.submit(call_v3_rules_shard, index, shard): index
                        for index, shard in enumerate(shards)
                    }
                    for future in as_completed(futures):
                        payload_index, payload = future.result()
                        rules_payloads[payload_index] = payload

            resolved_rules_payloads = [payload for payload in rules_payloads if payload is not None]
            payload = self._aggregate_graph_v3_payloads(
                graph_payload=graph_payload,
                rules_payloads=resolved_rules_payloads,
                rows=[row for shard in shards for row in shard["rows"]],
                skill_graph_view=skill_graph_view,
                value_profiles=value_profiles,
                representative_cases=representative_cases,
                skill=skill,
                history_poll=history_poll,
                log_dir=log_dir,
            )
        elif strategy in {"graph_b2", "graph-b2", "b2_graph", "b2-graph"}:
            enhanced_rows = self._serialize_enhanced_batch_states(states)
            graph_profile = self._graph_profile(enhanced_rows, skill)
            write_json(log_dir / "critic_graph_b2_rows.json", enhanced_rows)
            write_json(log_dir / "critic_graph_b2_profile.json", graph_profile)
            shards = self._make_shards(enhanced_rows)
            write_json(log_dir / "critic_graph_b2_shards.json", shards)
            shard_payloads: list[dict[str, Any] | None] = [None] * len(shards)

            def call_b2_shard(index: int, shard: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                payload = self._call_graph_b2_shard_critic(
                    mode="graph_b2_family_shard",
                    batch_rows=shard["rows"],
                    graph_profile=graph_profile,
                    skill=skill,
                    history_poll=history_poll,
                    log_dir=log_dir,
                    log_name=f"critic_graph_b2_shard_{shard['shard_id']}",
                    shard_context={
                        "shard_id": shard["shard_id"],
                        "family": shard["family"],
                        "task_count": len(shard["rows"]),
                        "b2_focus": "allowlist/rules signals plus strongly supported graph signals",
                    },
                )
                payload = dict(payload)
                payload["shard_id"] = shard["shard_id"]
                payload["family"] = shard["family"]
                return index, payload

            shard_concurrency = 1
            try:
                import os

                env_concurrency = os.environ.get("NLRL_CRITIC_SHARD_CONCURRENCY", "").strip()
                if env_concurrency:
                    shard_concurrency = int(env_concurrency)
                elif str(getattr(self.config.critic, "api_mode", "")).strip().lower() in {
                    "codex_cli",
                    "codex-exec",
                    "codex_exec",
                }:
                    shard_concurrency = len(shards)
            except Exception:
                shard_concurrency = 1
            shard_concurrency = max(1, min(shard_concurrency, len(shards) or 1))
            if shard_concurrency <= 1:
                for index, shard in enumerate(shards):
                    payload_index, payload = call_b2_shard(index, shard)
                    shard_payloads[payload_index] = payload
            else:
                with ThreadPoolExecutor(max_workers=shard_concurrency, thread_name_prefix="critic-graph-b2-shard") as pool:
                    futures = {
                        pool.submit(call_b2_shard, index, shard): index
                        for index, shard in enumerate(shards)
                    }
                    for future in as_completed(futures):
                        payload_index, payload = future.result()
                        shard_payloads[payload_index] = payload

            resolved_shard_payloads = [payload for payload in shard_payloads if payload is not None]
            payload = self._aggregate_graph_b2_payloads(
                shard_payloads=resolved_shard_payloads,
                rows=[row for shard in shards for row in shard["rows"]],
                graph_profile=graph_profile,
                skill=skill,
                history_poll=history_poll,
                log_dir=log_dir,
            )
        elif strategy in {"sharded", "family_sharded", "family-sharded"}:
            shards = self._make_shards(rows)
            write_json(log_dir / "critic_shards.json", shards)
            shard_payloads: list[dict[str, Any] | None] = [None] * len(shards)

            def call_shard(index: int, shard: dict[str, Any]) -> tuple[int, dict[str, Any]]:
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
                return index, payload

            shard_concurrency_raw = str(
                getattr(self.config.critic, "api_mode", "")
            ).strip().lower()
            env_concurrency = ""
            try:
                import os

                env_concurrency = os.environ.get("NLRL_CRITIC_SHARD_CONCURRENCY", "").strip()
            except Exception:
                env_concurrency = ""
            if env_concurrency:
                shard_concurrency = int(env_concurrency)
            elif shard_concurrency_raw in {"codex_cli", "codex-exec", "codex_exec"}:
                shard_concurrency = len(shards)
            else:
                shard_concurrency = 1
            shard_concurrency = max(1, min(shard_concurrency, len(shards) or 1))

            if shard_concurrency <= 1:
                for index, shard in enumerate(shards):
                    payload_index, payload = call_shard(index, shard)
                    shard_payloads[payload_index] = payload
            else:
                with ThreadPoolExecutor(max_workers=shard_concurrency, thread_name_prefix="critic-shard") as pool:
                    futures = {
                        pool.submit(call_shard, index, shard): index
                        for index, shard in enumerate(shards)
                    }
                    for future in as_completed(futures):
                        payload_index, payload = future.result()
                        shard_payloads[payload_index] = payload

            resolved_shard_payloads = [payload for payload in shard_payloads if payload is not None]
            payload = self._aggregate_shard_payloads(
                shard_payloads=resolved_shard_payloads,
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
