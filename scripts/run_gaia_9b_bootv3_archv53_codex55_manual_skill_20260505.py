from __future__ import annotations

import csv
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/project_gaia_skillrl")
SCRIPT_DIR = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

MANUAL_SKILL = Path(os.environ.get(
    "GAIA_CODEX55_MANUAL_SKILL",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/20260505_160904_9b_bootv3_archv53_codex55_manual_skill/gaia-general-skill/SKILL.md",
))
BOOT_DEV_RUN = Path(os.environ.get(
    "GAIA_CODEX55_BOOT_DEV_RUN",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/20260505_004012_9b_bootv3_archv53_fresh_bootv3_boot_dev_c20",
))
BOOT_TEST_RUN = Path(os.environ.get(
    "GAIA_CODEX55_BOOT_TEST_RUN",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/20260505_004055_9b_bootv3_archv53_fresh_bootv3_boot_test_c20",
))
BASELINE_DEV_RUN = Path(os.environ.get(
    "GAIA_CODEX55_BASELINE_DEV_RUN",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-4/20260504_234550_9b_p04_token_guard_baseline_dev_gpu0_c20",
))
BASELINE_TEST_GLOB = os.environ.get(
    "GAIA_CODEX55_BASELINE_TEST_GLOB",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-4/20260504_161135_9b_p04_token_guard_baseline_test_shard*_gpu*_9b_c20",
)
B2_DEV_RUN = Path(os.environ.get(
    "GAIA_CODEX55_B2_DEV_RUN",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/20260505_132829_9b_bootv3_archv53_b1_b2_B2_dev_eval_c20",
))
B2_TEST_RUN = Path(os.environ.get(
    "GAIA_CODEX55_B2_TEST_RUN",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/20260505_132829_9b_bootv3_archv53_b1_b2_B2_test_eval_c20",
))

PREFIX = os.environ.get("GAIA_CODEX55_MANUAL_PREFIX", "").strip() or "20260505_160904_9b_bootv3_archv53_codex55_manual"
CONCURRENCY = int(os.environ.get("GAIA_CODEX55_MANUAL_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_CODEX55_MANUAL_POLL_SECONDS", "600"))
QUEUE_NAME = f"{PREFIX}_dev_test_queue"
REPORT_DOC = Path(os.environ.get(
    "GAIA_CODEX55_MANUAL_REPORT_DOC",
    "/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.05_1609_GAIA_BOOTV3_ARCHV53_Codex55_manual_skill结果.md",
))

sys.path.insert(0, str(SCRIPT_DIR))
import run_gaia_9b_token_guard_p04_rerun_20260504 as p04  # noqa: E402


LANE_BY_NAME = {lane.name: lane for lane in p04.LANES}
lane_names_raw = os.environ.get("GAIA_CODEX55_MANUAL_LANES", "gpu0_9b,gpu1_9b")
LANES = [LANE_BY_NAME[name.strip()] for name in lane_names_raw.split(",") if name.strip()]
if len(LANES) != 2:
    raise RuntimeError(f"GAIA_CODEX55_MANUAL_LANES must name exactly two lanes, got {lane_names_raw!r}")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_ITERATIONS_PER_BATCH",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_TOOL_BASE_URL",
        "NLRL_TOOL_MODEL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    return {key: env[key] for key in keys if env.get(key)}


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    env_parts = [f"{key}={shlex.quote(value)}" for key, value in selected_env(env).items()]
    return "env " + " ".join([*env_parts, *[shlex.quote(part) for part in cmd]])


def refresh_process_registry() -> None:
    if not PROCESS_CSV.exists():
        return
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[2].strip() != "running":
            continue
        pid_text = row[5].strip()
        if not pid_text.isdigit():
            continue
        if p04.pid_alive(int(pid_text)):
            row[1] = now()
        else:
            row[1] = now()
            row[2] = "dead"
            if not row[7].strip():
                row[7] = now()
        changed = True
    if changed:
        with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)


def selected_lane_env(lane: p04.Lane) -> dict[str, str]:
    env = p04.common_env(lane.base_url)
    env.update(
        {
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_INITIAL_SKILL_PATH": str(MANUAL_SKILL),
            "NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL": "0",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
        }
    )
    return env


