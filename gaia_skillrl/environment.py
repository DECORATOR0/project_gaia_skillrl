from __future__ import annotations

import json
import os
from pathlib import Path

from .agent_loop import DirectExecutorAgent, PhaseExecutorAgent
from .config import SystemConfig
from .evaluation import evaluate_execution
from .prompting import render_prompt
from .schemas import DatasetTask, EnvRunResult, EnvState, PhaseTransition, SkillDetail, SkillPhase, to_dict
from .tools import ToolContext, Toolbox
from .utils import ensure_dir, write_json

_DEFAULT_PHASES = {
    "INIT": SkillPhase(
        name="INIT",
        content=(
            "Inspect the task directory first.\n"
            "Read `task.json` before touching any attachment.\n"
            "Do not answer in this phase.\n"
            "Do not jump directly to ANALYZE or CONCLUDE.\n"
            "When the task layout is clear, move to GATHER.\n\n"
            "Available actions:\n"
            "- <CALL>list_dir</CALL><ARGS>{\"path\": \".\"}</ARGS>\n"
            "- <CALL>read_json_file</CALL><ARGS>{\"path\": \"task.json\"}</ARGS>\n"
            "- <NEXT>GATHER</NEXT>"
        ),
        order=0,
    ),
    "GATHER": SkillPhase(
        name="GATHER",
        content=(
            "Collect evidence from local attachments first.\n"
            "Use attachment-specific tools when they fit: `extract_pdf_text` for PDFs, `read_table` for spreadsheets, `read_file` for plain text/JSON, "
            "`parse_docx` for DOCX, `parse_pptx` for PPTX, `extract_archive` for zip/tar bundles, `html_extract` for HTML, "
            "`audio_transcribe` for audio, and `ocr_image` or `image_qa` for images.\n"
            "Use `ocr_image` when the answer depends on text visible inside the image. Use `image_qa` when the answer depends on visual semantics, object positions, charts, or board states, and pass the task question or a tightly scoped sub-question.\n"
            "Use `web_search` and `fetch_url` only if the local files are insufficient.\n"
            "Do not answer in this phase.\n"
            "When the relevant evidence is collected, move to ANALYZE."
        ),
        order=1,
    ),
    "ANALYZE": SkillPhase(
        name="ANALYZE",
        content=(
            "Convert the gathered evidence into the exact short answer.\n"
            "Use `run_python` for arithmetic, counting, sorting, or structured parsing when needed.\n"
            "Explicitly identify the requested final units, scale, rounding rule, and output format before concluding.\n"
            "If the prompt asks for scaled units such as thousand-hours, millions, percentages, buckets, or dates, convert to that final representation before you conclude.\n"
            "Do not guess. If evidence is still missing, continue gathering before concluding.\n"
            "Do not answer in this phase. Move to CONCLUDE only after the final answer string is ready."
        ),
        order=2,
    ),
    "CONCLUDE": SkillPhase(
        name="CONCLUDE",
        content=(
            "Return the answer only after one final check against the prompt.\n"
            "Verify units, scale, rounding, separators, casing, and whether explanations are forbidden.\n"
            "Return the final answer as a short string inside the answer tag.\n"
            "Do not add explanations inside the answer tag.\n"
            "Format: <ANSWER>your final answer</ANSWER>"
        ),
        order=3,
    ),
}


def _ensure_phases(phases: dict[str, SkillPhase]) -> dict[str, SkillPhase]:
    result = dict(phases)
    for name, phase in _DEFAULT_PHASES.items():
        result.setdefault(name, phase)
    return result


def _build_phase_transition_graph(phases: dict[str, SkillPhase]) -> dict[str, list[str]]:
    ordered = sorted(phases.values(), key=lambda phase: phase.order)
    names = [phase.name for phase in ordered]
    graph: dict[str, list[str]] = {}
    for index, name in enumerate(names):
        if name == "CONCLUDE":
            graph[name] = []
            continue
        next_phase = names[index + 1] if index + 1 < len(names) else None
        graph[name] = [next_phase] if next_phase else []
    graph.setdefault("CONCLUDE", [])
    return graph


def _phase_prompt(name: str, content: str) -> str:
    return f"=== Phase: {name} ===\n\n{content}\n\nFollow the instructions above for this phase."


