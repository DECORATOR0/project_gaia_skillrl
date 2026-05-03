from __future__ import annotations

import csv
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path("/data/xsy/project_gaia_skillrl")
sys.path.insert(0, str(ROOT))

from gaia_skillrl.search_config import apply_search_runtime_env, build_search_runtime_env, load_search_runtime_config  # noqa: E402

PYTHON = ROOT / ".venv/bin/python"
LAUNCH_LOG_ROOT = ROOT / "runs" / "_launch_logs"
CODEX_TASK_ROOT = ROOT / "runs" / "_codex_tasks"
PROCESS_CSV = Path("/data/xsy/活的进程.csv")
STATUS_FILE = CODEX_TASK_ROOT / "20260429_2344_serper_queue_status.md"
API_DOC = Path("/data/xsy/skill-pool/API说明/26.4.29_2314_web_search_API管理.md")
METRICS_DOC = ROOT / "实验设计与迭代/26.4.29_2035_GAIA六路实验网络指标总表.md"

BRAVE_PREFIX = "20260429_2317_archv5_1_anyphase_brave_existing_bootskill"
BRAVE_QUEUE_PID = 488370

SEARCH_RUNTIME_CONFIG = load_search_runtime_config()
SEARCH_RUNTIME_ENV = build_search_runtime_env(SEARCH_RUNTIME_CONFIG)
SERPER_API_KEY = SEARCH_RUNTIME_ENV.get("NLRL_SERPER_API_KEY", "")
SERPER_ENDPOINT = SEARCH_RUNTIME_ENV.get("NLRL_SERPER_SEARCH_ENDPOINT", "https://google.serper.dev/search")
SERPER_USAGE_KNOWN = 0

SERPER_V51_CONFIG = ROOT / "configs/system_archv5_1_anyphase_rerun_20260429_2054.json"
SERPER_V51_SOURCE_SKILL = ROOT / "runs/2026/4/2026-4-29/20260429_095317_archv5_1_anyphase_fresh_bootv3_boot_dev_c20/bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md"
SERPER_V51_SCRIPT = ROOT / "scripts/run_gaia_archv5_existing_boot_skill_boot_ab_queue.py"

SERPER_V52_CONFIG = ROOT / "configs/system.json"
SERPER_V52_SCRIPT = ROOT / "scripts/run_gaia_archv5_toolv4_direct_boot_ab_queue.py"
STATIC_STATUS_NOTES = [
    "已完成 `/data/xsy/project_gaia_skillrl/gaia_skillrl/tools.py` 的 Serper provider 接入和 `selected_env` 留痕扩展。",
    "已完成 `py_compile`：`gaia_skillrl/tools.py`、`scripts/run_gaia_serper_takeover_queue.py`、`scripts/run_gaia_archv5_toolv4_direct_boot_ab_queue.py`。",
    "Serper 新 key 登记于 `2026-05-03 00:21 +0800`；为保留总额度，登记时未在线验证。",
    "历史 Serper key 曾验证：`2026-04-29 23:54 +0800` 发起 `q=OpenAI`、`num=1`，返回 `HTTP 200`，`credits=1`。",
]

QUEUE_COMMON_ENV = {
    "PYTHONUNBUFFERED": "1",
    "PYTHONFAULTHANDLER": "1",
    "GAIA_ARCHV5_QUEUE_CONCURRENCY": "20",
    "GAIA_ARCHV5_QUEUE_TAIL_THRESHOLD": "3",
    "GAIA_ARCHV5_QUEUE_ALLOW_PARTIAL_TAIL": "1",
    "GAIA_ARCHV5_QUEUE_TAIL_GRACE_POLLS": "3",
    "GAIA_ARCHV5_TOOL_PROFILE": "atomic_v2",
    "NLRL_RUNTIME_TASK_CONCURRENCY": "20",
    "NLRL_LLM_MAX_CONCURRENT_REQUESTS": "20",
    "NLRL_RUNTIME_ITERATIONS_PER_BATCH": "0",
    "NLRL_RUNTIME_TOOL_PROFILE": "atomic_v2",
    "NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY": "any_phase",
}
apply_search_runtime_env(QUEUE_COMMON_ENV)


@dataclass
class QueueLaunch:
    label: str
    prefix: str
    queue_name: str
    pid: int
    log_path: Path
    command: str


def now() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().strftime("%Y-%m-%dT%H:%M:%S%z")


def now_human() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S %z")


