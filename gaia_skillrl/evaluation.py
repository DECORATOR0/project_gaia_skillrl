from __future__ import annotations

import re
from typing import Any

from .schemas import EvaluationResult, ToolCallRecord


def normalize_answer(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = cleaned.strip("`")
    cleaned = cleaned.strip('"').strip("'")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip().lower()
    if cleaned.endswith("."):
        cleaned = cleaned[:-1].rstrip()
    return cleaned


def evaluate_execution(
    *,
    final_choice_label: str,
    final_answer: str,
    executed_steps: list[ToolCallRecord],
    gold_tool_names: list[str],
    gold_trajectory: list[dict[str, Any]],
    gold_answer: str,
) -> EvaluationResult:
    predicted = normalize_answer(final_answer or final_choice_label)
    gold = normalize_answer(gold_answer)
    success = bool(predicted) and predicted == gold
    step_count = len(executed_steps)
    successful_tool_calls = sum(1 for item in executed_steps if item.success)
    efficiency = 0.0 if step_count == 0 else round(successful_tool_calls / step_count, 4)
    notes = f"pred={predicted!r} gold={gold!r}"
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
