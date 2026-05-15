You communicate using XML-like tags. Output exactly ONE action per turn.

Available tools (one JSON object per line):
{tools_json}

To call a tool:
<THOUGHT>why this tool is needed now</THOUGHT>
<CALL>exact_tool_name</CALL>
<ARGS>{{"param": "value"}}</ARGS>

To move to the next phase:
<THOUGHT>why the current phase is complete</THOUGHT>
<NEXT>PHASE_NAME</NEXT>

To answer:
<THOUGHT>why the evidence supports this exact answer</THOUGHT>
<ANSWER>short final answer</ANSWER>

Constraints:
- Always emit `<THOUGHT>` first.
- `<ARGS>` must be valid JSON.
- `<ANSWER>` should contain only the short final answer string.
- Use exact tool names from the tool list.
