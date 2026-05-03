from __future__ import annotations

import os
import signal
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import run_gaia_archv5_toolv4_direct_boot_ab_queue as base  # noqa: E402


BOOT_SKILL_RAW = os.environ.get("GAIA_ARCHV5_EXISTING_BOOT_SKILL_PATH", "").strip()
BOOT_DEV_LANE = os.environ.get("GAIA_ARCHV5_BOOT_DEV_LANE", "").strip() or base.LANE_NAMES[0]
BOOT_TEST_LANE = (
    os.environ.get("GAIA_ARCHV5_BOOT_TEST_LANE", "").strip()
    or base.LANE_NAMES[min(1, len(base.LANE_NAMES) - 1)]
)
QUEUE_NAME = f"{base.RUN_PREFIX}_{base.PROFILE_LABEL}_existing_bootskill_boot_ab_queue_{base.CONCURRENCY_LABEL}"


def boot_skill_path() -> Path:
    if not BOOT_SKILL_RAW:
        raise RuntimeError("GAIA_ARCHV5_EXISTING_BOOT_SKILL_PATH is required.")
    path = Path(BOOT_SKILL_RAW).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"existing boot skill does not exist: {path}")
    return path


def preflight() -> None:
    missing = [
        str(path)
        for path in [base.PYTHON, base.CONFIG, base.DEV_DATASET, base.TEST_DATASET, Path(base.CODEX_BIN)]
        if not path.exists()
    ]
    if missing:
        raise RuntimeError(f"Missing required paths: {missing}")
    if BOOT_DEV_LANE not in base.LANES or BOOT_TEST_LANE not in base.LANES:
        raise RuntimeError(f"Unknown boot lanes: boot_dev={BOOT_DEV_LANE}, boot_test={BOOT_TEST_LANE}")
    tools = base.available_tool_names_for_profile(base.TOOL_PROFILE)
    if base.TOOL_PROFILE == "atomic_v2" and "read_json_file" not in tools:
        raise RuntimeError(f"TOOL-V2 preflight failed: read_json_file is missing from {base.TOOL_PROFILE}.")
    boot_skill_path()


def start_boot_jobs(skill: Path) -> list[base.RunJob]:
    jobs = [
        base.RunJob(
            key="boot_dev",
            run_name=base.BOOT_DEV_RUN_NAME,
            dataset=base.DEV_DATASET,
            command="train-local",
            lane=BOOT_DEV_LANE,
            skill_path=skill,
        ),
        base.RunJob(
            key="boot_test",
            run_name=base.BOOT_TEST_RUN_NAME,
            dataset=base.TEST_DATASET,
            command="train-local",
            lane=BOOT_TEST_LANE,
            skill_path=skill,
        ),
    ]
    for job in jobs:
        split = "validation_dev83" if job.key == "boot_dev" else "validation_test82"
        base.start_job(
            job,
            notes=(
                f"ARCH-V5.1 any_phase existing boot skill rerun {split}; "
                f"source_skill={skill}; tool_profile={base.TOOL_PROFILE}; "
                f"executor={base.EXECUTOR_MODEL}; lane={job.lane}; {base.CONCURRENCY_LABEL}."
            ),
        )
    return jobs


