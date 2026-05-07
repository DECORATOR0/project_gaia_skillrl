#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaia_skillrl.gaia_scorer import question_scorer


RUN_RE = re.compile(
    r"^(?P<prefix>20260504_0110\d{2})_abprio_"
    r"(?P<model>8b|9b)_p(?P<priority>\d+)_"
    r"(?P<input>cap\d+|nocap)_(?P<thinking>think4k|think_unlimited)_"
    r"(?P<output>out8k|out12k|out_unlimited)_fresh_bootv3_"
    r"(?P<phase>.+)$"
)


PHASE_LABELS = {
    "boot_dev_c10": "boot_dev",
    "boot_test_c10": "boot_test",
    "A_full_dev_eval_c10": "A_dev",
    "A_full_test_eval_c10": "A_test",
    "B_sharded_dev_eval_c10": "B_dev",
    "B_sharded_test_eval_c10": "B_test",
}


@dataclass
class RunMeta:
    run_dir: Path
    model: str
    priority: int
    input_cap: str
    thinking: str
    output_cap: str
    phase: str
    phase_label: str


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_meta(run_dir: Path) -> RunMeta | None:
    match = RUN_RE.match(run_dir.name)
    if not match:
        return None
    phase = match.group("phase")
    if phase not in PHASE_LABELS:
        return None
    return RunMeta(
        run_dir=run_dir,
        model=match.group("model"),
        priority=int(match.group("priority")),
        input_cap=match.group("input"),
        thinking=match.group("thinking"),
        output_cap=match.group("output"),
        phase=phase,
        phase_label=PHASE_LABELS[phase],
    )


def answer_attempts(state: dict[str, Any]) -> list[dict[str, Any]]:
    trace = (state.get("env_result") or {}).get("action_trace") or []
    attempts: list[dict[str, Any]] = []
    for item in trace:
        if item.get("action_type") != "answer":
            continue
        answer = str(item.get("answer") or "").strip()
        if not answer:
            continue
        attempts.append(
            {
                "step_index": item.get("step_index"),
                "phase_before": item.get("phase_before"),
                "phase_after": item.get("phase_after"),
                "outcome": item.get("outcome"),
                "answer": answer,
            }
        )
    return attempts


def score_answer(answer: str, gold: str) -> bool:
    return bool(question_scorer(answer, gold))


def summarize_run(meta: RunMeta) -> dict[str, Any]:
    state_paths = sorted(meta.run_dir.glob("iteration_01/*/state.json"))
    task_rows: list[dict[str, Any]] = []
    first_success = 0
    last_success = 0
    any_success = 0
    no_answer = 0
    original_success = 0
    original_empty = 0
    answer_attempt_count = 0

    for state_path in state_paths:
        state = load_json(state_path)
        task_id = str(state.get("task_id") or state_path.parent.name)
        gold = str((state.get("task_context") or {}).get("gold_answer") or "")
        env = state.get("env_result") or {}
        original_answer = str(env.get("final_answer") or env.get("final_choice_label") or "").strip()
        original_ok = bool((env.get("evaluation") or {}).get("task_success"))
        original_success += int(original_ok)
        original_empty += int(not original_answer)

        attempts = answer_attempts(state)
        answer_attempt_count += len(attempts)
        if not attempts:
            no_answer += 1
            task_rows.append(
                {
                    "task_id": task_id,
                    "gold_answer": gold,
                    "answer_attempts": 0,
                    "first_answer": "",
                    "first_step": "",
                    "first_phase": "",
                    "first_success": False,
                    "last_answer": "",
                    "last_step": "",
                    "last_phase": "",
                    "last_success": False,
                    "any_success": False,
                    "any_correct_answer": "",
                    "any_correct_step": "",
                    "any_correct_phase": "",
                    "original_success": original_ok,
                    "original_answer": original_answer,
                    "state_path": str(state_path),
                }
            )
            continue

        first = attempts[0]
        last = attempts[-1]
        first_ok = score_answer(first["answer"], gold)
        last_ok = score_answer(last["answer"], gold)
        correct_attempt = next((item for item in attempts if score_answer(item["answer"], gold)), None)
        any_ok = correct_attempt is not None
        first_success += int(first_ok)
        last_success += int(last_ok)
        any_success += int(any_ok)

        task_rows.append(
            {
                "task_id": task_id,
                "gold_answer": gold,
                "answer_attempts": len(attempts),
                "first_answer": first["answer"],
                "first_step": first.get("step_index"),
                "first_phase": first.get("phase_before"),
                "first_success": first_ok,
                "last_answer": last["answer"],
                "last_step": last.get("step_index"),
                "last_phase": last.get("phase_before"),
                "last_success": last_ok,
                "any_success": any_ok,
                "any_correct_answer": correct_attempt["answer"] if correct_attempt else "",
                "any_correct_step": correct_attempt.get("step_index") if correct_attempt else "",
                "any_correct_phase": correct_attempt.get("phase_before") if correct_attempt else "",
                "original_success": original_ok,
                "original_answer": original_answer,
                "state_path": str(state_path),
            }
        )

    return {
        "run_dir": str(meta.run_dir),
        "run_name": meta.run_dir.name,
        "model": meta.model,
        "priority": meta.priority,
        "input": meta.input_cap,
        "thinking": meta.thinking,
        "output": meta.output_cap,
        "phase": meta.phase,
        "phase_label": meta.phase_label,
        "states": len(state_paths),
        "original_success": original_success,
        "original_empty": original_empty,
        "tasks_with_answer_attempt": len(state_paths) - no_answer,
        "tasks_without_answer_attempt": no_answer,
        "answer_attempt_count": answer_attempt_count,
        "first_nonempty_success": first_success,
        "last_nonempty_success": last_success,
        "any_correct_success": any_success,
        "tasks": task_rows,
    }


