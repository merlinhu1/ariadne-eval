You are reviewing multiple AI agent tool calls and their immediate tool results.

Decide independently whether each tool result created a tool-outcome problem for its current turn. Do not label whole user requests or final assistant answers.

Return exactly one JSON object. Include one result for every input case and copy each tool_outcome_case_id exactly:

{
  "schema_version": "tool_outcome_batch_eval_v1",
  "results": [
    {
      "schema_version": "tool_outcome_review_v1",
      "tool_outcome_case_id": "copy from input tool_outcome_case_id",
      "outcome_label": "problem|ok|unsure",
      "reason_code": "execution_error|empty_output|invalid_tool_input|wrong_or_bad_output|other|null",
      "confidence": 0.0,
      "evidence_summary": "short visible evidence summary"
    }
  ]
}
