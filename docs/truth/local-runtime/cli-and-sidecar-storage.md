---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-19
source_of_truth:
  - ../../truthmark/areas/local-runtime.md
  - ../../../src/agent_health/cli.py
  - ../../../src/agent_health/config.py
  - ../../../src/agent_health/db.py
---

# CLI And Sidecar Storage

## Purpose

This behavior gives users a local command-line workflow and sidecar SQLite database for inspecting, importing, and querying instruction-health evaluation units.

## Scope

This doc owns CLI commands, Hermes-home initialization, sidecar database schema, and local runtime behavior.

This doc was created from the editable behavior-doc template at docs/templates/behavior-doc.md.

## Current Behavior

- The CLI exposes `init`, `inspect hermes`, `import hermes`, `units`, and `signals` commands.
- `init` creates the instruction-health home under the Hermes profile and migrates the sidecar eval database.
- `import hermes` reads due Hermes sessions, normalizes eval units, stores trace events, and stores deterministic signals.
- `units` lists recently imported eval units from the sidecar database.
- `signals` recomputes and stores deterministic signals for one eval unit.
- The sidecar SQLite schema includes eval units, trace events, deterministic signals, LLM evals, barriers, and eval state tables.

## Core Rules

- Evaluator state lives under the Hermes profile in `instruction-health/`.
- The database schema should remain queryable by status, barriers, sessions, dates, and eval-unit ids as later phases are added.
- CLI errors should return a non-zero command result and print a concise error message.

## Flows And States

- Init flow: resolve Hermes home, create config/log/event paths, migrate SQLite, print provider-locality caveat.
- Import flow: discover sessions, normalize each session into units, upsert units and trace events, replace deterministic signals.

## Contracts

- The placeholder console command is `agent-health`.
- The default eval DB path is `<hermes-home>/instruction-health/evals.db`.
- The recorded eval schema version is `eval_schema_v1`.

## Product Decisions

- Decision (2026-05-19): The MVP uses a local SQLite sidecar database rather than JSONL for evaluations.
- Decision (2026-05-19): Manual CLI batches come before scheduled background evaluation.

## Rationale

A CLI plus SQLite keeps the MVP inspectable and useful without committing to a dashboard or hosted observability system.

## Non-Goals

- This doc does not own Hermes hook internals.
- This doc does not own future web dashboard or scheduled evaluator behavior.

## Maintenance Notes

- Update this doc when CLI commands, database tables, or initialization paths change.
- Related tests currently include `tests/test_db_and_signals.py`.
