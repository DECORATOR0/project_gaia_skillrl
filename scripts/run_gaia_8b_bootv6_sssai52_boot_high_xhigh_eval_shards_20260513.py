from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"
PREFIX = os.environ.get(
    "GAIA_8B_BOOTV6_SSSAI52_BOOT_EVAL_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv6_sssai52_boot_high_xhigh_eval_shards_remote132"),
).strip()

os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_PREFIX"] = PREFIX
os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_CONCURRENCY"] = "20"
os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_POLL_SECONDS"] = "60"
os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_VLLM_GPU_MEMORY_UTILIZATION"] = os.environ.get(
    "GAIA_8B_BOOTV5V6_FULL_MATRIX_VLLM_GPU_MEMORY_UTILIZATION",
    "0.90",
)
os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_VLLM_MAX_NUM_SEQS"] = os.environ.get(
    "GAIA_8B_BOOTV5V6_FULL_MATRIX_VLLM_MAX_NUM_SEQS",
    "48",
)

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_bootv5v6_full_matrix_remote132_20260513 as q  # noqa: E402
from gaia_skillrl.dataset import load_converted_dataset  # noqa: E402


SUMMARY_JSON = q.QUEUE_LOG_ROOT / f"{PREFIX}_summary.json"
MANIFEST_JSON = q.QUEUE_LOG_ROOT / f"{PREFIX}_manifest.json"


@dataclass
class ShardSpec:
    key: str
    method_key: str
    split: str
    shard_index: int
    shard_count: int
    dataset: Path
    skill_path: Path
    lane_index: int
    task_ids: list[str]

    @property
    def run_name(self) -> str:
        return (
            f"{PREFIX}_{self.method_key}_{self.split}"
            f"_shard{self.shard_index + 1}of{self.shard_count}_c20"
        )

    @property
    def label(self) -> str:
        return f"{self.method_key} {self.split} shard {self.shard_index + 1}/{self.shard_count}"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def read_skill_from_compare(path: Path, key: str) -> Path:
    data = json.loads(path.read_text(encoding="utf-8"))
    skill = Path(str(data[key]["path"]))
    if not skill.exists():
        raise FileNotFoundError(skill)
    return skill


def split_task_ids(dataset: Path, shard_count: int = 2) -> list[list[str]]:
    tasks = load_converted_dataset(dataset)
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for index, task in enumerate(tasks):
        shards[index % shard_count].append(task.task_id)
    return shards


def build_specs() -> list[ShardSpec]:
    high_compare_json = Path(
        os.environ.get(
            "GAIA_8B_BOOTV6_SSSAI52_BOOT_HIGH_COMPARE_JSON",
            q.QUEUE_LOG_ROOT / "20260513_2230_8b_bootv6_bootonly_sssai52_high_compare_remote132_compare.json",
        )
    )
    xhigh_compare_json = Path(
        os.environ.get(
            "GAIA_8B_BOOTV6_SSSAI52_BOOT_XHIGH_COMPARE_JSON",
            q.QUEUE_LOG_ROOT / "20260513_2230_8b_bootv6_bootonly_sssai52_xhigh_compare_remote132_compare.json",
        )
    )
    boot_high_skill = read_skill_from_compare(
        high_compare_json,
        "sssaicode_gpt52_bootv6_skill",
    )
    boot_xhigh_skill = read_skill_from_compare(
        xhigh_compare_json,
        "sssaicode_gpt52_bootv6_skill",
    )
    datasets = {
        "dev": q.DEV_DATASET,
        "test": q.TEST_DATASET,
    }
    split_shards = {split: split_task_ids(path, 2) for split, path in datasets.items()}
    methods = [
        ("boot_high", boot_high_skill),
        ("boot_xhigh", boot_xhigh_skill),
    ]
    lane_index = 0
    specs: list[ShardSpec] = []
    for method_key, skill_path in methods:
        for split, dataset in datasets.items():
            for shard_index, task_ids in enumerate(split_shards[split]):
                specs.append(
                    ShardSpec(
                        key=f"{method_key}_{split}_s{shard_index + 1}",
                        method_key=method_key,
                        split=split,
                        shard_index=shard_index,
                        shard_count=2,
                        dataset=dataset,
                        skill_path=skill_path,
                        lane_index=lane_index,
                        task_ids=task_ids,
                    )
                )
                lane_index += 1
    return specs


def command_for(spec: ShardSpec) -> list[str]:
    cmd = [
        str(PYTHON),
        "-m",
        "gaia_skillrl.cli",
        "--config",
        str(q.CONFIGS["bootv6"]),
        "train-local",
        "--dataset-path",
        str(spec.dataset),
        "--run-name",
        spec.run_name,
    ]
    for task_id in spec.task_ids:
        cmd.extend(["--task-id", task_id])
    return cmd


def env_for(lane: Any, spec: ShardSpec) -> dict[str, str]:
    env = q.base.common_env(lane)
    env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(spec.skill_path)
    env["NLRL_RUNTIME_TASK_CONCURRENCY"] = "20"
    env["NLRL_LLM_MAX_CONCURRENT_REQUESTS"] = "20"
    env["NLRL_RUNTIME_ITERATIONS_PER_BATCH"] = "0"
    env["NLRL_RUNTIME_BOOTSTRAP_INITIAL_SKILL"] = "0"
    env["NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS"] = "0"
    return env


