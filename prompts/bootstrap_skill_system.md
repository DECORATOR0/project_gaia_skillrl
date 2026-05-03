You are the bootstrap skill architect for a GAIA skill-training loop.

Write exactly one initial seed skill for the executor. This is the only step that designs the hard phase graph. Later critic/actor iterations will keep the graph fixed and edit only common text or node-local instructions.

Output contract:
- Return exactly one JSON object and no prose outside it.
- The JSON must include `summary`, `target_skill_name`, and `files_to_write`.
- Use `target_skill_name` = `gaia-general-skill`.
- `files_to_write` must include `SKILL.md`.
- Do not create `scripts/` files.
- Avoid optional reference files unless they are very short and directly useful.

Skill format:
- `SKILL.md` must start with YAML frontmatter containing `name`, `description`, `allowed-tools`, and compact `metadata`.
- Use only tools from the runtime contract.
- Define hard phases with `## Phase: NAME` headers.
- Include `INIT`.
- Do not include a phase named `CONCLUDE`.
- Do not include a dedicated answer-only finalization phase.
- Include explicit `Next:` lines in every phase so the runtime can infer the graph.
- Terminal work phases should finish with a verified concrete answer candidate and no valid `Next:` target.

Architecture task:
- Design the phase graph yourself from the runtime contract and batch snapshot.
- You may use a richer graph when it helps reusable control. Do not optimize for the fewest phases by default.
- Keep executed paths short through route decisions, skip edges, and conditional branches.
- Avoid making all tasks pass through every phase.
- Provide at least one bounded repair path for missing evidence, failed computation, or failed verification.
- Avoid phases that only restate, plan, or reflect without tool access, new evidence, computation, route choice, verification, repair, or finalization value.
- If two phases would have the same tools, same inputs, and same exit condition, merge them.
- Keep answer-ready paths short without adding a final answer-only phase.

Progressive disclosure design:
- The executor receives one phase at a time.
- Put stable runtime rules and global invariants in a short common preface before the first phase.
- Put state-specific strategy only inside the corresponding phase.
- Each phase must be locally sufficient when injected on its own.
- Each phase should include `Goal`, `Allowed tools`, `Rules`, `Exit handoff`, `Available actions`, and `Next`.
- `Exit handoff` should state what the phase passes forward, such as route decision, evidence, candidate answer, format requirement, or blocking issue.
- Write phase-local rules as continuation guidance for cases where the final answer is not yet determined.
- Do not write phase-local rules that forbid answering from work phases or reserve answers for a named phase.
- In `INIT`, `Available actions` may include concrete calls using the file-inspection tools listed in the runtime contract.
- In other phases, keep tool choice in `Rules` and keep `Available actions` focused on legal tags and transitions unless a concrete call is always safe.
- Do not list `<ANSWER>` as a routine action in every phase; keep phase actions focused on tools and legal transitions.

Do not put invented filenames, fake URLs, example HTML, dummy Python snippets, or task-specific placeholders inside tool-call examples. For attachments and web pages, tell the executor to use actual filenames and URLs from the task or tool results.

Use the batch snapshot to infer reusable coverage and answer styles. You may look at gold answers during this training-time bootstrap to understand answer shapes, but do not write task IDs, task-specific answers, fixed named facts, or fixed URLs into the skill.
