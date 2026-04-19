Current skill library snapshot:
{current_skills_json}

Selected GAIA batch:
{batch_tasks_json}

Write the very first seed skill for this batch.

The skill should help a phase-based GAIA executor handle:
- tasks with no attachment
- tasks with PDFs, tables, text-like files, images, audio files, DOCX files, PPTX files, or archives
- tasks that require web lookup after local evidence is exhausted
- short-answer formatting constraints such as first name only, exact dates, units, scales, percentages, and rounded values

Additional constraints:
- Keep the skill aligned with the actual tool list in this environment.
- Keep the instructions concrete for `qwen3-8b`.
- Do not add task-specific answers, task IDs, or named-entity facts to the skill body unless they express a reusable workflow rule.
- Add explicit guardrails against answering before `CONCLUDE`.
- Add explicit reminders to verify the requested final representation before answering.
- In every phase, write `Available actions:` using literal tags such as `<CALL>read_json_file</CALL><ARGS>{{"path": "task.json"}}</ARGS>` and `<NEXT>GATHER</NEXT>`.
- Never use placeholder tags like `<ACTION>`.
- Do not create `scripts/` files.

Return exactly one JSON object:
{{
  "summary": "one-sentence overview",
  "target_skill_name": "gaia-general-skill",
  "files_to_write": {{
    "SKILL.md": "---\\nname: gaia-general-skill\\ndescription: ...\\nallowed-tools:\\n  - list_dir\\n  - read_file\\n  - read_json_file\\n  - extract_pdf_text\\n  - read_table\\n  - image_metadata\\n  - audio_transcribe\\n  - ocr_image\\n  - image_qa\\n  - parse_docx\\n  - parse_pptx\\n  - extract_archive\\n  - web_search\\n  - fetch_url\\n  - html_extract\\n  - run_python\\nmetadata:\\n  benchmark: GAIA\\n  version: \\\"boot-0.1\\\"\\n---\\n\\n## Phase: INIT\\n...",
    "references/GAIA_BATCH_BOOT_NOTES.md": "optional concise note"
  }}
}}