def _build_phase_tool_allowlist() -> dict[str, list[str]]:
    return {
        "INIT": ["list_dir", "read_json_file"],
        "GATHER": [
            "list_dir",
            "read_file",
            "read_json_file",
            "extract_pdf_text",
            "read_table",
            "image_metadata",
            "audio_transcribe",
            "ocr_image",
            "image_qa",
            "parse_docx",
            "parse_pptx",
            "extract_archive",
            "web_search",
            "fetch_url",
            "html_extract",
        ],
        "ANALYZE": [
            "list_dir",
            "read_file",
            "read_json_file",
            "extract_pdf_text",
            "read_table",
            "image_metadata",
            "audio_transcribe",
            "ocr_image",
            "image_qa",
            "parse_docx",
            "parse_pptx",
            "extract_archive",
            "web_search",
            "fetch_url",
            "html_extract",
            "run_python",
        ],
        "CONCLUDE": [],
    }


class SkillEnvironment:
    def __init__(self, config: SystemConfig):
        self.config = config
        tool_context = _build_tool_context(config)
        self.toolbox = Toolbox(tool_context)
        self.executor_agent = PhaseExecutorAgent(
            config.executor,
            config.prompt_root,
            self.toolbox,
            max_context_chars=config.runtime.max_context_chars,
        )

    def _build_initial_prompt(self, task: DatasetTask, skill: SkillDetail, init_phase: SkillPhase) -> str:
        task_info = json.dumps(
            {
                "task_id": task.task_id,
                "split": task.source_type,
                "level": task.metadata.get("level"),
                "question": task.prompt,
                "task_dir": task.data_dir,
                "files": task.file_list,
            },
            ensure_ascii=False,
            indent=2,
        )
        resources_list = ", ".join(skill.resources) if skill.resources else "(none)"
        return (
            f"## Task\n\n{task_info}\n\n"
            f"## Activated Skill: {skill.header.name}\n\n"
            f"Description: {skill.header.description}\n\n"
            f"Bundled resources: {resources_list}\n\n"
            f"{_phase_prompt('INIT', init_phase.content)}"
        )

    def run(self, task: DatasetTask, active_skill: SkillDetail, run_dir: Path) -> EnvState:
        ensure_dir(run_dir)
        phases = _ensure_phases(active_skill.phases)
        transition_graph = _build_phase_transition_graph(phases)
        phase_names = ", ".join(name for name in transition_graph)
        system_prompt = render_prompt(
            self.config.prompt_root / "executor_system.md",
            max_steps=self.config.runtime.max_executor_steps,
            phase_list=phase_names,
        )
        init_phase = phases["INIT"]
        initial_prompt = self._build_initial_prompt(task, active_skill, init_phase)
        resolved_conclude_prompt = _phase_prompt("CONCLUDE", phases["CONCLUDE"].content)
        fallback_conclude_prompt = (
            "Remaining steps are low. Stop gathering and map the evidence you already have to the final short answer.\n\n"
            + resolved_conclude_prompt
        )

        self.toolbox.set_active_skill_dir(active_skill.header.skill_dir)
        self.toolbox.set_active_task_data_dir(task.data_dir)
        try:
            final_action, raw_output, tool_records, transitions, action_trace = self.executor_agent.run(
                role_name="executor",
                system_prompt=system_prompt,
                initial_user_prompt=initial_prompt,
                phases=phases,
                allowed_tools=active_skill.header.allowed_tools or None,
                max_steps=self.config.runtime.max_executor_steps,
                log_dir=run_dir / "executor",
                phase_transition_graph=transition_graph,
                resolved_conclude_prompt=resolved_conclude_prompt,
                fallback_conclude_prompt=fallback_conclude_prompt,
                phase_tool_allowlist=_build_phase_tool_allowlist(),
            )
        finally:
            self.toolbox.set_active_skill_dir(None)
            self.toolbox.set_active_task_data_dir(None)

        env_result = EnvRunResult(
            final_answer=final_action.answer or "",
            final_choice_label=final_action.answer or "",
            tool_trajectory=tool_records,
            phase_transitions=transitions,
            action_trace=action_trace,
            executor_summary=final_action.thought,
            raw_executor_output=raw_output,
        )
        env_result.evaluation = evaluate_execution(
            final_choice_label=env_result.final_choice_label,
            final_answer=env_result.final_answer,
            executed_steps=tool_records,
            gold_tool_names=task.gold_tool_names,
            gold_trajectory=task.gold_trajectory,
            gold_answer=task.gold_answer,
        )
        state = EnvState(
            task_id=task.task_id,
            task_prompt=task.prompt,
            env_result=env_result,
            gold_trajectory=task.gold_trajectory,
            gold_tool_names=task.gold_tool_names,
            active_skill_name=active_skill.header.name,
            task_context={
                "choices": [],
                "gold_answer": task.gold_answer,
                "level": task.metadata.get("level"),
                "data_dir": task.data_dir,
                "file_list": task.file_list,
            },
        )
        write_json(run_dir / "state.json", to_dict(state))
        return state


