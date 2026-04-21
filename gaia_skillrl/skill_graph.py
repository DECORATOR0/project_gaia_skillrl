from __future__ import annotations

import re
from typing import Any

from .schemas import SkillDetail, SkillPhase


_NEXT_RE = re.compile(r"^\s*Next:\s*(.+?)\s*$", re.IGNORECASE)
_PHASE_ID_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,}\b")


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