def wait_selected_endpoints() -> None:
    deadline = time.time() + int(os.environ.get("GAIA_CODEX55_MANUAL_ENDPOINT_WAIT_SECONDS", "1800"))
    while True:
        missing = [lane for lane in LANES if lane.served_name not in p04.endpoint_models(lane.base_url)]
        if not missing:
            log("selected endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        if time.time() > deadline:
            raise RuntimeError("selected endpoints not ready: " + ", ".join(lane.base_url for lane in missing))
        log("waiting selected endpoints: " + ", ".join(lane.base_url for lane in missing))
        time.sleep(30)


def start_eval(*, run_name: str, dataset: Path, lane: p04.Lane) -> tuple[subprocess.Popen[str], Path]:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LAUNCH_LOG_ROOT / f"{run_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(CONFIG),
        "train-local",
        "--dataset-path",
        str(dataset),
        "--run-name",
        run_name,
    ]
    env = selected_lane_env(lane)
    with log_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    p04.append_process_row(
        kind="experiment",
        name=run_name,
        pid=proc.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes=f"Codex55 manual BOOT-V3 ARCH-V5.3 eval; lane={lane.name}; skill={MANUAL_SKILL}",
    )
    log(f"started {run_name} pid={proc.pid} lane={lane.name} endpoint={lane.base_url} log={log_path}")
    return proc, log_path


def latest_iteration_dir(run_dir: Path) -> Path | None:
    iterations = [
        path for path in run_dir.glob("iteration_*")
        if path.is_dir() and any(child.is_dir() for child in path.iterdir())
    ]
    return max(iterations, key=lambda path: path.name) if iterations else None


def selected_task_count(run_dir: Path) -> int:
    selected = run_dir / "selected_tasks.json"
    if not selected.exists():
        return 0
    try:
        data = json.loads(selected.read_text(encoding="utf-8"))
        return len(data.get("task_ids", []))
    except Exception:
        return 0


