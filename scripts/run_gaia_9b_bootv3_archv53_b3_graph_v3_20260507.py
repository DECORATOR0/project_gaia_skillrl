from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import run_gaia_9b_bootv3_archv53_b1_b2_20260505 as base


def main() -> int:
    prefix = os.environ.get("GAIA_B3_PREFIX", "").strip() or datetime.now().strftime(
        "%Y%m%d_%H%M%S_9b_bootv3_archv53_b3_graph_v3"
    )
    base.set_actor_critic_defaults()
    base.refresh_process_registry()
    for path in (base.PYTHON, base.CONFIG, base.BOOT_DEV_RUN, base.BOOT_SKILL):
        if not Path(path).exists():
            raise RuntimeError(f"missing required path: {path}")
    skill_path = base.run_offline_variant(
        strategy="graph_v3",
        run_name=f"{prefix}_B3_graph_v3_offline_actor_iter1",
        graph_policy="graph_v3",
    )
    base.log(f"B3 graph_v3 offline skill: {skill_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
