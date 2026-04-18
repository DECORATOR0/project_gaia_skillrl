from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from datasets import load_dataset
from huggingface_hub import hf_hub_download

from .config import SystemConfig
from .schemas import DatasetTask
from .utils import ensure_dir, ensure_preferred_proxy_env, read_json, write_json


def _resolve_hf_token(explicit_token: str | None = None) -> str:
    token = (explicit_token or "").strip() or os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HUGGINGFACE_TOKEN", "").strip()
    if not token:
        raise ValueError("HF token is required. Pass it explicitly or set HF_TOKEN.")
    return token


def download_gaia_attachment(
    *,
    cache_root: Path,
    token: str,
    file_path: str,
) -> Path:
    ensure_preferred_proxy_env()
    ensure_dir(cache_root)
    path = hf_hub_download(
        repo_id="gaia-benchmark/GAIA",
        repo_type="dataset",
        filename=file_path,
        token=token,
        cache_dir=str(cache_root),
    )
    return Path(path).resolve()


def _safe_attachment_name(file_name: str, file_path: str) -> str:
    if file_name:
        return Path(file_name).name
    if file_path:
        return Path(file_path).name
    return ""


def _link_or_copy(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    if dst.exists() or dst.is_symlink():
        return
    try:
        dst.symlink_to(src)
    except Exception:
        shutil.copy2(src, dst)


def _materialize_task_workspace(
    *,
    task_root: Path,
    cache_root: Path,
    split: str,
    record: dict[str, Any],
    token: str,
) -> tuple[str, list[str], dict[str, Any]]:
    task_id = str(record["task_id"])
    work_dir = ensure_dir(task_root / split / task_id)
    attachment_paths: list[str] = []

    file_name = str(record.get("file_name") or "").strip()
    file_path = str(record.get("file_path") or "").strip()
    attachment_name = _safe_attachment_name(file_name, file_path)

    manifest = {
        "task_id": task_id,
        "split": split,
        "level": int(record["Level"]),
        "question": str(record["Question"]),
        "file_name": file_name,
        "file_path": file_path,
    }

    if file_path:
        source_path = download_gaia_attachment(cache_root=cache_root, token=token, file_path=file_path)
        if source_path.exists():
            local_name = attachment_name or source_path.name
            local_target = work_dir / local_name
            _link_or_copy(source_path, local_target)
            attachment_paths.append(local_name)
            manifest["local_attachment_path"] = local_name

    write_json(work_dir / "task.json", manifest)
    return str(work_dir), attachment_paths, manifest


def build_gaia_converted_dataset(
    config: SystemConfig,
    *,
    hf_token: str,
    config_name: str = "2023_all",
    split: str = "validation",
    max_tasks: int | None = None,
    levels: list[int] | None = None,
) -> Path:
    token = _resolve_hf_token(hf_token)
    ensure_preferred_proxy_env()
    dataset = load_dataset(
        "gaia-benchmark/GAIA",
        config_name,
        split=split,
        token=token,
        cache_dir=str(config.dataset_cache_root),
    )
    tasks: list[dict[str, Any]] = []
    level_filter = set(levels or [])

    for index, record in enumerate(dataset):
        level = int(record["Level"])
        if level_filter and level not in level_filter:
            continue
        task_id = str(record["task_id"])
        work_dir, attachment_paths, manifest = _materialize_task_workspace(
            task_root=config.task_workspace_root,
            cache_root=config.dataset_cache_root,
            split=split,
            record=record,
            token=token,
        )
        tasks.append(
            {
                "task_id": task_id,
                "source_type": split,
                "prompt": str(record["Question"]),
                "choices": [],
                "gold_answer": str(record["Final answer"]),
                "data_dir": work_dir,
                "file_list": attachment_paths + ["task.json"],
                "gold_tool_names": [],
                "gold_trajectory": [],
                "metadata": {
                    "split": split,
                    "level": level,
                    "file_name": str(record.get("file_name") or ""),
                    "file_path": str(record.get("file_path") or ""),
                    "repo_id": "gaia-benchmark/GAIA",
                    "materialized_task_manifest": manifest,
                    "dataset_index": index,
                },
            }
        )
        if max_tasks is not None and len(tasks) >= max_tasks:
            break

    payload = {
        "dataset_name": "gaia-skill-rl",
        "dataset_version": "0.1.0",
        "task_count": len(tasks),
        "task_schema_version": "gaia-skillrl-v1",
        "config_name": config_name,
        "split": split,
        "tasks": tasks,
    }
    write_json(config.converted_dataset_path, payload)
    return config.converted_dataset_path


def load_converted_dataset(path: Path) -> list[DatasetTask]:
    raw = read_json(path)
    tasks: list[DatasetTask] = []
    for item in raw.get("tasks", []):
        tasks.append(
            DatasetTask(
                task_id=str(item["task_id"]),
                source_type=str(item.get("source_type", "")),
                prompt=str(item.get("prompt", "")),
                choices=[str(choice) for choice in item.get("choices", [])],
                gold_answer=str(item.get("gold_answer", "")),
                data_dir=str(item.get("data_dir", "")),
                file_list=[str(entry) for entry in item.get("file_list", [])],
                gold_trajectory=list(item.get("gold_trajectory", [])),
                gold_tool_names=[str(name) for name in item.get("gold_tool_names", [])],
                original_record=item,
                metadata=item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {},
            )
        )
    return tasks


def select_tasks(
    tasks: list[DatasetTask],
    *,
    task_ids: list[str] | None = None,
    count: int | None = None,
    level: int | None = None,
) -> list[DatasetTask]:
    selected = list(tasks)
    if level is not None:
        selected = [task for task in selected if int(task.metadata.get("level", -1)) == level]
    if task_ids:
        wanted = {str(item) for item in task_ids}
        selected = [task for task in selected if task.task_id in wanted]
    if count is not None:
        selected = selected[:count]
    return selected
