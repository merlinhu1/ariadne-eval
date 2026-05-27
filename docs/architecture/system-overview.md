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

Ariadne Eval V1 is a local, state.db-ingested instruction-health evaluator for Hermes Agent sessions. It includes an LLM judge for final ratings, first-class recurring eval task configuration, and an opt-in Hermes dashboard tab for visualization and explicit user actions.

## Main Components

- **CLI**: initializes local state, inspects Hermes sessions, imports eval units, lists units, shows deterministic signals, runs due judge evaluations, manages recurring eval tasks, and queries judged results.
- **Hermes state reader**: reads Hermes `state.db` sessions and messages without importing hidden reasoning fields.
- **Normalizer**: converts each user request into one evaluation unit with the assistant response, tool-message evidence, prior context, and next user reaction when available.
- **Signal extractor**: computes deterministic evidence such as tool errors, specific incident categories, repeated calls, duration, reaction type, and optional tiny non-LLM classifier predictions.
- **LLM judge**: consumes preflight-trimmed normalized evidence and assigns the health status, confidence, primary reason, and anomalies.
- **Scheduler worker**: claims due eval tasks with SQLite leases, imports Hermes state, runs the same due-eval path as manual batches, records run snapshots, and schedules the next run.
- **Sidecar eval DB**: stores normalized units, state.db-derived trace/tool events, deterministic signals, judge results, anomalies, recurring task configuration, task cursors, run leases/history, and eval state in local SQLite.
- **Hermes dashboard plugin**: an opt-in tab that visualizes sidecar summary/detail data and exposes explicit recurring-task controls through plugin API routes.

## V1 Architecture

```text
Hermes state.db -> reader -> normalizer -> signal/rule extractor -> optional tiny ML -> preflight trim/prefilter -> LLM judge
                                 |                                                                     |
                                 +------------------------- persisted evidence -----------------------+
                                                                                      v
                                                                                   evals.db
                                                                                 /   |      \
                                                                               CLI scheduler Hermes dashboard tab
```

## Judge Trigger

Ariadne Eval supports both the manual CLI batch command, `agent-health eval --due`, and recurring eval tasks driven by `agent-health scheduler tick` or `agent-health scheduler run --poll-seconds N`. The scheduler stores task configuration in `eval_tasks`, claims at most one run per task with SQLite leases, snapshots the effective config into `eval_runs`, imports Hermes sessions, and then uses the same due-unit judging semantics as manual eval batches. The judge inherits Hermes model routing by trying configured `auxiliary.approval` first, then the Hermes main provider/model. Budget guardrails keep runs small: no realtime page-load calls, no calls from import/list/show/signals, no re-judging successfully evaluated units unless requested, deterministic priority prefiltering before judging, aggressive trimming of bulky docs/code/images/tool previews, default max 5 judge calls per run, optional total-token caps, and a 120-minute cooldown before judging no-reaction last turns.

## Boundaries

- V1 does not require a passive Hermes hook plugin.
- V1 does not create an `events.jsonl` hook cache.
- V1 schedules background jobs only through explicit recurring eval tasks and the scheduler CLI; dashboard page load does not silently import, evaluate, or train.
- V1 includes an opt-in Hermes dashboard tab that reads `evals.db`; it is not a standalone UI or evaluator.
- V1 does not introduce a broad non-Hermes adapter framework.
- V1 does include an LLM judge because ratings need semantic interpretation beyond deterministic counters.
- Optional tiny non-LLM classifiers may contribute incident-category evidence, but they do not assign the final health status.

## Product Decisions

- Decision (2026-05-19): V1 ingestion is state.db-only; passive hook capture is deferred until the state.db evaluator proves useful.
- Decision (2026-05-19): V1 includes LLM judging via existing Hermes provider/model config so there is no separate evaluator API key; judge routing prefers configured `auxiliary.approval`, then main.
- Decision (2026-05-24): Recurring eval tasks are first-class SQLite configuration and run history. Config updates increment `config_version`; running leases preserve per-task concurrency 1; each run uses a config snapshot and records stop reasons such as `no_due`, `budget_exhausted`, `completed`, and `error`.
- Decision (2026-05-19): V1 stores eval units, trace events derived from state.db messages, deterministic signals, LLM evals, anomalies, and eval state.
- Decision (2026-05-19): Visualization starts inside the existing Hermes dashboard as a read-only plugin tab backed by SQLite, not as a standalone UI.
- Decision (2026-05-20): Tiny non-LLM incident classifiers are an optional evidence layer between deterministic rules and the LLM judge, not a replacement for judge ratings.

## Rationale

Hermes already stores enough durable session data for the first useful evaluator. Avoiding passive hook capture, a scheduler, standalone UI, and generic adapters keeps installation and debugging simple. Reusing the Hermes dashboard shell gives the visualization a first-class tab without creating a second evaluator path. Keeping the LLM judge preserves the core product value: rating each turn's health.
