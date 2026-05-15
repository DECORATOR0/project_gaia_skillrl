from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
PREFIX = "20260514_0112_8b_bootv7v8_v3v6_matrix_remote132_gpu0_5"
PREFIX_MARKER = "_".join(PREFIX.split("_")[2:])
MANIFEST = ROOT / "runs/_queue_logs" / f"{PREFIX}_manifest.json"
OUT_DOC = ROOT / "实验设计与迭代/26.5.14_20260514_0112_GAIA_8B_V7V8_V3V6_72行验收log.md"
INDEX_DOC = ROOT / "实验设计与迭代/ZZ_当前待验收事项与版本索引.md"
RUN_DAY_DIR = ROOT / "runs/2026/5/2026-5-14"

BOOT_STAGES = [(1, ["bootv7", "bootv8"]), (2, ["bootv3", "bootv6"])]
METHODS = ["A_full", "B1"]
SPLIT_TOTAL = {"dev": 83, "test": 82}
RUN_DIR_CACHE: list[Path] | None = None
TOKEN_GUARD_CACHE: dict[Path, bool] = {}


@dataclass(frozen=True)
class PlannedSpec:
    order: int
    stage: int
    boot: str
    repeat: int
    method: str
    split: str
    key: str
    run_name: str


def now_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def planned_specs() -> list[PlannedSpec]:
    rows: list[PlannedSpec] = []
    order = 1
    for stage, boot_keys in BOOT_STAGES:
        for repeat in range(1, 4):
            for boot in boot_keys:
                for split in ("dev", "test"):
                    rows.append(
                        PlannedSpec(
                            order=order,
                            stage=stage,
                            boot=boot,
                            repeat=repeat,
                            method="boot-only",
                            split=split,
                            key=f"{boot}_r{repeat}_boot_{split}",
                            run_name=f"{PREFIX}_8b_{boot}_r{repeat}_boot_{split}_c20",
                        )
                    )
                    order += 1
        for method in METHODS:
            for repeat in range(1, 4):
                for boot in boot_keys:
                    for split in ("dev", "test"):
                        rows.append(
                            PlannedSpec(
                                order=order,
                                stage=stage,
                                boot=boot,
                                repeat=repeat,
                                method=method,
                                split=split,
                                key=f"{boot}_r{repeat}_{method}_{split}",
                                run_name=f"{PREFIX}_8b_{boot}_r{repeat}_{method}_{split}_eval_c20",
                            )
                        )
                        order += 1
    return rows


