from __future__ import annotations

import re
from typing import Any

from .schemas import SkillDetail, SkillPhase


_NEXT_RE = re.compile(r"^\s*Next:\s*(.+?)\s*$", re.IGNORECASE)
_NEXT_LINE_RE = re.compile(r"^\s*Next:\s*.*$", re.IGNORECASE)
_PHASE_ID_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,}\b")
_PHASE_HEADER_LINE_RE = re.compile(r"(?m)^##\s+Phase:\s*(\S+).*$", re.IGNORECASE)


def extract_next_phases(phase: SkillPhase, phase_names: set[str]) -> list[str]:
    candidates: list[str] = []
    for line in phase.content.splitlines():
        match = _NEXT_RE.match(line)
        if not match:
            continue
        for token in _PHASE_ID_RE.findall(match.group(1).upper()):
            if token in phase_names and token != phase.name:
                candidates.append(token)
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def phase_order(phases: dict[str, SkillPhase]) -> list[str]:
    return [phase.name for phase in sorted(phases.values(), key=lambda item: item.order)]


def build_phase_graph(phases: dict[str, SkillPhase]) -> dict[str, list[str]]:
    names = phase_order(phases)
    phase_names = set(names)
    graph: dict[str, list[str]] = {}
    for index, name in enumerate(names):
        explicit_next = extract_next_phases(phases[name], phase_names)
        if explicit_next:
            graph[name] = explicit_next
            continue
        next_phase = names[index + 1] if index + 1 < len(names) else None
        graph[name] = [next_phase] if next_phase else []
    return graph


def graph_signature_from_phases(phases: dict[str, SkillPhase]) -> dict[str, Any]:
    order = phase_order(phases)
    graph = build_phase_graph(phases)
    return {
        "phase_order": order,
        "edges": [
            {"from": name, "to": target}
            for name in order
            for target in graph.get(name, [])
        ],
        "graph": graph,
    }


def graph_signature(skill: SkillDetail) -> dict[str, Any]:
    return graph_signature_from_phases(skill.phases)


def normalize_pseudocomplete_next_lines(skill_md: str) -> tuple[str, dict[str, Any]]:
    """Rewrite phase `Next:` lines to INIT-out / non-INIT complete graph form."""
    text = skill_md.replace("\r\n", "\n")
    matches = list(_PHASE_HEADER_LINE_RE.finditer(text))
    if not matches:
        return text, {
            "normalized": False,
            "reason": "no_phase_headers",
            "phase_order": [],
            "graph": {},
        }

    phase_order_names = [match.group(1).strip().upper() for match in matches]
    if "INIT" not in phase_order_names:
        return text, {
            "normalized": False,
            "reason": "missing_init",
            "phase_order": phase_order_names,
            "graph": {},
        }

    prefix = text[: matches[0].start()]
    sections: list[str] = []
    graph: dict[str, list[str]] = {}
    changed = False

    for index, match in enumerate(matches):
        name = match.group(1).strip().upper()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[start:end]
        targets = (
            [phase for phase in phase_order_names if phase != "INIT"]
            if name == "INIT"
            else [phase for phase in phase_order_names if phase not in {"INIT", name}]
        )
        graph[name] = targets
        next_line = "Next: " + (", ".join(targets) if targets else "none")

        lines = section.splitlines(keepends=True)
        rewritten: list[str] = []
        replaced = False
        for line in lines:
            if _NEXT_LINE_RE.match(line):
                if not replaced:
                    newline = "\n" if line.endswith("\n") else ""
                    rewritten.append(next_line + newline)
                    replaced = True
                    if line.rstrip("\n") != next_line:
                        changed = True
                else:
                    changed = True
                continue
            rewritten.append(line)
        if not replaced:
            if rewritten and not rewritten[-1].endswith("\n"):
                rewritten[-1] += "\n"
            rewritten.append(next_line + "\n")
            changed = True
        sections.append("".join(rewritten))

    normalized = prefix + "".join(sections)
    return normalized, {
        "normalized": True,
        "changed": changed,
        "phase_order": phase_order_names,
        "graph": graph,
        "edge_count": sum(len(targets) for targets in graph.values()),
        "rule": "INIT connects to all non-INIT phases; non-INIT phases connect to all other non-INIT phases; no self loops; no return to INIT.",
    }
