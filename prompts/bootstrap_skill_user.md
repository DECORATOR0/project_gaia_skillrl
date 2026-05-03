Runtime Contract:
{runtime_contract_json}

Batch Task Snapshot:
{batch_tasks_json}

Write the initial GAIA seed skill for this batch.

Use the runtime contract as the hard execution boundary and the batch snapshot as examples of task distribution. The goal is a clean starting skill whose phase graph is designed by you and can later be optimized without changing the graph.

Design requirements:
- Create a common preface followed by phase-local instructions.
- Let the phase graph express reusable control flow for GAIA tasks.
- Keep the final answer path legal for the runtime without adding a dedicated answer-only phase.
- Keep node-local instructions concise enough for `qwen3-8b`.
- Keep executed paths short for simple tasks and allow longer paths only when the task actually needs more evidence, computation, verification, or repair.
- Include a bounded repair path.
- Do not create a `CONCLUDE` phase or any other answer-only finalization phase.
- Treat final answering as a global executor stop action: if the current evidence determines the final answer, the executor stops; otherwise it continues with a phase-local tool call or transition.
- Do not add phase-local rules such as "do not answer in this phase" or "only a specific phase may answer."
- Direct answer-ready terminal states should be limited and conditionally described. Use them only where a final answer may become ready with full support and exact formatting.
- For phases that gather web/local/media evidence or perform partial reasoning, prefer a path through verification or computation unless the phase can already guarantee a concrete supported final string.
- In `INIT`, answer-ready terminal behavior should be rare and reserved for genuinely prompt-only, low-risk answers.

Do not overfit to the batch:
- Do not include task IDs.
- Do not include task-specific answers.
- Do not include fixed named-entity facts or fixed URLs.
- Do not include fake filenames, fake URLs, example HTML, dummy Python snippets, or copied task-specific paths in tool-call examples.
- Use gold answers only to infer reusable answer shapes and formatting patterns.
- In the JSON example below, replace `<copy each tool name from runtime_contract.available_tools>` with the actual tool names. Do not write placeholder text into `SKILL.md`.

Return exactly one JSON object:
{{
  "summary": "one-sentence overview",
  "target_skill_name": "gaia-general-skill",
  "files_to_write": {{
    "SKILL.md": "---\\nname: gaia-general-skill\\ndescription: ...\\nallowed-tools:\\n  - <copy each tool name from runtime_contract.available_tools>\\nmetadata:\\n  benchmark: GAIA\\n  version: boot-0.3\\n---\\n\\nCommon Base Info...\\n\\n## Phase: INIT\\n..."
  }}
}}
