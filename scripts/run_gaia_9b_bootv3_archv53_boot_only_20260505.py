from __future__ import annotations

import csv
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import re


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

PREFIX = os.environ.get("GAIA_9B_BOOTV3_ARCHV53_PREFIX", "").strip() or datetime.now().strftime(
    "%Y%m%d_%H%M%S_9b_bootv3_archv53"
)
CONCURRENCY = int(os.environ.get("GAIA_9B_BOOTV3_ARCHV53_CONCURRENCY", "20"))
POLL_SECONDS = int(os.environ.get("GAIA_9B_BOOTV3_ARCHV53_POLL_SECONDS", "60"))
BOOT_VARIANT_LABEL = os.environ.get("GAIA_9B_BOOT_VARIANT_LABEL", "bootv3").strip() or "bootv3"
BOOT_VERSION_LABEL = os.environ.get("GAIA_9B_BOOT_VERSION_LABEL", "BOOT-V3").strip() or "BOOT-V3"
BOOT_PREPROCESS = os.environ.get("GAIA_9B_BOOT_ARCHV53_PREPROCESS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
QUEUE_NAME = f"{PREFIX}_{BOOT_VARIANT_LABEL}_boot_only_c{CONCURRENCY}"
BOOT_DEV_RUN_NAME = f"{PREFIX}_fresh_{BOOT_VARIANT_LABEL}_boot_dev_c{CONCURRENCY}"
BOOT_TEST_RUN_NAME = f"{PREFIX}_fresh_{BOOT_VARIANT_LABEL}_boot_test_c{CONCURRENCY}"

os.environ.setdefault("GAIA_9B_TOKEN_GUARD_PREFIX", PREFIX)
os.environ.setdefault("GAIA_9B_TOKEN_GUARD_BASELINE_CONCURRENCY", str(CONCURRENCY))
os.environ.setdefault("GAIA_9B_TOKEN_GUARD_QUEUE_CONCURRENCY", str(CONCURRENCY))
sys.path.insert(0, str(SCRIPT_DIR))
import run_gaia_9b_token_guard_p04_rerun_20260504 as p04  # noqa: E402


LANE_BY_NAME = {lane.name: lane for lane in p04.LANES}
lane_names_raw = os.environ.get("GAIA_9B_BOOTV3_ARCHV53_LANES", "gpu0_9b,gpu1_9b")
LANES = [LANE_BY_NAME[name.strip()] for name in lane_names_raw.split(",") if name.strip()]
if len(LANES) != 2:
    raise RuntimeError(
        "GAIA_9B_BOOTV3_ARCHV53_LANES must name exactly two lanes, "
        f"got {lane_names_raw!r}"
    )


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def selected_env(env: dict[str, str]) -> dict[str, str]:
    keys = [
        "NLRL_RUNTIME_TASK_CONCURRENCY",
        "NLRL_LLM_MAX_CONCURRENT_REQUESTS",
        "NLRL_RUNTIME_MAX_CONTEXT_CHARS",
        "NLRL_RUNTIME_MAX_EXECUTOR_STEPS",
        "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS",
        "NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS",
        "NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP",
        "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY",
        "NLRL_RUNTIME_TOOL_PROFILE",
        "NLRL_EXECUTOR_MODEL",
        "NLRL_EXECUTOR_BASE_URL",
        "NLRL_EXECUTOR_MAX_TOKENS",
        "NLRL_EXECUTOR_TOKENIZER_PATH",
        "NLRL_EXECUTOR_MAX_MODEL_LEN",
        "NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN",
        "NLRL_EXECUTOR_ENABLE_THINKING",
        "NLRL_ACTOR_MODEL",
        "NLRL_ACTOR_BASE_URL",
        "NLRL_ACTOR_API_MODE",
        "NLRL_CRITIC_MODEL",
        "NLRL_CRITIC_BASE_URL",
        "NLRL_CRITIC_API_MODE",
        "GAIA_9B_BOOTV3_ARCHV53_PREFIX",
        "GAIA_9B_BOOTV3_ARCHV53_LANES",
        "GAIA_9B_BOOTV3_ARCHV53_CONCURRENCY",
        "GAIA_9B_BOOT_VARIANT_LABEL",
        "GAIA_9B_BOOT_VERSION_LABEL",
        "GAIA_9B_BOOT_ARCHV53_PREPROCESS",
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


def update_process_row(pid: int, status: str) -> None:
    p04.update_process_row(pid, status)


def run_name_suffix_for_match(run_name: str) -> str:
    match = re.match(r"^20\d{6}_[0-2]\d[0-5]\d(?:[0-5]\d)?_(.+)$", run_name)
    if match:
        return "_" + match.group(1)
    return run_name


def run_dir_for(run_name: str) -> Path | None:
    suffix = run_name_suffix_for_match(run_name)
    matches = [
        path
        for pattern in (f"**/{run_name}", f"**/*{suffix}")
        for path in RUN_ROOT.glob(pattern)
        if path.is_dir()
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def state_count(run_name: str) -> tuple[int, int]:
    run_dir = run_dir_for(run_name)
    if run_dir is None:
        return 0, 0
    selected = run_dir / "selected_tasks.json"
    total = 0
    if selected.exists():
        try:
            total = len(json.loads(selected.read_text(encoding="utf-8")).get("task_ids", []))
        except Exception:
            total = 0
    iteration_dirs = sorted(run_dir.glob("iteration_*"))
    if not iteration_dirs:
        return 0, total
    states = list(iteration_dirs[-1].glob("*/state.json"))
    return len(states), total


def bootstrap_skill_path() -> Path | None:
    run_dir = run_dir_for(BOOT_DEV_RUN_NAME)
    if run_dir is None:
        return None
    skill = run_dir / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
    return skill if skill.exists() else None


def wait_endpoints() -> None:
    deadline = time.time() + int(os.environ.get("GAIA_9B_BOOTV3_ARCHV53_ENDPOINT_WAIT_SECONDS", "1800"))
    while True:
        missing = [lane for lane in LANES if lane.served_name not in p04.endpoint_models(lane.base_url)]
        if not missing:
            log("selected 9B endpoints ready: " + ", ".join(lane.base_url for lane in LANES))
            return
        if time.time() >= deadline:
            raise RuntimeError("9B endpoints not ready: " + ", ".join(lane.base_url for lane in missing))
        log("waiting 9B endpoints: " + ", ".join(lane.base_url for lane in missing))
        time.sleep(30)


def build_env(base_url: str, *, skill_path: Path | None = None) -> dict[str, str]:
    env = p04.common_env(base_url)
    env.update(
        {
            "NLRL_RUNTIME_TASK_CONCURRENCY": str(CONCURRENCY),
            "NLRL_LLM_MAX_CONCURRENT_REQUESTS": str(CONCURRENCY),
            "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
            "NLRL_RUNTIME_HIDE_STALE_PHASE_PROMPTS": "1",
            "NLRL_ACTOR_MODEL": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_ACTOR_MODEL", "gpt-5.4"),
            "NLRL_ACTOR_BASE_URL": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_ACTOR_BASE_URL", "codex-cli"),
            "NLRL_ACTOR_API_KEY": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_ACTOR_API_KEY", "EMPTY"),
            "NLRL_ACTOR_API_MODE": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_ACTOR_API_MODE", "codex_cli"),
            "NLRL_ACTOR_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_ACTOR_TIMEOUT_SECONDS", "1800"),
            "NLRL_CRITIC_MODEL": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_CRITIC_MODEL", "gpt-5.4"),
            "NLRL_CRITIC_BASE_URL": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_CRITIC_BASE_URL", "codex-cli"),
            "NLRL_CRITIC_API_KEY": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_CRITIC_API_KEY", "EMPTY"),
            "NLRL_CRITIC_API_MODE": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_CRITIC_API_MODE", "codex_cli"),
            "NLRL_CRITIC_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_CRITIC_TIMEOUT_SECONDS", "1800"),
            "NLRL_CODEX_CLI_TIMEOUT_SECONDS": os.environ.get("GAIA_9B_BOOTV3_ARCHV53_CODEX_TIMEOUT_SECONDS", "1800"),
        }
    )
    if BOOT_PREPROCESS:
        env["NLRL_RUNTIME_BOOTSTRAP_TASK_PREPROCESS"] = "1"
        env["NLRL_RUNTIME_BOOTSTRAP_PREPROCESS_NOTE_CHAR_CAP"] = os.environ.get(
            "GAIA_9B_BOOT_ARCHV53_PREPROCESS_NOTE_CHAR_CAP",
            "700",
        )
    if skill_path is not None:
        env["NLRL_RUNTIME_INITIAL_SKILL_PATH"] = str(skill_path)
    return env


def start_train(
    *,
    key: str,
    run_name: str,
    dataset: Path,
    lane: p04.Lane,
    bootstrap: bool = False,
    skill_path: Path | None = None,
) -> subprocess.Popen[str]:
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
    if bootstrap:
        cmd.append("--bootstrap-skill")
    env = build_env(lane.base_url, skill_path=skill_path)
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
        notes=(
            f"{BOOT_VERSION_LABEL} + ARCH-V5.3 boot-only {key}; lane={lane.name}; "
            f"hide_stale_phase_prompts=1; skill={skill_path or '(bootstrap)'}."
        ),
    )
    log(f"started {key} pid={proc.pid} lane={lane.name} endpoint={lane.base_url} log={log_path}")
    return proc


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(
            "Run BOOT-V3/BOOT-V4 + ARCH-V5.3 boot-only dev/test queue.\n"
            "Options:\n"
            "  --preflight-only  validate local paths and resolved settings, then exit\n"
            "Environment:\n"
            "  GAIA_9B_BOOTV3_ARCHV53_PREFIX, GAIA_9B_BOOTV3_ARCHV53_CONCURRENCY, "
            "GAIA_9B_BOOTV3_ARCHV53_POLL_SECONDS, GAIA_9B_BOOTV3_ARCHV53_LANES, "
            "GAIA_9B_BOOT_VARIANT_LABEL, GAIA_9B_BOOT_ARCHV53_PREPROCESS\n"
        )
        return 0
    for path in (PYTHON, CONFIG, DEV_DATASET, TEST_DATASET):
        if not path.exists():
            raise RuntimeError(f"missing required path: {path}")
    refresh_process_registry()
    if "--preflight-only" in sys.argv:
        log(
            f"preflight ok: queue={QUEUE_NAME}; lanes={[lane.base_url for lane in LANES]}; "
            f"concurrency={CONCURRENCY}; variant={BOOT_VARIANT_LABEL}; "
            f"preprocess={BOOT_PREPROCESS}; hide_stale_phase_prompts=1"
        )
        return 0
    QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    queue_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", QUEUE_LOG_ROOT / f"{QUEUE_NAME}.log"))
    append_process_row(
        kind="experiment_queue",
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(BOOT-V3 ARCH-V5.3 boot-only queue manager)",
        command=command_display(os.environ, [str(PYTHON), "-u", __file__]),
        log_path=queue_log,
        notes=(
            f"{BOOT_VERSION_LABEL} prompt + ARCH-V5.3 stale phase prompt hiding; "
            f"preprocess={BOOT_PREPROCESS}; lanes={[asdict(lane) for lane in LANES]}; c{CONCURRENCY}."
        ),
    )
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        for lane in LANES:
            p04.start_or_reuse_vllm(lane)
        wait_endpoints()
        boot_dev = start_train(
            key="boot_dev",
            run_name=BOOT_DEV_RUN_NAME,
            dataset=DEV_DATASET,
            lane=LANES[0],
            bootstrap=True,
        )
        boot_test: subprocess.Popen[str] | None = None
        same_lane_sequential = LANES[0].name == LANES[1].name
        while True:
            skill = bootstrap_skill_path()
            dev_code = boot_dev.poll()
            if skill is not None and boot_test is None and (not same_lane_sequential or dev_code == 0):
                log(f"bootstrap skill ready: {skill}")
                boot_test = start_train(
                    key="boot_test",
                    run_name=BOOT_TEST_RUN_NAME,
                    dataset=TEST_DATASET,
                    lane=LANES[1],
                    skill_path=skill,
                )

            test_code = None if boot_test is None else boot_test.poll()
            if dev_code is not None:
                update_process_row(boot_dev.pid, "finished" if dev_code == 0 else "dead")
            if boot_test is not None and test_code is not None:
                update_process_row(boot_test.pid, "finished" if test_code == 0 else "dead")
            if dev_code is not None and dev_code != 0 and boot_test is None:
                raise RuntimeError(f"boot_dev failed before skill was ready: returncode={dev_code}")
            if dev_code is not None and boot_test is not None and test_code is not None:
                if dev_code != 0 or test_code != 0:
                    raise RuntimeError(f"boot-only child failure: boot_dev={dev_code}, boot_test={test_code}")
                log("BOOT-V3 + ARCH-V5.3 boot-only queue completed")
                return 0

            dev_done, dev_total = state_count(BOOT_DEV_RUN_NAME)
            test_done, test_total = state_count(BOOT_TEST_RUN_NAME)
            log(f"progress boot_dev={dev_done}/{dev_total or '?'} boot_test={test_done}/{test_total or '?'}")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception:
        status = "dead"
        raise
    finally:
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
