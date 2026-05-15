Runtime Contract:
{runtime_contract_json}

Batch Task Snapshot:
{batch_tasks_json}

Write the initial GAIA seed skill for this batch.

Use the runtime contract as the hard execution boundary and the batch snapshot as examples of task distribution. The goal is a BOOT-V6 starting skill whose phase graph follows the BOOT-V4 hard-graph idea, while each node has much longer local guidance for the small executor.

BOOT-V8 graph requirement:
- Start from the BOOT-V6 long-rules design style.
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
- Make node-local instructions operational for `Qwen3.5-9B-local`. The executor sees one phase at a time, and stale phase prompts are hidden after transition, so each phase can carry a long local operating manual.
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
- For BOOT-V6, write `Rules` at roughly 1000-1500 words for every phase. Let the phase's role, allowed tools, step budget, and likely GAIA task pressure determine the content; keep the graph legal and the node useful when injected alone.

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
    "SKILL.md": "---\\nname: gaia-general-skill\\ndescription: ...\\nallowed-tools:\\n  - <copy each tool name from runtime_contract.available_tools>\\nmetadata:\\n  benchmark: GAIA\\n  version: boot-v8\\n  parent: boot-v6\\n  graph_policy: pseudo_complete_no_return_to_init\\n  rules_target_words: 1000-1500\\n---\\n\\nCommon Base Info...\\n\\n## Phase: INIT\\n..."
  }}
}}
