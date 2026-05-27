---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-24
source_of_truth:
  - ../../truthmark/areas/evaluation-model.md
  - ../../../src/agent_health/normalize.py
  - ../../../src/agent_health/reactions.py
  - ../../../src/agent_health/signals.py
  - ../../../src/agent_health/incident_taxonomy.py
  - ../../../src/agent_health/incident_features.py
  - ../../../src/agent_health/incident_model.py
  - ../../../src/agent_health/incident_routing.py
  - ../../../src/agent_health/prompts/instruction_health_v1.txt
  - ../../../src/agent_health/prompts/incident_judge.md
  - ../../../src/agent_health/judge.py
---

# Eval Units, Signals, And Judge Contract

## Purpose

This behavior turns Hermes messages into per-user-request evaluation units, extracts deterministic evidence, and prepares the evidence contract used by the LLM judge.

## Scope

This doc owns user-turn normalization, previous-context selection, next-user reaction capture, reaction classification, deterministic evidence-signal extraction, the request-level V1 judge evidence contract, and the separate ML-first tool-call incident evaluation contract.

## Current Behavior

- Each user message starts one candidate eval unit.
- The normalizer attaches the next final assistant response when available.
- Tool messages between the user request and assistant response become trace events with capped result previews (currently up to 6,000 characters) and error heuristics.
- Previous context is limited to recent user/assistant pairs and capped by character count.
- The next user message after the assistant response is stored as reaction evidence when available, but natural follow-ups and new instructions are not treated as failures by themselves.
- Deterministic signals include only request-level evidence: tool count, API-call count, turn duration, tool-error count, repeated tool count, reaction type, and assistant completion-claim heuristic. They do not emit incident subtype labels.
- Tool-call incidents are represented only as normalized `incident_eval_examples` plus their canonical `incident_labels` and `incident_predictions`; no deterministic incident extractor or subtype table is part of the production contract.
- The normalizer filters runtime housekeeping user-role messages (context-compaction handoffs, preserved task-list notices, max-tool-iteration continuation notices) and replayed document-upload messages that immediately precede compaction handoffs, so compacted sessions do not double-count the same original request.
- Judge payloads include a configurable judgement threshold (`strict`, `balanced`, or `relaxed`). The default strict threshold requires concrete trace/assistant evidence before marking anomalies and treats natural follow-ups as non-failures.
- The LLM judge is the final V1 rating mechanism and returns strict JSON with health status, confidence, primary reason, normalized per-request `request_friction_score` from 0.0 to 1.0, user reaction, and anomalies.
- Tool-call incident examples are a separate layer over assistant `tool_calls` and immediate tool-result messages. They store one complete example per tool-call/result pair with the emitting assistant message id, result message id, tool-call id, tool name, tool arguments, immediate tool result, visible request/following-assistant context, and no hidden reasoning/provider fields.
- Incident decision labels are exactly `incident`, `not_incident`, and `unsure`; reason codes are `execution_error`, `no_result`, `bad_request`, `bad_output`, and `other`.
- Incident feature building converts normalized incident examples into model-ready structured/text evidence, including tool name, argument text, result text, exit code, error fields, empty/truncation flags, and result length. These fields are features, not deterministic labels.
- The incident judge prompt and validator are incident-specific. They label only tool-call/result pairs and return strict JSON with incident label, optional reason code, numeric confidence, and an evidence summary. The single-example prompt returns `incident_eval_v1`; the batched prompt returns one `incident_batch_eval_v1.results[]` row per input `incident_example_id`. Both prompts reject request anomaly labels and legacy deterministic subtype labels as valid incident labels. The validator treats quoted `"null"` reason codes as null, and the incident judge client performs one repair retry for malformed output.
- ML-first incident routing treats confident ML `incident` and `not_incident` decisions as final, defers low-confidence decisions to the incident judge when deferred judging is enabled and budget remains, and persists best-effort ML fallback predictions with `budget_fallback=true`, `should_defer_to_llm=false`, and the routed LLM budget availability when the incident judge is unavailable or over budget.

## Core Rules

- Evaluate at user-turn granularity rather than only whole sessions.
- Current user request and assistant response should preserve diagnostic intent/evidence up to configured caps, not raw bulk content.
- User reaction is supporting evidence, not absolute proof of failure; under strict judgement it cannot create an anomaly without matching trace or assistant-response evidence.
- Deterministic signals are first-class judge evidence, prefilter evidence, and fallback inspection data, but never incident labels.
- There is one source of incident records: `incident_eval_examples` enriched by `incident_labels` and `incident_predictions`. Dashboard feedback writes accepted human labels into `incident_labels` rather than a separate review table.
- Deterministic signals prioritize judge budget toward corrections, complaints, repeated requests, tool errors, repeated-tool loops, and prolonged runs.
- Deterministic signals do not replace the LLM judge for final ratings. Judge calls store reported prompt/completion/total token usage so eval runs can be audited for cost.
- Request anomaly evaluation and tool-call incident evaluation are different training targets. Request-level anomaly labels from `llm_evals`/`anomalies` must not become incident training labels. Tool-call incident labels may be used as evidence for request-level evaluation later, but they are not request anomaly labels.

