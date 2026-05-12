Role:
Aggregate Critic for FLOW-batch-V3.

Inputs:
- Graph Critic findings propose graph/routing changes.
- Rules/List shard findings propose common text, per-phase rules, allowlist, exit handoff, and existing-edge wording changes.
- Quantitative value profiles and compact cases are the shared evidence base.

Graph Critic findings:
{graph_findings_json}

Rules/List shard findings:
{rules_findings_json}

Global batch overview:
{batch_overview_json}

Skill graph view without Rules:
{skill_graph_view_json}

Quantitative graph value profiles:
{value_profiles_json}

Representative cases:
{representative_cases_json}

Current graph signature:
{graph_signature_json}

Current full skill:
{skill_json}

Recent history:
{history_poll_json}

Aggregate the findings into one actor-facing reward. Resolve conflicts explicitly:
- If Graph Critic wants to remove/tighten an edge that Rules/List Critics rely on, choose keep/tighten/remove based on metric support and protected success anchors.
- Keep Graph Critic proposals graph-scoped.
- Keep Rules/List proposals inside common text, phase-local rules, allowlist, exit handoff, and existing-edge wording.
- The final reward may ask the actor to edit both graph and rules/list if both are supported.

Return exactly one JSON object compatible with CriticReward:
{{
  "natural_language_reward": "actor-facing diagnosis with graph patch signal and rules/list signal",
  "reward_dimensions": {{
    "flow_version": "FLOW-batch-V3",
    "graph_signal": {{
      "patches_to_apply": [
        {{
          "op": "keep | remove_edge | add_edge | tighten_edge_condition | route_via_repair | split_phase | merge_phase | rename_phase | reorder_phase",
          "target": "PHASE or FROM->TO edge",
          "proposal": "specific actor instruction",
          "evidence": "metric support and compact task ids",
          "risk_control": "how to preserve successful behavior"
        }}
      ],
      "protected_behaviors": ["graph behaviors that must survive"],
      "deferred_or_uncertain": ["low-support graph ideas to avoid or revisit later"]
    }},
    "rules_list_signal": {{
      "common_base_edits": ["short common-text edit recommendation"],
      "node_edits": [
        {{
          "phase": "PHASE_NAME",
          "problem": "what failed",
          "edit": "what actor should change inside this phase",
          "evidence": "compact reference to task ids, paths, tools, or outcome counts"
        }}
      ],
      "protected_behaviors": ["rules/list behaviors that should remain"]
    }},
    "conflict_resolution": ["which conflicting recommendations were resolved and how"],
    "fixed_constants": [
      "answer guide / any-phase answer / forced answer / no-CONCLUDE / BOOT / ARCH are not rollback candidates in this reward"
    ]
  }},
  "experience_note": "compact V3 failure signature",
  "summary": "one-sentence recommendation to the actor"
}}
