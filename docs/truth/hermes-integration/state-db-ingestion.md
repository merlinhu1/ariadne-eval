---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-19
source_of_truth:
  - ../../truthmark/areas/hermes-integration.md
  - ../../../src/agent_health/adapters/hermes.py
---

# Hermes State.db Ingestion

## Purpose

This behavior lets Ariadne Eval inspect Hermes conversations from the local Hermes `state.db` without changing Hermes runtime behavior.

## Scope

This doc owns Hermes state.db reading, schema-tolerant field selection, message ordering, and hidden reasoning exclusion.

## Current Behavior

- `HermesStateReader` reads sessions and messages from a configured Hermes `state.db`.
- Session reads select only known fields that exist in the current database schema.
- Message reads select only user-visible/message metadata fields and exclude hidden reasoning fields.
- Messages are ordered by timestamp and id.
- `HermesAdapter` exposes source discovery, source loading, request eval-unit normalization, and incident example normalization for Hermes sessions.

## Core Rules

- Hermes `state.db` is the only V1 source of truth.
- V1 must not require a Hermes plugin or runtime hook capture.
- Normalized records must not depend on hidden provider reasoning fields.

## Flows And States

- Reader flow: resolve Hermes home, open `state.db`, select schema-tolerant columns, return dictionaries.
- Import flow: discover recent session ids, load session/messages, pass them to the request normalizer and the incident example normalizer.

## Contracts

- `HermesAdapter.framework_name` is `hermes`.
- Hidden fields excluded from message output include `reasoning`, `reasoning_content`, `reasoning_details`, `codex_reasoning_items`, and `codex_message_items`.
- Incident example normalization uses the same hidden-field-excluded message dictionaries and joins assistant `tool_calls` to immediate tool-role results by `tool_call_id`.

## Product Decisions

- Decision (2026-05-19): Ariadne Eval V1 reads Hermes `state.db` only; hook/plugin capture is deferred.
- Decision (2026-05-19): Hermes is the first integration and should work before broader agent-framework abstractions are added.

## Rationale

Hermes already stores rich durable session data. Reading `state.db` directly keeps V1 local, historical-session-friendly, and simple to install.

## Non-Goals

- This doc does not own deterministic signal semantics.
- This doc does not define plugin or hook capture behavior for V1.
- This doc does not define non-Hermes adapters.

## Maintenance Notes

- Update this doc when Hermes DB column selection, hidden-field exclusion, source-discovery behavior, or adapter normalization surfaces change.
- Related tests currently include `tests/test_hermes_reader.py` and `tests/test_normalize.py`.
