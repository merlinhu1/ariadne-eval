---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-30
source_of_truth:
  - ../../truthmark/areas/local-runtime.md
  - ../../../src/agent_health/cli.py
  - ../../../src/agent_health/db.py
  - ../../../src/agent_health/scheduler.py
---

# CLI And Sidecar Storage

## Purpose

This behavior gives users a local command-line workflow and sidecar SQLite database for importing, reviewing, querying turn cases, and managing tool outcome reviews.

## Scope

This doc owns CLI commands, sidecar database schema, scheduler review jobs, and local runtime storage behavior.

## Current Behavior

- The CLI exposes `init`, `inspect hermes`, `import hermes`, `cases`, `cases show`, `case-signals`, `review --due`, `reviews list`, `reviews summary`, `tool-outcomes cases`, `tool-outcomes review`, `tool-outcomes llm-review`, `tool-outcomes predict`, `tool-outcomes train-reviewer`, `tool-outcomes export-training`, `dashboard install`, `scheduler`, and `review-jobs` commands.
- `import hermes` stores source sessions, turn cases, case events, tool interactions, tool outcome cases, and case signals in the sidecar database.
- `review --due` reviews due turn cases first, stores `case_reviews` and `case_findings`, and records prompt/completion/total token usage. Automatic LLM turn-case review is fail-closed: a turn case with a prior automatic LLM `case_review`, automatic LLM judge claim, child tool-outcome automatic LLM review, or child tool-outcome automatic LLM judge claim is never automatically judged by an LLM again.
- Automatic LLM tool-outcome review uses remaining review budget only for tool outcome cases with no prior automatic LLM `tool_outcome_review`, no tool-outcome automatic LLM judge claim, and no parent turn case automatic LLM review or claim. Candidate selection and the immediate pre-call path both enforce this guard.
- Automatic LLM claims live in `automatic_llm_review_claims`, are acquired atomically before each automatic LLM call, and are never deleted as a retry mechanism. Failed, interrupted, or partially completed automatic LLM attempts remain spend barriers.
- Refreshing or replacing a turn-case review does not mutate existing tool outcome reviews.
- `tool-outcomes review` writes human or human-correction tool outcome reviews. `tool-outcomes predict` writes ML-model tool outcome reviews. Human correction, imported reviews, and local ML predictions are not blocked by the automatic LLM claim guard.
- `tool-outcomes train-reviewer` trains from training-eligible tool outcome reviews and records tool outcome reviewer model metadata under `tool_outcome_reviewer_models`.
- `review-jobs` manages stored recurring review job configuration and run history; read commands are read-only and explicit run-now/resume actions only mark work due for the scheduler. `review-jobs set --max-judge-total-tokens` stores the scheduler run token cap as `max_review_total_tokens`.
- The sidecar schema is the clean turn-case model: `source_sessions`, `turn_cases`, `case_events`, `tool_interactions`, `case_signals`, `case_reviews`, `case_findings`, `tool_outcome_cases`, `tool_outcome_reviews`, `automatic_llm_review_claims`, `tool_outcome_reviewer_models`, plus review job/run/feedback/state tables.

## Core Rules

- Evaluator state lives under the Hermes profile in `instruction-health/`.
- No active compatibility aliases, old command aliases, old JSON duplicate keys, or old sidecar tables are part of the clean schema.
- Import, review, scheduler execution, and model training require explicit CLI or dashboard actions.
- Case evidence is extracted and stored before LLM judgment.

## Flows And States

- Init flow creates config/log paths and migrates a clean sidecar database.
- Import flow discovers Hermes sessions, normalizes review-domain rows, and replaces case signals for imported turn cases.
- Review flow loads due turn cases, applies priority and budget gates, acquires an automatic LLM claim immediately before any automatic LLM call, stores case reviews/findings, then optionally reviews eligible tool outcome cases with the same claim guard.
- Scheduler flow claims due review jobs, imports Hermes sessions, runs the same claim-gated review path, heartbeats the lease, stores review run metrics, and schedules the next due time.

## Contracts

- The placeholder console command is `agent-health`.
- The default database path is `<hermes-home>/instruction-health/evals.db`.
- The recorded schema version state is `turn_case_review_schema_v1`.
- Tool outcome review labels are `problem`, `ok`, and `unsure`.
- `--reevaluate` may revisit non-automatic state, but it cannot rerun automatic LLM reviews or bypass automatic LLM claims.

## Product Decisions

- Decision (2026-05-30): The sidecar schema uses the clean turn-case and tool-outcome review model with no old-table compatibility.
- Decision (2026-05-30): CLI command spelling follows the review-domain model and old aliases are not retained.
- Decision (2026-05-30): Automatic LLM judging is fail-closed for both turn cases and tool outcome cases; prior automatic LLM review history or claim history blocks another automatic LLM call for that target or its paired parent/child turn context.
- Decision (2026-05-30): Automatic LLM claim failures prefer skipped coverage over duplicate LLM spend.

## Rationale

The local runtime is easier to audit when CLI verbs, database tables, JSON schemas, and scheduler metrics use the same domain nouns. Keeping the guard as a one-way claim and selection rule rather than a cleanup rule preserves review history while preventing repeat automatic LLM spend.

## Non-Goals

- This doc does not own dashboard visualization details.
- This doc does not define deployment migration from older development databases.

## Maintenance Notes

Update this doc when CLI commands, database tables, scheduler metrics, review job behavior, or local storage contracts change.
