from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl")
SCRIPTS = ROOT / "scripts"
SOURCE_PREFIX = os.environ.get(
    "GAIA_8B_BOOTV6_CONT_SOURCE_PREFIX",
    "20260513_1519_8b_bootv5v6_fullmatrix_remote132_retry_sem",
).strip()
OLD_MASTER_PID = int(os.environ.get("GAIA_8B_BOOTV6_CONT_OLD_MASTER_PID", "1774730"))
PREFIX = os.environ.get(
    "GAIA_8B_BOOTV6_CONT_PREFIX",
    datetime.now().strftime("%Y%m%d_%H%M_8b_bootv6_downstream_from_1519"),
).strip()

os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_PREFIX"] = PREFIX
os.environ["GAIA_8B_BOOTV5V6_FULL_MATRIX_DOWNSTREAM_BOOT_KEYS"] = "bootv6"
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_BOOT_WORKERS", "3")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_OFFLINE_WORKERS", "4")
os.environ.setdefault("GAIA_8B_BOOTV5V6_FULL_MATRIX_PARTIAL_SOURCE_STALL_SECONDS", "0")

sys.path.insert(0, str(SCRIPTS))
import run_gaia_8b_bootv5v6_full_matrix_remote132_20260513 as q  # noqa: E402


EXPECTED_DOWNSTREAM_EVALS = 3 * len(q.METHOD_ORDER) * 2
q.EXPECTED_COUNTED_EVALS = EXPECTED_DOWNSTREAM_EVALS


def log(message: str) -> None:
    q.log(f"[v6-cont] {message}")


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def old_eval_processes(*, boot_key: str | None = None) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,stat=,cmd="],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    records: list[dict[str, Any]] = []
    current = os.getpid()
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if SOURCE_PREFIX not in line:
            continue
        if "gaia_skillrl.cli" not in line and "codex exec" not in line:
            continue
        if boot_key and boot_key not in line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid = int(parts[0])
        if pid == current:
            continue
        records.append({"pid": pid, "ppid": int(parts[1]), "stat": parts[2], "cmd": parts[3]})
    return records


def old_occupied_ports(*, boot_key: str | None = None) -> set[int]:
    ports: set[int] = set()
    for record in old_eval_processes(boot_key=boot_key):
        env_path = Path(f"/proc/{record['pid']}/environ")
        try:
            env_items = env_path.read_bytes().split(b"\0")
        except OSError:
            continue
        for item in env_items:
            if not item.startswith(b"NLRL_EXECUTOR_BASE_URL="):
                continue
            value = item.split(b"=", 1)[1].decode("utf-8", errors="ignore")
            marker = "127.0.0.1:"
            if marker not in value:
                continue
            try:
                ports.add(int(value.split(marker, 1)[1].split("/", 1)[0]))
            except ValueError:
                pass
    return ports


def stop_old_master_only() -> None:
    if not process_alive(OLD_MASTER_PID):
        log(f"old master pid={OLD_MASTER_PID} already gone")
        return
    log(f"stopping old master pid={OLD_MASTER_PID}; existing boot-only children are left running")
    os.kill(OLD_MASTER_PID, signal.SIGTERM)
    deadline = time.time() + 90
    while process_alive(OLD_MASTER_PID) and time.time() < deadline:
        time.sleep(3)
    if process_alive(OLD_MASTER_PID):
        log(f"old master pid={OLD_MASTER_PID} still alive after SIGTERM; continuation will keep waiting")
    else:
        log(f"old master pid={OLD_MASTER_PID} stopped")


def wait_old_v6_children_clear() -> None:
    while True:
        records = old_eval_processes(boot_key="bootv6")
        if not records:
            return
        sample = "; ".join(f"{item['pid']} {item['stat']}" for item in records[:8])
        log(f"waiting old V6 boot-only child processes clear: n={len(records)}; {sample}")
        time.sleep(60)


def exclude_lanes_still_used_by_v5() -> None:
    ports = old_occupied_ports(boot_key="bootv5")
    if not ports:
        return
    kept = [lane for lane in q.LANES if lane.port not in ports]
    dropped = [lane.name for lane in q.LANES if lane.port in ports]
    if not kept:
        raise RuntimeError(f"all lanes still occupied by old V5 processes: ports={sorted(ports)}")
    q.LANES[:] = kept
    q.write_manifest("running", {"stage": "v5_occupied_lanes_excluded", "dropped_lanes": dropped})
    log(f"exclude lanes still used by V5 old children: {dropped}")


def source_skill(run_name: str) -> Path | None:
    return q.bootstrap_skill_path(run_name)


