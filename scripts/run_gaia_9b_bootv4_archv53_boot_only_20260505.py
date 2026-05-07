from __future__ import annotations

import os


os.environ.setdefault("GAIA_9B_BOOT_VARIANT_LABEL", "bootv4")
os.environ.setdefault("GAIA_9B_BOOT_VERSION_LABEL", "BOOT-V4")
os.environ.setdefault("GAIA_9B_BOOT_ARCHV53_PREPROCESS", "1")
os.environ.setdefault("GAIA_9B_BOOT_ARCHV53_PREPROCESS_NOTE_CHAR_CAP", "700")

import run_gaia_9b_bootv3_archv53_boot_only_20260505 as base


if __name__ == "__main__":
    raise SystemExit(base.main())
