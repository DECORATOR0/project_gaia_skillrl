from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import run_gaia_archv5_toolv4_direct_boot_ab_queue as base


BOOT_ONLY_QUEUE_NAME = (
    f"{base.RUN_PREFIX}_{base.PROFILE_LABEL}_boot_only_queue_{base.CONCURRENCY_LABEL}"
)


def run_boot_only() -> None:
    base.wait_for_prior_pids()
    for lane in sorted(base.LANES):
        base.start_vllm(lane)
    base.wait_vllm_ready()
    for lane in sorted(base.LANES):
        pid = base.model_server_pids.get(lane) or base.find_vllm_pid(
            8100 + int(lane.replace("gpu", ""))
        )
        base.log(f"model lane={lane} endpoint={base.LANES[lane]} pid={pid or 'unknown'}")

    boot_dev_lane = os.environ.get("GAIA_ARCHV5_BOOT_DEV_LANE", "").strip() or "gpu0"
    boot_test_preferred_lane = (
        os.environ.get("GAIA_ARCHV5_BOOT_TEST_LANE", "").strip() or "gpu2"
    )
    jobs: list[base.RunJob] = [
        base.RunJob(
            key="boot_dev",
            run_name=base.BOOT_DEV_RUN_NAME,
            dataset=base.DEV_DATASET,
            command="train-local",
            lane=boot_dev_lane,
            bootstrap=True,
        )
    ]
    base.start_job(
        jobs[0],
        notes=(
            f"ARCH-V5 {base.PROFILE_LABEL} boot-only rerun validation_dev83; "
            f"tool_profile={base.TOOL_PROFILE}; ROLE=Codex CLI GPT-5.4; "
            f"executor=Qwen3-8B-local; lane={boot_dev_lane}; {base.CONCURRENCY_LABEL}."
        ),
    )

    boot_test: base.RunJob | None = None
    boot_skill: Path | None = None
    while True:
        base.monitor_jobs(jobs)
        if boot_skill is None:
            boot_skill = base.bootstrap_skill_path(base.BOOT_DEV_RUN_NAME)
            if boot_skill is not None:
                base.log(f"bootstrap skill ready: {boot_skill}")
        if boot_skill is not None and boot_test is None:
            lane = base.choose_free_lane(jobs, preferred=boot_test_preferred_lane)
            if lane is not None:
                boot_test = base.RunJob(
                    key="boot_test",
                    run_name=base.BOOT_TEST_RUN_NAME,
                    dataset=base.TEST_DATASET,
                    command="train-local",
                    lane=lane,
                    skill_path=boot_skill,
                )
                base.start_job(
                    boot_test,
                    notes=(
                        f"ARCH-V5 {base.PROFILE_LABEL} boot-only rerun validation_test82; "
                        f"tool_profile={base.TOOL_PROFILE}; skill={boot_skill}; "
                        f"lane={lane}; {base.CONCURRENCY_LABEL}."
                    ),
                )
                jobs.append(boot_test)

        boot_dev_done = base.all_terminal([jobs[0]])
        boot_test_done = boot_test is not None and base.all_terminal([boot_test])
        if boot_dev_done and boot_test_done:
            base.log(f"ARCH-V5 {base.PROFILE_LABEL} boot-only queue completed")
            return

        base.log_running_progress(jobs)
        time.sleep(base.POLL_SECONDS)


def main() -> int:
    base.refresh_process_registry()
    os.environ.update(base.codex_role_env())
    os.environ["NLRL_RUNTIME_TOOL_PROFILE"] = base.TOOL_PROFILE
    base.preflight()
    if "--preflight-only" in sys.argv:
        base.log(
            f"preflight ok: tool_profile={base.TOOL_PROFILE}; "
            f"schedule=boot_only; concurrency={base.CONCURRENCY}"
        )
        return 0

    queue_log_raw = os.environ.get("NLRL_QUEUE_LOG_PATH", "").strip()
    queue_log = (
        Path(queue_log_raw)
        if queue_log_raw
        else base.LAUNCH_LOG_ROOT / f"{BOOT_ONLY_QUEUE_NAME}.log"
    )
    base.append_process_row(
        name=BOOT_ONLY_QUEUE_NAME,
        pid=os.getpid(),
        cwd=base.ROOT,
        run_dir="(boot-only queue manager)",
        command=base.command_display(os.environ, [str(base.PYTHON), *sys.argv]),
        log_path=queue_log,
        notes=(
            "ARCH-V5.2 no-CONCLUDE boot-only rerun after web_search timeout incident; "
            f"tool_profile={base.TOOL_PROFILE}; answer_policy="
            f"{os.environ.get('NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY', 'conclude_only')}; "
            f"executor Qwen3-8B local; {base.CONCURRENCY_LABEL}; "
            f"partial tail allowed={base.ALLOW_PARTIAL_TAIL}."
        ),
        kind="experiment_queue",
    )
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        run_boot_only()
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
