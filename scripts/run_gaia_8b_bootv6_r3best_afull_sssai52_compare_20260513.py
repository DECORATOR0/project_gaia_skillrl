from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"

PREFIX = os.environ.get(
    "GAIA_8B_BOOTV6_R3BEST_AFULL_SSSAI52_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv6_r3best_afull_sssai52_compare_remote132"),
).strip()

SSSAI_BASE_URL = os.environ.get(
    "GAIA_GPT52_SSSAI_BASE_URL",
    "https://node-hk.sssaicode.com/api/v1/responses",
).strip()
SSSAI_MODEL = os.environ.get("GAIA_GPT52_SSSAI_MODEL", "gpt-5.2").strip()
SSSAI_REASONING_EFFORT = os.environ.get("GAIA_GPT52_SSSAI_REASONING_EFFORT", "high").strip() or "high"


def read_sssai_api_key() -> str:
    for name in ("GAIA_GPT52_SSSAI_API_KEY", "NLRL_ACTOR_API_KEY", "NLRL_LLM_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value and value != "EMPTY":
            return value

    config_path = ROOT / "configs/system.json"
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        for section_name in ("actor", "critic", "executor"):
            section = data.get(section_name, {})
            value = str(section.get("api_key", "")).strip()
            if value.startswith("sk-sssaicode-"):
                return value

    api_doc = ROOT / "skill-pool/API说明/最新api说明.txt"
    if api_doc.exists():
        text = api_doc.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"sk-sssaicode-[A-Za-z0-9]+", text)
        if match:
            return match.group(0)

    raise RuntimeError("No SSSAI API key found in env, configs/system.json, or 最新api说明.txt.")


SSSAI_API_KEY = read_sssai_api_key()

os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_PREFIX"] = PREFIX
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_CONCURRENCY", "20")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_CODEX_TIMEOUT_SECONDS", "7200")
os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_CRITIC_SHARD_CONCURRENCY"] = "1"
os.environ["NLRL_LLM_MAX_CONCURRENT_REQUESTS"] = "1"
os.environ.setdefault("NLRL_LLM_MAX_RETRIES", "2")
os.environ.setdefault("NLRL_LLM_RETRY_DELAY_SECONDS", "10")
os.environ.setdefault("NLRL_LLM_RETRY_BACKOFF_MULTIPLIER", "1.4")
os.environ.setdefault("NLRL_LLM_RETRY_MAX_DELAY_SECONDS", "90")

for role in ("ACTOR", "CRITIC"):
    os.environ[f"NLRL_{role}_MODEL"] = SSSAI_MODEL
    os.environ[f"NLRL_{role}_BASE_URL"] = SSSAI_BASE_URL
    os.environ[f"NLRL_{role}_API_KEY"] = SSSAI_API_KEY
    os.environ[f"NLRL_{role}_API_MODE"] = "responses_sse"
    os.environ[f"NLRL_{role}_ENABLE_THINKING"] = "1"
    os.environ[f"NLRL_{role}_REASONING_EFFORT"] = SSSAI_REASONING_EFFORT
    os.environ[f"NLRL_{role}_TIMEOUT_SECONDS"] = "2400"
    os.environ[f"NLRL_{role}_TEMPERATURE"] = "0.2"

os.environ["NLRL_ACTOR_MAX_TOKENS"] = "24000"
os.environ["NLRL_CRITIC_MAX_TOKENS"] = "12000"

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
XHIGH_SKILL = (
    ROOT
    / "runs/2026/5/2026-5-13/20260513_213403_8b_bootv6_r3best_afull_xhigh_compare_remote132_8b_bootv6_r3best_xhigh_compare_A_full_offline_actor_iter1"
    / "offline_iter1/skill_after_actor/gaia-general-skill/SKILL.md"
)
COMPARE_JSON = q.QUEUE_LOG_ROOT / f"{PREFIX}_compare.json"

TIMINGS: dict[str, Any] = {}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def skill_stats(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    text = path.read_text(encoding="utf-8")
    headings = [line.strip() for line in text.splitlines() if line.startswith("#")]
    phase_blocks = re.findall(r"(?ms)^## Phase:\s*([^\n]+)\n(.*?)(?=^## Phase:|\Z)", text)
    return {
        "path": str(path),
        "exists": True,
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "token_est_chars_div4": round(len(text) / 4),
        "sha256_12": sha12(text),
        "headings": headings,
        "phase_line_counts": {name.strip(): len(body.splitlines()) for name, body in phase_blocks},
    }


def install_timing_hooks() -> None:
    original_evaluate_batch = q.remain20.SkillCritic.evaluate_batch
    original_act = q.remain20.SkillActor.act

    def timed_evaluate_batch(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        TIMINGS["critic_started_at"] = now()
        try:
            return original_evaluate_batch(self, *args, **kwargs)
        finally:
            TIMINGS["critic_seconds"] = round(time.monotonic() - started, 3)
            TIMINGS["critic_ended_at"] = now()

    def timed_act(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        TIMINGS["actor_started_at"] = now()
        try:
            return original_act(self, *args, **kwargs)
        finally:
            TIMINGS["actor_seconds"] = round(time.monotonic() - started, 3)
            TIMINGS["actor_ended_at"] = now()

    q.remain20.SkillCritic.evaluate_batch = timed_evaluate_batch
    q.remain20.SkillActor.act = timed_act


def main() -> int:
    q.configure_modules()
    q.verify_inputs()
    install_timing_hooks()
    if not SOURCE_RUN.exists():
        raise FileNotFoundError(f"missing source run: {SOURCE_RUN}")
    if not BASE_SKILL.exists():
        raise FileNotFoundError(f"missing base skill: {BASE_SKILL}")

    q.remain20.CONFIG = q.CONFIGS["bootv6"]
    q.remain20.PREFIX = PREFIX
    q.remain20.CRITIC_SHARD_CONCURRENCY = "1"
    q.remain20.CODEX_TIMEOUT_SECONDS = 7200

    source = q.remain20.BootSource(
        model_key="8b",
        boot_key="bootv6_r3best_sssai52_compare",
        label="8B BOOTV6 r3best SSSAI gpt-5.2 A_full compare",
        boot_dev_run=SOURCE_RUN,
        boot_skill=BASE_SKILL,
        supplemental_runs=[],
        source_notes=(
            "SSSAI gpt-5.2 Responses/SSE A_full skill-generation comparison only; "
            "no downstream executor eval is enqueued."
        ),
    )

    started = time.monotonic()
    skill = q.remain20.run_offline_variant(
        source,
        method_key="A_full",
        strategy="full",
        graph_policy="locked",
    )
    total_seconds = round(time.monotonic() - started, 3)
    payload = {
        "prefix": PREFIX,
        "provider": "sssaicode",
        "model": SSSAI_MODEL,
        "base_url": SSSAI_BASE_URL,
        "api_mode": "responses_sse",
        "reasoning_effort": SSSAI_REASONING_EFFORT,
        "max_concurrent_strong_model_requests": 1,
        "source_run": str(SOURCE_RUN),
        "total_seconds": total_seconds,
        "stage_timings": TIMINGS,
        "base_skill": skill_stats(BASE_SKILL),
        "codex_high_afull_skill": skill_stats(HIGH_SKILL),
        "codex_xhigh_afull_skill": skill_stats(XHIGH_SKILL),
        "sssaicode_gpt52_afull_skill": skill_stats(skill),
    }
    COMPARE_JSON.parent.mkdir(parents=True, exist_ok=True)
    COMPARE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
