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
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/project_gaia_skillrl")
PYTHON = ROOT / ".venv/bin/python"
CONFIG = ROOT / "configs/system.json"
DEV_DATASET = ROOT / "data/converted/gaia_2023_all_validation_dev_tasks.json"
TEST_DATASET = ROOT / "data/converted/gaia_2023_all_validation_test_tasks.json"
RUN_ROOT = ROOT / "runs"
LAUNCH_LOG_ROOT = RUN_ROOT / "_launch_logs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")

BOOT_DEV_RUN = Path(os.environ.get(
    "GAIA_B1_B2_BOOT_DEV_RUN",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/20260505_004012_9b_bootv3_archv53_fresh_bootv3_boot_dev_c20",
))
BOOT_TEST_RUN = Path(os.environ.get(
    "GAIA_B1_B2_BOOT_TEST_RUN",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-5/20260505_004055_9b_bootv3_archv53_fresh_bootv3_boot_test_c20",
))
BOOT_SKILL = Path(os.environ.get(
    "GAIA_B1_B2_BOOT_SKILL",
    str(BOOT_DEV_RUN / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"),
))
BASELINE_DEV_RUN = Path(os.environ.get(
    "GAIA_B1_B2_BASELINE_DEV_RUN",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-4/20260504_234550_9b_p04_token_guard_baseline_dev_gpu0_c20",
))
BASELINE_TEST_GLOB = os.environ.get(
    "GAIA_B1_B2_BASELINE_TEST_GLOB",
    "/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-4/20260504_161135_9b_p04_token_guard_baseline_test_shard*_gpu*_9b_c20",
)

PREFIX = os.environ.get("GAIA_B1_B2_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_9b_bootv3_archv53_b1_b2"
)
CONCURRENCY = int(os.environ.get("GAIA_B1_B2_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_B1_B2_POLL_SECONDS", "600"))
QUEUE_NAME = f"{PREFIX}_queue"
REPORT_DOC = Path(os.environ.get(
    "GAIA_B1_B2_REPORT_DOC",
    f"/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.05_{PREFIX}_GAIA_BOOTV3_ARCHV53_B1_B2结果.md",
))

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_gaia_9b_token_guard_p04_rerun_20260504 as p04  # noqa: E402
from gaia_skillrl.actor import SkillActor  # noqa: E402
from gaia_skillrl.config import clone_system_config, load_system_config  # noqa: E402
from gaia_skillrl.critic import SkillCritic  # noqa: E402
from gaia_skillrl.schemas import (  # noqa: E402
    EnvRunResult,
    EnvState,
    EvaluationResult,
    ExecutorStepRecord,
    PhaseTransition,
    ToolCallRecord,
    to_dict,
)
from gaia_skillrl.skills import (  # noqa: E402
    discover_skills,
    load_skill_detail,
    reset_experience_buffer,
    reset_skill_library,
    write_skill_bundle,
)
from gaia_skillrl.trainer import GaiaSkillTrainer  # noqa: E402
from gaia_skillrl.utils import ensure_dir, write_json  # noqa: E402


LANE_BY_NAME = {lane.name: lane for lane in p04.LANES}
lane_names_raw = os.environ.get("GAIA_B1_B2_LANES", "gpu0_9b,gpu1_9b,gpu2_9b,gpu3_9b")
LANES = [LANE_BY_NAME[name.strip()] for name in lane_names_raw.split(",") if name.strip()]
if len(LANES) != 4:
    raise RuntimeError(f"GAIA_B1_B2_LANES must name exactly four lanes, got {lane_names_raw!r}")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def dataclass_from_dict(cls: type, data: dict[str, Any]):
    allowed = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in allowed})


def load_state(path: Path) -> EnvState:
    data = json.loads(path.read_text(encoding="utf-8"))
    env_data = data["env_result"]
    env_result = EnvRunResult(
        final_answer=env_data.get("final_answer", ""),
        final_choice_label=env_data.get("final_choice_label", ""),
        tool_trajectory=[dataclass_from_dict(ToolCallRecord, item) for item in env_data.get("tool_trajectory", [])],
        phase_transitions=[dataclass_from_dict(PhaseTransition, item) for item in env_data.get("phase_transitions", [])],
        action_trace=[dataclass_from_dict(ExecutorStepRecord, item) for item in env_data.get("action_trace", [])],
        executor_summary=env_data.get("executor_summary", ""),
        raw_executor_output=env_data.get("raw_executor_output", ""),
        evaluation=dataclass_from_dict(EvaluationResult, env_data.get("evaluation", {})),
    )
    return EnvState(
        task_id=data["task_id"],
        task_prompt=data["task_prompt"],
        env_result=env_result,
        gold_trajectory=data.get("gold_trajectory", []),
        gold_tool_names=data.get("gold_tool_names", []),
        active_skill_name=data.get("active_skill_name", "gaia-general-skill"),
        task_context=data.get("task_context", {}),
    )


