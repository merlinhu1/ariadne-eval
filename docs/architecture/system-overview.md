---
status: active
doc_type: architecture
last_reviewed: 2026-05-19
source_of_truth:
  - ../design.md
  - ../../src/agent_health/
---

# System Overview

## Scope

Ariadne Eval is a local instruction-health evaluator for Hermes Agent sessions.

## Main Components

- **CLI**: initializes local state, inspects Hermes sessions, imports eval units, lists units, and shows deterministic signals.
- **Hermes adapter**: reads Hermes `state.db` sessions and messages without importing hidden reasoning fields.
- **Normalizer**: converts each user request into one evaluation unit with the assistant response, tools, prior context, and next user reaction when available.
- **Event cache**: stores lightweight hook events in JSONL for supplemental timing and tool evidence.
- **Sidecar eval DB**: stores normalized units, trace events, deterministic signals, LLM evals, and barriers in local SQLite.
- **Signal extractor**: computes deterministic evidence such as tool errors, repeated calls, duration, and reaction type.

## Boundaries

- The MVP does not replace Langfuse or provide a hosted dashboard.
- The Hermes plugin must observe only; it must not alter prompts, tool results, memory, skills, or configuration.
- LLM judging is batch-oriented and should use existing Hermes provider configuration by default.

## Product Decisions

- Decision (2026-05-19): The first implementation is Hermes-first, with other agent adapters deferred until the Hermes path works.
- Decision (2026-05-19): Visualization starts as CLI summaries backed by SQLite, not a web dashboard.

## Rationale

A narrow Hermes-first architecture keeps the MVP useful quickly while preserving room for later adapter interfaces. Local SQLite and CLI output keep the evaluator inspectable without introducing hosted observability infrastructure.
