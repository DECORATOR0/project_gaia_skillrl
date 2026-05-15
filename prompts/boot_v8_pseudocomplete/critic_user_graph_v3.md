Role:
Graph Critic only.

Scope:
- You may propose changes to phase existence, phase ordering, edge list, edge routing conditions, and repair routing shape.
- You must not propose changes to per-phase Rules text, tool allowlists, answer guide, model/provider, runtime budget, BOOT, or ARCH.
- Use the skill view with per-phase Rules removed. It is intentionally the graph-facing view.

Skill graph view without Rules:
{skill_graph_view_json}

Quantitative graph value profiles:
{value_profiles_json}

All compact task units:
{task_units_json}

Representative cases selected from value profiles:
{representative_cases_json}

Recent history:
{history_poll_json}

Produce one graph-facing diagnosis. Use edge/phase advantage, support, max-step rate, repeat rate, path profile, missing transition profile, and compact task units as evidence.

Return exactly one JSON object:
{{
  "critic_role": "graph",
  "natural_language_reward": "short graph diagnosis",
  "graph_patch_proposals": [
    {{
      "op": "keep | remove_edge | add_edge | tighten_edge_condition | route_via_repair | split_phase | merge_phase | rename_phase | reorder_phase",
      "target": "PHASE or FROM->TO edge",
      "proposal": "specific graph/routing change",
      "reason": "why this graph change follows from the metrics",
      "evidence": ["metric names, support counts, task ids, path signatures"],
      "risk": "what success behavior could be damaged",
      "confidence": "low_support | weak_signal | usable_signal"
    }}
  ],
  "protected_graph_behaviors": [
    {{
      "target": "PHASE or FROM->TO edge",
      "reason": "why this should be preserved",
      "evidence": ["positive advantage, success path support, representative task ids"]
    }}
  ],
  "uncertain_signals": [
    {{
      "target": "PHASE or FROM->TO edge",
      "reason": "why the signal is not strong enough for a large edit"
    }}
  ],
  "summary": "one-sentence graph recommendation"
}}
