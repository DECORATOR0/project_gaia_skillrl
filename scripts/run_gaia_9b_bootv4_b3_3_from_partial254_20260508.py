from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
RUN_ROOT = ROOT / "runs"
QUEUE_LOG_ROOT = RUN_ROOT / "_queue_logs"

PREFIX = os.environ.get(
    "GAIA_B3_3_FROM_PARTIAL254_PREFIX",
    "20260508_0945_9b_bootv4_b3_3_from_partial254",
).strip()
MASTER_NAME = f"{PREFIX}_master"
MASTER_LOG = QUEUE_LOG_ROOT / f"{MASTER_NAME}.log"

os.environ["GAIA_B3_CONTINUE254_PREFIX"] = PREFIX
os.environ["GAIA_B3_CONTINUE254_MASTER_LOG"] = str(MASTER_LOG)
os.environ.setdefault("GAIA_B3_CONTINUE254_SKIP_DEPLOY", "1")
os.environ.setdefault("GAIA_B3_CONTINUE254_CONCURRENCY", "20")
os.environ.setdefault("GAIA_B3_CONTINUE254_CODEX_TIMEOUT_SECONDS", "3600")
os.environ.setdefault("GAIA_B3_CONTINUE254_POLL_SECONDS", "180")
os.environ.setdefault("NLRL_CODEX_CLI_WORKDIR", str(ROOT))

sys.path.insert(0, str(SCRIPTS))
import run_gaia_9b_bootv4_b3_continue254_20260508 as prev  # noqa: E402


B3_2_DEV_RUN = (
    RUN_ROOT
    / "2026/5/2026-5-8/20260508_000945_9b_bootv4_b3_continue254_9b_bootv4_B3_2_dev_eval_c20"
)
B3_2_SKILL = (
    RUN_ROOT
    / "2026/5/2026-5-8/20260508_000956_9b_bootv4_b3_continue254_9b_bootv4_B3_2_offline_actor_iter1/"
    "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)


def main() -> int:
    start = prev.now()
    status = "finished"
    prev.QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    prev.LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    prev.base.ensure_process_csv_header()
    prev.base.refresh_process_registry()
    missing = [str(path) for path in [B3_2_DEV_RUN, B3_2_SKILL, prev.CONFIG, prev.DEV_DATASET, prev.TEST_DATASET] if not path.exists()]
    if missing:
        raise RuntimeError("missing required paths: " + ", ".join(missing))

    master_command = os.environ.get(
        "GAIA_B3_3_FROM_PARTIAL254_MASTER_COMMAND",
        f"{prev.PYTHON} -u {Path(__file__).resolve()}",
    )
    prev.append_process_row(
        kind="experiment_master",
        name=prev.MASTER_NAME,
        pid=os.getpid(),
        cwd=str(prev.ROOT),
        run_dir=str(prev.QUEUE_LOG_ROOT),
        command=master_command,
        log_path=str(prev.MASTER_LOG),
        notes=(
            "9B BOOT-V4 B3-3 from partial B3-2 dev source on 254; "
            f"source={B3_2_DEV_RUN}; source_skill={B3_2_SKILL}; summary={prev.SUMMARY_JSON}; "
            f"manifest={prev.MANIFEST_JSON}"
        ),
    )
    try:
        prev.wait_9b_endpoints()
        prev.write_manifest(
            "running",
            {
                "source_run": str(B3_2_DEV_RUN),
                "source_skill": str(B3_2_SKILL),
                "source_stats": prev.stats_for_run(B3_2_DEV_RUN.name),
                "started_at": start,
                "mode": "b3_3_from_partial_b3_2_dev",
            },
        )
        b3_3_skill = prev.run_offline("B3-3", B3_2_DEV_RUN, B3_2_SKILL, [])
        prev.launch_eval(
            label="9b_bootv4_B3-3_dev",
            run_name=f"{prev.PREFIX}_9b_bootv4_B3_3_dev_eval_c{prev.CONCURRENCY}",
            dataset=prev.DEV_DATASET,
            lane=prev.LANES[1],
            runner="train-local",
            skill_path=b3_3_skill,
        )
        prev.launch_eval(
            label="9b_bootv4_B3-3_test",
            run_name=f"{prev.PREFIX}_9b_bootv4_B3_3_test_eval_c{prev.CONCURRENCY}",
            dataset=prev.TEST_DATASET,
            lane=prev.LANES[0],
            runner="train-local",
            skill_path=b3_3_skill,
        )
        prev.wait_all()
        failed = {label: item.returncode for label, item in prev.launched.items() if item.returncode not in {0, None}}
        status = "finished_with_eval_failures" if failed else "finished"
        prev.write_manifest(status, {"started_at": start, "ended_at": prev.now(), "failed": failed})
        prev.write_summary(status)
        return 0 if not failed else 1
    except KeyboardInterrupt:
        status = "stopped"
        prev.write_manifest(status, {"started_at": start, "ended_at": prev.now()})
        prev.write_summary(status)
        return 130
    except Exception as exc:
        status = "dead"
        prev.write_manifest(status, {"started_at": start, "ended_at": prev.now(), "error": repr(exc)})
        prev.write_summary(status)
        raise
    finally:
        prev.base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
