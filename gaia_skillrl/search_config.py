from __future__ import annotations

import json
import os
from pathlib import Path
from typing import MutableMapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEARCH_RUNTIME_CONFIG = ROOT / "configs/search_runtime.json"


def search_runtime_config_path() -> Path:
    raw_path = os.environ.get("GAIA_SEARCH_RUNTIME_CONFIG", "").strip()
    return Path(raw_path) if raw_path else DEFAULT_SEARCH_RUNTIME_CONFIG


def load_search_runtime_config(path: str | Path | None = None) -> dict[str, object]:
    config_path = Path(path) if path is not None else search_runtime_config_path()
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def build_search_runtime_env(config: dict[str, object] | None = None) -> dict[str, str]:
    data = config if config is not None else load_search_runtime_config()
    env: dict[str, str] = {}

    provider = str(data.get("provider", "")).strip()
    if provider:
        env["NLRL_WEB_SEARCH_PROVIDER"] = provider

    fallback = str(data.get("fallback_provider", "")).strip()
    if fallback:
        env["NLRL_WEB_SEARCH_FALLBACK_PROVIDER"] = fallback

    serper_keys = _string_list(data.get("serper_api_keys"))
    serper_key = str(data.get("serper_api_key", "")).strip()
    if serper_key:
        serper_keys.insert(0, serper_key)
    serper_keys.extend(_string_list(data.get("serper_backup_api_keys")))
    serper_keys = _dedupe(serper_keys)
    if serper_keys:
        env["NLRL_SERPER_API_KEY"] = serper_keys[0]
        env["NLRL_SERPER_API_KEYS"] = ",".join(serper_keys)

    serper_endpoint = str(data.get("serper_search_endpoint", "")).strip()
    if serper_endpoint:
        env["NLRL_SERPER_SEARCH_ENDPOINT"] = serper_endpoint

    timeout = data.get("serper_search_timeout_seconds")
    if timeout is not None and str(timeout).strip():
        env["NLRL_SERPER_SEARCH_TIMEOUT_SECONDS"] = str(timeout)

    http_proxy = str(data.get("http_proxy", "")).strip()
    if http_proxy:
        env["HTTP_PROXY"] = http_proxy
        env["http_proxy"] = http_proxy

    https_proxy = str(data.get("https_proxy", "")).strip()
    if https_proxy:
        env["HTTPS_PROXY"] = https_proxy
        env["https_proxy"] = https_proxy

    no_proxy = str(data.get("no_proxy", "")).strip()
    if no_proxy:
        env["NO_PROXY"] = no_proxy
        env["no_proxy"] = no_proxy

    return env


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]
    return []


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def apply_search_runtime_env(
    env: MutableMapping[str, str] | None = None,
    *,
    path: str | Path | None = None,
) -> MutableMapping[str, str]:
    target = os.environ if env is None else env
    config = load_search_runtime_config(path)
    if not config:
        return target
    if bool(config.get("clear_all_proxy", True)):
        target.pop("ALL_PROXY", None)
        target.pop("all_proxy", None)
    target.update(build_search_runtime_env(config))
    return target
