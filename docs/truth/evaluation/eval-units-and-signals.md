---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-19
source_of_truth:
  - ../../truthmark/areas/evaluation-model.md
  - ../../../src/agent_health/normalize.py
  - ../../../src/agent_health/reactions.py
  - ../../../src/agent_health/signals.py
  - ../../../src/agent_health/incidents.py
  - ../../../src/agent_health/prompts/instruction_health_v1.txt
  - ../../../src/agent_health/judge.py
---

# Eval Units, Signals, And Judge Contract

## Purpose

This behavior turns Hermes messages into per-user-request evaluation units, extracts deterministic evidence, and prepares the evidence contract used by the LLM judge.

## Scope

This doc owns user-turn normalization, previous-context selection, next-user reaction capture, reaction classification, deterministic signal extraction, deterministic incident-event extraction, and the V1 judge evidence contract.

## Current Behavior

- Each user message starts one candidate eval unit.
- The normalizer attaches the next final assistant response when available.
- Tool messages between the user request and assistant response become trace events with capped result previews and error heuristics.
- Previous context is limited to recent user/assistant pairs and capped by character count.
- The next user message after the assistant response is stored as reaction evidence when available, but natural follow-ups and new instructions are not treated as failures by themselves.
- Deterministic signals include tool count, API-call count, turn duration, tool-error count, repeated tool count, next-user reaction type, and assistant completion-claim heuristic.
- Deterministic incident events are extracted separately from judge status: each tool-error trace event becomes one `tool_error` incident, and additional incident types cover repeated tool loops, excessive tool/API calls, excessive duration, incomplete turns, and completion claims after tool errors.
- The normalizer filters runtime housekeeping user-role messages (context-compaction handoffs, preserved task-list notices, max-tool-iteration continuation notices) and replayed document-upload messages that immediately precede compaction handoffs, so compacted sessions do not double-count the same original request.
- Judge payloads include a configurable judgement threshold (`strict`, `balanced`, or `relaxed`). The default strict threshold requires concrete trace/assistant evidence before marking anomalies and treats natural follow-ups as non-failures.
- The LLM judge is the final V1 rating mechanism and returns strict JSON with health status, confidence, primary reason, user reaction, anomalies, and not-evaluable reason.

## Core Rules

- Evaluate at user-turn granularity rather than only whole sessions.
- Current user request and assistant response should preserve diagnostic intent/evidence up to configured caps, not raw bulk content.
- User reaction is supporting evidence, not absolute proof of failure; under strict judgement it cannot create an anomaly without matching trace or assistant-response evidence.
- Deterministic signals are first-class judge evidence, prefilter evidence, and fallback inspection data.
- Deterministic incident events are first-class failure/anomaly records; they are counted independently of the LLM's turn-level success judgement, so one turn can have multiple concrete failures.
- Deterministic signals prioritize judge budget toward corrections, complaints, repeated requests, tool errors, repeated-tool loops, and prolonged runs.
- Deterministic signals do not replace the LLM judge for final ratings. Judge calls store reported prompt/completion/total token usage so eval runs can be audited for cost.

## Flows And States

- Normalization scans messages in timestamp order, filters runtime housekeeping user-role messages (context-compaction handoffs, preserved task-list notices, max-tool-iteration continuation notices, and replayed document uploads adjacent to compaction handoffs), increments a turn index for each real user message, and builds an eval-unit id as `hermes:<session_id>:turn:<n>`.
- Signal extraction consumes an eval unit plus trace events and emits named values with optional severity and evidence.
- Incident extraction consumes the same eval unit plus trace events and emits event-level failure/anomaly records. `tool_error` is per trace event, not aggregated.
- Judge input combines the eval unit, trimmed trace evidence, deterministic signals, next-user reaction, and a preflight trim policy. The judge client validates strict JSON and performs one repair retry for malformed output, so a malformed model response can cost one extra call for that unit.

## Contracts

- Normalized units use `normalization_v1`.
- Reaction categories include acceptance, continuation, clarification, correction, complaint, repeated_request, scope_change, unrelated, unknown, and none.
- Health statuses are `succeed`, `failed`, `mishandled`, `prolonged`, and `not_evaluable`.
- Anomaly evidence is multi-valued and stored separately from the primary health status.

## Product Decisions

- Decision (2026-05-19): The first evaluator stores one unit per user request so multi-request sessions can be inspected precisely.
- Decision (2026-05-19): Deterministic evidence is first-class V1 behavior, but not the final rating mechanism.
- Decision (2026-05-19): LLM judging remains in V1 because semantic health classification needs more than counters and regexes.
- Decision (2026-05-19): Aggressive preflight trimming is part of V1 budget control because pasted documents, large code snippets, screenshots/data URLs, and bulky tool results usually add cost without improving diagnosis.

## Rationale

Per-turn units make failures and user corrections easier to locate. Deterministic signals make the tool auditable and useful when judge calls fail. The LLM judge is still required to rate ambiguous cases such as user corrections, scope changes, over-claims, and mishandled requirements.

## Non-Goals

- This doc does not own SQLite persistence or CLI display.
- This doc does not define plugin or hook capture behavior.

## Maintenance Notes

- Update this doc when turn-boundary logic, trace-event collection, reaction rules, thresholds, signal names, or judge schema changes.
- Related tests currently include `tests/test_normalize.py`, `tests/test_db_and_signals.py`, and `tests/test_judge.py`.
