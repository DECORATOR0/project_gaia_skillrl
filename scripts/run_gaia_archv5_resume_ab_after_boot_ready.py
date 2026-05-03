from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import run_gaia_archv5_resume_ab_queue as resume_ab  # noqa: E402
import run_gaia_archv5_toolv4_direct_boot_ab_queue as base  # noqa: E402


BOOT_DEV_PID_RAW = os.environ.get("GAIA_ARCHV5_EXISTING_BOOT_DEV_PID", "").strip()
BOOT_DEV_PID = int(BOOT_DEV_PID_RAW) if BOOT_DEV_PID_RAW.isdigit() else 0
QUEUE_NAME = f"{base.RUN_PREFIX}_{base.PROFILE_LABEL}_ab_after_boot_ready_queue_{base.CONCURRENCY_LABEL}"


def release_boot_dev_tail_if_ready() -> None:
    if not BOOT_DEV_PID:
        return
    done, total = base.progress_for(resume_ab.BOOT_DEV_RUN_NAME)
    if total <= 0 or done < base.min_required_states(total):
        return
    if not base.pid_alive(BOOT_DEV_PID):
        base.update_process_row(BOOT_DEV_PID, "dead")
        return
    base.log(f"boot_dev tail kept running pid={BOOT_DEV_PID}: {done}/{total}")


def wait_for_boot_dev_ready() -> None:
    while True:
        done, total = base.progress_for(resume_ab.BOOT_DEV_RUN_NAME)
        required = base.min_required_states(total)
        skill = base.bootstrap_skill_path(resume_ab.BOOT_DEV_RUN_NAME)
        if total > 0 and done >= required and skill is not None and skill.exists():
            base.log(
                f"boot_dev ready for AB: {done}/{total}, required={required}, skill={skill}"
            )
            release_boot_dev_tail_if_ready()
            return
        if BOOT_DEV_PID and not base.pid_alive(BOOT_DEV_PID):
            raise RuntimeError(
                f"boot_dev pid={BOOT_DEV_PID} exited before AB readiness: "
                f"{done}/{total}, required={required}, skill={skill}"
            )
        base.log(
            f"waiting boot_dev for AB: {done}/{total}, required={required}, "
            f"skill_ready={bool(skill and skill.exists())}"
        )
        time.sleep(base.POLL_SECONDS)


def main() -> int:
    base.refresh_process_registry()
    os.environ.update(base.codex_role_env())
    os.environ["NLRL_RUNTIME_TOOL_PROFILE"] = base.TOOL_PROFILE
    base.preflight()
    if "--preflight-only" in sys.argv:
        base.log(
            f"preflight ok: tool_profile={base.TOOL_PROFILE}; "
            f"schedule=ab_after_boot_ready; concurrency={base.CONCURRENCY}"
        )
        return 0

    queue_log_raw = os.environ.get("NLRL_QUEUE_LOG_PATH", "").strip()
    queue_log = (
        Path(queue_log_raw)
        if queue_log_raw
        else base.LAUNCH_LOG_ROOT / f"{QUEUE_NAME}.log"
    )
    base.append_process_row(
        name=QUEUE_NAME,
        pid=os.getpid(),
        cwd=base.ROOT,
        run_dir="(resume AB after boot-ready manager)",
        command=base.command_display(os.environ, [str(base.PYTHON), *sys.argv]),
        log_path=queue_log,
        notes=(
            "ARCH-V5.2 resume AB watcher; wait for existing boot_dev states/skill, "
            "then reuse them to generate A/B and run four evals; "
            f"tool_profile={base.TOOL_PROFILE}; answer_policy="
            f"{os.environ.get('NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY', 'conclude_only')}; "
            f"existing_boot_dev_pid={BOOT_DEV_PID or 'unknown'}; "
            f"external_boot_test={resume_ab.BOOT_TEST_RUN_NAME}; "
            f"boot_test_pid={resume_ab.BOOT_TEST_PID or 'unknown'}; "
            f"boot_test_lane={resume_ab.BOOT_TEST_LANE}; {base.CONCURRENCY_LABEL}."
        ),
        kind="experiment_queue",
    )
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        wait_for_boot_dev_ready()
        resume_ab.run_resume_ab()
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