def selected_task_count(run_dir: Path) -> int:
    selected = run_dir / "selected_tasks.json"
    if not selected.exists():
        return 0
    try:
        return len(json.loads(selected.read_text(encoding="utf-8")).get("task_ids", []))
    except Exception:
        return 0


def latest_iteration_dir(run_dir: Path) -> Path | None:
    iterations = [
        path for path in run_dir.glob("iteration_*")
        if path.is_dir() and any(child.is_dir() for child in path.iterdir())
    ]
    return max(iterations, key=lambda path: path.name) if iterations else None


def load_boot_states() -> list[EnvState]:
    iteration_dir = latest_iteration_dir(BOOT_DEV_RUN)
    if iteration_dir is None:
        raise RuntimeError(f"No iteration dir under boot_dev run: {BOOT_DEV_RUN}")
    state_paths = sorted(iteration_dir.glob("*/state.json"))
    if not state_paths:
        raise RuntimeError(f"No state.json files under {iteration_dir}")
    return [load_state(path) for path in state_paths]


def command_display(env: dict[str, str], cmd: list[str]) -> str:
    keys = [
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_INITIAL_SKILL_PATH",
        "NLRL_RUNTIME_CRITIC_STRATEGY",
        "NLRL_RUNTIME_ACTOR_GRAPH_EDIT_POLICY",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_API_MODE",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_API_MODE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    env_parts = [f"{key}={shlex.quote(env[key])}" for key in keys if env.get(key)]
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


def append_process_row(
    *,
    kind: str,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
) -> None:
    p04.append_process_row(
        kind=kind,
        name=name,
        pid=pid,
        cwd=cwd,
        run_dir=run_dir,
        command=command,
        log_path=log_path,
        notes=notes,
    )


def set_actor_critic_defaults() -> None:
    os.environ.setdefault("NLRL_ACTOR_MODEL", "gpt-5.4")
    os.environ.setdefault("NLRL_ACTOR_BASE_URL", "codex-cli")
    os.environ.setdefault("NLRL_ACTOR_API_KEY", "EMPTY")
    os.environ.setdefault("NLRL_ACTOR_API_MODE", "codex_cli")
    os.environ.setdefault("NLRL_ACTOR_TIMEOUT_SECONDS", "1800")
    os.environ.setdefault("NLRL_CRITIC_MODEL", "gpt-5.4")
    os.environ.setdefault("NLRL_CRITIC_BASE_URL", "codex-cli")
    os.environ.setdefault("NLRL_CRITIC_API_KEY", "EMPTY")
    os.environ.setdefault("NLRL_CRITIC_API_MODE", "codex_cli")
    os.environ.setdefault("NLRL_CRITIC_TIMEOUT_SECONDS", "1800")
    os.environ.setdefault("NLRL_CODEX_CLI_TIMEOUT_SECONDS", "1800")


def run_offline_variant(*, strategy: str, run_name: str, graph_policy: str) -> Path:
    log(f"offline {strategy}: preparing run from {BOOT_DEV_RUN}")
    config = clone_system_config(
        load_system_config(CONFIG),
        runtime={
            "critic_strategy": strategy,
            "critic_shard_size": 12,
            "actor_graph_edit_policy": graph_policy,
        },
    )
    trainer = GaiaSkillTrainer(config)
    run_dir = trainer.prepare_run_dir(run_name, mode="gaia-offline-critic-actor")
    state_config = trainer._batch_state_config(run_dir)
    reset_experience_buffer(state_config.experience_buffer_path)
    reset_skill_library(state_config.skill_library_root)
    write_skill_bundle(
        state_config.skill_library_root,
        "gaia-general-skill",
        {"SKILL.md": BOOT_SKILL.read_text(encoding="utf-8")},
    )
    headers = discover_skills(state_config.skill_library_root)
    if not headers:
        raise RuntimeError(f"No skill loaded for offline actor run {run_name}.")
    active_skill = load_skill_detail(headers[0])
    iteration_dir = ensure_dir(run_dir / "offline_iter1")
    states = load_boot_states()
    critic = SkillCritic(state_config)
    actor = SkillActor(state_config)
    history_poll: list[dict[str, Any]] = [
        {
            "iteration_index": 0,
            "summary": (
                "BOOT-V3 ARCH-V5.3 boot_dev states are reused as off-policy diagnosis input; "
                f"strategy={strategy}; graph_policy={graph_policy}; source={BOOT_DEV_RUN}"
            ),
        }
    ]
    reward = critic.evaluate_batch(states, active_skill, history_poll, iteration_dir)
    decision = actor.act(reward, active_skill, history_poll, iteration_dir)
    actor.apply(decision)
    updated_skill = load_skill_detail(discover_skills(state_config.skill_library_root)[0])
    skill_after = iteration_dir / "skill_after_actor/gaia-general-skill"
    if skill_after.exists():
        import shutil

        shutil.rmtree(skill_after)
    import shutil

    shutil.copytree(Path(updated_skill.header.skill_dir), skill_after)
    write_json(
        run_dir / "offline_actor_summary.json",
        {
            "run_name": run_dir.name,
            "strategy": strategy,
            "graph_policy": graph_policy,
            "source_run": str(BOOT_DEV_RUN),
            "source_state_count": len(states),
            "input_skill": str(BOOT_SKILL),
            "reward": to_dict(reward),
            "actor_decision": to_dict(decision),
            "skill_after_actor": str(skill_after / "SKILL.md"),
        },
    )
    log(f"offline {strategy}: produced {skill_after / 'SKILL.md'}")
    return skill_after / "SKILL.md"


def build_eval_env(lane: p04.Lane, skill_path: Path) -> dict[str, str]:
    env = p04.common_env(lane.base_url)
    env.update(
        {
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_INITIAL_SKILL_PATH": str(skill_path),
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
        }
    )
    return env


def start_eval(*, run_name: str, dataset: Path, skill_path: Path, lane: p04.Lane) -> subprocess.Popen[str]:
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
    env = build_eval_env(lane, skill_path)
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
    append_process_row(
        kind="experiment",
        name=run_name,
        pid=proc.pid,
        cwd=ROOT,
        run_dir="(created by trainer after launch)",
        command=command_display(env, cmd),
        log_path=log_path,
        notes=f"BOOT-V3 ARCH-V5.3 B1/B2 eval; lane={lane.name}; skill={skill_path}",
    )
    log(f"started eval {run_name} pid={proc.pid} lane={lane.name} endpoint={lane.base_url} log={log_path}")
    return proc


def latest_run_dir(run_name: str) -> Path | None:
    suffix = re.sub(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_", "", run_name)
    matches = [
        path for pattern in (f"**/{run_name}", f"**/*{suffix}")
        for path in RUN_ROOT.glob(pattern)
        if path.is_dir()
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def run_stats(run_dir: Path) -> dict[str, Any]:
    total = selected_task_count(run_dir)
    iteration = latest_iteration_dir(run_dir)
    states = []
    if iteration is not None:
        states = list(iteration.glob("*/state.json"))
    success = 0
    for path in states:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            success += int(bool(data.get("env_result", {}).get("evaluation", {}).get("task_success")))
        except Exception:
            pass
    return {
        "run_dir": str(run_dir),
        "total": total or len(states),
        "landed": len(states),
        "success": success,
        "missing": max(0, (total or len(states)) - len(states)),
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
    return f"{run_name}={stats['landed']}/{stats['total'] or '?'}"


def write_final_report(specs: list[dict[str, Any]], b1_skill: Path, b2_skill: Path) -> None:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    if BASELINE_DEV_RUN.exists():
        rows.append(("9B", "baseline_dev", run_stats(BASELINE_DEV_RUN)))
    baseline_test_paths = sorted(Path(path) for path in RUN_ROOT.glob("dummy-never-match"))
    baseline_test_paths = sorted(Path("/").glob(BASELINE_TEST_GLOB.lstrip("/"))) if BASELINE_TEST_GLOB.startswith("/") else sorted(RUN_ROOT.glob(BASELINE_TEST_GLOB))
    if baseline_test_paths:
        rows.append(("9B", "baseline_test", aggregate_stats("baseline_test", baseline_test_paths)))
    rows.append(("9B", "BOOT-V3 dev", run_stats(BOOT_DEV_RUN)))
    rows.append(("9B", "BOOT-V3 test", run_stats(BOOT_TEST_RUN)))
    for spec in specs:
        run_dir = latest_run_dir(spec["run_name"])
        rows.append(("9B", spec["label"], run_stats(run_dir) if run_dir else {
            "run_dir": "(missing)",
            "total": 0,
            "landed": 0,
            "success": 0,
            "missing": 0,
        }))

    best = max((row for row in rows if row[1].startswith("B")), key=lambda item: item[2]["success"], default=None)
    lines = [
        "# GAIA BOOT-V3 ARCH-V5.3 B1/B2 结果",
        "",
        f"记录时间：{now()}",
        "",
        "## 结果表",
        "",
        "| 模型 | 实验 | 分数 | 落盘 | 缺失 | run_dir |",
        "|---|---|---:|---:|---:|---|",
    ]
    for model, label, stats in rows:
        total = stats["total"] or "?"
        lines.append(
            f"| {model} | {label} | {stats['success']}/{total} | {stats['landed']} | {stats['missing']} | "
            f"{stats['run_dir']} |"
        )
    lines.extend([
        "",
        "## Skill",
        "",
        f"- B1 skill：`{b1_skill}`",
        f"- B2 skill：`{b2_skill}`",
        "",
        "## 结论",
        "",
    ])
    if best is not None:
        lines.append(f"- 当前 B 系列最高分：`{best[1]}` = `{best[2]['success']}/{best[2]['total'] or '?'}`。")
    lines.append("- B1 是现有 sharded family critic + fixed-graph actor；B2 是 graph_b2 critic + graph-unlocked actor。")
    lines.append("- 本表使用各 run 的 `state.json -> env_result.evaluation.task_success`，缺失按错题展示。")
    REPORT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"wrote final report: {REPORT_DOC}")


def stop_children(children: list[subprocess.Popen[str]]) -> None:
    for process in children:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                pass


def main() -> int:
    for path in (PYTHON, CONFIG, DEV_DATASET, TEST_DATASET, BOOT_DEV_RUN, BOOT_TEST_RUN, BOOT_SKILL):
        if not path.exists():
            raise RuntimeError(f"missing required path: {path}")
    set_actor_critic_defaults()
    refresh_process_registry()
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    queue_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", QUEUE_LOG_ROOT / f"{QUEUE_NAME}.log"))
    append_process_row(
        kind="experiment_queue",
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(BOOT-V3 ARCH-V5.3 B1/B2 queue manager)",
        command=command_display(os.environ, [str(PYTHON), "-u", __file__]),
        log_path=queue_log,
        notes=f"BOOT-V3 ARCH-V5.3 B1/B2; lanes={[asdict(lane) for lane in LANES]}; c{CONCURRENCY}; report={REPORT_DOC}",
    )
    status = "finished"
    children: list[subprocess.Popen[str]] = []
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
        for lane in LANES:
            p04.start_or_reuse_vllm(lane)
        p04.wait_endpoints()
        b1_skill = run_offline_variant(
            strategy="sharded",
            run_name=f"{PREFIX}_B1_sharded_offline_actor_iter1",
            graph_policy="locked",
        )
        b2_skill = run_offline_variant(
            strategy="graph_b2",
            run_name=f"{PREFIX}_B2_graph_b2_offline_actor_iter1",
            graph_policy="graph_unlocked",
        )
        specs = [
            {"label": "B1 dev", "run_name": f"{PREFIX}_B1_dev_eval_c{CONCURRENCY}", "dataset": DEV_DATASET, "skill": b1_skill, "lane": LANES[0]},
            {"label": "B1 test", "run_name": f"{PREFIX}_B1_test_eval_c{CONCURRENCY}", "dataset": TEST_DATASET, "skill": b1_skill, "lane": LANES[1]},
            {"label": "B2 dev", "run_name": f"{PREFIX}_B2_dev_eval_c{CONCURRENCY}", "dataset": DEV_DATASET, "skill": b2_skill, "lane": LANES[2]},
            {"label": "B2 test", "run_name": f"{PREFIX}_B2_test_eval_c{CONCURRENCY}", "dataset": TEST_DATASET, "skill": b2_skill, "lane": LANES[3]},
        ]
        for spec in specs:
            children.append(start_eval(
                run_name=spec["run_name"],
                dataset=spec["dataset"],
                skill_path=spec["skill"],
                lane=spec["lane"],
            ))
        while True:
            for child in children:
                code = child.poll()
                if code is not None:
                    p04.update_process_row(child.pid, "finished" if code == 0 else "dead")
            progress = " ".join(progress_line(spec["run_name"]) for spec in specs)
            log(f"progress {progress}")
            if all(child.poll() is not None for child in children):
                failures = [child for child in children if child.returncode not in {0, None}]
                if failures:
                    raise RuntimeError("eval child failure: " + ", ".join(f"{child.pid}:{child.returncode}" for child in failures))
                write_final_report(specs, b1_skill, b2_skill)
                log("B1/B2 queue completed")
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
        p04.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
