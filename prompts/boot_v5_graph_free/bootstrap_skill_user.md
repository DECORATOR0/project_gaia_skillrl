Runtime Contract:
{runtime_contract_json}

Batch Task Snapshot:
{batch_tasks_json}

Write the initial GAIA seed skill for this batch.

Use the runtime contract as the hard execution boundary and the batch snapshot as examples of task distribution. The topic/task-side input is the same as BOOT-V4: task rows may include compact `preprocess_note` annotations. Use those notes as training-time hints about likely workflow and tool order, then generalize them into reusable phase-local guidance.

Design requirements:
- Create a common preface followed by phase-local instructions.
- Let the phase graph express reusable control flow for GAIA tasks, with the graph structure chosen by you.
- Do not optimize toward any requested phase count, graph size, route length, repair topology, or verification topology; this prompt intentionally leaves those graph choices open.
- Do not create a `CONCLUDE` phase or any other answer-only, finalization-only, or answer-reporting phase.
- Treat final answering as a global executor stop action: if the current evidence determines the exact final answer, the executor may stop from the current phase; otherwise it continues with a phase-local tool call or transition.
- The runtime accepts `<ANSWER>` from any phase.
- The executor has a finite step budget. If the run reaches the maximum step count, the runtime will force an answer from the best available state. Make phase instructions compatible with preserving useful evidence, candidate answers, format requirements, and blocking issues for that forced-answer behavior.
- Do not make phase names themselves answer gates. Describe the evidence, computation, and format-readiness conditions that must be satisfied before answering.
- In every phase, keep `Allowed tools` as the hard allowlist. `Available actions` should be a small menu of legal action forms: call one tool from `Allowed tools`, transition to one phase listed in `Next`, or answer when the global answer policy and local readiness checks are satisfied.
- Put practical tool-selection and use experience in `Rules`, including what to do when the needed tool belongs to another phase.
- Make node-local instructions operational enough for `qwen3-8b`.

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
    "SKILL.md": "---\\nname: gaia-general-skill\\ndescription: ...\\nallowed-tools:\\n  - <copy each tool name from runtime_contract.available_tools>\\nmetadata:\\n  benchmark: GAIA\\n  version: boot-v5\\n---\\n\\nCommon Base Info...\\n\\n## Phase: INIT\\n..."
  }}
}}
