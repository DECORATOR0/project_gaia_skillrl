from __future__ import annotations

import argparse
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

SSSAI_BASE_URL = os.environ.get(
    "GAIA_GPT52_SSSAI_BASE_URL",
    "https://node-hk.sssaicode.com/api/v1/responses",
).strip()
SSSAI_MODEL = os.environ.get("GAIA_GPT52_SSSAI_MODEL", "gpt-5.2").strip()


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

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_bootv5v6_full_matrix_remote132_20260513 as q  # noqa: E402

from gaia_skillrl.bootstrap import SkillBootstrapper  # noqa: E402
from gaia_skillrl.config import clone_system_config, load_system_config  # noqa: E402
from gaia_skillrl.dataset import load_converted_dataset, select_tasks  # noqa: E402
from gaia_skillrl.llm import OpenAICompatibleLLM  # noqa: E402
from gaia_skillrl.skills import discover_skills, load_skill_detail, reset_skill_library, write_skill_bundle  # noqa: E402
from gaia_skillrl.trainer import GaiaSkillTrainer  # noqa: E402
from gaia_skillrl.utils import ensure_dir, write_json  # noqa: E402


REFERENCE_BOOT_RUN = (
    ROOT
    / "runs/2026/5/2026-5-13/20260513_151911_8b_bootv5v6_fullmatrix_remote132_retry_sem_8b_bootv6_r3_boot_dev_c20"
)
REFERENCE_BOOT_SKILL = REFERENCE_BOOT_RUN / "bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"


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


def read_reference_task_ids() -> list[str]:
    selected_path = REFERENCE_BOOT_RUN / "selected_tasks.json"
    if not selected_path.exists():
        return []
    payload = json.loads(selected_path.read_text(encoding="utf-8"))
    ids = payload.get("task_ids", [])
    return [str(item) for item in ids] if isinstance(ids, list) else []


