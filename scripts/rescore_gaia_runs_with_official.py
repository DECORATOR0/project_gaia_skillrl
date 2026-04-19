#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gaia_skillrl.gaia_scorer import official_scorer_path, question_scorer, using_official_scorer


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def old_success_from_state(state: dict[str, Any]) -> bool:
    return bool(
        state.get("env_result", {})
        .get("evaluation", {})
        .get("task_success", False)
    )


def new_success_from_state(state: dict[str, Any]) -> bool:
    env_result = state.get("env_result", {})
    task_context = state.get("task_context", {})
    raw_prediction = env_result.get("final_answer") or env_result.get("final_choice_label")
    gold_answer = task_context.get("gold_answer", "")
    return bool(question_scorer(raw_prediction, gold_answer))


@dataclass
class IterationStats:
    iteration_index: int
    task_count: int
    old_success_count: int
    new_success_count: int
    changed_task_ids: list[str]
    corrected_to_success: list[str]
    corrected_to_failure: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration_index": self.iteration_index,
            "task_count": self.task_count,
            "old_success_count": self.old_success_count,
            "new_success_count": self.new_success_count,
            "delta": self.new_success_count - self.old_success_count,
            "changed_task_ids": self.changed_task_ids,
            "corrected_to_success": self.corrected_to_success,
            "corrected_to_failure": self.corrected_to_failure,
        }


def compute_stats_for_items(
    items: list[dict[str, Any]],
    *,
    iteration_index: int,
) -> IterationStats:
    changed_task_ids: list[str] = []
    corrected_to_success: list[str] = []
    corrected_to_failure: list[str] = []
    old_success_count = 0
    new_success_count = 0

    for item in items:
        task_id = str(item.get("task_id", ""))
        old_success = old_success_from_state(item)
        new_success = new_success_from_state(item)
        old_success_count += int(old_success)
        new_success_count += int(new_success)
        if old_success != new_success:
            changed_task_ids.append(task_id)
            if new_success:
                corrected_to_success.append(task_id)
            else:
                corrected_to_failure.append(task_id)

    return IterationStats(
        iteration_index=iteration_index,
        task_count=len(items),
        old_success_count=old_success_count,
        new_success_count=new_success_count,
        changed_task_ids=changed_task_ids,
        corrected_to_success=corrected_to_success,
        corrected_to_failure=corrected_to_failure,
    )


def compute_iteration_stats(iteration_summary_path: Path) -> IterationStats:
    payload = load_json(iteration_summary_path)
    return compute_stats_for_items(
        payload.get("states", []),
        iteration_index=int(payload.get("iteration_index", 0)),
    )


def list_iteration_summaries(run_dir: Path) -> list[Path]:
    return sorted(run_dir.glob("iteration_*/iteration_summary.json"))


def summarize_run(run_dir: Path) -> dict[str, Any]:
    iteration_paths = list_iteration_summaries(run_dir)
    iteration_stats = [compute_iteration_stats(path) for path in iteration_paths]

    run_summary_path = run_dir / "run_summary.json"
    final_source = "iteration_summary.json"
    final_stats: IterationStats | None = iteration_stats[-1] if iteration_stats else None
    if run_summary_path.exists():
        run_summary = load_json(run_summary_path)
        final_stats = compute_stats_for_items(
            run_summary.get("tasks", []),
            iteration_index=0,
        )
        final_source = "run_summary.json"

    return {
        "run_dir": str(run_dir),
        "scorer_source": "official_vendored_hf_space" if using_official_scorer() else "local_fallback_equivalent",
        "official_scorer_path": str(official_scorer_path()),
        "final_source": final_source,
        "final_task_count": final_stats.task_count if final_stats else 0,
        "old_final_success_count": final_stats.old_success_count if final_stats else 0,
        "new_final_success_count": final_stats.new_success_count if final_stats else 0,
        "final_delta": (
            final_stats.new_success_count - final_stats.old_success_count
            if final_stats
            else 0
        ),
        "final_changed_task_ids": final_stats.changed_task_ids if final_stats else [],
        "final_corrected_to_success": final_stats.corrected_to_success if final_stats else [],
        "final_corrected_to_failure": final_stats.corrected_to_failure if final_stats else [],
        "iterations": [item.to_dict() for item in iteration_stats],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-score GAIA run outputs with the vendored official GAIA scorer."
    )
    parser.add_argument("run_dirs", nargs="+", help="One or more run directories.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for raw_run_dir in args.run_dirs:
        run_dir = Path(raw_run_dir).resolve()
        report = summarize_run(run_dir)
        report_path = run_dir / "official_gaia_rescore.json"
        dump_json(report_path, report)
        print(
            f"{run_dir.name}: "
            f"{report['old_final_success_count']}/{report['final_task_count']} -> "
            f"{report['new_final_success_count']}/{report['final_task_count']} "
            f"(delta {report['final_delta']:+d})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