def _build_tool_context(config: SystemConfig) -> ToolContext:
    return ToolContext(
        workspace_root=config.workspace_root,
        skill_library_root=config.skill_library_root,
        temp_root=config.run_root / "temp",
        python_executable=config.runtime.python_executable,
        shell_program=config.runtime.shell_program,
        search_results_limit=config.runtime.search_results_limit,
        web_fetch_char_limit=config.runtime.web_fetch_char_limit,
        tool_api_base_url=os.environ.get("NLRL_TOOL_BASE_URL", "").strip() or config.executor.base_url,
        tool_api_key=os.environ.get("NLRL_TOOL_API_KEY", "").strip() or config.executor.api_key,
        tool_api_timeout_seconds=int(os.environ.get("NLRL_TOOL_TIMEOUT_SECONDS", "").strip() or config.executor.timeout_seconds),
        tool_vision_model=os.environ.get("NLRL_TOOL_VISION_MODEL", "").strip() or os.environ.get("NLRL_TOOL_MODEL", "").strip() or "gpt-4o-mini",
        tool_audio_model=os.environ.get("NLRL_TOOL_AUDIO_MODEL", "").strip() or os.environ.get("NLRL_TOOL_MODEL", "").strip() or "gpt-4o-mini-transcribe",
        tool_api_max_retries=int(os.environ.get("NLRL_TOOL_MAX_RETRIES", "").strip() or "2"),
    )


class DirectEnvironment:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.toolbox = Toolbox(_build_tool_context(config))
        self.executor_agent = DirectExecutorAgent(
            config.executor,
            config.prompt_root,
            self.toolbox,
            max_context_chars=config.runtime.max_context_chars,
        )

    def _build_initial_prompt(self, task: DatasetTask) -> str:
        task_info = json.dumps(
            {
                "task_id": task.task_id,
                "split": task.source_type,
                "level": task.metadata.get("level"),
                "question": task.prompt,
                "task_dir": task.data_dir,
                "files": task.file_list,
            },
            ensure_ascii=False,
            indent=2,
        )
        return (
            f"## Task\n\n{task_info}\n\n"
            "## Run Mode\n\n"
            "No activated skill is provided in this run. Solve the task directly with the available tools.\n\n"
            "Suggested approach:\n"
            "- inspect `task.json` and local files first\n"
            "- pick the attachment-specific tool first: `audio_transcribe`, `ocr_image`, `image_qa`, `parse_docx`, `parse_pptx`, `extract_archive`, or `html_extract`\n"
            "- use web tools only when the task needs external evidence\n"
            "- use `run_python` for arithmetic or structured parsing\n"
            "- answer with the exact final short string only when the evidence is sufficient"
        )

    def run(self, task: DatasetTask, run_dir: Path) -> EnvState:
        ensure_dir(run_dir)
        system_prompt = render_prompt(
            self.config.prompt_root / "direct_executor_system.md",
            max_steps=self.config.runtime.max_executor_steps,
        )
        initial_prompt = self._build_initial_prompt(task)

        self.toolbox.set_active_skill_dir(None)
        self.toolbox.set_active_task_data_dir(task.data_dir)
        try:
            final_action, raw_output, tool_records, action_trace = self.executor_agent.run(
                role_name="executor",
                system_prompt=system_prompt,
                initial_user_prompt=initial_prompt,
                allowed_tools=None,
                max_steps=self.config.runtime.max_executor_steps,
                log_dir=run_dir / "executor",
            )
        finally:
            self.toolbox.set_active_task_data_dir(None)

        env_result = EnvRunResult(
            final_answer=final_action.answer or "",
            final_choice_label=final_action.answer or "",
            tool_trajectory=tool_records,
            phase_transitions=[],
            action_trace=action_trace,
            executor_summary=final_action.thought,
            raw_executor_output=raw_output,
        )
        env_result.evaluation = evaluate_execution(
            final_choice_label=env_result.final_choice_label,
            final_answer=env_result.final_answer,
            executed_steps=tool_records,
            gold_tool_names=task.gold_tool_names,
            gold_trajectory=task.gold_trajectory,
            gold_answer=task.gold_answer,
        )
        state = EnvState(
            task_id=task.task_id,
            task_prompt=task.prompt,
            env_result=env_result,
            gold_trajectory=task.gold_trajectory,
            gold_tool_names=task.gold_tool_names,
            active_skill_name="direct-baseline",
            task_context={
                "choices": [],
                "gold_answer": task.gold_answer,
                "level": task.metadata.get("level"),
                "data_dir": task.data_dir,
                "file_list": task.file_list,
            },
        )
        write_json(run_dir / "state.json", to_dict(state))
        return state
