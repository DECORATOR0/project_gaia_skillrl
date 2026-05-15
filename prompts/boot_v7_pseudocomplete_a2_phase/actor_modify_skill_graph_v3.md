Critic reward and FLOW-batch-V3 signals:
{reward_json}

Current graph signature:
{graph_signature_json}

Current skill:
{skill_json}

Recent history:
{history_poll_json}

Similar past experiences:
{experiences_json}

Modify the skill according to the aggregate critic reward. You may change graph structure, `Next:` edges, phase-local tool allowlists, and node-local rules only when the reward explicitly supports those edits. Keep the output as one complete `SKILL.md`; do not emit a patch fragment.
