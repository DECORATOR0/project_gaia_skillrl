from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from gaia_skillrl.actor import SkillActor
from gaia_skillrl.agent_loop import (
    _invalid_answer_feedback,
    _invalid_phase_tool_feedback,
    _invalid_phase_transition_feedback,
    _unknown_phase_feedback,
    parse_executor_tags,
)
from gaia_skillrl.config import clone_system_config, load_system_config
from gaia_skillrl.critic import SkillCritic
from gaia_skillrl.dataset import load_converted_dataset
from gaia_skillrl.environment import SkillEnvironment, _build_phase_tool_allowlist, _build_phase_transition_graph, _ensure_phases
from gaia_skillrl.schemas import (
    EnvRunResult,
    EnvState,
    EvaluationResult,
    ExecutorStepRecord,
    PhaseTransition,
    ToolCallRecord,
    to_dict,
)
from gaia_skillrl.skills import load_skill_detail, discover_skills
from gaia_skillrl.trainer import GaiaSkillTrainer
from gaia_skillrl.utils import append_jsonl, ensure_dir, utc_timestamp, write_json


_TOOL_RESULT_HEADER_RE = re.compile(r"Tool result for step (\d+):")
_TOOL_NAME_RE = re.compile(r"- tool:\s*(.+)")
_TOOL_SUCCESS_RE = re.compile(r"- success:\s*(.+)")
_TOOL_OBS_RE = re.compile(r"- observation:\s*(.*)", re.DOTALL)


def _load_active_skill_detail(skill_library_root: Path):
    headers = discover_skills(skill_library_root)
    if not headers:
        raise RuntimeError(f"No active skill found in {skill_library_root}")
    return load_skill_detail(headers[0])


def _load_state(path: Path) -> EnvState:
    data = json.loads(path.read_text(encoding="utf-8"))
    env_result_raw = data["env_result"]
    env_result = EnvRunResult(
        final_answer=env_result_raw.get("final_answer", ""),
        final_choice_label=env_result_raw.get("final_choice_label", ""),
        tool_trajectory=[ToolCallRecord(**item) for item in env_result_raw.get("tool_trajectory", [])],
        phase_transitions=[PhaseTransition(**item) for item in env_result_raw.get("phase_transitions", [])],
        action_trace=[ExecutorStepRecord(**item) for item in env_result_raw.get("action_trace", [])],
        executor_summary=env_result_raw.get("executor_summary", ""),
        raw_executor_output=env_result_raw.get("raw_executor_output", ""),
        evaluation=EvaluationResult(**env_result_raw.get("evaluation", {})),
    )
    return EnvState(
        task_id=data["task_id"],
        task_prompt=data.get("task_prompt", ""),
        env_result=env_result,
        gold_trajectory=data.get("gold_trajectory", []),
        gold_tool_names=data.get("gold_tool_names", []),
        active_skill_name=data.get("active_skill_name", ""),
        task_context=data.get("task_context", {}),
    )


def _extract_tool_result_from_request(request_path: Path, step_index: int) -> tuple[str, bool, str]:
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    marker = f"Tool result for step {step_index}:"
    for message in payload.get("messages", []):
        content = str(message.get("content", ""))
        if marker not in content:
            continue
        tool_match = _TOOL_NAME_RE.search(content)
        success_match = _TOOL_SUCCESS_RE.search(content)
        obs_match = _TOOL_OBS_RE.search(content)
        tool_name = tool_match.group(1).strip() if tool_match else ""
        success = success_match.group(1).strip().lower() == "true" if success_match else False
        observation = obs_match.group(1).strip() if obs_match else ""
        return tool_name, success, observation
    return "", False, ""


