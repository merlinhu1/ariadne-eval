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
---

# Eval Units And Deterministic Signals

## Purpose

This behavior turns Hermes messages into per-user-request evaluation units and extracts deterministic evidence before any LLM judge is used.

## Scope

This doc owns user-turn normalization, previous-context selection, next-user reaction capture, reaction classification, and deterministic signal extraction.

This doc was created from the editable behavior-doc template at docs/templates/behavior-doc.md.

## Current Behavior

- Each user message starts one candidate eval unit.
- The normalizer attaches the next final assistant response when available.
- Tool messages between the user request and assistant response become trace events with capped result previews and error heuristics.
- Previous context is limited to recent user/assistant pairs and capped by character count.
- The next user message after the assistant response is stored as reaction evidence when available.
- Deterministic signals include tool count, API-call count, turn duration, tool-error count, repeated tool count, next-user reaction type, and assistant completion-claim heuristic.

## Core Rules

- Evaluate at user-turn granularity rather than only whole sessions.
- Current user request and assistant response should remain verbatim up to configured caps.
- User reaction is evidence, not absolute proof of failure.
- Deterministic signals should be available even when LLM judging is unavailable.

## Flows And States

- Normalization scans messages in timestamp order, increments a turn index for each user message, and builds an eval-unit id as `hermes:<session_id>:turn:<n>`.
- Signal extraction consumes an eval unit plus trace events and emits named values with optional severity and evidence.

## Contracts

- Normalized units use `normalization_v1`.
- Reaction categories include acceptance, continuation, clarification, correction, complaint, repeated_request, scope_change, unrelated, unknown, and none.

## Product Decisions

- Decision (2026-05-19): The first evaluator stores one unit per user request so multi-request sessions can be inspected precisely.
- Decision (2026-05-19): Deterministic evidence is first-class and should not be hidden behind the future LLM judge.

## Rationale

Per-turn units make failures and user corrections easier to locate. Deterministic signals make the tool useful for local inspection even before model-based evaluation is complete.

## Non-Goals

- This doc does not own SQLite persistence or CLI display.
- This doc does not define the final LLM health-status judge implementation.

## Maintenance Notes

- Update this doc when turn-boundary logic, trace-event collection, reaction rules, thresholds, or signal names change.
- Related tests currently include `tests/test_normalize.py` and `tests/test_db_and_signals.py`.
