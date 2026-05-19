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
  - ../../../src/agent_health/prompts/instruction_health_v1.txt
  - ../../../src/agent_health/judge.py
---

# Eval Units, Signals, And Judge Contract

## Purpose

This behavior turns Hermes messages into per-user-request evaluation units, extracts deterministic evidence, and prepares the evidence contract used by the LLM judge.

## Scope

This doc owns user-turn normalization, previous-context selection, next-user reaction capture, reaction classification, deterministic signal extraction, and the V1 judge evidence contract.

## Current Behavior

- Each user message starts one candidate eval unit.
- The normalizer attaches the next final assistant response when available.
- Tool messages between the user request and assistant response become trace events with capped result previews and error heuristics.
- Previous context is limited to recent user/assistant pairs and capped by character count.
- The next user message after the assistant response is stored as reaction evidence when available.
- Deterministic signals include tool count, API-call count, turn duration, tool-error count, repeated tool count, next-user reaction type, and assistant completion-claim heuristic.
- Judge payload construction aggressively trims large documents, large code fences, image/data blobs, and bulky tool previews before LLM calls.
- The LLM judge is the final V1 rating mechanism and returns strict JSON with health status, confidence, primary reason, user reaction, barriers, and not-evaluable reason.

## Core Rules

- Evaluate at user-turn granularity rather than only whole sessions.
- Current user request and assistant response should preserve diagnostic intent/evidence up to configured caps, not raw bulk content.
- User reaction is evidence, not absolute proof of failure.
- Deterministic signals are first-class judge evidence, prefilter evidence, and fallback inspection data.
- Deterministic signals prioritize judge budget toward corrections, complaints, repeated requests, tool errors, repeated-tool loops, and prolonged runs.
- Deterministic signals do not replace the LLM judge for final ratings.

## Flows And States

- Normalization scans messages in timestamp order, increments a turn index for each user message, and builds an eval-unit id as `hermes:<session_id>:turn:<n>`.
- Signal extraction consumes an eval unit plus trace events and emits named values with optional severity and evidence.
- Judge input combines the eval unit, trimmed trace evidence, deterministic signals, next-user reaction, and a preflight trim policy. The judge client validates strict JSON and performs one repair retry for malformed output, so a malformed model response can cost one extra call for that unit.

## Contracts

- Normalized units use `normalization_v1`.
- Reaction categories include acceptance, continuation, clarification, correction, complaint, repeated_request, scope_change, unrelated, unknown, and none.
- Health statuses are `succeed`, `failed`, `mishandled`, `prolonged`, and `not_evaluable`.
- Barrier evidence is multi-valued and stored separately from the primary health status.

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
