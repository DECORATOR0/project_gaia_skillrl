from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RUN_DATE_TOKEN_RE = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
_RUN_NAME_SECOND_PREFIX_RE = re.compile(
    r"^(?P<date>20\d{6})_(?P<hour>[01]\d|2[0-3])(?P<minute>[0-5]\d)(?P<second>[0-5]\d)(?:_(?P<rest>.+))?$"
)
_RUN_NAME_MINUTE_PREFIX_RE = re.compile(
    r"^(?P<date>20\d{6})_(?P<hour>[01]\d|2[0-3])(?P<minute>[0-5]\d)(?:_(?P<rest>.+))?$"
)
_RUN_NAME_DATE_PREFIX_RE = re.compile(r"^(?P<date>20\d{6})(?:_(?P<rest>.+))?$")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def second_timestamp(dt: datetime | None = None) -> str:
    reference_dt = dt or datetime.now()
    return reference_dt.strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_empty_dir(path: Path) -> Path:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"Directory already exists and is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_run_calendar_parts(
    *,
    run_name: str | None = None,
    path: Path | None = None,
    dt: datetime | None = None,
) -> tuple[str, str, str]:
    if run_name:
        match = _RUN_DATE_TOKEN_RE.search(run_name)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            return str(year), str(month), f"{year}-{month}-{day}"

    reference_dt = dt
    if reference_dt is None and path is not None:
        reference_dt = datetime.fromtimestamp(path.stat().st_mtime)
    if reference_dt is None:
        reference_dt = datetime.now()
    return (
        str(reference_dt.year),
        str(reference_dt.month),
        f"{reference_dt.year}-{reference_dt.month}-{reference_dt.day}",
    )


def dated_run_parent(
    run_root: Path,
    *,
    run_name: str | None = None,
    path: Path | None = None,
    dt: datetime | None = None,
) -> Path:
    year, month, day_label = resolve_run_calendar_parts(run_name=run_name, path=path, dt=dt)
    return run_root / year / month / day_label


def normalize_run_name(
    run_name: str | None,
    *,
    dt: datetime | None = None,
    default_label: str = "gaia_run",
) -> str:
    current_prefix = second_timestamp(dt)
    current_time = current_prefix.split("_", maxsplit=1)[1]
    raw_name = (run_name or "").strip()
    if not raw_name:
        return f"{current_prefix}_{default_label}"

    second_match = _RUN_NAME_SECOND_PREFIX_RE.match(raw_name)
    if second_match:
        return raw_name

    minute_match = _RUN_NAME_MINUTE_PREFIX_RE.match(raw_name)
    if minute_match:
        preserved_prefix = (
            f"{minute_match.group('date')}_{minute_match.group('hour')}{minute_match.group('minute')}{current_time[-2:]}"
        )
        rest = minute_match.group("rest")
        return f"{preserved_prefix}_{rest}" if rest else preserved_prefix

    date_match = _RUN_NAME_DATE_PREFIX_RE.match(raw_name)
    if date_match:
        preserved_prefix = f"{date_match.group('date')}_{current_time}"
        rest = date_match.group("rest")
        return f"{preserved_prefix}_{rest}" if rest else preserved_prefix

    return f"{current_prefix}_{raw_name}"


def prepare_dated_run_dir(
    run_root: Path,
    *,
    run_name: str | None = None,
    dt: datetime | None = None,
) -> Path:
    resolved_run_name = normalize_run_name(run_name, dt=dt)
    run_dir = dated_run_parent(
        run_root,
        run_name=resolved_run_name,
        dt=dt,
    ) / resolved_run_name
    return ensure_empty_dir(run_dir)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> Path:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return path


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_json(path: Path, data: Any) -> Path:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return path


def append_jsonl(path: Path, row: dict[str, Any]) -> Path:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "skill"