## Flows And States

- Normalization scans messages in timestamp order, filters runtime housekeeping user-role messages (context-compaction handoffs, preserved task-list notices, max-tool-iteration continuation notices, and replayed document uploads adjacent to compaction handoffs), increments a turn index for each real user message, and builds an eval-unit id as `hermes:<session_id>:turn:<n>`.
- Signal extraction consumes an eval unit plus trace events and emits named values with optional severity and evidence.
- Judge input combines the eval unit, trimmed trace evidence, deterministic signals, next-user reaction, a preflight trim policy, and instructions to score normalized request friction without penalizing raw session length. The judge client validates strict JSON and performs one repair retry for malformed output, so a malformed model response can cost one extra call for that unit.
- Incident normalization scans assistant `tool_calls`, defensively parses call payloads, matches each call to the immediate following tool message with the same `tool_call_id`, and skips missing-result calls rather than creating complete training examples.
- Incident ML decisions use the three-label decision contract with confidence, uncertainty, optional reason metadata, model identity, defer/fallback metadata, LLM budget availability, and evidence summary. Deterministic rule/subtype labels are not valid ML-first incident decision labels and are not preserved as a production path.

## Contracts

- Normalized units use `normalization_v1`.
- Reaction categories include acceptance, continuation, clarification, correction, complaint, repeated_request, scope_change, unrelated, unknown, and none.
- Health statuses are exactly `succeed`, `failed`, `mishandled`, and `prolonged`.
- Request anomaly labels may include timeout, permission/auth denial, approval denial, cancellation, rate-limit, network, resource, dependency, path, test, quality-gate, and git-rejection categories in addition to generic `tool_error`; tool-call incident labels remain only `incident`, `not_incident`, and `unsure`.
- Anomaly evidence is multi-valued and stored separately from the primary health status.
- Request friction is stored per LLM eval as `request_friction_score`; dashboard summaries and ranking expose friction fields only.
- ML-first incident labels are `incident`, `not_incident`, and `unsure`; subtype labels such as `tool_error` are rejected by the incident decision object and incident judge validator.

## Product Decisions

- Decision (2026-05-19): The first evaluator stores one unit per user request so multi-request sessions can be inspected precisely.
- Decision (2026-05-19): Deterministic evidence is first-class V1 behavior, but not the final rating mechanism.
- Decision (2026-05-24): LLM judging remains permanently in the evaluator path because local ML and deterministic evidence will not cover every semantic health case with high confidence.
- Decision (2026-05-23): The deterministic incident subtype/classifier surface was removed. Tool-call incident learning uses canonical examples, human/LLM labels, predictions, and model registry rows only.
- Decision (2026-05-19): Aggressive preflight trimming is part of V1 budget control because pasted documents, large code snippets, screenshots/data URLs, and bulky tool results usually add cost without improving diagnosis.
- Decision (2026-05-23): The mature incident path is ML-first over incident-specific LLM/human labels. Request anomaly labels, ML self-predictions, and legacy deterministic/rule labels are not accepted incident training data.
- Decision (2026-05-23): When the incident judge budget is exhausted, the default incident route is best-effort ML fallback with explicit fallback metadata rather than conservative `unsure`.
- Decision (2026-05-23): Request-level dashboard ranking uses normalized `request_friction_score` instead of raw deterministic incident counts so long sessions are not penalized merely for length.

## Rationale

Per-turn units make failures and user corrections easier to locate. Deterministic signals make the tool auditable and useful when judge calls fail. The request-level LLM judge remains required because some corrections, scope changes, over-claims, and mishandled requirements will stay below local-model confidence thresholds. The incident layer has a narrower tool-call/result target so accepted labels can train a local incident model without polluting request anomaly training.

## Non-Goals

- This doc does not own SQLite persistence or CLI display.
- This doc does not define plugin or hook capture behavior.

## Maintenance Notes

- Update this doc when turn-boundary logic, trace-event collection, reaction rules, thresholds, signal names, incident labels/features/routing, or judge schema changes.
- Related tests currently include `tests/test_normalize.py`, `tests/test_db_and_signals.py`, `tests/test_incident_model.py`, `tests/test_incident_features.py`, `tests/test_incident_routing.py`, `tests/test_judge_contract.py`, and `tests/test_cli.py`.