def _synthesize_state_for_missing_task(
    *,
    task,
    active_skill_name: str,
    skill_detail,
    task_run_dir: Path,
) -> EnvState:
    step_dir = task_run_dir / "executor" / "executor_steps"
    response_paths = sorted(step_dir.glob("*_response.json"))
    request_paths = {
        int(path.name.split("_executor_step_")[1].split("_request.json")[0]): path
        for path in step_dir.glob("*_request.json")
    }
    phases = _ensure_phases(skill_detail.phases)
    transition_graph = _build_phase_transition_graph(phases)
    phase_tool_allowlist = _build_phase_tool_allowlist(
        phases,
        skill_detail.header.allowed_tools or [],
    )

    current_phase = "INIT"
    tool_records: list[ToolCallRecord] = []
    transitions: list[PhaseTransition] = []
    action_trace: list[ExecutorStepRecord] = []
    raw_outputs: list[str] = []

    for response_path in response_paths:
        step_index = int(response_path.name.split("_executor_step_")[1].split("_response.json")[0])
        response_payload = json.loads(response_path.read_text(encoding="utf-8"))
        raw_text = str(response_payload.get("text", ""))
        raw_outputs.append(raw_text)
        parsed = parse_executor_tags(raw_text)
        phase_before = current_phase

        if parsed.action_type == "call":
            allowed_tools = phase_tool_allowlist.get(current_phase, [])
            if parsed.tool_name not in allowed_tools:
                feedback = _invalid_phase_tool_feedback(current_phase, parsed.tool_name, allowed_tools)
                action_trace.append(
                    ExecutorStepRecord(
                        step_index=step_index,
                        action_type="call",
                        phase_before=phase_before,
                        phase_after=current_phase,
                        thought=parsed.thought,
                        tool_name=parsed.tool_name,
                        arguments=parsed.tool_args or {},
                        accepted=False,
                        success=False,
                        outcome="invalid_phase_tool",
                        feedback=feedback,
                        raw_output=raw_text,
                    )
                )
                continue

            request_path = request_paths.get(step_index + 1)
            observed_tool_name, tool_success, observation = ("", False, "")
            if request_path is not None:
                observed_tool_name, tool_success, observation = _extract_tool_result_from_request(request_path, step_index)
            tool_name = observed_tool_name or parsed.tool_name
            tool_records.append(
                ToolCallRecord(
                    step_index=step_index,
                    thought=parsed.thought,
                    tool_name=tool_name,
                    arguments=parsed.tool_args or {},
                    observation=observation,
                    success=tool_success or bool(observation),
                    raw_result=observation,
                    error="" if (tool_success or bool(observation)) else "Interrupted before tool result was captured.",
                )
            )
            action_trace.append(
                ExecutorStepRecord(
                    step_index=step_index,
                    action_type="call",
                    phase_before=phase_before,
                    phase_after=current_phase,
                    thought=parsed.thought,
                    tool_name=tool_name,
                    arguments=parsed.tool_args or {},
                    accepted=True,
                    success=tool_success or bool(observation),
                    outcome="tool_ok" if (tool_success or bool(observation)) else "tool_interrupted",
                    observation=observation,
                    raw_output=raw_text,
                )
            )
            continue

        if parsed.action_type == "next":
            allowed_next = transition_graph.get(current_phase, [])
            next_phase = parsed.next_phase.strip().upper()
            if next_phase not in phases:
                feedback = _unknown_phase_feedback(current_phase, next_phase, allowed_next)
                action_trace.append(
                    ExecutorStepRecord(
                        step_index=step_index,
                        action_type="next",
                        phase_before=phase_before,
                        phase_after=current_phase,
                        thought=parsed.thought,
                        next_phase=next_phase,
                        accepted=False,
                        success=False,
                        outcome="unknown_phase",
                        feedback=feedback,
                        raw_output=raw_text,
                    )
                )
                continue
            if next_phase not in allowed_next:
                feedback = _invalid_phase_transition_feedback(current_phase, next_phase, allowed_next)
                action_trace.append(
                    ExecutorStepRecord(
                        step_index=step_index,
                        action_type="next",
                        phase_before=phase_before,
                        phase_after=current_phase,
                        thought=parsed.thought,
                        next_phase=next_phase,
                        accepted=False,
                        success=False,
                        outcome="invalid_phase_transition",
                        feedback=feedback,
                        raw_output=raw_text,
                    )
                )
                continue

            current_phase = next_phase
            transitions.append(
                PhaseTransition(
                    step_index=step_index,
                    from_phase=phase_before,
                    to_phase=current_phase,
                    thought=parsed.thought,
                )
            )
            action_trace.append(
                ExecutorStepRecord(
                    step_index=step_index,
                    action_type="next",
                    phase_before=phase_before,
                    phase_after=current_phase,
                    thought=parsed.thought,
                    next_phase=current_phase,
                    accepted=True,
                    success=True,
                    outcome="phase_transition",
                    raw_output=raw_text,
                )
            )
            continue

        if parsed.action_type == "answer":
            if current_phase == "CONCLUDE":
                action_trace.append(
                    ExecutorStepRecord(
                        step_index=step_index,
                        action_type="answer",
                        phase_before=phase_before,
                        phase_after=current_phase,
                        thought=parsed.thought,
                        answer=parsed.answer,
                        accepted=True,
                        success=True,
                        outcome="final_answer",
                        raw_output=raw_text,
                    )
                )
            else:
                feedback = _invalid_answer_feedback(current_phase)
                action_trace.append(
                    ExecutorStepRecord(
                        step_index=step_index,
                        action_type="answer",
                        phase_before=phase_before,
                        phase_after=current_phase,
                        thought=parsed.thought,
                        answer=parsed.answer,
                        accepted=False,
                        success=False,
                        outcome="invalid_answer_phase",
                        feedback=feedback,
                        raw_output=raw_text,
                    )
                )
            continue

        action_trace.append(
            ExecutorStepRecord(
                step_index=step_index,
                action_type="none",
                phase_before=phase_before,
                phase_after=current_phase,
                thought=parsed.thought,
                accepted=False,
                success=False,
                outcome="unrecognized_action",
                feedback=(
                    "Your response did not contain a recognized action tag. "
                    "You must use exactly one of <CALL>, <NEXT>, or <ANSWER>."
                ),
                raw_output=raw_text,
            )
        )

    summary = (
        "Interrupted task synthesized after the run was manually stopped. "
        f"Observed {len(response_paths)} executor responses; current phase remained {current_phase}; "
        "the agent repeatedly attempted premature answers instead of following the phase protocol."
    )
    env_result = EnvRunResult(
        final_answer="",
        final_choice_label="",
        tool_trajectory=tool_records,
        phase_transitions=transitions,
        action_trace=action_trace,
        executor_summary=summary,
        raw_executor_output="\n\n".join(raw_outputs),
        evaluation=EvaluationResult(
            accuracy=0.0,
            efficiency=0.0 if not tool_records else round(sum(1 for item in tool_records if item.success) / len(tool_records), 4),
            tool_any_order=0.0,
            tool_in_order=0.0,
            tool_exact_match=0.0,
            parameter_accuracy=0.0,
            task_success=False,
            notes="interrupted_after_manual_stop repeated_invalid_answer_phase",
        ),
    )
    return EnvState(
        task_id=task.task_id,
        task_prompt=task.prompt,
        env_result=env_result,
        gold_trajectory=task.gold_trajectory,
        gold_tool_names=task.gold_tool_names,
        active_skill_name=active_skill_name,
        task_context={
            "choices": [],
            "gold_answer": task.gold_answer,
            "level": task.metadata.get("level"),
            "data_dir": task.data_dir,
            "file_list": task.file_list,
        },
    )


