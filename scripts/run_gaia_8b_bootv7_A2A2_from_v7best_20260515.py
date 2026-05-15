#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PREFIX_DEFAULT = "20260515_1220_8b_bootv7_A2A2_from_v7best_gpu0_5"

os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_PREFIX", PREFIX_DEFAULT)
os.environ.setdefault("GAIA_BOOT_MATRIX_EXECUTOR_LABEL", "8B")
os.environ.setdefault("GAIA_BOOT_MATRIX_RUN_MODEL_TOKEN", "8b")
os.environ.setdefault("GAIA_BOOT_MATRIX_MODEL_PATH", "/data/xsy/codes/checkpoints/Qwen3-8B")
os.environ.setdefault("GAIA_BOOT_MATRIX_SERVED_NAME", "Qwen3-8B-local")
os.environ.setdefault("GAIA_BOOT_MATRIX_TOKENIZER_PATH", "/data/xsy/codes/checkpoints/Qwen3-8B")
os.environ.setdefault("GAIA_BOOT_MATRIX_MAX_MODEL_LEN", "40960")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_GPU_IDS", "0,1,2,3,4,5")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CONCURRENCY", "20")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_POLL_SECONDS", "120")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_MODEL", "gpt-5.2")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_STRONG_REASONING_EFFORT", "high")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_OFFLINE_WORKERS", "1")
os.environ.setdefault("GAIA_8B_BOOTV7V8_V3V6_MATRIX_CRITIC_SHARD_CONCURRENCY", "4")
os.environ.setdefault("GAIA_8B_BASELINE_VLLM_PYTHON", "/data/xsy/miniconda3/envs/env_vllm_qwen35/bin/python")
os.environ.setdefault("GAIA_9B_BOOTV7_A2A2_BOOT_SOURCE_PREFIX", "20260514_0112_8b_bootv7v8_v3v6_matrix_remote132_gpu0_5")
os.environ.setdefault("GAIA_9B_BOOTV7_A2A2_BOOT_SOURCE_REPEAT", "2")
os.environ.setdefault("GAIA_9B_BOOTV7_A2A2_FIXED_BOOT_REPEATS", "")
os.environ.setdefault("GAIA_9B_BOOTV7_A2A2_A2_REPEATS", "1,2,3")
os.environ.setdefault("GAIA_9B_BOOTV7_A2A2_A2A2_REPEATS", "1,2,3")
os.environ.setdefault("GAIA_9B_BOOTV7_A2A2_BASELINE_REPEATS", "")
os.environ.setdefault(
    "GAIA_8B_BOOTV7V8_V3V6_MATRIX_REPORT_DOC",
    str(ROOT / "实验设计与迭代/26.5.15_1220_GAIA_8B_BOOTV7_A2A2_from_v7best执行记录.md"),
)

BASE = SCRIPTS / "run_gaia_9b_bootv7_r1fixed_A2A2_20260515.py"
spec = importlib.util.spec_from_file_location("gaia_9b_a2a2_base_20260515", BASE)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot import base script: {BASE}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def boot_source_run_name(split: str) -> str:
    return f"{module.BOOT_SOURCE_PREFIX}_8b_bootv7_r{module.BOOT_SOURCE_REPEAT}_boot_{split}_c20"


def fixed_boot_run_name(repeat: int, split: str) -> str:
    return f"{module.BOOT_SOURCE_PREFIX}_8b_bootv7_r{repeat}_boot_{split}_c20"


def append_custom_report(summary, *, fixed_best, a2_best) -> None:
    lines = [
        "",
        "## 8B Wrapper Notes",
        "",
        "- executor: Qwen3-8B-local thinking endpoints on GPU0-5.",
        "- source: BOOT-V7 r2 from `20260514_0112_8b_bootv7v8_v3v6_matrix_remote132_gpu0_5`.",
        "- flow: selected BOOT-V7 source -> A2 -> A2 eval x3 -> best A2 -> A2A2 -> A2A2 eval x3.",
        "- note: some inherited internal labels may still contain `9B`; the configured executor token/model is 8B.",
        f"- selected_boot_source: `{fixed_best['label']}` score_total=`{fixed_best['score_total']}`",
        f"- selected_A2_source: `{a2_best['label']}` score_total=`{a2_best['score_total']}`",
        f"- summary_json: `{module.matrix.SUMMARY_JSON}`",
        "",
    ]
    with module.matrix.REPORT_DOC.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


module.boot_source_run_name = boot_source_run_name
module.fixed_boot_run_name = fixed_boot_run_name
module.append_custom_report = append_custom_report


if __name__ == "__main__":
    raise SystemExit(module.main())