def now_minute() -> str:
    return now().strftime("%Y-%m-%d %H:%M +0800")


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        stat = stat_path.read_text(encoding="utf-8", errors="replace").split()
        return len(stat) > 2 and stat[2] != "Z"
    except FileNotFoundError:
        return False
    except Exception:
        return True


def read_process_rows() -> list[list[str]]:
    if not PROCESS_CSV.exists():
        return []
    with PROCESS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def write_process_rows(rows: list[list[str]]) -> None:
    with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def refresh_process_registry() -> None:
    rows = read_process_rows()
    if not rows:
        return
    changed = False
    for row in rows[1:]:
        if len(row) < 8:
            continue
        status = row[2].strip().lower()
        pid_text = row[5].strip()
        if status != "running" or not pid_text.isdigit():
            continue
        row[1] = now_iso()
        if not pid_alive(int(pid_text)):
            row[2] = "dead"
            if not row[7].strip():
                row[7] = now_iso()
        changed = True
    if changed:
        write_process_rows(rows)


def ensure_process_csv_header() -> None:
    if PROCESS_CSV.exists():
        return
    PROCESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with PROCESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(
            [
                "recorded_at",
                "last_checked_at",
                "status",
                "kind",
                "name",
                "pid",
                "start_time",
                "ended_at",
                "cwd",
                "run_dir",
                "command",
                "recorded_at",
                "notes",
                "log_path",
            ]
        )


def append_process_row(
    *,
    name: str,
    pid: int,
    cwd: Path,
    run_dir: str,
    command: str,
    log_path: Path,
    notes: str,
    kind: str,
) -> None:
    ensure_process_csv_header()
    row = [
        now_iso(),
        now_iso(),
        "running",
        kind,
        name,
        str(pid),
        now_iso(),
        "",
        str(cwd),
        run_dir,
        command,
        "",
        notes,
        str(log_path),
    ]
    with PROCESS_CSV.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(row)


def update_process_row(pid: int, status: str) -> None:
    rows = read_process_rows()
    if not rows:
        return
    changed = False
    for row in rows[1:]:
        if len(row) < 8 or row[5].strip() != str(pid):
            continue
        row[1] = now_iso()
        row[2] = status
        if status != "running" and not row[7].strip():
            row[7] = now_iso()
        changed = True
    if changed:
        write_process_rows(rows)


def process_status(pid: int) -> str:
    rows = read_process_rows()
    for row in rows[1:]:
        if len(row) >= 3 and row[5].strip() == str(pid):
            return row[2].strip()
    return ""


