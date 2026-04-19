from __future__ import annotations

from typing import Any

from .gaia_scorer import normalize_str, question_scorer
from .schemas import EvaluationResult, ToolCallRecord


def normalize_answer(text: str) -> str:
    return normalize_str(text, remove_punct=False)


def evaluate_execution(
    *,
    final_choice_label: str,
    final_answer: str,
    executed_steps: list[ToolCallRecord],
    gold_tool_names: list[str],
    gold_trajectory: list[dict[str, Any]],
    gold_answer: str,
) -> EvaluationResult:
    raw_prediction = final_answer or final_choice_label
    predicted = normalize_answer(raw_prediction)
    gold = normalize_answer(gold_answer)
    success = question_scorer(raw_prediction, gold_answer)
    step_count = len(executed_steps)
    successful_tool_calls = sum(1 for item in executed_steps if item.success)
    efficiency = 0.0 if step_count == 0 else round(successful_tool_calls / step_count, 4)
    notes = f"pred={predicted!r} gold={gold!r} official_gaia_match={success}"
    return EvaluationResult(
        accuracy=1.0 if success else 0.0,
        efficiency=efficiency,
        tool_any_order=0.0,
        tool_in_order=0.0,
        tool_exact_match=0.0,
        parameter_accuracy=0.0,
        task_success=success,
        notes=notes,
    )
