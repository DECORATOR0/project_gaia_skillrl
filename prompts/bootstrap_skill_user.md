Runtime Contract:
{runtime_contract_json}

Batch Task Snapshot:
{batch_tasks_json}

Write the initial GAIA seed skill for this batch.

Use the runtime contract as the hard execution boundary and the batch snapshot as examples of task distribution. The goal is a clean starting skill whose phase graph is designed by you and can later be optimized without changing the graph.

Design requirements:
- Create a common preface followed by phase-local instructions.
- Let the phase graph express reusable control flow for GAIA tasks.
- Keep the final answer path legal for the runtime.
- Keep node-local instructions concise enough for `qwen3-8b`.
- Keep executed paths short for simple tasks and allow longer paths only when the task actually needs more evidence, computation, verification, or repair.
- Include a bounded repair path.
- Make the final answer phase reachable from any phase where a final candidate answer may become ready.

Do not overfit to the batch:
- Do not include task IDs.
- Do not include task-specific answers.
- Do not include fixed named-entity facts or fixed URLs.
- Do not include fake filenames, fake URLs, example HTML, dummy Python snippets, or copied task-specific paths in tool-call examples.
- Use gold answers only to infer reusable answer shapes and formatting patterns.

Return exactly one JSON object:
{{
  "summary": "one-sentence overview",
  "target_skill_name": "gaia-general-skill",
  "files_to_write": {{
    "SKILL.md": "---\\nname: gaia-general-skill\\ndescription: ...\\nallowed-tools:\\n  - list_dir\\n  - read_file\\n  - read_json_file\\n  - extract_pdf_text\\n  - read_table\\n  - image_metadata\\n  - audio_transcribe\\n  - ocr_image\\n  - image_qa\\n  - parse_docx\\n  - parse_pptx\\n  - extract_archive\\n  - web_search\\n  - fetch_url\\n  - html_extract\\n  - run_python\\nmetadata:\\n  benchmark: GAIA\\n  version: boot-0.3\\n---\\n\\nCommon Base Info...\\n\\n## Phase: INIT\\n..."
  }}
}}
