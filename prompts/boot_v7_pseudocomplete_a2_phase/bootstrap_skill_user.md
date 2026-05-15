Runtime Contract:
{runtime_contract_json}

Batch Task Snapshot:
{batch_tasks_json}

Write the initial GAIA seed skill for this batch.

Use the runtime contract as the hard execution boundary and the batch snapshot as examples of task distribution. The goal is a clean starting skill whose phase graph is designed by you and can later be optimized without changing the graph.

BOOT-V7 graph requirement:
- Start from the BOOT-V3 design style, with concise reusable states and no preprocessing-specific assumptions.
- Design the states yourself, but force the routing graph into pseudo-complete form.
- Include `INIT`.
- `INIT` must list every non-`INIT` phase in `Next:`.
- Every non-`INIT` phase must list every other non-`INIT` phase in `Next:`.
- No non-`INIT` phase may route back to `INIT`.
- No phase may route to itself.
- These edges are only legal routing permissions. Use each phase's `Rules` and `Exit handoff` to describe preferred routes, early answer conditions, and repair behavior.
- The graph is locked for later A/B/B1 iterations, so choose phase names and phase roles carefully.

Design requirements:
- Create a common preface followed by phase-local instructions.
- Let the phase graph express reusable control flow for GAIA tasks.
- Keep the final answer path legal for the runtime without adding a dedicated answer-only phase.
- Make node-local instructions operational enough for `qwen3-8b`: concise routing phases are fine, but evidence/computation/verification/repair phases should contain enough concrete local guidance to steer tool choice, transition timing, and answer readiness.
- Keep executed paths short for simple tasks and allow longer paths only when the task actually needs more evidence, computation, verification, or repair.
- Include a bounded repair path.
- Do not create a `CONCLUDE` phase or any other answer-only finalization phase.
- Treat final answering as a global executor stop action: if the current evidence determines the final answer, the executor stops; otherwise it continues with a phase-local tool call or transition.
- Do not make phase names themselves answer gates. Instead, describe the evidence, computation, and format-readiness conditions that must be satisfied before answering.
- Direct answer-ready behavior should be limited and conditionally described. Use it only where a final answer may become ready with full support and exact formatting.
- For phases that gather web/local/media evidence or perform partial reasoning, prefer a path through verification or computation unless the phase can already guarantee a concrete supported final string.
- In `INIT`, answer-ready terminal behavior should be rare and reserved for genuinely prompt-only, low-risk answers.
- In every phase, keep `Allowed tools` as the hard allowlist. `Available actions` should be a small menu of legal action forms: call one tool from `Allowed tools`, transition to one phase listed in `Next`, or answer only when the global answer policy and local readiness checks are satisfied.
- Put practical tool-selection and use experience in `Rules`, including what to do when the needed tool belongs to another phase.
- For major work phases, `Rules` may be substantially more detailed than earlier seeds, roughly 250-600 words when useful. Avoid filler and avoid repeating the same global protocol text in every phase.

Do not overfit to the batch:
- Do not include task IDs.
- Do not include task-specific answers.
- Do not include fixed named-entity facts or fixed URLs.
- Do not include fake filenames, fake URLs, example HTML, dummy Python snippets, or copied task-specific paths in tool-call examples.
- Use gold answers only to infer reusable answer shapes and formatting patterns.
- If task rows include `preprocess_note`, use it as compact guidance about the likely workflow and tool order. Do not copy those notes into the skill; generalize them into reusable phase-local rules.
- In the JSON example below, replace `<copy each tool name from runtime_contract.available_tools>` with the actual tool names. Do not write placeholder text into `SKILL.md`.

Return exactly one JSON object:
{{
  "summary": "one-sentence overview",
  "target_skill_name": "gaia-general-skill",
  "files_to_write": {{
    "SKILL.md": "---\\nname: gaia-general-skill\\ndescription: ...\\nallowed-tools:\\n  - <copy each tool name from runtime_contract.available_tools>\\nmetadata:\\n  benchmark: GAIA\\n  version: boot-v7\\n  parent: boot-v3\\n  graph_policy: pseudo_complete_no_return_to_init\\n---\\n\\nCommon Base Info...\\n\\n## Phase: INIT\\n..."
  }}
}}