def _strip_model_json_wrappers(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"(?is)<think>.*?</think>\s*", "", cleaned).strip()
    fence_match = re.search(r"(?is)^```(?:json)?\s*(.*?)\s*```$", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return cleaned


def _escape_json_control_chars(text: str) -> str:
    escaped: list[str] = []
    in_string = False
    pending_escape = False
    replacement_map = {
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }

    for ch in text:
        if in_string:
            if pending_escape:
                escaped.append(ch)
                pending_escape = False
                continue
            if ch == "\\":
                escaped.append(ch)
                pending_escape = True
                continue
            if ch == '"':
                escaped.append(ch)
                in_string = False
                continue
            if ord(ch) < 0x20:
                escaped.append(replacement_map.get(ch, f"\\u{ord(ch):04x}"))
                continue
            escaped.append(ch)
            continue

        escaped.append(ch)
        if ch == '"':
            in_string = True

    return "".join(escaped)


def _iter_json_object_candidates(text: str):
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_string = False
        pending_escape = False
        for end in range(start, len(text)):
            current = text[end]
            if in_string:
                if pending_escape:
                    pending_escape = False
                    continue
                if current == "\\":
                    pending_escape = True
                    continue
                if current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
                continue
            if current == "{":
                depth += 1
                continue
            if current == "}":
                depth -= 1
                if depth == 0:
                    yield start, text[start : end + 1]
                    break


def _load_json_dict(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object.")
    return data


_PREFERRED_TOP_LEVEL_JSON_KEYS = {
    "action",
    "tool_name",
    "arguments",
    "final_answer",
    "choice_label",
    "summary",
    "natural_language_reward",
    "reward_dimensions",
    "experience_note",
    "action_type",
    "target_skill_name",
    "files_to_write",
    "files_to_delete",
    "experience_entry",
    "skill",
}


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_model_json_wrappers(text)
    if not cleaned:
        raise ValueError("Empty model response; expected JSON object.")

    attempts = [cleaned]
    sanitized = _escape_json_control_chars(cleaned)
    if sanitized != cleaned:
        attempts.append(sanitized)

    last_error: Exception | None = None
    for candidate in attempts:
        try:
            return _load_json_dict(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc

    valid_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    seen_candidates: set[tuple[int, str]] = set()
    for source in attempts:
        for start, candidate in _iter_json_object_candidates(source):
            marker = (start, candidate)
            if marker in seen_candidates:
                continue
            seen_candidates.add(marker)
            for variant in (candidate, _escape_json_control_chars(candidate)):
                try:
                    payload = _load_json_dict(variant)
                    preferred_key_hits = len(set(payload) & _PREFERRED_TOP_LEVEL_JSON_KEYS)
                    if preferred_key_hits or start == 0:
                        valid_candidates.append((preferred_key_hits, -start, len(candidate), payload))
                        break
                except (json.JSONDecodeError, ValueError) as exc:
                    last_error = exc
    if valid_candidates:
        _, _, _, payload = max(valid_candidates)
        return payload

    if "{" not in cleaned or "}" not in cleaned:
        raise ValueError(f"Unable to locate JSON object in response: {cleaned[:400]}")
    raise ValueError(str(last_error) if last_error is not None else "Unable to parse model response as a JSON object.")


def safe_relative_path(base_dir: Path, user_path: str) -> Path:
    candidate = (base_dir / user_path).resolve()
    base_resolved = base_dir.resolve()
    if base_resolved == candidate or base_resolved in candidate.parents:
        return candidate
    raise ValueError(f"Path escapes base directory: {user_path}")


def relative_to(path: Path, base_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(base_dir.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def ensure_preferred_proxy_env() -> None:
    preferred_http = "http://127.0.0.1:17890"
    legacy_markers = ("127.0.0.1:43214",)
    current_http = os.environ.get("HTTP_PROXY", "") or os.environ.get("http_proxy", "")
    current_https = os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", "")
    current_all = os.environ.get("ALL_PROXY", "") or os.environ.get("all_proxy", "")
    if not current_http or any(marker in current_http for marker in legacy_markers):
        os.environ["HTTP_PROXY"] = preferred_http
        os.environ["http_proxy"] = preferred_http
    if not current_https or any(marker in current_https for marker in legacy_markers):
        os.environ["HTTPS_PROXY"] = preferred_http
        os.environ["https_proxy"] = preferred_http
    if not current_all or any(marker in current_all for marker in legacy_markers):
        os.environ.pop("ALL_PROXY", None)
        os.environ.pop("all_proxy", None)