def latest_run_dir(run_name: str) -> Path | None:
    suffix = re.sub(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_", "", run_name)
    matches = [
        path for pattern in (f"**/{run_name}", f"**/*{suffix}")
        for path in RUN_ROOT.glob(pattern)
        if path.is_dir()
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def load_states(run_dir: Path) -> list[dict[str, Any]]:
    iteration = latest_iteration_dir(run_dir)
    if iteration is None:
        return []
    states = []
    for path in sorted(iteration.glob("*/state.json")):
        try:
            states.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return states


def run_stats(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None or not run_dir.exists():
        return {"run_dir": "(missing)", "total": 0, "landed": 0, "success": 0, "missing": 0}
    states = load_states(run_dir)
    total = selected_task_count(run_dir) or len(states)
    success = sum(
        int(bool(state.get("env_result", {}).get("evaluation", {}).get("task_success")))
        for state in states
    )
    return {
        "run_dir": str(run_dir),
        "total": total,
        "landed": len(states),
        "success": success,
        "missing": max(0, total - len(states)),
    }


def aggregate_stats(label: str, paths: list[Path]) -> dict[str, Any]:
    stats = [run_stats(path) for path in paths if path.exists()]
    total = sum(item["total"] for item in stats)
    landed = sum(item["landed"] for item in stats)
    success = sum(item["success"] for item in stats)
    return {
        "run_dir": "; ".join(item["run_dir"] for item in stats) or label,
        "total": total,
        "landed": landed,
        "success": success,
        "missing": max(0, total - landed),
    }


def progress_line(run_name: str) -> str:
    run_dir = latest_run_dir(run_name)
    if run_dir is None:
        return f"{run_name}=pending"
    stats = run_stats(run_dir)
    total = stats["total"] or "?"
    return f"{run_name}={stats['landed']}/{total}"


def write_report(specs: list[dict[str, Any]], *, status: str) -> None:
    baseline_test_paths = (
        sorted(Path("/").glob(BASELINE_TEST_GLOB.lstrip("/")))
        if BASELINE_TEST_GLOB.startswith("/")
        else sorted(RUN_ROOT.glob(BASELINE_TEST_GLOB))
    )
    rows: list[tuple[str, dict[str, Any]]] = []
    rows.append(("baseline_dev", run_stats(BASELINE_DEV_RUN)))
    rows.append(("baseline_test", aggregate_stats("baseline_test", baseline_test_paths)))
    rows.append(("BOOT-V3 dev", run_stats(BOOT_DEV_RUN)))
    rows.append(("BOOT-V3 test", run_stats(BOOT_TEST_RUN)))
    rows.append(("B2 dev", run_stats(B2_DEV_RUN)))
    rows.append(("B2 test", run_stats(B2_TEST_RUN)))
    for spec in specs:
        rows.append((spec["label"], run_stats(latest_run_dir(spec["run_name"]))))

    lines = [
        "# GAIA BOOT-V3 ARCH-V5.3 Codex55 Manual Skill 结果",
        "",
        f"记录时间：{now()}",
        f"状态：`{status}`",
        "",
        "## 结果表",
        "",
        "| 实验 | 分数 | 落盘 | 缺失 | run_dir |",
        "|---|---:|---:|---:|---|",
    ]
    for label, stats in rows:
        total = stats["total"] or "?"
        lines.append(
            f"| {label} | {stats['success']}/{total} | {stats['landed']} | {stats['missing']} | {stats['run_dir']} |"
        )
    lines.extend(
        [
            "",
            "## 本轮 Skill",
            "",
            f"- 手工 skill：`{MANUAL_SKILL}`",
            f"- 源 BOOT skill：`{BOOT_DEV_RUN / 'bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md'}`",
            "- 允许改动范围：phase rules、Next 边顺序、phase allowlist；本轮保持 7 个 phase 和 34 条边，新增 `COMPUTE:list_dir` 与 `VERIFY_AND_ANSWER:read_table`。",
            "- dev 信息用于失败类型诊断和规则泛化，没有写入 dev/test 题号或答案表。",
            "",
            "## 启动信息",
            "",
        ]
    )
    for spec in specs:
        lines.append(f"- {spec['label']}：run_name `{spec['run_name']}`，lane `{spec['lane'].name}`，log `{spec['log_path']}`")
    REPORT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"wrote report: {REPORT_DOC}")


def stop_children(children: list[subprocess.Popen[str]]) -> None:
    for child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except Exception:
                pass


def main() -> int:
    for path in (PYTHON, CONFIG, DEV_DATASET, TEST_DATASET, MANUAL_SKILL):
        if not path.exists():
            raise RuntimeError(f"missing required path: {path}")
    refresh_process_registry()
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    queue_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", QUEUE_LOG_ROOT / f"{QUEUE_NAME}.log"))
    p04.append_process_row(
        kind="experiment_queue",
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(Codex55 manual skill dev/test queue manager)",
        command=command_display(os.environ, [str(PYTHON), "-u", __file__]),
        log_path=queue_log,
        notes=f"Codex55 manual BOOT-V3 ARCH-V5.3 skill; lanes={[asdict(lane) for lane in LANES]}; c{CONCURRENCY}; report={REPORT_DOC}",
    )
    specs = [
        {"label": "Codex55 manual dev", "run_name": f"{PREFIX}_dev_eval_c{CONCURRENCY}", "dataset": DEV_DATASET, "lane": LANES[0]},
        {"label": "Codex55 manual test", "run_name": f"{PREFIX}_test_eval_c{CONCURRENCY}", "dataset": TEST_DATASET, "lane": LANES[1]},
    ]
    children: list[subprocess.Popen[str]] = []
    status = "finished"
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
        for lane in LANES:
            p04.start_or_reuse_vllm(lane)
        wait_selected_endpoints()
        for spec in specs:
            child, log_path = start_eval(run_name=spec["run_name"], dataset=spec["dataset"], lane=spec["lane"])
            spec["log_path"] = log_path
            children.append(child)
        write_report(specs, status="running")
        while True:
            for child in children:
                code = child.poll()
                if code is not None:
                    p04.update_process_row(child.pid, "finished" if code == 0 else "dead")
            progress = " ".join(progress_line(spec["run_name"]) for spec in specs)
            log(f"progress {progress}")
            write_report(specs, status="running")
            if all(child.poll() is not None for child in children):
                failures = [child for child in children if child.returncode not in {0, None}]
                if failures:
                    raise RuntimeError("eval child failure: " + ", ".join(f"{child.pid}:{child.returncode}" for child in failures))
                write_report(specs, status="finished")
                log("Codex55 manual skill dev/test completed")
                return 0
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        status = "stopped"
        stop_children(children)
        raise
    except Exception:
        status = "dead"
        stop_children(children)
        raise
    finally:
        try:
            write_report(specs, status=status)
        finally:
            p04.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