def live_processes_with_token(token: str) -> dict[int, str]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        capture_output=True,
        text=True,
        check=True,
    )
    matches: dict[int, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or token not in stripped:
            continue
        pid_text, _, cmd = stripped.partition(" ")
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == os.getpid():
            continue
        matches[pid] = cmd.strip()
    return matches


def format_launch_block(launches: list[QueueLaunch]) -> str:
    if not launches:
        return "- 暂无"
    return "\n".join(
        f"- {item.label}: prefix=`{item.prefix}`；queue PID `{item.pid}`；log `{item.log_path}`"
        for item in launches
    )


def write_status(
    *,
    phase: str,
    launches: list[QueueLaunch],
    next_condition: str,
    notes: list[str] | None = None,
    error: str = "",
) -> None:
    CODEX_TASK_ROOT.mkdir(parents=True, exist_ok=True)
    content = [
        "# 20260429_2344 Serper Queue Status",
        "",
        f"- 更新时间：`{now_human()}`",
        f"- 当前阶段：`{phase}`",
        f"- Serper 已知消耗：`{SERPER_USAGE_KNOWN}` 次",
        f"- 下一步等待条件：{next_condition}",
        "",
        "## 已启动队列",
        format_launch_block(launches),
    ]
    merged_notes = [*STATIC_STATUS_NOTES, *(notes or [])]
    if merged_notes:
        content.extend(["", "## 备注", *[f"- {note}" for note in merged_notes]])
    if error:
        content.extend(["", "## 错误", f"- {error}"])
    STATUS_FILE.write_text("\n".join(content) + "\n", encoding="utf-8")


def replace_line(text: str, pattern: str, replacement: str) -> str:
    updated, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if count == 0:
        raise RuntimeError(f"pattern not found: {pattern}")
    return updated


def update_metrics_timestamp(text: str) -> str:
    return replace_line(text, r"^更新时间：.*$", f"更新时间：{now_minute()}")


def update_metrics_row(label: str, start_text: str, state_text: str, note: str) -> None:
    text = METRICS_DOC.read_text(encoding="utf-8")
    text = update_metrics_timestamp(text)
    row = (
        f"| {start_text} | {label} | {state_text} | 待收口 | 待收口 | 待收口 | 待收口 | 待收口 | "
        f"待收口 | 待收口 | 待收口 | 待收口 | 待收口 | {note} |"
    )
    pattern = rf"^\| .* \| {re.escape(label)} \| .*$"
    text = replace_line(text, pattern, row)
    METRICS_DOC.write_text(text, encoding="utf-8")


def launch_command(script_path: Path, env_updates: dict[str, str], log_path: Path) -> str:
    env_parts = " ".join(f"{key}={shlex.quote(value)}" for key, value in env_updates.items())
    return (
        f"cd {shlex.quote(str(ROOT))} && "
        f"nohup env {env_parts} {shlex.quote(str(PYTHON))} {shlex.quote(str(script_path))} "
        f"> {shlex.quote(str(log_path))} 2>&1 & echo $!"
    )


def launch_queue(
    *,
    label: str,
    prefix_suffix: str,
    queue_name: str,
    script_path: Path,
    env_updates: dict[str, str],
) -> QueueLaunch:
    LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    prefix = f"{now().strftime('%Y%m%d_%H%M')}_{prefix_suffix}"
    queue_log = LAUNCH_LOG_ROOT / f"{queue_name.format(prefix=prefix)}_{now().strftime('%Y%m%d_%H%M%S')}_popen.log"
    command_env = dict(env_updates)
    command_env["GAIA_ARCHV5_QUEUE_PREFIX"] = prefix
    command_env["NLRL_QUEUE_LOG_PATH"] = str(queue_log)
    command = launch_command(script_path, command_env, queue_log)
    result = subprocess.run(
        ["bash", "-lc", command],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    pid_text = result.stdout.strip().splitlines()[-1].strip()
    if not pid_text.isdigit():
        raise RuntimeError(f"failed to parse pid from launch output: {result.stdout!r}")
    pid = int(pid_text)
    log(f"launched {label} queue pid={pid} prefix={prefix} log={queue_log}")
    return QueueLaunch(
        label=label,
        prefix=prefix,
        queue_name=queue_name.format(prefix=prefix),
        pid=pid,
        log_path=queue_log,
        command=command,
    )


def wait_for_queue_exit(queue: QueueLaunch, launches: list[QueueLaunch]) -> str:
    write_status(
        phase=f"waiting_{queue.label.lower()}",
        launches=launches,
        next_condition=f"等待 `{queue.pid}` 退出并确认队列状态可继续",
    )
    while pid_alive(queue.pid):
        refresh_process_registry()
        write_status(
            phase=f"waiting_{queue.label.lower()}",
            launches=launches,
            next_condition=f"等待 `{queue.pid}` 退出并确认队列状态可继续",
        )
        time.sleep(60)
    refresh_process_registry()
    status = process_status(queue.pid) or "unknown"
    log(f"{queue.label} queue pid={queue.pid} exited with recorded status={status}")
    return status


def wait_for_brave_completion(launches: list[QueueLaunch]) -> None:
    write_status(
        phase="waiting_brave_queue",
        launches=launches,
        next_condition=f"等待 Brave queue PID `{BRAVE_QUEUE_PID}` 退出",
        notes=[
            f"Brave prefix=`{BRAVE_PREFIX}`",
            f"状态文件路径=`{STATUS_FILE}`",
        ],
    )
    while pid_alive(BRAVE_QUEUE_PID):
        refresh_process_registry()
        write_status(
            phase="waiting_brave_queue",
            launches=launches,
            next_condition=f"等待 Brave queue PID `{BRAVE_QUEUE_PID}` 退出",
            notes=[f"Brave prefix=`{BRAVE_PREFIX}` 仍有 queue PID 在跑"],
        )
        time.sleep(60)

    log(f"Brave queue pid={BRAVE_QUEUE_PID} has exited")
    while True:
        refresh_process_registry()
        active = live_processes_with_token(BRAVE_PREFIX)
        if not active:
            write_status(
                phase="brave_prefix_clear",
                launches=launches,
                next_condition="Brave 同 prefix 活跃 PID 已清空，可以启动 Serper V5.1",
            )
            return
        notes = [f"Brave 同 prefix 活跃 PID：{pid} -> {cmd}" for pid, cmd in active.items()]
        write_status(
            phase="waiting_brave_children",
            launches=launches,
            next_condition=f"等待 `{BRAVE_PREFIX}` 同 prefix 活跃 PID 全部结束",
            notes=notes,
        )
        time.sleep(60)


def main() -> int:
    refresh_process_registry()
    watcher_log = Path(os.environ.get("NLRL_QUEUE_LOG_PATH", str(LAUNCH_LOG_ROOT / "20260429_2344_serper_takeover_queue.log")))
    append_process_row(
        name="20260429_2344_serper_takeover_queue",
        pid=os.getpid(),
        cwd=ROOT,
        run_dir="(serper takeover watcher)",
        command=f"env NLRL_QUEUE_LOG_PATH={watcher_log} {PYTHON} {__file__}",
        log_path=watcher_log,
        notes=(
            "Wait for Brave V5.1 existing-bootskill chain to finish, then launch "
            "Serper V5.1 existing-bootskill six-lane queue, then Serper V5.2 boot+AB queue."
        ),
        kind="codex_task",
    )
    launches: list[QueueLaunch] = []
    status = "finished"
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        wait_for_brave_completion(launches)

        serper_v51 = launch_queue(
            label="Serper V5.1",
            prefix_suffix="archv5_1_anyphase_serper_existing_bootskill",
            queue_name="{prefix}_toolv2_existing_bootskill_boot_ab_queue_c20",
            script_path=SERPER_V51_SCRIPT,
            env_updates={
                **QUEUE_COMMON_ENV,
                "GAIA_ARCHV5_CONFIG": str(SERPER_V51_CONFIG),
                "GAIA_ARCHV5_EXISTING_BOOT_SKILL_PATH": str(SERPER_V51_SOURCE_SKILL),
                "GAIA_ARCHV5_BOOT_DEV_LANE": "gpu0",
                "GAIA_ARCHV5_BOOT_TEST_LANE": "gpu1",
            },
        )
        launches.append(serper_v51)
        update_metrics_row(
            "ARCH-V5.1 old boot-skill rerun c20 + Serper search",
            now().strftime("%Y-%m-%d %H:%M"),
            "running",
            (
                f"queue PID `{serper_v51.pid}`；log `{serper_v51.log_path}`；run prefix "
                f"`{serper_v51.prefix}`；source skill `{SERPER_V51_SOURCE_SKILL}`；fallback=ddg"
            ),
        )
        v51_status = wait_for_queue_exit(serper_v51, launches)
        if v51_status != "finished":
            raise RuntimeError(
                f"Serper V5.1 queue ended with status={v51_status}; "
                f"log={serper_v51.log_path}"
            )

        serper_v52 = launch_queue(
            label="Serper V5.2",
            prefix_suffix="archv5_2_noconclude_anyphase_serper",
            queue_name="{prefix}_v5-2_toolv2_boot_ab_queue_c20",
            script_path=SERPER_V52_SCRIPT,
            env_updates={
                **QUEUE_COMMON_ENV,
                "GAIA_ARCHV5_CONFIG": str(SERPER_V52_CONFIG),
                "GAIA_ARCHV5_QUEUE_INCLUDE_DIRECT": "0",
                "GAIA_ARCHV5_QUEUE_PROFILE_LABEL": "v5-2_toolv2",
            },
        )
        launches.append(serper_v52)
        update_metrics_row(
            "ARCH-V5.2 no-CONCLUDE c20 + Serper search",
            now().strftime("%Y-%m-%d %H:%M"),
            "running",
            (
                f"queue PID `{serper_v52.pid}`；log `{serper_v52.log_path}`；run prefix "
                f"`{serper_v52.prefix}`；config `{SERPER_V52_CONFIG}`；schedule=boot_ab；fallback=ddg"
            ),
        )
        v52_status = wait_for_queue_exit(serper_v52, launches)
        if v52_status != "finished":
            raise RuntimeError(
                f"Serper V5.2 queue ended with status={v52_status}; "
                f"log={serper_v52.log_path}"
            )

        write_status(
            phase="completed",
            launches=launches,
            next_condition="两段 Serper 队列都已完成，等待后续指标收口",
        )
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        write_status(
            phase="stopped",
            launches=launches,
            next_condition="watcher 已停止，需要人工决定是否重启排队",
            error="收到 SIGTERM / KeyboardInterrupt",
        )
        raise
    except Exception as exc:
        status = "dead"
        write_status(
            phase="error",
            launches=launches,
            next_condition="查看对应 queue log 和本状态文件，再决定是否重启 watcher 或单独补跑",
            error=str(exc),
        )
        raise
    finally:
        update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
