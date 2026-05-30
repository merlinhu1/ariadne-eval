# Ariadne Eval V1 Simplified Design

Date: 2026-05-19
Status: active V1 direction
Original draft: `research/agent_instruction_health_evaluator_design1.md`

## Decision

V1 should be as simple as possible, but the **LLM judge stays in V1**. The judge is essential because case signals can surface evidence, but they do not reliably assign `succeed`, `failed`, `mishandled`, or `prolonged` on their own.

The simplification is: **read Hermes `state.db` directly and drop the passive hook plugin for now**.

The passive Ariadne/Hermes hook plugin from the original draft is deferred. It may become useful later for exact tool timings, approval events, interruption evidence, or dirty-session markers, but it is not required for the first useful product.

## V1 Pipeline

```text
Hermes state.db
  -> schema-tolerant Hermes reader
  -> one turn case per user message
  -> state.db-derived tool-message evidence
  -> case signals and specific tool outcome categories
  -> optional tiny non-LLM classifier evidence when configured
  -> compact judge input
  -> LLM judge through existing Hermes provider/model config
  -> structured status + findings
  -> local evals.db
  -> CLI inspection and Hermes dashboard tab
```

## Judge Trigger

The LLM judge is triggered by a manual CLI batch or by explicitly configured recurring review jobs.

The V1 control flow should be:

```bash
agent-health import hermes --since 24h
agent-health review --due --limit 50
agent-health review-jobs set default --enabled --every 3600 --no-gap
agent-health review-jobsr run --poll-seconds 600
```

`review --due` loads imported turn cases that need judging, builds compact judge inputs, calls the judge through Hermes provider/model resolution, then stores `case_reviews` and finding rows. Recurring tasks store schedule, import, candidate, budget, cooldown, and threshold settings in `evals.db`; each scheduler run leases one task, snapshots the effective config into `review_runs`, imports Hermes state, judges due request cases, shares the same budget with tool outcome reviews, and records the next due time.

## V1 Components

1. `HermesStateReader`
   - Reads sessions and messages from a configured Hermes `state.db`.
   - Selects only available columns.
   - Excludes hidden/provider reasoning fields.

2. Normalizer
   - Creates one turn case per user message.
   - Finds the next assistant response.
   - Captures tool messages between request and response.
   - Captures the next user message as reaction evidence.

3. Case signal extractor
   - Tool call count.
   - API call count.
   - Turn duration from message timestamps.
   - Tool error count from tool result text.
   - Specific tool outcome categories from high-precision rules and an optional tiny non-LLM classifier.
   - Repeated tool/action evidence where available.
   - Next-user reaction classification.
   - Assistant completion-claim heuristic.

4. LLM judge
   - Uses the existing Hermes provider/model path by default.
   - Consumes the normalized turn case, compact trace evidence, case signals, optional classifier-derived tool outcome evidence, and next-user reaction.
   - Returns strict JSON with one health status, confidence, primary reason, and findings.
   - Stores provider/model metadata so judge behavior is auditable.

5. Sidecar SQLite
   - `$HERMES_HOME/instruction-health/evals.db`.
   - Tables needed in V1: `turn_cases`, `case_events`, `case_signals`, `case_reviews`, `findings`, `tool_outcome_cases`, `tool_outcome_reviews`, `tool_outcome_reviews`, `tool_outcome_reviewer_models`, `review_jobs`, `review_runs`, `review_job_cursors`, `review_state`.

6. CLI
   - `init`.
   - `inspect hermes`.
   - `import hermes`.
   - `cases`.
   - `case-signals`.
   - `review --due`.
   - `scheduler tick`, `scheduler run`, and `schedule` task-management subcommands.
   - `list`, `show`, and `summary` over judged results.
   - `dashboard install` to install the opt-in Hermes tab.

7. Hermes dashboard tab
   - Installs into `$HERMES_HOME/plugins/ariadne-eval/dashboard`.
   - Exposes read-only `/summary` and `/cases/{turn_case_id}` plugin API routes.
   - Visualizes request friction, requests needing attention, statuses, tool outcomes, findings, token totals, finding timeline buckets, and secondary session groups from `evals.db`.
   - Does not import sessions or call the judge on page load; explicit task controls may pause, resume, or mark a task due now.

## Explicitly Deferred

- Hermes plugin / passive hook capture.
- `events.jsonl` runtime event cache.
- Exact tool start/end duration from hooks.
- Approval/interruption runtime events.
- Tiny classifier as a replacement for the LLM judge's final status assignment.
- Standalone web/TUI dashboard outside Hermes.
- Non-Hermes adapters.
- Automatic prompt/memory/skill changes.

## Why This Is Enough For V1

Hermes `state.db` already contains the durable conversation record. A state.db-only ingestion path works on historical sessions, has no installation side effects inside Hermes, avoids hook fragility, avoids runtime overhead, and keeps the first debugging loop short.

The LLM judge is what turns evidence into ratings. Case signals remain first-class because they make the judge auditable and keep the CLI useful if a judge call fails.
