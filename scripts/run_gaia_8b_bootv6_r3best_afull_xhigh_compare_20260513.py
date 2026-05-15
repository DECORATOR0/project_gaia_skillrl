from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
PYTHON = ROOT / ".venv/bin/python"

PREFIX = os.environ.get(
    "GAIA_8B_BOOTV6_R3BEST_AFULL_XHIGH_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv6_r3best_afull_xhigh_compare_remote132"),
).strip()

os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_PREFIX"] = PREFIX
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_CONCURRENCY", "20")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_CODEX_TIMEOUT_SECONDS", "7200")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_CRITIC_SHARD_CONCURRENCY", "4")
os.environ["NLRL_CODEX_CLI_EXTRA_ARGS"] = "-c 'model_reasoning_effort=\"xhigh\"'"
os.environ["NLRL_ACTOR_REASONING_EFFORT"] = "xhigh"
os.environ["NLRL_CRITIC_REASONING_EFFORT"] = "xhigh"

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_bootv5v6_full_matrix_remote132_20260513 as q  # noqa: E402


SOURCE_RUN = (
    ROOT
    / "runs/2026/5/2026-5-13/20260513_151911_8b_bootv5v6_fullmatrix_remote132_retry_sem_8b_bootv6_r3_boot_dev_c20"
)
BASE_SKILL = SOURCE_RUN / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
HIGH_SKILL = (
    ROOT
    / "runs/2026/5/2026-5-13/20260513_184850_8b_bootv6_r3_fixed_ab1_repeats_remote132_8b_bootv6_r3best_A_full_offline_actor_iter1"
    / "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)
COMPARE_JSON = q.QUEUE_LOG_ROOT / f"{PREFIX}_compare.json"


def skill_stats(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    headings = [line.strip() for line in text.splitlines() if line.startswith("#")]
    return {
        "path": str(path),
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "token_est_chars_div4": round(len(text) / 4),
        "headings": headings,
    }


def main() -> int:
    q.configure_modules()
    q.verify_inputs()
    if not SOURCE_RUN.exists():
        raise FileNotFoundError(f"missing source run: {SOURCE_RUN}")
    if not BASE_SKILL.exists():
        raise FileNotFoundError(f"missing base skill: {BASE_SKILL}")
    if not HIGH_SKILL.exists():
        raise FileNotFoundError(f"missing high skill for comparison: {HIGH_SKILL}")

    q.remain20.CONFIG = q.CONFIGS["bootv6"]
    q.remain20.PREFIX = PREFIX
    q.remain20.CRITIC_SHARD_CONCURRENCY = "4"
    q.remain20.CODEX_TIMEOUT_SECONDS = 7200

    source = q.remain20.BootSource(
        model_key="8b",
        boot_key="bootv6_r3best_xhigh_compare",
        label="8B BOOTV6 r3best xhigh A_full compare",
        boot_dev_run=SOURCE_RUN,
        boot_skill=BASE_SKILL,
        supplemental_runs=[],
        source_notes=(
            "xhigh A_full comparison only; no downstream executor eval is enqueued. "
            "Source matches the high A_full r3best run."
        ),
    )

    skill = q.remain20.run_offline_variant(
        source,
        method_key="A_full",
        strategy="full",
        graph_policy="locked",
    )
    payload = {
        "prefix": PREFIX,
        "effort": "xhigh",
        "source_run": str(SOURCE_RUN),
        "base_skill": skill_stats(BASE_SKILL),
        "high_afull_skill": skill_stats(HIGH_SKILL),
        "xhigh_afull_skill": skill_stats(skill),
    }
    COMPARE_JSON.parent.mkdir(parents=True, exist_ok=True)
    COMPARE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
