Critic reward:
{reward_json}

Locked graph signature:
{graph_signature_json}

Current skill:
{skill_json}

Recent history:
{history_poll_json}

Similar past experiences:
{experiences_json}

Modify the skill according to the critic reward while preserving the locked graph exactly. You may edit common text and node-local instructions, including phase-local allowed tools. You must keep every phase name, phase order, and `Next:` target set unchanged.

A2 consumption order:
1. Read `positive_signals` first and preserve the named phase behaviors.
2. Apply only high-value `negative_signals` with clear triggers and feasible actions.
3. Use `tradeoffs` to narrow each edit so it does not weaken protected fast paths.
4. Ignore `audit_evidence` as skill text; use it only to understand why a signal exists.
