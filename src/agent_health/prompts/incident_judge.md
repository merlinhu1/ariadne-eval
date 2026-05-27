You are evaluating one AI agent tool call and its immediate tool result.

Decide only whether this tool-call/result pair is an incident. Do not label the
whole user request, the final assistant answer, or request-level anomalies.
Request anomaly labels are not valid incident labels.
Legacy deterministic subtype names are not valid incident labels.
Use only the ML-first label field: not_incident|incident|unsure, with optional reason_code.
Use the human phrase "not an incident" when explaining not_incident.

Return exactly one JSON object:

{
  "schema_version": "incident_eval_v1",
  "label": "not_incident|incident|unsure",
  "reason_code": "execution_error|no_result|bad_request|bad_output|other|null",
  "confidence": 0.0,
  "evidence_summary": "short visible evidence summary"
}
