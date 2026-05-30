---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-30
source_of_truth:
  - ../../truthmark/areas/evaluation-model.md
  - ../../../src/agent_health/normalize.py
  - ../../../src/agent_health/signals.py
  - ../../../src/agent_health/judge.py
  - ../../../src/agent_health/tool_outcome_taxonomy.py
  - ../../../src/agent_health/tool_outcome_features.py
  - ../../../src/agent_health/tool_outcome_reviewer_model.py
  - ../../../src/agent_health/tool_outcome_routing.py
---

# Turn Cases, Case Signals, And Review Contracts

## Purpose

This behavior turns Hermes messages into reviewable turn cases, extracts deterministic case evidence before LLM judgment, and defines the request-level and tool-level review contracts.

## Scope

This doc owns user-turn normalization, case signal extraction, request-level review JSON validation, tool outcome review JSON validation, and ML-first tool outcome review routing.

## Current Behavior

- A persisted `turn_case` is one real user turn plus normalized request text, assistant response text, prior context, optional next request text, and evidence counts.
- `source_sessions` stores imported Hermes session metadata once, and `turn_cases` reference it by `source_session_id`.
- `case_events` store evidence events for a turn case. `tool_interactions` normalize assistant tool-call and immediate tool-result pairs, and `tool_outcome_cases` reference those interactions.
- `case_signals` are case evidence for a turn case. They are extracted before LLM judgment and include tool count, source-session API count, turn duration, tool-error count, repeated-tool count, reaction type, and completion-claim evidence.
- Request-level LLM output must use `schema_version: turn_case_review_v1`, `outcome_status`, `confidence`, `summary_reason`, bounded `friction_score`, and `findings`.
- Request-level review writes store `case_reviews` and replace that review's `case_findings` only. They do not delete, reset, relabel, invalidate, or mark stale any `tool_outcome_reviews`.
- Tool outcome LLM output must use `schema_version: tool_outcome_review_v1`, `tool_outcome_case_id`, `outcome_label`, `reason_code`, `confidence`, and `evidence_summary`.
- Tool outcome review labels are exactly `problem`, `ok`, and `unsure`. Reason codes are `execution_error`, `empty_output`, `invalid_tool_input`, `wrong_or_bad_output`, and `other`.
- Old request review shapes and old tool outcome review labels are rejected by active validators.
- Automatic LLM judging is fail-closed. A `turn_case` with a prior automatic LLM `case_review`, automatic LLM judge claim, child `tool_outcome_case` automatic LLM review, or child `tool_outcome_case` automatic LLM judge claim is never automatically judged by an LLM again.
- A `tool_outcome_case` with a prior automatic LLM `tool_outcome_review` or automatic LLM judge claim, or whose parent `turn_case` has a prior automatic LLM `case_review` or automatic LLM judge claim, is never automatically judged by an LLM again.
- Automatic LLM eligibility checks fail closed for missing IDs. The final authorization before automatic LLM spend is an atomic database claim, not a stale selected row or CLI flag.
- Human, human-correction, rule, imported, and ML-model tool-outcome reviews are allowed independently of the automatic LLM claim guard.
- ML-model tool-outcome reviews preserve defer, budget-fallback, uncertainty, LLM budget availability, and training-eligibility metadata in `tool_outcome_reviews`.

## Core Rules

- Deterministic evidence is preserved before LLM judgment.
- Turn-case review and tool-outcome review are separate targets with separate schemas and storage rows.
- Tool outcome review records are append-only for this cleanup; refreshing a turn-case review never mutates existing tool outcome review rows.
- Tool outcome review training data comes from eligible tool outcome reviews, not request-level findings.

## Flows And States

- Normalization scans filtered Hermes messages in order, builds turn cases at user-turn boundaries, emits case events, builds tool interactions from assistant tool calls plus immediate tool results, and derives tool outcome cases from those tool interactions.
- Turn-case judging stores at most one automatic LLM review per turn case and writes child findings for that review. Historical duplicate diagnostics may report older duplicate rows without rewriting them.
- Tool-outcome judging stores review rows on `tool_outcome_reviews`; ML routing can either write an `ml_model` review, defer to automatic LLM review when a claim is acquired, or write a budget-fallback review.

## Contracts

- Turn outcome statuses are `succeed`, `failed`, `mishandled`, and `prolonged`.
- Review confidence values are `low`, `medium`, and `high` for turn-case reviews, and numeric 0.0 to 1.0 for tool-outcome reviews.
- `findings[].case_event_id` is preserved when provided.
- Active code does not provide compatibility aliases for previous review schemas or labels.
- Automatic LLM duplicate writes are rejected for future writes; human, imported, and ML-model review rows are not rejected by this automatic LLM duplicate barrier.

## Product Decisions

- Decision (2026-05-30): `turn_case` is the canonical request-level review target and `review` is the canonical judgment noun.
- Decision (2026-05-30): Tool-call/result judgment is modeled as `tool_outcome_case` plus `tool_outcome_review`, not as a request-level finding.
- Decision (2026-05-30): Automatic LLM turn-case and tool-outcome reviews are mutually suppressing within the same parent turn context, while existing tool-outcome reviews remain intact.
- Decision (2026-05-30): Failed or partial automatic LLM claims are retained as spend barriers, so automatic coverage gaps are preferred over duplicate automatic LLM calls.
- Decision (2026-05-24): LLM judging remains part of the evaluator path because case evidence and local ML do not cover every semantic case.

## Rationale

The split model keeps request-level judgment, case evidence, tool interactions, and tool-outcome judgment distinct. This makes storage and dashboard contracts easier to audit and prevents tool-level labels from polluting request-level findings.

## Non-Goals

- This doc does not own CLI command spelling or dashboard routes.
- This doc does not define historical migration compatibility.

## Maintenance Notes

Update this doc when normalization, case-signal extraction, review schemas, tool outcome reviews, ML routing, or automatic LLM guard behavior changes.