def source_run_ready(run_name: str) -> Path | None:
    run_dir = q.latest_run_dir(run_name)
    if run_dir is None:
        return None
    done, _total, _mtime = q.state_count(run_name)
    if done >= q.PARTIAL_SOURCE_MIN_STATES:
        return run_dir
    return None


def wait_old_source(repeat: int, timeout_seconds: int = 0) -> tuple[Path, Path] | None:
    run_name = f"{SOURCE_PREFIX}_8b_bootv6_r{repeat}_boot_dev_c{q.CONCURRENCY}"
    deadline = time.time() + timeout_seconds if timeout_seconds else None
    while True:
        skill = source_skill(run_name)
        run_dir = source_run_ready(run_name)
        done, total, _mtime = q.state_count(run_name)
        if skill is not None and run_dir is not None:
            log(f"reuse V6 r{repeat} old boot source states={done}/{total or '?'} skill={skill}")
            return run_dir, skill
        if deadline and time.time() >= deadline:
            return None
        log(f"waiting old V6 r{repeat} boot source: states={done}/{total or '?'} skill={bool(skill)}")
        time.sleep(60)


def launch_fallback_boot_source(repeat: int) -> tuple[Path, Path]:
    boot_key = "bootv6"
    run_name = f"{PREFIX}_8b_bootv6_r{repeat}_boot_dev_c{q.CONCURRENCY}"
    log(f"fallback launch V6 r{repeat} boot dev under continuation prefix")
    with q.bootstrap_semaphore:
        q.enqueue_eval(
            q.EvalSpec(
                key=f"bootv6_r{repeat}_boot_dev",
                label=f"8B BOOTV6 r{repeat} boot-only dev fallback",
                command="train-local",
                config_path=q.CONFIGS[boot_key],
                dataset=q.DEV_DATASET,
                run_name=run_name,
                counted=True,
                boot_key=boot_key,
                method_key="boot-only",
                repeat=repeat,
                split="dev",
                bootstrap=True,
                bootstrap_preprocess=True,
                notes=f"fallback because source prefix {SOURCE_PREFIX} did not provide reusable V6 r{repeat}",
            )
        )
        skill = q.wait_for_bootstrap_skill(run_name)
    run_dir = q.wait_for_boot_source(run_name)
    return run_dir, skill


def ensure_v6_source(repeat: int) -> q.remain20.BootSource:
    old = wait_old_source(repeat, timeout_seconds=30)
    if old is None:
        run_dir, skill = launch_fallback_boot_source(repeat)
        source_note = f"{PREFIX}: fallback V6 r{repeat} source"
    else:
        run_dir, skill = old
        source_note = f"{PREFIX}: continuation from {SOURCE_PREFIX} V6 r{repeat}"
    return q.remain20.BootSource(
        model_key="8b",
        boot_key=f"bootv6_r{repeat}",
        label=f"8B BOOT-V6 repeat {repeat}",
        boot_dev_run=run_dir,
        boot_skill=skill,
        supplemental_runs=[],
        source_notes=source_note,
    )


def boot_test_sufficient(run_name: str) -> bool:
    run_dir = q.latest_run_dir(run_name)
    if run_dir is None:
        return False
    done, total, _mtime = q.state_count(run_name)
    if total and done >= min(total, q.PARTIAL_SOURCE_MIN_STATES):
        return True
    return done >= q.PARTIAL_SOURCE_MIN_STATES


def ensure_boot_test(repeat: int, skill: Path) -> None:
    old_run_name = f"{SOURCE_PREFIX}_8b_bootv6_r{repeat}_boot_test_c{q.CONCURRENCY}"
    if boot_test_sufficient(old_run_name):
        log(f"reuse existing V6 r{repeat} boot test: {old_run_name}")
        return
    run_name = f"{PREFIX}_8b_bootv6_r{repeat}_boot_test_c{q.CONCURRENCY}"
    log(f"launch missing V6 r{repeat} boot test under continuation prefix")
    q.enqueue_eval(
        q.EvalSpec(
            key=f"bootv6_r{repeat}_boot_test",
            label=f"8B BOOTV6 r{repeat} boot-only test continuation",
            command="train-local",
            config_path=q.CONFIGS["bootv6"],
            dataset=q.TEST_DATASET,
            run_name=run_name,
            counted=True,
            boot_key="bootv6",
            method_key="boot-only",
            repeat=repeat,
            split="test",
            skill_path=skill,
            notes=f"fills missing boot-only test after stopping old master {OLD_MASTER_PID}",
        )
    )