def _history_entry(iteration_index: int, states: list[EnvState], reward_summary: str) -> dict:
    succeeded = [state.task_id for state in states if state.env_result.evaluation.task_success]
    failed = [state.task_id for state in states if not state.env_result.evaluation.task_success]
    return {
        "iteration_index": iteration_index,
        "succeeded_task_ids": succeeded,
        "failed_task_ids": failed,
        "summary": reward_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume a stopped GAIA run after a partial iteration.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--iterations-per-batch", type=int, default=2)
    parser.add_argument("--task-concurrency", type=int, default=20)
    parser.add_argument("--max-executor-steps", type=int, default=None)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    config = load_system_config(args.config)
    runtime_overrides = {
        "iterations_per_batch": args.iterations_per_batch,
        "task_concurrency": args.task_concurrency,
    }
    if args.max_executor_steps is not None:
        runtime_overrides["max_executor_steps"] = args.max_executor_steps
    config = clone_system_config(
        config,
        runtime=runtime_overrides,
    )
    trainer = GaiaSkillTrainer(config)
    logger = trainer._build_logger(run_dir)
    logger.info("Resuming partial GAIA run after manual stop")
    logger.info(
        "Runtime: iterations_per_batch=%d task_concurrency=%d max_executor_steps=%d",
        config.runtime.iterations_per_batch,
        config.runtime.task_concurrency,
        config.runtime.max_executor_steps,
    )

    selected_summary = json.loads((run_dir / "selected_tasks.json").read_text(encoding="utf-8"))
    dataset_path = Path(selected_summary.get("dataset_path") or config.converted_dataset_path)
    task_by_id = {task.task_id: task for task in load_converted_dataset(dataset_path)}
    ordered_task_ids = list(selected_summary["task_ids"])
    selected_tasks = [task_by_id[task_id] for task_id in ordered_task_ids]

    state_config = trainer._batch_state_config(run_dir)
    active_skill = _load_active_skill_detail(state_config.skill_library_root)
    iteration_one_dir = run_dir / "iteration_01"

    state_by_id: dict[str, EnvState] = {}
    for task_id in ordered_task_ids:
        state_path = iteration_one_dir / task_id / "state.json"
        if state_path.exists():
            state_by_id[task_id] = _load_state(state_path)

    missing_ids = [task_id for task_id in ordered_task_ids if task_id not in state_by_id]
    if missing_ids:
        logger.info("Synthesizing %d missing task state(s) for iteration 01: %s", len(missing_ids), ",".join(missing_ids))
    for task_id in missing_ids:
        task = task_by_id[task_id]
        task_run_dir = ensure_dir(iteration_one_dir / task_id)
        state = _synthesize_state_for_missing_task(
            task=task,
            active_skill_name=active_skill.header.name,
            skill_detail=active_skill,
            task_run_dir=task_run_dir,
        )
        write_json(task_run_dir / "state.json", to_dict(state))
        state_by_id[task_id] = state

    states_iter1 = [state_by_id[task_id] for task_id in ordered_task_ids]
    critic = SkillCritic(state_config)
    actor = SkillActor(state_config)
    history_poll: list[dict] = []

    if not (iteration_one_dir / "iteration_summary.json").exists():
        logger.info("Closing out iteration 01 with critic and actor")
        reward = critic.evaluate_batch(states_iter1, active_skill, history_poll, iteration_one_dir)
        decision = actor.act(reward, active_skill, history_poll, iteration_one_dir)
        actor.apply(decision)
        updated_skill = _load_active_skill_detail(state_config.skill_library_root)
        trainer._snapshot_active_skill(updated_skill, iteration_one_dir / "skill_after_actor")
        entry = _history_entry(1, states_iter1, reward.summary)
        history_poll.append(entry)
        append_jsonl(run_dir / "history_poll.jsonl", entry)
        write_json(
            iteration_one_dir / "iteration_summary.json",
            {
                "iteration_index": 1,
                "states": [to_dict(state) for state in states_iter1],
                "reward": to_dict(reward),
                "actor_decision": to_dict(decision),
            },
        )
    else:
        history_path = run_dir / "history_poll.jsonl"
        if history_path.exists():
            history_poll = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    for iteration_index in range(2, state_config.runtime.iterations_per_batch + 1):
        logger.info("Iteration %02d: executor -> critic -> actor", iteration_index)
        active_skill = _load_active_skill_detail(state_config.skill_library_root)
        iteration_dir = ensure_dir(run_dir / f"iteration_{iteration_index:02d}")
        trainer._snapshot_active_skill(active_skill, iteration_dir / "skill_before_actor")
        states = trainer._run_tasks(
            selected_tasks=selected_tasks,
            iteration_dir=iteration_dir,
            task_concurrency=state_config.runtime.task_concurrency,
            logger=logger,
            iteration_label=f"Iteration {iteration_index:02d}",
            run_one=lambda task, task_dir: SkillEnvironment(state_config).run(task, active_skill, task_dir),
        )
        reward = critic.evaluate_batch(states, active_skill, history_poll, iteration_dir)
        decision = actor.act(reward, active_skill, history_poll, iteration_dir)
        actor.apply(decision)
        updated_skill = _load_active_skill_detail(state_config.skill_library_root)
        trainer._snapshot_active_skill(updated_skill, iteration_dir / "skill_after_actor")
        entry = _history_entry(iteration_index, states, reward.summary)
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
    final_skill = _load_active_skill_detail(state_config.skill_library_root)
    final_iteration_index = state_config.runtime.iterations_per_batch + 1
    final_iteration_dir = ensure_dir(run_dir / f"iteration_{final_iteration_index:02d}")
    trainer._snapshot_active_skill(final_skill, final_iteration_dir / "skill_for_eval")
    final_states = trainer._run_tasks(
        selected_tasks=selected_tasks,
        iteration_dir=final_iteration_dir,
        task_concurrency=state_config.runtime.task_concurrency,
        logger=logger,
        iteration_label=f"Iteration {final_iteration_index:02d}",
        run_one=lambda task, task_dir: SkillEnvironment(state_config).run(task, final_skill, task_dir),
    )
    write_json(
        run_dir / "run_summary.json",
        {
            "run_name": run_dir.name,
            "generated_at": utc_timestamp(),
            "task_count": len(final_states),
            "success_count": sum(1 for state in final_states if state.env_result.evaluation.task_success),
            "tasks": [to_dict(state) for state in final_states],
            "active_skill": to_dict(final_skill),
        },
    )
    logger.info("Resume flow completed")


if __name__ == "__main__":
    main()
