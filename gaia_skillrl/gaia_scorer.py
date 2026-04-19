from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import string
import warnings
from functools import lru_cache
from pathlib import Path
from types import ModuleType


_OFFICIAL_SCORER_PATH = (
    Path(__file__).resolve().parent.parent
    / "external_refs"
    / "gaia_benchmark_leaderboard"
    / "scorer.py"
)


@lru_cache(maxsize=1)
def _load_official_module() -> ModuleType | None:
    if not _OFFICIAL_SCORER_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        "gaia_benchmark_leaderboard_scorer",
        _OFFICIAL_SCORER_PATH,
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def official_scorer_path() -> Path:
    return _OFFICIAL_SCORER_PATH


def using_official_scorer() -> bool:
    return _load_official_module() is not None


def _normalize_number_str_fallback(number_str: str) -> float:
    normalized = str(number_str)
    for char in ["$", "%", ","]:
        normalized = normalized.replace(char, "")
    try:
        return float(normalized)
    except ValueError:
        return float("inf")


def normalize_number_str(number_str: str) -> float:
    official = _load_official_module()
    if official is not None:
        return official.normalize_number_str(str(number_str))
    return _normalize_number_str_fallback(number_str)


def split_string(value: str, char_list: list[str] | None = None) -> list[str]:
    separators = char_list or [",", ";"]
    official = _load_official_module()
    if official is not None and char_list is None:
        return official.split_string(str(value))
    pattern = f"[{''.join(separators)}]"
    return re.split(pattern, str(value))


def normalize_str(value: str, remove_punct: bool = True) -> str:
    official = _load_official_module()
    if official is not None:
        return official.normalize_str(str(value), remove_punct=remove_punct)
    no_spaces = re.sub(r"\s", "", str(value))
    if remove_punct:
        translator = str.maketrans("", "", string.punctuation)
        return no_spaces.lower().translate(translator)
    return no_spaces.lower()


def is_float(value: object) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def question_scorer(model_answer: str | None, ground_truth: str) -> bool:
    official = _load_official_module()
    resolved_answer = "None" if model_answer is None else str(model_answer)
    resolved_ground_truth = str(ground_truth)
    if official is not None:
        with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return official.question_scorer(resolved_answer, resolved_ground_truth)

    if is_float(resolved_ground_truth):
        normalized_answer = normalize_number_str(resolved_answer)
        return normalized_answer == float(resolved_ground_truth)

    if any(char in resolved_ground_truth for char in [",", ";"]):
        gt_elems = split_string(resolved_ground_truth)
        answer_elems = split_string(resolved_answer)
        if len(gt_elems) != len(answer_elems):
            return False

        comparisons: list[bool] = []
        for answer_elem, gt_elem in zip(answer_elems, gt_elems):
            if is_float(gt_elem):
                comparisons.append(normalize_number_str(answer_elem) == float(gt_elem))
                continue
            comparisons.append(
                normalize_str(answer_elem, remove_punct=False)
                == normalize_str(gt_elem, remove_punct=False)
            )
        return all(comparisons)

    return normalize_str(resolved_answer) == normalize_str(resolved_ground_truth)