def wait_method_if_registered(method_key: str) -> None:
    while True:
        with q.status_lock:
            method_names = {spec.run_name for spec in q.all_eval_specs if spec.method_key == method_key}
        if method_names:
            q.wait_method_evals_tail_or_done(method_key)
            return
        return


def run_downstream(sources: list[q.remain20.BootSource]) -> None:
    with ThreadPoolExecutor(max_workers=max(1, q.OFFLINE_WORKERS), thread_name_prefix="8b-v6-cont-offline") as pool:
        for method_key in q.METHOD_ORDER:
            strategy, graph_policy = q.METHOD_SPECS[method_key]
            futures = {
                pool.submit(q.offline_then_enqueue, source, method_key, strategy, graph_policy): source
                for source in sources
            }
            pending = set(futures)
            while pending:
                done_now = {future for future in pending if future.done()}
                for future in done_now:
                    pending.remove(future)
                    source = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        record = q.pipeline_records.setdefault(source.boot_key, {})
                        record.setdefault("offline_errors", {})[method_key] = repr(exc)
                        log(f"offline failed: source={source.boot_key} method={method_key} error={exc!r}")
                log(f"waiting V6 offline {method_key}: done={len(futures) - len(pending)}/{len(futures)}")
                q.write_manifest("running", {"stage": f"waiting_v6_offline_{method_key}"})
                if pending:
                    time.sleep(q.POLL_SECONDS)
            q.wait_method_evals_tail_or_done(method_key)


def main() -> int:
    start_time = q.now()
    status = "finished"
    q.configure_modules()
    q.verify_inputs()
    q.QUEUE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    q.LAUNCH_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    q.DESIGN_ROOT.mkdir(parents=True, exist_ok=True)
    q.base.ensure_process_csv_header()
    q.base.refresh_process_registry()
    q.base.append_process_row(
        kind="experiment_master",
        name=q.MASTER_NAME,
        pid=os.getpid(),
        cwd=q.ROOT,
        run_dir=str(q.QUEUE_LOG_ROOT),
        command=os.environ.get(
            "GAIA_8B_BOOTV6_CONT_MASTER_COMMAND",
            q.shell_join([str(q.PYTHON), "-u", str(Path(__file__).resolve())]),
        ),
        log_path=q.MASTER_LOG,
        notes=(
            f"8B BOOT-V6 downstream continuation from {SOURCE_PREFIX}; "
            f"old_master_pid={OLD_MASTER_PID}; expected_downstream_evals={EXPECTED_DOWNSTREAM_EVALS}"
        ),
    )
    scheduler = threading.Thread(target=q.scheduler_loop, name="gaia-8b-bootv6-cont-scheduler", daemon=True)
    q.write_manifest("starting", {"source_prefix": SOURCE_PREFIX, "old_master_pid": OLD_MASTER_PID})
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt))
        q.base.wait_endpoints()
        q.write_manifest("vllm_ready", {"source_prefix": SOURCE_PREFIX, "old_master_pid": OLD_MASTER_PID})
        scheduler.start()
        stop_old_master_only()
        wait_old_v6_children_clear()
        exclude_lanes_still_used_by_v5()
        sources = [ensure_v6_source(repeat) for repeat in range(1, 4)]
        for source in sources:
            repeat = int(source.boot_key.rsplit("_r", 1)[1])
            ensure_boot_test(repeat, source.boot_skill)
        wait_method_if_registered("boot-only")
        run_downstream(sources)
        q.wait_counted_evals_tail_or_done()
        if q.offline_failures and q.eval_failures:
            status = "finished_with_offline_and_eval_failures"
        elif q.offline_failures:
            status = "finished_with_offline_failures"
        elif q.eval_failures:
            status = "finished_with_eval_failures"
        elif q.released_evals:
            status = "finished_with_longtail_live"
        summary = q.write_summary(status, start_time=start_time, end_time=q.now())
        q.append_report(summary)
        q.write_manifest(status)
        return 0
    except KeyboardInterrupt:
        status = "stopped"
        summary = q.write_summary(status, start_time=start_time, end_time=q.now())
        q.append_report(summary)
        q.write_manifest(status)
        raise
    except Exception:
        status = "dead"
        summary = q.write_summary(status, start_time=start_time, end_time=q.now())
        q.append_report(summary)
        q.write_manifest(status)
        raise
    finally:
        q.scheduler_stop.set()
        scheduler.join(timeout=60)
        q.base.update_process_row(os.getpid(), status)


if __name__ == "__main__":
    raise SystemExit(main())