def run_name_suffix_for_match(run_name: str) -> str:
    match = re.match(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_(.+)$", run_name)
    return "_" + match.group(1) if match else run_name


def latest_run_dir(run_name: str) -> Path | None:
    global RUN_DIR_CACHE
    if RUN_DIR_CACHE is None:
        RUN_DIR_CACHE = [
            path
            for path in RUN_DAY_DIR.glob(f"*{PREFIX_MARKER}*")
            if path.is_dir()
        ]
    suffix = run_name_suffix_for_match(run_name)
    matches = [path for path in RUN_DIR_CACHE if path.name == run_name or path.name.endswith(suffix)]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def latest_iteration_dir(run_dir: Path | None) -> Path | None:
    if run_dir is None:
        return None
    iterations = [path for path in run_dir.glob("iteration_*") if path.is_dir()]
    return max(iterations, key=lambda path: path.name) if iterations else None


def selected_task_count(run_dir: Path | None) -> int:
    if run_dir is None:
        return 0
    path = run_dir / "selected_tasks.json"
    if not path.exists():
        return 0
    try:
        data = read_json(path)
    except Exception:
        return 0
    task_ids = data.get("task_ids") if isinstance(data, dict) else data
    return len(task_ids) if isinstance(task_ids, list) else 0


def recursive_token_guard_hit(obj: Any) -> bool:
    if isinstance(obj, dict):
        if "_token_guard" in obj and isinstance(obj["_token_guard"], dict):
            guard = obj["_token_guard"]
            if int(guard.get("dropped_message_count") or 0) > 0:
                return True
            if int(guard.get("initial_prompt_tokens") or 0) > int(guard.get("final_prompt_tokens") or 0):
                return True
        return any(recursive_token_guard_hit(value) for value in obj.values())
    if isinstance(obj, list):
        return any(recursive_token_guard_hit(value) for value in obj)
    return False


def task_has_token_guard(task_dir: Path) -> bool:
    cached = TOKEN_GUARD_CACHE.get(task_dir)
    if cached is not None:
        return cached
    for request_path in task_dir.glob("executor/executor_steps/*_request.json"):
        try:
            text = request_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "_token_guard" not in text and "truncated by token guard" not in text:
            continue
        try:
            if recursive_token_guard_hit(json.loads(text)):
                TOKEN_GUARD_CACHE[task_dir] = True
                return True
        except Exception:
            TOKEN_GUARD_CACHE[task_dir] = True
            return True
    TOKEN_GUARD_CACHE[task_dir] = False
    return False


def state_metrics(run_dir: Path | None, fallback_total: int) -> dict[str, Any]:
    iteration = latest_iteration_dir(run_dir)
    state_paths = sorted(iteration.glob("*/state.json")) if iteration else []
    total = selected_task_count(run_dir) or fallback_total
    metrics = {
        "landed": len(state_paths),
        "total": total,
        "success": 0,
        "nonempty": 0,
        "empty": 0,
        "missing": max(0, total - len(state_paths)),
        "hit24": 0,
        "forced": 0,
        "token_guard": 0,
        "web_calls": 0,
        "web_fail": 0,
        "fetch_calls": 0,
        "fetch_fail": 0,
        "tool_fail": 0,
        "answer_reject": 0,
        "phase_reject": 0,
        "avg_steps": "",
    }
    step_counts: list[int] = []
    for state_path in state_paths:
        try:
            data = read_json(state_path)
        except Exception:
            continue
        env = data.get("env_result") or {}
        evaluation = env.get("evaluation") or {}
        action_trace = env.get("action_trace") or []
        step_counts.append(len(action_trace))
        metrics["success"] += int(evaluation.get("task_success") is True)
        nonempty = bool(str(env.get("final_answer") or "").strip())
        metrics["nonempty"] += int(nonempty)
        metrics["empty"] += int(not nonempty)
        forced = any("forced" in str(step.get("outcome") or "") for step in action_trace)
        metrics["forced"] += int(forced)
        metrics["hit24"] += int(forced or len(action_trace) >= 24)
        if task_has_token_guard(state_path.parent):
            metrics["token_guard"] += 1
        for step in action_trace:
            tool = str(step.get("tool_name") or step.get("tool") or "").strip()
            success = step.get("success")
            accepted = step.get("accepted")
            action_type = str(step.get("action_type") or "").lower()
            if tool in {"web_search", "search"}:
                metrics["web_calls"] += 1
                metrics["web_fail"] += int(success is False)
            if tool in {"fetch_url", "browse"}:
                metrics["fetch_calls"] += 1
                metrics["fetch_fail"] += int(success is False)
            if tool:
                metrics["tool_fail"] += int(success is False)
            if action_type == "answer" and accepted is False:
                metrics["answer_reject"] += 1
            if accepted is False and action_type in {"next", "call"}:
                metrics["phase_reject"] += 1
    if step_counts:
        metrics["avg_steps"] = f"{sum(step_counts) / len(step_counts):.1f}"
    return metrics


def log_metrics(log_path: str | None) -> dict[str, Any]:
    if not log_path:
        return {"empty_retry": 0, "fail6": 0, "http400": 0, "error": ""}
    path = Path(log_path)
    if not path.exists():
        return {"empty_retry": 0, "fail6": 0, "http400": 0, "error": "log_missing"}
    text = path.read_text(encoding="utf-8", errors="ignore")
    lower = text.lower()
    error_lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(key in low for key in ["traceback", "error", "exception", "failed", "timeout", "killed", "exceeded wall"]):
            error_lines.append(line.strip())
    error = " | ".join(error_lines[-2:])
    if len(error) > 180:
        error = error[:177] + "..."
    return {
        "empty_retry": lower.count("empty content"),
        "fail6": lower.count("failed after 6 attempts") + lower.count("failed after 6") + lower.count("6 attempts"),
        "http400": lower.count("http 400") + lower.count("status code: 400") + lower.count("400 client error"),
        "error": error,
    }


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def duration_text(started_at: str | None, ended_at: str | None) -> str:
    start = parse_dt(started_at)
    end = parse_dt(ended_at) or datetime.now().astimezone()
    if start is None:
        return ""
    seconds = max(0, int((end - start).total_seconds()))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def compact_path(path: str | None) -> str:
    if not path:
        return ""
    return str(path).replace(str(ROOT) + "/", "")


def status_maps(manifest: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    completed: dict[str, dict[str, Any]] = {}
    for record in (manifest.get("completed") or {}).values():
        spec = record.get("spec") or {}
        key = spec.get("key")
        if key:
            completed[key] = record
    running: dict[str, dict[str, Any]] = {}
    for record in manifest.get("running") or []:
        spec = record.get("spec") or {}
        key = spec.get("key")
        if key:
            running[key] = record
    registered: dict[str, dict[str, Any]] = {}
    for spec in manifest.get("counted_eval_specs") or []:
        key = spec.get("key")
        if key:
            registered[key] = spec
    return completed, running, registered


def row_for(spec: PlannedSpec, manifest: dict[str, Any]) -> dict[str, Any]:
    completed, running, registered = status_maps(manifest)
    record = completed.get(spec.key) or running.get(spec.key)
    status = "planned_not_registered"
    returncode: Any = ""
    started_at = ""
    ended_at = ""
    log_path = ""
    lane = ""
    longtail = ""
    failure = ""
    if spec.key in completed:
        status = "completed" if completed[spec.key].get("returncode") == 0 else "eval_failed_partial"
        returncode = completed[spec.key].get("returncode")
        started_at = completed[spec.key].get("started_at") or ""
        ended_at = completed[spec.key].get("ended_at") or ""
        log_path = completed[spec.key].get("log_path") or ""
        lane = (completed[spec.key].get("lane") or {}).get("name", "")
    elif spec.key in running:
        progress = running[spec.key].get("progress") or {}
        longtail = "Y" if progress.get("longtail_ready") else ""
        status = "running_longtail_released" if longtail else "running"
        returncode = "live"
        started_at = running[spec.key].get("started_at") or ""
        log_path = running[spec.key].get("log_path") or ""
        lane = (running[spec.key].get("lane") or {}).get("name", "")
    elif spec.key in registered:
        status = "registered_waiting"

    run_name = ((record or {}).get("spec") or {}).get("run_name") or spec.run_name
    run_dir = latest_run_dir(run_name)
    total = selected_task_count(run_dir) or SPLIT_TOTAL[spec.split]
    metrics = state_metrics(run_dir, total)
    log_stat = log_metrics(log_path)
    if status == "planned_not_registered":
        failure = ""
    elif log_stat["error"]:
        failure = log_stat["error"]
    elif longtail:
        failure = "longtail lane released; process still live"
    score = f"{metrics['success']}/{metrics['total']}" if metrics["total"] else ""
    web = f"{metrics['web_fail']}/{metrics['web_calls']}" if metrics["web_calls"] else "0/0"
    fetch = f"{metrics['fetch_fail']}/{metrics['fetch_calls']}" if metrics["fetch_calls"] else "0/0"
    return {
        "order": spec.order,
        "stage": spec.stage,
        "boot": spec.boot,
        "repeat": spec.repeat,
        "method": spec.method,
        "split": spec.split,
        "status": status,
        "score": score,
        "landed": f"{metrics['landed']}/{metrics['total']}",
        "missing": metrics["missing"],
        "empty": metrics["empty"],
        "hit24": metrics["hit24"],
        "forced": metrics["forced"],
        "token_guard": metrics["token_guard"],
        "web": web,
        "fetch": fetch,
        "tool_fail": metrics["tool_fail"],
        "answer_reject": metrics["answer_reject"],
        "phase_reject": metrics["phase_reject"],
        "avg_steps": metrics["avg_steps"],
        "empty_retry": log_stat["empty_retry"],
        "fail6": log_stat["fail6"],
        "http400": log_stat["http400"],
        "returncode": returncode,
        "lane": lane,
        "duration": duration_text(started_at, ended_at),
        "longtail": longtail,
        "run_name": run_name,
        "log": compact_path(log_path),
        "failure": failure.replace("|", "/"),
    }


def md_escape(value: Any) -> str:
    text = str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def build_doc() -> str:
    manifest = read_json(MANIFEST)
    rows = [row_for(spec, manifest) for spec in planned_specs()]
    completed = sum(1 for row in rows if row["status"] in {"completed", "eval_failed_partial"})
    running = sum(1 for row in rows if row["status"].startswith("running"))
    planned = sum(1 for row in rows if row["status"] == "planned_not_registered")
    registered = sum(1 for row in rows if row["status"] == "registered_waiting")
    success_rows = [row for row in rows if row["status"] in {"completed", "eval_failed_partial", "running", "running_longtail_released"}]
    lines = [
        "# GAIA 8B V7/V8/V3/V6 72 行验收 Log",
        "",
        f"- 生成时间：`{now_text()}`",
        f"- prefix：`{PREFIX}`",
        f"- manifest：`{MANIFEST}`",
        f"- 当前 manifest 状态：`{manifest.get('status')}`，updated_at `{manifest.get('updated_at')}`。",
        f"- 72 行口径：stage1 `BOOT-V7/BOOT-V8`，stage2 `BOOT-V3/BOOT-V6`；每个版本 `3 repeat x (boot-only/A_full/B1) x dev/test`。",
        f"- 当前行状态汇总：completed/partial `{completed}`，running `{running}`，registered_waiting `{registered}`，planned_not_registered `{planned}`。",
        "- 长尾补位口径：进入 longtail_ready 后该 lane 立即视为可补位；长尾进程保留自然完成，只在影响后续资源或输出冲突时单独处理。",
        "- 调度修正：脚本默认 `BOOT_WORKERS=6`、`BOOTSTRAP_WORKERS=2`；boot pipeline 可排满 lane，同时 bootstrap 强模型并发仍受限。",
        "- 分数只读 `state.json -> env_result.evaluation.task_success`，缺失按错；未启动项只保留 planned 行，不参与当前均值解释。",
        "",
        "## 列说明",
        "",
        "- `web f/c` 和 `fetch f/c` 分别是 web/search 与 fetch/browse 工具失败数/调用数。",
        "- `emptyRetry/fail6/HTTP400` 从 launch log 文本统计，用于定位 executor/LLM 层错误。",
        "- `hit24` 为 action_trace 达到 24 步或触发 forced answer 的题数；`forced` 为 forced answer 题数。",
        "",
        "## 72 行表",
        "",
        "| # | stage | boot | r | method | split | status | score | landed | miss | empty | hit24 | forced | tokGuard | web f/c | fetch f/c | toolFail | ansRej | phaseRej | avgStep | emptyRetry | fail6 | HTTP400 | rc | lane | dur | LT | run_name | log | failure/error |",
        "| ---: | ---: | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        values = [
            row["order"],
            row["stage"],
            row["boot"],
            row["repeat"],
            row["method"],
            row["split"],
            row["status"],
            row["score"],
            row["landed"],
            row["missing"],
            row["empty"],
            row["hit24"],
            row["forced"],
            row["token_guard"],
            row["web"],
            row["fetch"],
            row["tool_fail"],
            row["answer_reject"],
            row["phase_reject"],
            row["avg_steps"],
            row["empty_retry"],
            row["fail6"],
            row["http400"],
            row["returncode"],
            row["lane"],
            row["duration"],
            row["longtail"],
            row["run_name"],
            row["log"],
            row["failure"],
        ]
        lines.append("| " + " | ".join(md_escape(value) for value in values) + " |")
    lines.extend(["", "## 当前只读聚合", ""])
    group: dict[tuple[str, str, str], list[tuple[int, int]]] = {}
    for row in success_rows:
        if not row["score"]:
            continue
        success_text, total_text = str(row["score"]).split("/", 1)
        try:
            success = int(success_text)
            total = int(total_text)
        except ValueError:
            continue
        group.setdefault((row["boot"], row["method"], row["split"]), []).append((success, total))
    lines.append("| boot | method | split | n_observed | scores | mean |")
    lines.append("| --- | --- | --- | ---: | --- | --- |")
    for key in sorted(group):
        vals = group[key]
        scores = ",".join(f"{success}/{total}" for success, total in vals)
        mean = sum(success for success, _ in vals) / len(vals)
        total = vals[0][1]
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {len(vals)} | {scores} | {mean:.1f}/{total} |")
    lines.extend(
        [
            "",
            "## 当前读数边界",
            "",
            "- stage2 downstream 尚未启动，72 行里后 24 行仍为 `planned_not_registered`，当前不做最终胜负判断。",
            "- 当前可以严谨陈述的是：已观察的 BOOT-V7 boot-only dev 均值在 22 分量级，test 约 16 分量级；A_full test 暂未显示增益；BOOT-V8 boot-only 低于 BOOT-V7 boot-only。",
            "- BOOT-V3/BOOT-V6 当前仍在 stage2 boot-only 补齐阶段，V3/V7 需要等同口径 downstream 行补完后再比较。",
        ]
    )
    return "\n".join(lines) + "\n"


def update_index_link() -> None:
    link = f"- 72 行验收 log：`{OUT_DOC}`。"
    text = INDEX_DOC.read_text(encoding="utf-8")
    if link in text:
        return
    marker = "- 长尾风险："
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith(marker):
            lines.insert(idx + 1, link)
            INDEX_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return


def main() -> int:
    OUT_DOC.write_text(build_doc(), encoding="utf-8")
    update_index_link()
    print(OUT_DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
