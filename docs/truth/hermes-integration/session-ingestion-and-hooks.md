---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-19
source_of_truth:
  - ../../truthmark/areas/hermes-integration.md
  - ../../../src/agent_health/adapters/hermes.py
  - ../../../src/agent_health/hermes_plugin/__init__.py
  - ../../../src/agent_health/events.py
---

# Hermes Session Ingestion And Hooks

## Purpose

This behavior lets Ariadne Eval inspect Hermes conversations and capture lightweight supplemental events without changing Hermes agent behavior.

## Scope

This doc owns Hermes state.db reading, hidden reasoning exclusion, passive hook registration, and JSONL event-cache helpers.

This doc was created from the editable behavior-doc template at docs/templates/behavior-doc.md.

## Current Behavior

- `HermesStateReader` reads sessions and messages from a configured Hermes `state.db`.
- Message reads select only known user-visible/message metadata fields and exclude hidden reasoning fields.
- `HermesAdapter` exposes source discovery, source loading, eval-unit normalization, and a placeholder trace-event loader.
- The Hermes plugin registers session, LLM, tool, finalize, and approval hooks.
- Hook handlers append compact JSONL events under `instruction-health/events.jsonl` and fail open on exceptions.

## Core Rules

- Hermes `state.db` remains the primary source of truth for session and message history.
- Plugin hooks must be passive, fast, and fail-open.
- Hook code must not call an LLM or mutate prompts, tool results, memory, skills, or configuration.
- Normalized records must not depend on hidden provider reasoning fields.

## Flows And States

- Reader flow: resolve Hermes home, open `state.db`, select schema-tolerant columns, return dictionaries.
- Hook flow: receive event, build bounded previews/hashes, append one JSONL record, ignore local write failures.

## Contracts

- Event cache records use schema version `event_v1` and include framework, session id, event type, timestamp, and payload.
- `HermesAdapter.framework_name` is `hermes`.

## Product Decisions

- Decision (2026-05-19): Hermes is the first adapter and should work before broader agent-framework abstractions are added.
- Decision (2026-05-19): Hooks capture lightweight evidence only; expensive evaluation belongs in later batch work.

## Rationale

Hermes already stores rich session data, so the integration can stay local and simple. Passive hooks fill timing and event gaps without making the evaluator part of the live agent loop.

## Non-Goals

- This doc does not own LLM judging, final health classification, or dashboard reporting.
- This doc does not define non-Hermes adapters.

## Maintenance Notes

- Update this doc when Hermes DB column selection, event schema, hook registration, or fail-open behavior changes.
- Related tests currently include `tests/test_hermes_reader.py`.