def write_csv(path: Path, summaries: list[dict[str, Any]]) -> None:
    fields = [
        "model",
        "priority",
        "input",
        "thinking",
        "output",
        "phase_label",
        "states",
        "original_success",
        "original_empty",
        "tasks_with_answer_attempt",
        "tasks_without_answer_attempt",
        "answer_attempt_count",
        "first_nonempty_success",
        "last_nonempty_success",
        "any_correct_success",
        "run_name",
        "run_dir",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in summaries:
            writer.writerow({field: item.get(field, "") for field in fields})


def write_task_csv(path: Path, summaries: list[dict[str, Any]]) -> None:
    fields = [
        "model",
        "priority",
        "input",
        "thinking",
        "output",
        "phase_label",
        "task_id",
        "answer_attempts",
        "first_success",
        "last_success",
        "any_success",
        "first_step",
        "first_phase",
        "last_step",
        "last_phase",
        "any_correct_step",
        "any_correct_phase",
        "first_answer",
        "last_answer",
        "any_correct_answer",
        "gold_answer",
        "state_path",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            prefix = {
                "model": summary["model"],
                "priority": summary["priority"],
                "input": summary["input"],
                "thinking": summary["thinking"],
                "output": summary["output"],
                "phase_label": summary["phase_label"],
            }
            for task in summary["tasks"]:
                row = dict(prefix)
                row.update({field: task.get(field, "") for field in fields if field not in prefix})
                writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-score rejected non-empty <ANSWER> attempts as if V5.2 any_phase acceptance were enabled."
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-4"),
    )
    parser.add_argument(
        "--phase",
        action="append",
        choices=sorted(set(PHASE_LABELS.values())),
        help="Phase label to include. Can be repeated. Default: boot_dev and boot_test only.",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260504_0110_abprio_rejected_answer_rescore"),
    )
    args = parser.parse_args()

    wanted_phases = set(args.phase or ["boot_dev", "boot_test"])
    metas: list[RunMeta] = []
    for run_dir in sorted(args.run_root.glob("20260504_0110*_abprio_*")):
        if not run_dir.is_dir():
            continue
        meta = parse_meta(run_dir)
        if meta is None or meta.phase_label not in wanted_phases:
            continue
        metas.append(meta)

    summaries = [summarize_run(meta) for meta in metas]
    payload = {
        "run_root": str(args.run_root),
        "phases": sorted(wanted_phases),
        "summary_count": len(summaries),
        "scoring_modes": {
            "first_nonempty_success": "Mimics runtime answer_acceptance_policy=any_phase: the first non-empty <ANSWER> would stop the task.",
            "last_nonempty_success": "Diagnostic only: score the last non-empty rejected answer in the trace.",
            "any_correct_success": "Optimistic upper bound: score success if any rejected answer attempt matches gold.",
        },
        "summaries": summaries,
    }

    json_path = args.output_prefix.with_suffix(".json")
    csv_path = args.output_prefix.with_suffix(".csv")
    task_csv_path = args.output_prefix.with_name(args.output_prefix.name + "_tasks").with_suffix(".csv")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(csv_path, summaries)
    write_task_csv(task_csv_path, summaries)

    print(f"WROTE {json_path}")
    print(f"WROTE {csv_path}")
    print(f"WROTE {task_csv_path}")
    print("SUMMARY")
    for item in summaries:
        print(
            f"{item['model']} p{item['priority']:02d} {item['input']} {item['thinking']} {item['output']} "
            f"{item['phase_label']}: states={item['states']} "
            f"first={item['first_nonempty_success']} last={item['last_nonempty_success']} "
            f"any={item['any_correct_success']} attempts={item['answer_attempt_count']} "
            f"no_answer={item['tasks_without_answer_attempt']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
