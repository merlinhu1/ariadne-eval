You are reviewing one AI agent tool call and its immediate tool result.

Decide whether the tool result created a tool-outcome problem for the current turn. Do not label the whole user request or the final assistant answer.

Return exactly one JSON object:

{
  "schema_version": "tool_outcome_review_v1",
  "tool_outcome_case_id": "copy from input tool_outcome_case_id",
  "outcome_label": "problem|ok|unsure",
  "reason_code": "execution_error|empty_output|invalid_tool_input|wrong_or_bad_output|other|null",
  "confidence": 0.0,
  "evidence_summary": "short visible evidence summary"
}
