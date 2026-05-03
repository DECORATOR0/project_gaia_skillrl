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


BOOT_DEV_RUN_NAME = os.environ.get("GAIA_ARCHV5_RESUME_BOOT_DEV_RUN_NAME", "").strip() or base.BOOT_DEV_RUN_NAME
BOOT_SKILL_PATH = os.environ.get("GAIA_ARCHV5_RESUME_BOOT_SKILL_PATH", "").strip()
BOOT_DEV_PID_RAW = os.environ.get("GAIA_ARCHV5_EXISTING_BOOT_DEV_PID", "").strip()
BOOT_DEV_PID = int(BOOT_DEV_PID_RAW) if BOOT_DEV_PID_RAW.isdigit() else 0
BOOT_DEV_LANE = os.environ.get("GAIA_ARCHV5_EXISTING_BOOT_DEV_LANE", "").strip() or "gpu0"
BOOT_TEST_RUN_NAME = os.environ.get("GAIA_ARCHV5_EXISTING_BOOT_TEST_RUN_NAME", "").strip() or base.BOOT_TEST_RUN_NAME
BOOT_TEST_PID_RAW = os.environ.get("GAIA_ARCHV5_EXISTING_BOOT_TEST_PID", "").strip()
BOOT_TEST_PID = int(BOOT_TEST_PID_RAW) if BOOT_TEST_PID_RAW.isdigit() else 0
BOOT_TEST_LANE = os.environ.get("GAIA_ARCHV5_EXISTING_BOOT_TEST_LANE", "").strip() or "gpu1"
QUEUE_NAME = f"{base.RUN_PREFIX}_{base.PROFILE_LABEL}_ab_resume_queue_{base.CONCURRENCY_LABEL}"
WAIT_FOR_BOOT = os.environ.get("GAIA_ARCHV5_RESUME_WAIT_FOR_BOOT", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def boot_dev_busy() -> bool:
    if BOOT_DEV_PID and base.pid_alive(BOOT_DEV_PID):
        return True
    if BOOT_DEV_PID:
        base.update_process_row(BOOT_DEV_PID, "dead")
    return False


def boot_test_busy() -> bool:
    if not BOOT_TEST_RUN_NAME or BOOT_TEST_LANE not in base.LANES:
        return False
    if BOOT_TEST_PID:
        if base.pid_alive(BOOT_TEST_PID):
            return True
        base.update_process_row(BOOT_TEST_PID, "dead")
        return False
    if base.run_has_required_states(BOOT_TEST_RUN_NAME):
        return False
    return True


def release_boot_test_tail_if_ready() -> None:
    return


def choose_free_unpinned_lane(jobs: list[base.RunJob]) -> str | None:
    active = base.active_lanes(jobs)
    if boot_dev_busy():
        active.add(BOOT_DEV_LANE)
    if boot_test_busy():
        active.add(BOOT_TEST_LANE)
    free = [lane for lane in base.LANES if lane != BOOT_TEST_LANE and lane not in active]
    return free[0] if free else None


def start_unpinned_eval_if_possible(
    jobs: list[base.RunJob],
    ready_specs: list[base.EvalSpec],
    eval_jobs: list[base.RunJob],
) -> None:
    while ready_specs:
        lane = choose_free_unpinned_lane(jobs)
        if lane is None:
            return
        spec = ready_specs.pop(0)
        job = base.RunJob(
            key=spec.key,
            run_name=spec.run_name,
            dataset=spec.dataset,
            command="train-local",
            lane=lane,
            skill_path=spec.skill_path,
        )
        base.start_job(job, notes=f"{spec.notes}; lane={lane}; {base.CONCURRENCY_LABEL}; resume-ab.")
        jobs.append(job)
        eval_jobs.append(job)


def start_boot_lane_eval_if_possible(
    jobs: list[base.RunJob],
    ready_specs: list[base.EvalSpec],
    eval_jobs: list[base.RunJob],
) -> None:
    if not ready_specs:
        return
    if boot_test_busy():
        return
    if BOOT_TEST_LANE in base.active_lanes(jobs):
        return
    spec = ready_specs.pop(0)
    job = base.RunJob(
        key=spec.key,
        run_name=spec.run_name,
        dataset=spec.dataset,
        command="train-local",
        lane=BOOT_TEST_LANE,
        skill_path=spec.skill_path,
    )
    base.start_job(
        job,
        notes=(
            f"{spec.notes}; lane={BOOT_TEST_LANE}; {base.CONCURRENCY_LABEL}; "
            "resume-ab pinned to former boot_test lane."
        ),
    )
    jobs.append(job)
    eval_jobs.append(job)


def run_resume_ab() -> None:
    boot_run_dir = base.latest_run_dir(BOOT_DEV_RUN_NAME)
    if boot_run_dir is None:
        raise RuntimeError(f"boot_dev run not found: {BOOT_DEV_RUN_NAME}")
    boot_skill = Path(BOOT_SKILL_PATH) if BOOT_SKILL_PATH else base.bootstrap_skill_path(BOOT_DEV_RUN_NAME)
    if boot_skill is None or not boot_skill.exists():
        raise RuntimeError(f"boot skill not found: {boot_skill}")
    while not base.run_has_required_states(BOOT_DEV_RUN_NAME):
        done, total = base.progress_for(BOOT_DEV_RUN_NAME)
        required = base.min_required_states(total)
        if not WAIT_FOR_BOOT:
            raise RuntimeError(
                f"boot_dev states not ready for AB: {done}/{total}, required={required}"
            )
        base.log(
            f"waiting for boot_dev states before AB: {done}/{total}, "
            f"required={required}, sleep={base.POLL_SECONDS}s"
        )
        time.sleep(base.POLL_SECONDS)

    base.log(f"resume AB from boot_dev={boot_run_dir}")
    base.log(f"resume AB skill={boot_skill}")
    if BOOT_DEV_PID:
        base.log(
            f"external boot_dev={BOOT_DEV_RUN_NAME} pid={BOOT_DEV_PID} "
            f"lane={BOOT_DEV_LANE} busy={boot_dev_busy()}"
        )
    if BOOT_TEST_RUN_NAME:
        done, total = base.progress_for(BOOT_TEST_RUN_NAME)
        base.log(
            f"external boot_test={BOOT_TEST_RUN_NAME} pid={BOOT_TEST_PID or 'unknown'} "
            f"lane={BOOT_TEST_LANE} progress={done}/{total}"
        )

    jobs: list[base.RunJob] = []
    eval_jobs: list[base.RunJob] = []
    ready_unpinned_specs: list[base.EvalSpec] = []
    ready_boot_lane_specs: list[base.EvalSpec] = []
    expected_eval_count = 0
    pending_futures: dict[Future[Path], str] = {}

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="resume-ab") as pool:
        pending_futures[
            pool.submit(
                base.run_offline_variant,
                strategy="full",
                run_name=base.A_OFFLINE_RUN_NAME,
                boot_run_dir=boot_run_dir,
                boot_skill=boot_skill,
            )
        ] = "a"
        pending_futures[
            pool.submit(
                base.run_offline_variant,
                strategy="sharded",
                run_name=base.B_OFFLINE_RUN_NAME,
                boot_run_dir=boot_run_dir,
                boot_skill=boot_skill,
            )
        ] = "b"

        while True:
            base.monitor_jobs(jobs)
            release_boot_test_tail_if_ready()

            for future, variant in list(pending_futures.items()):
                if not future.done():
                    continue
                del pending_futures[future]
                skill = future.result()
                if variant == "a":
                    specs = base.eval_specs_for_a(skill)
                    base.log(f"A skill ready; queueing A dev/test: {skill}")
                else:
                    specs = base.eval_specs_for_b(skill)
                    base.log(f"B skill ready; queueing B test now and B dev on {BOOT_TEST_LANE}: {skill}")
                expected_eval_count += len(specs)
                for spec in specs:
                    if spec.key == "b_dev":
                        ready_boot_lane_specs.append(spec)
                    else:
                        ready_unpinned_specs.append(spec)

            start_unpinned_eval_if_possible(jobs, ready_unpinned_specs, eval_jobs)
            start_boot_lane_eval_if_possible(jobs, ready_boot_lane_specs, eval_jobs)

            eval_done = expected_eval_count == 4 and len(eval_jobs) == 4 and base.all_terminal(eval_jobs)
            if not pending_futures and not ready_unpinned_specs and not ready_boot_lane_specs and eval_done:
                base.log("resume AB queue completed")
                return

            base.log_running_progress(jobs)
            if ready_unpinned_specs:
                base.log(
                    f"ready unpinned eval jobs waiting for a non-busy lane: "
                    f"{[spec.key for spec in ready_unpinned_specs]}"
                )
            if ready_boot_lane_specs:
                base.log(f"ready eval jobs waiting for {BOOT_TEST_LANE}: {[spec.key for spec in ready_boot_lane_specs]}")
            if BOOT_DEV_PID and boot_dev_busy():
                done, total = base.progress_for(BOOT_DEV_RUN_NAME)
                base.log(f"external boot_dev busy: {done}/{total} lane={BOOT_DEV_LANE}")
            if BOOT_TEST_RUN_NAME and boot_test_busy():
                done, total = base.progress_for(BOOT_TEST_RUN_NAME)
                base.log(f"external boot_test busy: {done}/{total} lane={BOOT_TEST_LANE}")
            time.sleep(base.POLL_SECONDS)


def main() -> int:
    base.refresh_process_registry()
    queue_log_raw = os.environ.get("NLRL_QUEUE_LOG_PATH", "").strip()
    queue_log = Path(queue_log_raw) if queue_log_raw else base.LAUNCH_LOG_ROOT / f"{QUEUE_NAME}.log"
    base.append_process_row(
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=base.ROOT,
        run_dir="(resume AB queue manager)",
        command=base.command_display(os.environ, [str(base.PYTHON), *sys.argv]),
        log_path=queue_log,
        notes=(
            "ARCH-V5 resume AB queue; reuse existing boot_dev states/skill; "
            f"tool_profile={base.TOOL_PROFILE}; answer_policy="
            f"{os.environ.get('NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY', 'conclude_only')}; "
            f"external_boot_dev={BOOT_DEV_RUN_NAME}; boot_dev_lane={BOOT_DEV_LANE}; "
            f"external_boot_test={BOOT_TEST_RUN_NAME}; lane={BOOT_TEST_LANE}; "
            f"wait_for_boot={WAIT_FOR_BOOT}; c={base.CONCURRENCY}."
        ),
        kind="experiment_queue",
    )
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        run_resume_ab()
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception:
        status = "dead"
        raise
    finally:
        base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
