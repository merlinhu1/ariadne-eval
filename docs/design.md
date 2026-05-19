# Ariadne Eval V1 Simplified Design

Date: 2026-05-19
Status: active V1 direction
Original draft: `research/agent_instruction_health_evaluator_design1.md`

## Decision

V1 should be as simple as possible, but the **LLM judge stays in V1**. The judge is essential because deterministic signals can surface evidence, but they do not reliably assign `succeed`, `failed`, `mishandled`, `prolonged`, or `not_evaluable` on their own.

The simplification is: **read Hermes `state.db` directly and drop the passive hook plugin for now**.

The passive Ariadne/Hermes hook plugin from the original draft is deferred. It may become useful later for exact tool timings, approval events, interruption evidence, or dirty-session markers, but it is not required for the first useful product.

## V1 Pipeline

```text
Hermes state.db
  -> schema-tolerant Hermes reader
  -> one eval unit per user message
  -> state.db-derived tool-message evidence
  -> deterministic signals
  -> compact judge input
  -> LLM judge through existing Hermes provider/model config
  -> structured status + anomalies
  -> local evals.db
  -> CLI inspection and Hermes dashboard tab
```

## Judge Trigger

The LLM judge is triggered by a manual CLI batch, not by a built-in scheduler.

The V1 control flow should be:

```bash
agent-health import hermes --since 24h
agent-health eval --due --limit 50
```

`eval --due` loads imported eval units that need judging, builds compact judge inputs, calls the judge through Hermes provider/model resolution, then stores `llm_evals` and anomaly rows. A future scheduler can wrap this command with cron/systemd, but scheduling is not itself a product component for V1.

## V1 Components

1. `HermesStateReader`
   - Reads sessions and messages from a configured Hermes `state.db`.
   - Selects only available columns.
   - Excludes hidden/provider reasoning fields.

2. Normalizer
   - Creates one eval unit per user message.
   - Finds the next assistant response.
   - Captures tool messages between request and response.
   - Captures the next user message as reaction evidence.

3. Deterministic signal extractor
   - Tool call count.
   - API call count.
   - Turn duration from message timestamps.
   - Tool error count from tool result text.
   - Repeated tool/action evidence where available.
   - Next-user reaction classification.
   - Assistant completion-claim heuristic.

4. LLM judge
   - Uses the existing Hermes provider/model path by default.
   - Consumes the normalized eval unit, compact trace evidence, deterministic signals, and next-user reaction.
   - Returns strict JSON with one health status, confidence, primary reason, and anomalies.
   - Stores provider/model metadata so judge behavior is auditable.

5. Sidecar SQLite
   - `$HERMES_HOME/instruction-health/evals.db`.
   - Tables needed in V1: `eval_units`, `trace_events`, `deterministic_signals`, `llm_evals`, `anomalies`, `eval_state`.

6. CLI
   - `init`.
   - `inspect hermes`.
   - `import hermes`.
   - `units`.
   - `signals`.
   - `eval --due`.
   - `list`, `show`, and `summary` over judged results.
   - `dashboard install` to install the opt-in Hermes tab.

7. Hermes dashboard tab
   - Installs into `$HERMES_HOME/plugins/ariadne-eval/dashboard`.
   - Exposes read-only `/summary` and `/units/{eval_unit_id}` plugin API routes.
   - Visualizes statuses, incidents, anomalies, token totals, timeline buckets, and hot sessions from `evals.db`.
   - Does not import sessions or call the judge.

## Explicitly Deferred

- Hermes plugin / passive hook capture.
- `events.jsonl` runtime event cache.
- Exact tool start/end duration from hooks.
- Approval/interruption runtime events.
- Built-in scheduler or daemon. A future cron/systemd entry may call `agent-health eval --due`, but the manual CLI command is the V1 trigger.
- Standalone web/TUI dashboard outside Hermes.
- Non-Hermes adapters.
- Automatic prompt/memory/skill changes.

## Why This Is Enough For V1

Hermes `state.db` already contains the durable conversation record. A state.db-only ingestion path works on historical sessions, has no installation side effects inside Hermes, avoids hook fragility, avoids runtime overhead, and keeps the first debugging loop short.

The LLM judge is what turns evidence into ratings. Deterministic signals remain first-class because they make the judge auditable and keep the CLI useful if a judge call fails.
