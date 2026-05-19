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

Ariadne Eval V1 is a local, state.db-ingested instruction-health evaluator for Hermes Agent sessions. It includes an LLM judge for final ratings and an opt-in read-only Hermes dashboard tab for visualization.

## Main Components

- **CLI**: initializes local state, inspects Hermes sessions, imports eval units, lists units, shows deterministic signals, runs due judge evaluations, and queries judged results.
- **Hermes state reader**: reads Hermes `state.db` sessions and messages without importing hidden reasoning fields.
- **Normalizer**: converts each user request into one evaluation unit with the assistant response, tool-message evidence, prior context, and next user reaction when available.
- **Signal extractor**: computes deterministic evidence such as tool errors, repeated calls, duration, and reaction type.
- **LLM judge**: consumes preflight-trimmed normalized evidence and assigns the health status, confidence, primary reason, and anomalies.
- **Sidecar eval DB**: stores normalized units, state.db-derived trace/tool events, deterministic signals, judge results, anomalies, and eval state in local SQLite.
- **Hermes dashboard plugin**: an opt-in read-only tab that visualizes sidecar summary/detail data through plugin API routes.

## V1 Architecture

```text
Hermes state.db -> reader -> normalizer -> signal extractor -> preflight trim/prefilter -> LLM judge
                                 |                                                   |
                                 +---------------- persisted evidence ---------------+
                                                                                     v
                                                                                  evals.db
                                                                                 /        \
                                                                               CLI   Hermes dashboard tab
```

## Judge Trigger

Ariadne Eval does not need a resident scheduler to run the LLM judge. V1 exposes a manual CLI batch command, `agent-health eval --due`, that triggers judging for due imported eval units. The judge inherits Hermes model routing by trying configured `auxiliary.compression` first, then the Hermes main provider/model. Budget guardrails keep the default run small: no realtime calls, no calls from import/list/show/signals, no re-judging successfully evaluated units unless requested, deterministic priority prefiltering before judging, aggressive trimming of bulky docs/code/images/tool previews, max 5 judge calls per invocation by default, and a 120-minute cooldown before judging no-reaction last turns. Automation can be added later by having cron/systemd invoke the same command.

## Boundaries

- V1 does not require a passive Hermes hook plugin.
- V1 does not create an `events.jsonl` hook cache.
- V1 does not schedule background jobs.
- V1 includes an opt-in Hermes dashboard tab that reads `evals.db`; it is not a standalone UI or evaluator.
- V1 does not introduce a broad non-Hermes adapter framework.
- V1 does include an LLM judge because ratings need semantic interpretation beyond deterministic counters.

## Product Decisions

- Decision (2026-05-19): V1 ingestion is state.db-only; passive hook capture is deferred until the state.db evaluator proves useful.
- Decision (2026-05-19): V1 includes LLM judging via existing Hermes provider/model config so there is no separate evaluator API key; judge routing prefers configured `auxiliary.compression`, then main.
- Decision (2026-05-19): Judge batches are manually triggered and budget-gated; default `eval --due` considers 10 candidates, judges only deterministic-priority units, performs at most 5 judge calls, and defers no-reaction turns for 120 minutes.
- Decision (2026-05-19): V1 stores eval units, trace events derived from state.db messages, deterministic signals, LLM evals, anomalies, and eval state.
- Decision (2026-05-19): Visualization starts inside the existing Hermes dashboard as a read-only plugin tab backed by SQLite, not as a standalone UI.

## Rationale

Hermes already stores enough durable session data for the first useful evaluator. Avoiding passive hook capture, a scheduler, standalone UI, and generic adapters keeps installation and debugging simple. Reusing the Hermes dashboard shell gives the visualization a first-class tab without creating a second evaluator path. Keeping the LLM judge preserves the core product value: rating each turn's health.