def latest_run_dir(run_name: str) -> Path | None:
    suffix = run_name
    parts = run_name.split("_", 2)
    if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
        suffix = parts[2]
    patterns = [
        f"2026/5/2026-5-13/*{run_name}",
        f"2026/5/2026-5-13/*_{suffix}",
    ]
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(q.RUN_ROOT.glob(pattern))
    matches = sorted(
        set(matches),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def run_progress(run_name: str, live: bool) -> dict[str, Any]:
    run_dir = latest_run_dir(run_name)
    total = 0
    landed = 0
    success = 0
    if run_dir is not None:
        selected = run_dir / "selected_tasks.json"
        if selected.exists():
            try:
                total = len(json.loads(selected.read_text(encoding="utf-8")).get("task_ids", []))
            except Exception:
                total = 0
        iteration_dirs = sorted(run_dir.glob("iteration_*"))
        if iteration_dirs:
            states = list(iteration_dirs[-1].glob("*/state.json"))
            landed = len(states)
            for state_path in states:
                try:
                    payload = json.loads(state_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                success += int(bool(payload.get("env_result", {}).get("evaluation", {}).get("task_success")))
    return {
        "run_dir": str(run_dir) if run_dir else "",
        "total": total,
        "landed": landed,
        "missing": max(0, total - landed) if total else 0,
        "success": success,
        "score": f"{success}/{total or '?'}",
        "live": live,
    }


def write_summary(records: list[dict[str, Any]], status: str) -> None:
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        progress = record.get("progress", {})
        key = f"{record['method_key']}_{record['split']}"
        group = grouped.setdefault(
            key,
            {
                "method_key": record["method_key"],
                "split": record["split"],
                "total": 0,
                "landed": 0,
                "success": 0,
                "missing": 0,
                "live_shards": 0,
            },
        )
        group["total"] += int(progress.get("total") or len(record.get("task_ids", [])))
        group["landed"] += int(progress.get("landed") or 0)
        group["success"] += int(progress.get("success") or 0)
        group["missing"] = max(0, group["total"] - group["landed"])
        group["live_shards"] += int(bool(progress.get("live")))
    for group in grouped.values():
        total = int(group["total"])
        group["score"] = f"{group['success']}/{total or '?'}"
        group["success_rate"] = (group["success"] / total) if total else None
    payload = {
        "prefix": PREFIX,
        "status": status,
        "updated_at": now(),
        "concurrency_per_shard": 20,
        "records": records,
        "grouped": grouped,
    }
    SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    q.configure_modules()
    q.verify_inputs()
    q.base.STOP_CONFLICTING_SERVICES = False
    q.base.CONFLICTING_VLLM_PORTS = [lane.port for lane in q.LANES]
    q.base.LANES = q.LANES
    q.base.CONCURRENCY = 20
    q.base.RUN_PREFIX = PREFIX
    q.base.HOST_TAG = "132"

    specs = build_specs()
    if len(specs) != 8:
        raise RuntimeError(f"expected 8 shard specs, got {len(specs)}")

    for lane in q.LANES:
        q.base.start_or_reuse_vllm(lane)
    q.base.wait_endpoints()

    records: list[dict[str, Any]] = []
    processes: list[tuple[ShardSpec, Any, subprocess.Popen[str], Path]] = []
    q.LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        lane = q.LANES[spec.lane_index]
        cmd = command_for(spec)
        env = env_for(lane, spec)
        log_path = q.LAUNCH_LOG_ROOT / f"{spec.run_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
        q.base.append_process_row(
            kind="experiment",
            name=spec.run_name,
            pid=proc.pid,
            cwd=ROOT,
            run_dir="(created by trainer after launch)",
            command=shell_join(["env", "NLRL_RUNTIME_TASK_CONCURRENCY=20", f"NLRL_RUNTIME_INITIAL_SKILL_PATH={spec.skill_path}", *cmd]),
            log_path=log_path,
            notes=(
                f"{spec.label}; lane={lane.name}; endpoint={lane.base_url}; "
                f"task_count={len(spec.task_ids)}; split-sharded boot high/xhigh eval."
            ),
        )
        record = {
            **asdict(spec),
            "dataset": str(spec.dataset),
            "skill_path": str(spec.skill_path),
            "task_ids": spec.task_ids,
            "lane": asdict(lane),
            "pid": proc.pid,
            "log_path": str(log_path),
            "run_name": spec.run_name,
            "started_at": now(),
            "progress": {"total": len(spec.task_ids), "landed": 0, "success": 0, "missing": len(spec.task_ids), "live": True},
        }
        records.append(record)
        processes.append((spec, lane, proc, log_path))
        log(f"started {spec.run_name} pid={proc.pid} lane={lane.name} tasks={len(spec.task_ids)} log={log_path}")

    write_summary(records, "running")

    while True:
        live_count = 0
        for record, (spec, _lane, proc, _log_path) in zip(records, processes):
            live = proc.poll() is None
            live_count += int(live)
            progress = run_progress(spec.run_name, live)
            record["progress"] = progress
            if not live:
                record["exit_code"] = proc.returncode
                record.setdefault("ended_at", now())
        write_summary(records, "running" if live_count else "completed")
        grouped = json.loads(SUMMARY_JSON.read_text(encoding="utf-8")).get("grouped", {})
        log(
            "progress "
            + "; ".join(
                f"{key}={value['success']}/{value['total']} landed={value['landed']} live_shards={value['live_shards']}"
                for key, value in sorted(grouped.items())
            )
        )
        if live_count == 0:
            return 0
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