def run_existing_boot_skill_schedule() -> None:
    skill = boot_skill_path()
    jobs = start_boot_jobs(skill)
    eval_jobs: list[base.RunJob] = []
    ready_specs: list[base.EvalSpec] = []
    expected_eval_count = 0
    offline_started = False
    pending_futures: dict[Future[Path], str] = {}

    offline_workers = max(1, int(os.environ.get("GAIA_ARCHV5_OFFLINE_AB_WORKERS", "1")))
    with ThreadPoolExecutor(max_workers=offline_workers, thread_name_prefix="existing-boot-ab") as pool:
        while True:
            base.monitor_jobs(jobs)

            if not offline_started and base.run_has_required_states(base.BOOT_DEV_RUN_NAME):
                boot_run_dir = base.latest_run_dir(base.BOOT_DEV_RUN_NAME)
                if boot_run_dir is None:
                    raise RuntimeError(f"boot_dev run not found: {base.BOOT_DEV_RUN_NAME}")
                offline_started = True
                base.log(f"boot_dev ready for offline A/B: {boot_run_dir}")
                pending_futures[
                    pool.submit(
                        base.run_offline_variant,
                        strategy="full",
                        run_name=base.A_OFFLINE_RUN_NAME,
                        boot_run_dir=boot_run_dir,
                        boot_skill=skill,
                    )
                ] = "a"
                pending_futures[
                    pool.submit(
                        base.run_offline_variant,
                        strategy="sharded",
                        run_name=base.B_OFFLINE_RUN_NAME,
                        boot_run_dir=boot_run_dir,
                        boot_skill=skill,
                    )
                ] = "b"

            for future, variant in list(pending_futures.items()):
                if not future.done():
                    continue
                del pending_futures[future]
                variant_skill = future.result()
                if variant == "a":
                    specs = base.eval_specs_for_a(variant_skill)
                    base.log(f"A skill ready; queueing A dev/test: {variant_skill}")
                else:
                    specs = base.eval_specs_for_b(variant_skill)
                    base.log(f"B skill ready; queueing B dev/test: {variant_skill}")
                expected_eval_count += len(specs)
                ready_specs.extend(specs)

            base.start_eval_if_possible(jobs, ready_specs, eval_jobs)

            boot_dev_ready = base.run_has_required_states(base.BOOT_DEV_RUN_NAME)
            boot_test_done = any(job.key == "boot_test" and job.process is not None and job.process.poll() is not None for job in jobs)
            offline_done = offline_started and not pending_futures
            eval_done = expected_eval_count == 4 and len(eval_jobs) == 4 and base.all_terminal(eval_jobs)
            if boot_dev_ready and boot_test_done and offline_done and eval_done:
                base.log("existing boot skill boot+AB queue completed")
                return

            base.log_running_progress(jobs)
            if ready_specs:
                base.log(f"ready eval jobs waiting for a free lane: {[spec.key for spec in ready_specs]}")
            time.sleep(base.POLL_SECONDS)


def main() -> int:
    base.refresh_process_registry()
    os.environ.update(base.codex_role_env())
    os.environ["NLRL_RUNTIME_TOOL_PROFILE"] = base.TOOL_PROFILE
    preflight()
    if "--preflight-only" in sys.argv:
        base.log(
            f"preflight ok: existing_boot_skill={boot_skill_path()}; "
            f"tool_profile={base.TOOL_PROFILE}; concurrency={base.CONCURRENCY}"
        )
        return 0

    base.LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    queue_log_raw = os.environ.get("NLRL_QUEUE_LOG_PATH", "").strip()
    queue_log = Path(queue_log_raw) if queue_log_raw else base.LAUNCH_LOG_ROOT / f"{QUEUE_NAME}.log"
    base.append_process_row(
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=base.ROOT,
        run_dir="(existing boot skill boot+AB queue manager)",
        command=base.command_display(os.environ, [str(base.PYTHON), *sys.argv]),
        log_path=queue_log,
        notes=(
            "ARCH-V5.1 any_phase rerun from existing 09:52 boot skill; "
            "run boot_dev/boot_test first, then derive A/B from new boot_dev states and run four evals; "
            f"source_skill={boot_skill_path()}; tool_profile={base.TOOL_PROFILE}; "
            f"answer_policy={os.environ.get('NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY', 'conclude_only')}; "
            f"boot_dev_lane={BOOT_DEV_LANE}; boot_test_lane={BOOT_TEST_LANE}; {base.CONCURRENCY_LABEL}."
        ),
        kind="experiment_queue",
    )
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        for lane in sorted(base.LANES):
            base.start_vllm(lane)
        base.wait_vllm_ready()
        for lane in sorted(base.LANES):
            pid = base.lane_model_pid(lane)
            base.log(f"model lane={lane} endpoint={base.LANES[lane]} pid={pid or 'remote'}")
        run_existing_boot_skill_schedule()
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception:
        status = "dead"
        raise
    finally:
        base.cleanup_model_servers()
        base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
