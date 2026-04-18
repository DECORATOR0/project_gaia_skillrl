#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


STEP_FILE_RE = re.compile(r"(?P<ts>\d{8}T\d{6}Z)_executor_step_(?P<step>\d+)_response\.json$")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def compact(text: Any, limit: int = 90) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = " ".join(text.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def md_escape(text: Any) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return text.replace("|", "\\|").replace("\n", " ")


def parse_step_timestamp(name: str) -> datetime | None:
    match = STEP_FILE_RE.search(name)
    if not match:
        return None
    return datetime.strptime(match.group("ts"), "%Y%m%dT%H%M%SZ")


def extract_tag(text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def parse_response_action(text: str) -> dict[str, Any]:
    thought = extract_tag(text, "THOUGHT")
    call = extract_tag(text, "CALL")
    args = extract_tag(text, "ARGS")
    nxt = extract_tag(text, "NEXT")
    answer = extract_tag(text, "ANSWER")
    if call is not None:
        return {
            "action_type": "CALL",
            "thought": thought or "",
            "tool_name": call,
            "arguments": args or "",
        }
    if nxt is not None:
        return {
            "action_type": "NEXT",
            "thought": thought or "",
            "next_phase": nxt,
        }
    if answer is not None:
        return {
            "action_type": "ANSWER",
            "thought": thought or "",
            "answer": answer,
        }
    return {
        "action_type": "UNKNOWN",
        "thought": thought or compact(text, 120),
    }


@dataclass
class StepRecord:
    step_index: int
    timestamp: datetime | None
    text: str
    action_type: str
    thought: str
    tool_name: str = ""
    arguments: str = ""
    next_phase: str = ""
    answer: str = ""


@dataclass
class TaskReview:
    seq: int
    task_id: str
    iteration_index: int
    state_path: Path
    task_json_path: Path
    task_dir: Path
    question: str
    gold_answer: str
    pred_answer: str
    success: bool
    phase_path: str
    final_phase: str
    max_step: int
    wall_span_sec: int | None
    tool_calls: int
    failed_tool_calls: int
    phase_transitions: int
    runtime_feedback_count: int
    invalid_answer_count: int
    invalid_phase_count: int
    phase_guard_tool_rejections: int
    fallback_conclude_count: int
    http_4xx_count: int
    max_steps_reached: bool
    executor_summary: str
    accuracy: float | None
    efficiency: float | None
    step_records: list[dict[str, Any]]
    notes: str
    result_reason: str
    doc_name: str


def read_executor_steps(task_dir: Path) -> list[StepRecord]:
    steps_dir = task_dir / "executor" / "executor_steps"
    if not steps_dir.exists():
        return []
    out: list[StepRecord] = []
    for path in sorted(steps_dir.glob("*_response.json")):
        payload = load_json(path)
        text = payload.get("text", "")
        meta = parse_response_action(text)
        match = STEP_FILE_RE.search(path.name)
        if not match:
            continue
        out.append(
            StepRecord(
                step_index=int(match.group("step")),
                timestamp=parse_step_timestamp(path.name),
                text=text,
                action_type=meta.get("action_type", "UNKNOWN"),
                thought=meta.get("thought", ""),
                tool_name=meta.get("tool_name", ""),
                arguments=meta.get("arguments", ""),
                next_phase=meta.get("next_phase", ""),
                answer=meta.get("answer", ""),
            )
        )
    return out


def accepted_answer_step(raw_output: str, steps: list[StepRecord]) -> int | None:
    answer_steps = [step for step in steps if step.action_type == "ANSWER"]
    if not answer_steps:
        return None
    last_answer = answer_steps[-1]
    tail = f"<ANSWER>{last_answer.answer}</ANSWER>"
    if raw_output.strip().endswith(tail):
        return last_answer.step_index
    return None


def phase_path_from_transitions(transitions: list[dict[str, Any]]) -> tuple[str, str]:
    ordered = sorted(transitions, key=lambda item: item.get("step_index", 0))
    path = ["INIT"]
    for item in ordered:
        path.append(str(item.get("to_phase", "")))
    final_phase = path[-1]
    return " -> ".join(path), final_phase


def build_notes(
    *,
    success: bool,
    failed_tool_calls: int,
    invalid_answer_count: int,
    invalid_phase_count: int,
    fallback_conclude_count: int,
    http_4xx_count: int,
    max_steps_reached: bool,
    pred_answer: str,
    gold_answer: str,
) -> str:
    notes: list[str] = []
    if max_steps_reached:
        notes.append("max steps reached")
    if failed_tool_calls:
        notes.append(f"{failed_tool_calls} 次失败调用")
    if invalid_phase_count:
        notes.append(f"{invalid_phase_count} 次非法 NEXT")
    if invalid_answer_count:
        notes.append(f"{invalid_answer_count} 次提前 ANSWER")
    if fallback_conclude_count:
        notes.append(f"{fallback_conclude_count} 次 fallback conclude")
    if http_4xx_count:
        notes.append(f"{http_4xx_count} 次 HTTP 4xx")
    if not success and pred_answer:
        notes.append(f"pred={compact(pred_answer, 24)} / gold={compact(gold_answer, 24)}")
    if not notes:
        return "路径较干净"
    return "；".join(notes)


def build_result_reason(
    *,
    success: bool,
    max_steps_reached: bool,
    pred_answer: str,
    gold_answer: str,
    final_phase: str,
    failed_tool_calls: int,
    invalid_answer_count: int,
    invalid_phase_count: int,
    http_4xx_count: int,
) -> str:
    if success:
        if failed_tool_calls or invalid_answer_count or invalid_phase_count:
            return "最终答案是对的，但中途存在阶段违规、提前作答或失败调用，流程靠回退修正后才收敛。"
        return "路径整体顺，收集到证据后正常进入 CONCLUDE 并给出正确答案。"

    reasons: list[str] = []
    if max_steps_reached:
        reasons.append("流程耗尽步数")
    if not pred_answer:
        reasons.append("没有落出最终答案")
    elif gold_answer and pred_answer != gold_answer:
        reasons.append(f"最终答案 `{compact(pred_answer, 32)}` 和 gold `{compact(gold_answer, 32)}` 不一致")
    if final_phase != "CONCLUDE":
        reasons.append(f"结束时停在 `{final_phase}`")
    if invalid_phase_count:
        reasons.append("阶段推进多次被 phase guard 拦下")
    if invalid_answer_count:
        reasons.append("出现提前 ANSWER")
    if failed_tool_calls:
        reasons.append("关键信息获取里有失败调用")
    if http_4xx_count:
        reasons.append("网页抓取里出现 HTTP 4xx")
    if not reasons:
        return "最终结果未对上 gold，当前轨迹里也没有明显补救动作。"
    return "；".join(reasons) + "。"


def build_task_review(
    *,
    seq: int,
    iteration_index: int,
    task_id: str,
    state: dict[str, Any],
    run_dir: Path,
) -> TaskReview:
    env = state.get("env_result", {})
    evaluation = env.get("evaluation", {})
    task_context = state.get("task_context", {})
    task_dir = run_dir / f"iteration_{iteration_index:02d}" / task_id
    state_path = task_dir / "state.json"
    task_json_path = Path(task_context.get("data_dir", "")) / "task.json"

    transitions = env.get("phase_transitions", [])
    phase_path, final_phase = phase_path_from_transitions(transitions)
    tool_trajectory = env.get("tool_trajectory", [])
    tool_map = {int(item.get("step_index", 0)): item for item in tool_trajectory}
    transition_map = {int(item.get("step_index", 0)): item for item in transitions}
    raw_output = env.get("raw_executor_output", "") or ""
    steps = read_executor_steps(task_dir)
    accepted_answer = accepted_answer_step(raw_output, steps)

    first_ts = steps[0].timestamp if steps else None
    last_ts = steps[-1].timestamp if steps else None
    wall_span_sec = None
    if first_ts and last_ts:
        wall_span_sec = int((last_ts - first_ts).total_seconds())

    phase_guard_tool_rejections = sum(
        1
        for item in tool_trajectory
        if "Tool use is not allowed in the current phase." in str(item.get("error", ""))
    )
    http_4xx_count = 0
    for item in tool_trajectory:
        raw_result = item.get("raw_result")
        if isinstance(raw_result, dict):
            status_code = raw_result.get("status_code")
            if isinstance(status_code, int) and 400 <= status_code < 500:
                http_4xx_count += 1

    current_phase = "INIT"
    step_records: list[dict[str, Any]] = []
    for step in steps:
        phase_before = current_phase
        outcome = ""
        accepted = False
        detail = ""

        if step.action_type == "CALL":
            tool_item = tool_map.get(step.step_index)
            if tool_item:
                accepted = True
                success = bool(tool_item.get("success", False))
                outcome = "工具返回成功" if success else "工具调用失败"
                detail = compact(tool_item.get("error") or tool_item.get("observation") or "", 120)
            else:
                outcome = "未在 state 里记录到对应工具调用"
        elif step.action_type == "NEXT":
            transition_item = transition_map.get(step.step_index)
            if transition_item:
                accepted = True
                current_phase = str(transition_item.get("to_phase", current_phase))
                outcome = f"阶段推进成功，进入 {current_phase}"
            else:
                outcome = "阶段推进被 phase guard 拒绝"
        elif step.action_type == "ANSWER":
            if accepted_answer == step.step_index:
                accepted = True
                outcome = "最终答案被接受"
            else:
                outcome = "答案输出被拒绝"
        else:
            outcome = "响应格式未被当前解析器识别"

        phase_after = current_phase
        step_records.append(
            {
                "step_index": step.step_index,
                "timestamp": step.timestamp.strftime("%H:%M:%S") if step.timestamp else "",
                "phase_before": phase_before,
                "phase_after": phase_after,
                "action_type": step.action_type,
                "tool_name": step.tool_name,
                "arguments": step.arguments,
                "next_phase": step.next_phase,
                "answer": step.answer,
                "accepted": accepted,
                "outcome": outcome,
                "thought": compact(step.thought, 180),
                "detail": detail,
            }
        )

    pred_answer = str(env.get("final_choice_label") or env.get("final_answer") or "")
    gold_answer = str(task_context.get("gold_answer") or "")
    success = bool(evaluation.get("task_success"))
    if "task_success" not in evaluation:
        success = str(pred_answer) == str(gold_answer)

    invalid_answer_count = raw_output.count("Invalid answer output.")
    invalid_phase_count = raw_output.count("Invalid phase transition.")
    runtime_feedback_count = raw_output.count("[runtime_feedback]")
    fallback_conclude_count = raw_output.count("[fallback conclude]")
    failed_tool_calls = sum(1 for item in tool_trajectory if not bool(item.get("success", False)))
    max_steps_reached = "max steps reached" in str(env.get("executor_summary", ""))

    notes = build_notes(
        success=success,
        failed_tool_calls=failed_tool_calls,
        invalid_answer_count=invalid_answer_count,
        invalid_phase_count=invalid_phase_count,
        fallback_conclude_count=fallback_conclude_count,
        http_4xx_count=http_4xx_count,
        max_steps_reached=max_steps_reached,
        pred_answer=pred_answer,
        gold_answer=gold_answer,
    )
    result_reason = build_result_reason(
        success=success,
        max_steps_reached=max_steps_reached,
        pred_answer=pred_answer,
        gold_answer=gold_answer,
        final_phase=final_phase,
        failed_tool_calls=failed_tool_calls,
        invalid_answer_count=invalid_answer_count,
        invalid_phase_count=invalid_phase_count,
        http_4xx_count=http_4xx_count,
    )

    return TaskReview(
        seq=seq,
        task_id=task_id,
        iteration_index=iteration_index,
        state_path=state_path,
        task_json_path=task_json_path,
        task_dir=task_dir,
        question=str(state.get("task_prompt") or ""),
        gold_answer=gold_answer,
        pred_answer=pred_answer,
        success=success,
        phase_path=phase_path,
        final_phase=final_phase,
        max_step=max((step.step_index for step in steps), default=0),
        wall_span_sec=wall_span_sec,
        tool_calls=len(tool_trajectory),
        failed_tool_calls=failed_tool_calls,
        phase_transitions=len(transitions),
        runtime_feedback_count=runtime_feedback_count,
        invalid_answer_count=invalid_answer_count,
        invalid_phase_count=invalid_phase_count,
        phase_guard_tool_rejections=phase_guard_tool_rejections,
        fallback_conclude_count=fallback_conclude_count,
        http_4xx_count=http_4xx_count,
        max_steps_reached=max_steps_reached,
        executor_summary=str(env.get("executor_summary") or ""),
        accuracy=evaluation.get("accuracy"),
        efficiency=evaluation.get("efficiency"),
        step_records=step_records,
        notes=notes,
        result_reason=result_reason,
        doc_name=f"task_{seq:02d}_{task_id[:8]}.md",
    )


def render_top_readme(
    *,
    title: str,
    run_name: str,
    run_dir: Path,
    reviews_by_iter: dict[int, list[TaskReview]],
) -> str:
    lines = [f"# {title}", "", f"- run name：`{run_name}`", f"- run dir：`{run_dir}`", "", "## 目录", ""]
    for iteration_index in sorted(reviews_by_iter):
        lines.append(f"- [iteration_{iteration_index:02d}](iteration_{iteration_index:02d}/README.md)")
    lines.append("- [两轮对错总表](两轮对错总表.md)")
    lines.append("")

    for iteration_index in sorted(reviews_by_iter):
        reviews = reviews_by_iter[iteration_index]
        success_count = sum(review.success for review in reviews)
        runtime_feedback_tasks = sum(review.runtime_feedback_count > 0 for review in reviews)
        failed_call_tasks = sum(review.failed_tool_calls > 0 for review in reviews)
        max_step_tasks = sum(review.max_steps_reached for review in reviews)
        http_4xx_tasks = sum(review.http_4xx_count > 0 for review in reviews)
        skill_before = run_dir / f"iteration_{iteration_index:02d}" / "skill_before_actor" / "gaia-general-skill" / "SKILL.md"
        skill_after = run_dir / f"iteration_{iteration_index:02d}" / "skill_after_actor" / "gaia-general-skill" / "SKILL.md"
        lines.extend(
            [
                f"## iteration_{iteration_index:02d}",
                "",
                f"- success：`{success_count}/{len(reviews)}`",
                f"- 含 `max steps reached` 的题数：`{max_step_tasks}`",
                f"- 含运行时反馈的题数：`{runtime_feedback_tasks}`",
                f"- 含显式失败调用的题数：`{failed_call_tasks}`",
                f"- 含 HTTP 4xx 页面抓取的题数：`{http_4xx_tasks}`",
                f"- skill before actor：[{skill_before.name}]({skill_before})" if skill_before.exists() else "- skill before actor：`missing`",
                f"- skill after actor：[{skill_after.name}]({skill_after})" if skill_after.exists() else "- skill after actor：`missing`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_total_table(
    *,
    reviews_1: list[TaskReview],
    reviews_2: list[TaskReview],
) -> str:
    by_task_1 = {review.task_id: review for review in reviews_1}
    by_task_2 = {review.task_id: review for review in reviews_2}
    task_ids = [review.task_id for review in reviews_1]

    both_correct = sum(by_task_1[task_id].success and by_task_2[task_id].success for task_id in task_ids)
    both_wrong = sum((not by_task_1[task_id].success) and (not by_task_2[task_id].success) for task_id in task_ids)
    recovered = sum((not by_task_1[task_id].success) and by_task_2[task_id].success for task_id in task_ids)
    regressed = sum(by_task_1[task_id].success and (not by_task_2[task_id].success) for task_id in task_ids)

    lines = [
        "# 两轮对错总表",
        "",
        f"- iteration_01：`{sum(review.success for review in reviews_1)}/{len(reviews_1)}`",
        f"- iteration_02：`{sum(review.success for review in reviews_2)}/{len(reviews_2)}`",
        f"- 两轮都对：`{both_correct}`",
        f"- 两轮都错：`{both_wrong}`",
        f"- 第二轮修回：`{recovered}`",
        f"- 第二轮退化：`{regressed}`",
        "",
        "| 序号 | task_id | iteration_01 | iteration_02 | 变化 | iteration_01 备注 | iteration_02 备注 | 文档 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for review_1 in reviews_1:
        review_2 = by_task_2[review_1.task_id]
        if review_1.success == review_2.success:
            change = "持平"
        elif (not review_1.success) and review_2.success:
            change = "修回"
        else:
            change = "退化"
        doc_links = (
            f"[iter1](iteration_01/{review_1.doc_name}) / "
            f"[iter2](iteration_02/{review_2.doc_name})"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(review_1.seq),
                    review_1.task_id[:8],
                    "对" if review_1.success else "错",
                    "对" if review_2.success else "错",
                    change,
                    md_escape(compact(review_1.notes, 60)),
                    md_escape(compact(review_2.notes, 60)),
                    doc_links,
                ]
            )
            + " |"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_iteration_readme(
    *,
    iteration_index: int,
    reviews: list[TaskReview],
    run_dir: Path,
    compare_reviews: list[TaskReview] | None = None,
) -> str:
    success_count = sum(review.success for review in reviews)
    runtime_feedback_tasks = sum(review.runtime_feedback_count > 0 for review in reviews)
    failed_call_tasks = sum(review.failed_tool_calls > 0 for review in reviews)
    max_step_tasks = sum(review.max_steps_reached for review in reviews)
    invalid_next_tasks = sum(review.invalid_phase_count > 0 for review in reviews)
    invalid_answer_tasks = sum(review.invalid_answer_count > 0 for review in reviews)

    lines = [
        f"# iteration_{iteration_index:02d}",
        "",
        f"- iteration dir：`{run_dir / f'iteration_{iteration_index:02d}'}`",
        f"- success：`{success_count}/{len(reviews)}`",
        f"- 含 `max steps reached` 的题数：`{max_step_tasks}`",
        f"- 含运行时反馈的题数：`{runtime_feedback_tasks}`",
        f"- 含显式失败调用的题数：`{failed_call_tasks}`",
        f"- 含非法 NEXT 的题数：`{invalid_next_tasks}`",
        f"- 含提前 ANSWER 的题数：`{invalid_answer_tasks}`",
        "",
    ]

    skill_before = run_dir / f"iteration_{iteration_index:02d}" / "skill_before_actor" / "gaia-general-skill" / "SKILL.md"
    skill_after = run_dir / f"iteration_{iteration_index:02d}" / "skill_after_actor" / "gaia-general-skill" / "SKILL.md"
    if skill_before.exists() or skill_after.exists():
        lines.extend(
            [
                "## Skill",
                "",
                f"- before actor：[{skill_before.name}]({skill_before})" if skill_before.exists() else "- before actor：`missing`",
                f"- after actor：[{skill_after.name}]({skill_after})" if skill_after.exists() else "- after actor：`missing`",
                "",
            ]
        )

    if compare_reviews is not None:
        compare_map = {review.task_id: review for review in compare_reviews}
        recovered = sum((not compare_map[review.task_id].success) and review.success for review in reviews)
        regressed = sum(compare_map[review.task_id].success and (not review.success) for review in reviews)
        lines.extend(
            [
                "## 与上一轮对比",
                "",
                f"- 修回：`{recovered}`",
                f"- 退化：`{regressed}`",
                "",
            ]
        )

    lines.extend(
        [
            "## 逐题总览",
            "",
            "| 序号 | task_id | 对错 | phase path | 步数 | 失败调用 | 运行时反馈 | 备注 | 文档 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for review in reviews:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(review.seq),
                    review.task_id[:8],
                    "对" if review.success else "错",
                    md_escape(compact(review.phase_path, 36)),
                    str(review.max_step),
                    str(review.failed_tool_calls),
                    str(review.runtime_feedback_count),
                    md_escape(compact(review.notes, 60)),
                    f"[{review.doc_name}]({review.doc_name})",
                ]
            )
            + " |"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_step_line(step: dict[str, Any]) -> str:
    prefix = f"{step['step_index']}. "
    if step["action_type"] == "CALL":
        body = f"`CALL {step['tool_name']}`"
        if step["arguments"]:
            body += f" `args={compact(step['arguments'], 70)}`"
    elif step["action_type"] == "NEXT":
        body = f"`NEXT {step['next_phase']}`"
    elif step["action_type"] == "ANSWER":
        body = f"`ANSWER {compact(step['answer'], 40)}`"
    else:
        body = "`UNKNOWN`"

    status = "accepted" if step["accepted"] else "rejected"
    ts = f"`{step['timestamp']}` " if step["timestamp"] else ""
    detail = f"；{step['detail']}" if step["detail"] else ""
    thought = f"；thought：{step['thought']}" if step["thought"] else ""
    return (
        f"{prefix}{ts}{body}，phase `{step['phase_before']}` -> `{step['phase_after']}`，"
        f"{status}，{step['outcome']}{detail}{thought}"
    )


def render_task_doc(
    *,
    review: TaskReview,
    previous_review: TaskReview | None = None,
) -> str:
    lines = [
        f"# task_{review.seq:02d}_{review.task_id[:8]}",
        "",
        f"- task_id：`{review.task_id}`",
        f"- iteration：`iteration_{review.iteration_index:02d}`",
        f"- state：[{review.state_path.name}]({review.state_path})",
        f"- task manifest：[{review.task_json_path.name}]({review.task_json_path})" if review.task_json_path.exists() else f"- task manifest：`{review.task_json_path}`",
        "",
        "## 题目",
        "",
        review.question,
        "",
        "## 结果",
        "",
        f"- success：`{review.success}`",
        f"- gold：`{review.gold_answer}`",
        f"- pred：`{review.pred_answer}`",
        f"- phase path：`{review.phase_path}`",
        f"- final phase：`{review.final_phase}`",
        f"- executor summary：`{review.executor_summary}`",
        f"- accuracy：`{review.accuracy}`",
        f"- efficiency：`{review.efficiency}`",
        "",
        "## 节奏指标",
        "",
        f"- executor 响应步数：`{review.max_step}`",
        f"- 估算 wall span：`{review.wall_span_sec}s`" if review.wall_span_sec is not None else "- 估算 wall span：`n/a`",
        f"- 工具调用数：`{review.tool_calls}`",
        f"- 失败调用数：`{review.failed_tool_calls}`",
        f"- 接受的 phase transition 数：`{review.phase_transitions}`",
        f"- runtime feedback 次数：`{review.runtime_feedback_count}`",
        f"- 提前 ANSWER 次数：`{review.invalid_answer_count}`",
        f"- 非法 NEXT 次数：`{review.invalid_phase_count}`",
        f"- phase guard 工具拒绝次数：`{review.phase_guard_tool_rejections}`",
        f"- fallback conclude 次数：`{review.fallback_conclude_count}`",
        f"- HTTP 4xx 次数：`{review.http_4xx_count}`",
        "",
    ]

    if previous_review is not None:
        if previous_review.success == review.success:
            delta = "结果持平"
        elif (not previous_review.success) and review.success:
            delta = "本轮修回"
        else:
            delta = "本轮退化"
        lines.extend(
            [
                "## 与上一轮对比",
                "",
                f"- 结果变化：`{delta}`",
                f"- 上一轮 success：`{previous_review.success}`",
                f"- 上一轮 pred：`{previous_review.pred_answer}`",
                f"- 上一轮 phase path：`{previous_review.phase_path}`",
                f"- 步数变化：`{previous_review.max_step} -> {review.max_step}`",
                f"- 失败调用变化：`{previous_review.failed_tool_calls} -> {review.failed_tool_calls}`",
                f"- runtime feedback 变化：`{previous_review.runtime_feedback_count} -> {review.runtime_feedback_count}`",
                "",
            ]
        )

    lines.extend(
        [
            "## 执行概览",
            "",
        ]
    )
    for step in review.step_records:
        lines.append(render_step_line(step))

    lines.extend(
        [
            "",
            "## 成败判断",
            "",
            review.result_reason,
            "",
            "## 收尾",
            "",
            f"这题的简评：{review.notes}。",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def build_reviews(run_dir: Path, iteration_index: int, selected_task_ids: list[str]) -> list[TaskReview]:
    summary_path = run_dir / f"iteration_{iteration_index:02d}" / "iteration_summary.json"
    summary = load_json(summary_path)
    state_map = {state["task_id"]: state for state in summary.get("states", [])}
    reviews: list[TaskReview] = []
    for seq, task_id in enumerate(selected_task_ids, start=1):
        if task_id not in state_map:
            continue
        reviews.append(
            build_task_review(
                seq=seq,
                iteration_index=iteration_index,
                task_id=task_id,
                state=state_map[task_id],
                run_dir=run_dir,
            )
        )
    return reviews


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-iteration GAIA review docs.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--title", default="GAIA C20 前两轮 53 题逐题复盘")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    out_dir = args.output_dir.resolve()
    ensure_dir(out_dir)

    run_summary = load_json(run_dir / "run_summary.json")
    selected = load_json(run_dir / "selected_tasks.json")
    selected_task_ids = list(selected.get("task_ids", []))

    reviews_1 = build_reviews(run_dir, 1, selected_task_ids)
    reviews_2 = build_reviews(run_dir, 2, selected_task_ids)
    reviews_by_iter = {1: reviews_1, 2: reviews_2}

    write_text(
        out_dir / "README.md",
        render_top_readme(
            title=args.title,
            run_name=str(run_summary.get("run_name", "")),
            run_dir=run_dir,
            reviews_by_iter=reviews_by_iter,
        ),
    )
    write_text(out_dir / "两轮对错总表.md", render_total_table(reviews_1=reviews_1, reviews_2=reviews_2))
    write_text(
        out_dir / "iteration_01" / "README.md",
        render_iteration_readme(iteration_index=1, reviews=reviews_1, run_dir=run_dir),
    )
    write_text(
        out_dir / "iteration_02" / "README.md",
        render_iteration_readme(
            iteration_index=2,
            reviews=reviews_2,
            run_dir=run_dir,
            compare_reviews=reviews_1,
        ),
    )

    review_map_1 = {review.task_id: review for review in reviews_1}
    for review in reviews_1:
        write_text(
            out_dir / "iteration_01" / review.doc_name,
            render_task_doc(review=review),
        )
    for review in reviews_2:
        write_text(
            out_dir / "iteration_02" / review.doc_name,
            render_task_doc(review=review, previous_review=review_map_1.get(review.task_id)),
        )

    summary_lines = [
        f"generated: {out_dir}",
        f"iteration_01: {sum(review.success for review in reviews_1)}/{len(reviews_1)}",
        f"iteration_02: {sum(review.success for review in reviews_2)}/{len(reviews_2)}",
    ]
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
