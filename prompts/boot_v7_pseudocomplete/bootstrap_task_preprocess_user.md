Runtime Contract:
{runtime_contract_json}

Batch Task Snapshot:
{batch_tasks_json}

Write one compact `preprocess_note` for each task.

Character budget:
- Target each note at 1-3 short sentences.
- Hard cap per note: {note_char_cap} characters.
- The total added text should stay close to the original question volume; avoid long reasoning.

Return exactly one JSON object:
{{
  "items": [
    {{
      "task_id": "copy the input task_id",
      "preprocess_note": "brief workflow/tool-order/format-check note"
    }}
  ]
}}
