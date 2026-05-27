You are evaluating multiple AI agent tool calls and their immediate tool results.

Decide independently for each example whether that tool-call/result pair is an incident. Do not label the whole user request, the final assistant answer, or request-level anomalies.
Request anomaly labels are not valid incident labels.
Legacy deterministic subtype names are not valid incident labels.
Use only the ML-first label field: not_incident|incident|unsure, with optional reason_code.
Use the human phrase "not an incident" when explaining not_incident.

Return exactly one JSON object. Include one result for every input example and copy each incident_example_id exactly:

{
  "schema_version": "incident_batch_eval_v1",
  "results": [
    {
      "incident_example_id": "copy from input incident_example_id",
      "label": "not_incident|incident|unsure",
      "reason_code": "execution_error|no_result|bad_request|bad_output|other|null",
      "confidence": 0.0,
      "evidence_summary": "short visible evidence summary"
    }
  ]
}