def install_timing_hook(timings: dict[str, Any]) -> None:
    original_chat_json = OpenAICompatibleLLM.chat_json
    call_index = {"value": 0}

    def timed_chat_json(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        call_index["value"] += 1
        index = call_index["value"]
        label = "bootstrap_task_preprocess" if index == 1 else "bootstrap_initial_skill"
        started = time.monotonic()
        timings[f"{label}_started_at"] = now()
        try:
            return original_chat_json(self, *args, **kwargs)
        finally:
            timings[f"{label}_seconds"] = round(time.monotonic() - started, 3)
            timings[f"{label}_ended_at"] = now()

    OpenAICompatibleLLM.chat_json = timed_chat_json


def build_config(reasoning_effort: str, run_dir: Path):
    for role in ("ACTOR", "CRITIC"):
        os.environ[f"NLRL_{role}_MODEL"] = SSSAI_MODEL
        os.environ[f"NLRL_{role}_BASE_URL"] = SSSAI_BASE_URL
        os.environ[f"NLRL_{role}_API_KEY"] = SSSAI_API_KEY
        os.environ[f"NLRL_{role}_API_MODE"] = "responses_sse"
        os.environ[f"NLRL_{role}_ENABLE_THINKING"] = "1"
        os.environ[f"NLRL_{role}_REASONING_EFFORT"] = reasoning_effort
        os.environ[f"NLRL_{role}_TIMEOUT_SECONDS"] = "2400"
        os.environ[f"NLRL_{role}_TEMPERATURE"] = "0.2"
    os.environ["NLRL_ACTOR_MAX_TOKENS"] = "26000"
    os.environ["NLRL_CRITIC_MAX_TOKENS"] = "12000"
    os.environ["NLRL_LLM_MAX_CONCURRENT_REQUESTS"] = "1"
    os.environ.setdefault("NLRL_LLM_MAX_RETRIES", "2")
    os.environ.setdefault("NLRL_LLM_RETRY_DELAY_SECONDS", "10")
    os.environ.setdefault("NLRL_LLM_RETRY_BACKOFF_MULTIPLIER", "1.4")
    os.environ.setdefault("NLRL_LLM_RETRY_MAX_DELAY_SECONDS", "90")

    base_config = load_system_config(q.CONFIGS["bootv6"])
    return clone_system_config(
        base_config,
        paths={
            "run_root": run_dir / "batch_state/runs",
            "skill_library_root": run_dir / "batch_state/skill_library",
            "experience_buffer_path": run_dir / "batch_state/experience_buffer.jsonl",
        },
        runtime={
            "bootstrap_initial_skill": True,
            "bootstrap_task_preprocess": True,
            "bootstrap_preprocess_note_char_cap": 700,
            "task_concurrency": 1,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reasoning-effort", required=True, choices=["high", "xhigh"])
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()

    q.configure_modules()
    q.verify_inputs()

    effort = args.reasoning_effort.strip()
    prefix = args.prefix.strip() or datetime.now().strftime(
        f"%Y%m%d_%H%M_8b_bootv6_bootonly_sssai52_{effort}_remote132"
    )
    run_name = f"{prefix}_8b_bootv6_bootonly_sssai52_{effort}"
    compare_json = q.QUEUE_LOG_ROOT / f"{prefix}_compare.json"
    timings: dict[str, Any] = {}
    install_timing_hook(timings)

    temp_config = load_system_config(q.CONFIGS["bootv6"])
    trainer = GaiaSkillTrainer(temp_config)
    run_dir = trainer.prepare_run_dir(run_name, mode="gaia-bootstrap-only-sssaicode")
    config = build_config(effort, run_dir)
    trainer = GaiaSkillTrainer(config)
    bootstrap_dir = ensure_dir(run_dir / "bootstrap")

    task_ids = read_reference_task_ids()
    tasks = select_tasks(load_converted_dataset(q.DEV_DATASET), task_ids=task_ids or None)
    if not tasks:
        raise RuntimeError("No tasks selected for bootv6 bootstrap.")
    trainer._write_selected_tasks_summary(
        run_dir,
        source={
            "source_mode": "local_dataset_reference_task_ids",
            "dataset_path": str(q.DEV_DATASET),
            "reference_run": str(REFERENCE_BOOT_RUN),
        },
        selected_tasks=tasks,
    )

    started = time.monotonic()
    decision = SkillBootstrapper(config).bootstrap(tasks, bootstrap_dir)
    reset_skill_library(config.skill_library_root)
    write_skill_bundle(
        config.skill_library_root,
        str(decision.get("target_skill_name") or "gaia-general-skill"),
        {
            str(path): str(content)
            for path, content in dict(decision.get("files_to_write", {})).items()
        },
    )
    active_skill = load_skill_detail(discover_skills(config.skill_library_root)[0])
    skill_dir = trainer._snapshot_active_skill(active_skill, bootstrap_dir / "skill_after_bootstrap")
    skill_path = skill_dir / "SKILL.md"
    total_seconds = round(time.monotonic() - started, 3)

    payload = {
        "prefix": prefix,
        "run_name": run_dir.name,
        "provider": "sssaicode",
        "model": SSSAI_MODEL,
        "base_url": SSSAI_BASE_URL,
        "api_mode": "responses_sse",
        "reasoning_effort": effort,
        "task_count": len(tasks),
        "reference_boot_run": str(REFERENCE_BOOT_RUN),
        "total_seconds": total_seconds,
        "stage_timings": timings,
        "reference_codex_bootv6_skill": skill_stats(REFERENCE_BOOT_SKILL),
        "sssaicode_gpt52_bootv6_skill": skill_stats(skill_path),
        "bootstrap_summary": str(decision.get("summary", "")),
    }
    write_json(run_dir / "bootstrap_only_summary.json", payload)
    compare_json.parent.mkdir(parents=True, exist_ok=True)
    compare_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
