# Ariadne Eval Domain Naming Cleanup Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace the current mixed `eval unit` / `request` / `incident` / `anomaly` vocabulary with one coherent review-domain model.

**Architecture:** Treat this as a deep refactor/rewrite of the evaluation domain, not a compatibility-preserving rename. Build a clean SQLite schema around `turn_case` as the request-level review target, `case_review` as the request-level judgment, `case_finding` as the request-level judgment output, `tool_interaction` as the normalized tool call/result pair, `tool_outcome_case` as the tool-level review target, and `tool_outcome_review` as the tool-level judgment. Backward compatibility is intentionally out of scope: replace DB tables, fields, APIs, CLI output, tests, prompts, dashboard payloads, model artifacts, and docs with the new model.

**Tech Stack:** Python, SQLite, unittest, Ariadne Eval CLI/dashboard docs.

---

## Closed Decisions

1. **This is a deep refactor/rewrite, not just a rename.** Prefer the clean domain model over preserving the current table boundaries.
2. **Persist `tool_interactions`.** The clean model has a normalized table for tool call/result pairs instead of embedding the same tool evidence only in events and tool-outcome cases.
3. **Do not support old DB compatibility.** The project has not deployed yet. Delete/recreate development `evals.db`; do not write migrations, views, compatibility shims, dual JSON keys, or old CLI aliases.
4. **Use `schema_version` in LLM prompt/result JSON.** Keep the existing version-discriminator shape while changing the version values to `turn_case_review_v1` and `tool_outcome_review_v1`.
5. **Preserve existing `tool_outcome_reviews` when `case_reviews` change.** The automatic LLM guard blocks future automatic tool-outcome LLM calls; it never clears, resets, deletes, relabels, invalidates, or marks stale previous tool-outcome reviews.
6. **Rename model artifacts cleanly.** Old incident model artifacts are disposable and should not be loaded by new code.

---

## Naming Principles

1. **One canonical target noun:** `turn_case` is the request-level thing being reviewed.
2. **One canonical action noun:** `review` is the judgment event/result over a target.
3. **One canonical output noun:** `finding` is a problem/evidence item produced by a request-level review.
4. **Tool-layer terms stay tool-scoped:** use `tool_interaction` / `tool_outcome_*`; do not call them request incidents or anomalies.
5. **No overloaded `eval`:** keep `eval` only in project/product names, not table/entity names.
6. **No `incident` in persisted or active runtime names:** replace with `tool_outcome`.
7. **No `anomaly` in persisted or active runtime names:** replace with `finding`; anomaly sounds like statistical detection, but current rows are judge-reported issues.
8. **No compatibility aliases:** no views, duplicate JSON keys, deprecated CLI flags, old dashboard routes, old prompt schema names, or old Python API wrappers.
9. **Plural table names, singular IDs:** tables are plural (`turn_cases`), foreign keys are singular (`turn_case_id`).
10. **Fields say what they store:** `request_text`, `response_text`, `review_json`, `prompt_tokens`, not ambiguous `eval_json` or `judge_*` everywhere.
11. **Source-boundary names are allowed only at the boundary:** `user_turn` may appear in normalizer/source-boundary code, but persisted review-domain code uses `turn_case`.

---

## Canonical Vocabulary

| Current term | New term | Why |
|---|---|---|
| source session string repeated on units | source session | Persist normalized source-session metadata once. |
| eval unit | turn case | The row is one user-turn case for review, not a generic evaluation unit. |
| request | user turn / request text | A request is text inside the case, not the case itself. |
| request boundary | user turn boundary | Source-normalization concept only; not a persisted review target. |
| tool call | tool interaction | The normalized pair is a call plus its immediate result, not just a call. |
| trace event | case event | It is evidence attached to a turn case. |
| deterministic signal | case signal | Signal is evidence for one case. |
| LLM eval | case review | It is a judgment record over a turn case; reviewer can be LLM/human/imported later. |
| anomaly | case finding | Findings are review outputs; not all are statistical anomalies. |
| incident example | tool outcome case | The review target is one tool interaction plus context, not a broad incident. |
| incident label | tool outcome review | The judgment event over a tool outcome case. The label is only one field on the review. |
| incident prediction | tool outcome review | ML predictions are also reviews, with `reviewer_type='ml_model'`; do not keep a second prediction concept. |
| incident model | tool outcome reviewer model | Classifier used as a tool-outcome reviewer. |
| eval feedback | review feedback | Human correction/feedback over reviews, cases, findings, or tool outcomes. |
| eval task | review job | Scheduled work definition. |
| eval run | review run | One execution of a review job. |
| eval state | review_state | Sidecar operational state. |

---

## Relationship Model

Persisted model:

```text
source_sessions
  -> turn_cases
      -> case_events
      -> tool_interactions
          -> tool_outcome_cases
              -> tool_outcome_reviews
      -> case_signals
      -> case_reviews
          -> case_findings
```

Logical-only source boundary:

```text
user_turn = the source user-message boundary that a turn_case is derived from
```

Rules:

- Do **not** add a separate `user_turns` table. `user_turn` is explanatory/source-normalizer language only.
- Add `source_sessions` as the normalized imported-session table instead of repeating all source-session metadata on every case.
- `turn_case` is the persisted request-level review target.
- One `turn_case` is derived from one source user turn, plus normalized response/context/evidence.
- `tool_interaction` is the persisted tool call/result pair attached to a turn case.
- `tool_outcome_case` is the narrower tool-level review target derived from a tool interaction plus review context.
- `case_review` is the judgment over a `turn_case`.
- `tool_outcome_review` is the judgment over a `tool_outcome_case`.
- `case_finding` is a child evidence item from a `case_review`.
- `tool_outcome_review` usually does not need child findings; it carries `outcome_label`, `reason_code`, `confidence`, and `evidence_summary` directly.
- A `tool_outcome_case` linked to a `turn_case` that already has an automatic LLM `case_review` must not be sent to an automatic LLM reviewer again.
- Updating, replacing, or force-refreshing a `case_review` must **not** clear, reset, delete, relabel, invalidate, or mark stale existing `tool_outcome_reviews`. The guard prevents future automatic LLM tool-outcome review selection; it is not a cleanup rule for reviews that already exist.

---

## Target Table Model

This section is the target contract. Implement the clean schema directly; do not keep old table names, views, migrations, or aliases.

### `source_sessions`

Normalized imported-session metadata.

Required columns:

- `id TEXT PRIMARY KEY` — canonical source session ID used by cases.
- `framework TEXT NOT NULL`
- `source TEXT`
- `model TEXT`
- `title TEXT`
- `parent_session_id TEXT`
- `started_at TEXT`
- `ended_at TEXT`
- `input_tokens INTEGER`
- `output_tokens INTEGER`
- `source_payload_json TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Indexes:

- `idx_source_sessions_framework_started_at(framework, started_at)`
- `idx_source_sessions_parent(parent_session_id)`

### `turn_cases`

Request-level review targets.

Required columns:

- `id TEXT PRIMARY KEY`
- `source_session_id TEXT NOT NULL REFERENCES source_sessions(id) ON DELETE CASCADE`
- `turn_index INTEGER NOT NULL`
- `request_message_id TEXT`
- `response_message_id TEXT`
- `next_request_message_id TEXT`
- `request_text TEXT NOT NULL`
- `response_text TEXT`
- `prior_context_summary TEXT`
- `next_request_text TEXT`
- `tool_interaction_count INTEGER NOT NULL DEFAULT 0` — per-turn count from `tool_interactions`.
- `source_session_api_interaction_count INTEGER` — source-session aggregate copied from source data when present; do not use the misleading per-case name `api_interaction_count`.
- `case_builder_version TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Constraints/indexes:

- `UNIQUE(source_session_id, turn_index)`
- `idx_turn_cases_source_session(source_session_id)`
- `idx_turn_cases_created_at(created_at)`

### `case_events`

Evidence events attached to a turn case.

Required columns:

- `id TEXT PRIMARY KEY`
- `turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE`
- `source_event_id TEXT`
- `event_type TEXT NOT NULL`
- `event_at TEXT`
- `tool_interaction_id TEXT REFERENCES tool_interactions(id) ON DELETE SET NULL`
- `tool_name TEXT`
- `input_hash TEXT`
- `input_preview TEXT`
- `output_hash TEXT`
- `output_preview TEXT`
- `output_error TEXT`
- `duration_ms INTEGER`
- `source_payload_json TEXT`

Constraints/indexes:

- `idx_case_events_turn_case(turn_case_id)`
- `idx_case_events_tool_interaction(tool_interaction_id)`
- `idx_case_events_type(event_type)`

### `tool_interactions`

Normalized tool call/result pairs. This is a real persisted table in the clean refactor.

Required columns:

- `id TEXT PRIMARY KEY`
- `turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE`
- `call_case_event_id TEXT REFERENCES case_events(id) ON DELETE SET NULL`
- `result_case_event_id TEXT REFERENCES case_events(id) ON DELETE SET NULL`
- `source_tool_call_id TEXT`
- `tool_name TEXT NOT NULL`
- `tool_input_text TEXT`
- `tool_input_hash TEXT`
- `tool_input_preview TEXT`
- `tool_output_text TEXT`
- `tool_output_hash TEXT`
- `tool_output_preview TEXT`
- `tool_output_error TEXT`
- `called_at TEXT`
- `completed_at TEXT`
- `duration_ms INTEGER`
- `source_payload_json TEXT`
- `created_at TEXT NOT NULL`

Constraints/indexes:

- `UNIQUE(turn_case_id, source_tool_call_id)` when `source_tool_call_id IS NOT NULL`.
- `idx_tool_interactions_turn_case(turn_case_id)`
- `idx_tool_interactions_tool_name(tool_name)`

Builder rule:

- Build `tool_interactions` by pairing assistant tool-call messages with their immediate tool-result messages.
- Link related `case_events` to `tool_interactions` through `tool_interaction_id` when a source event represents the call or result.
- `tool_outcome_cases` reference `tool_interactions`; they should not duplicate call/result pairing logic.

### `case_signals`

Deterministic evidence signals attached to a turn case.

Required columns:

- `id TEXT PRIMARY KEY`
- `turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE`
- `signal_type TEXT NOT NULL`
- `signal_value TEXT`
- `score REAL`
- `severity TEXT`
- `evidence_text TEXT`
- `case_event_id TEXT REFERENCES case_events(id) ON DELETE SET NULL`
- `source_payload_json TEXT`
- `created_at TEXT NOT NULL`

Indexes:

- `idx_case_signals_turn_case(turn_case_id)`
- `idx_case_signals_type(signal_type)`

### `case_reviews`

Judgments over `turn_cases`.

Required columns:

- `id TEXT PRIMARY KEY`
- `turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE`
- `reviewer_type TEXT NOT NULL` — `automatic_llm`, `human`, `imported`.
- `review_scope TEXT NOT NULL DEFAULT 'turn_case'`
- `review_prompt_version TEXT`
- `reviewer_provider TEXT`
- `reviewer_model TEXT`
- `outcome_status TEXT NOT NULL` — use `TURN_OUTCOME_STATUSES`.
- `summary_reason TEXT`
- `confidence TEXT`
- `friction_score REAL`
- `review_json TEXT NOT NULL`
- `review_error TEXT`
- `prompt_tokens INTEGER`
- `completion_tokens INTEGER`
- `total_tokens INTEGER`
- `review_call_count INTEGER NOT NULL DEFAULT 0`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Constraints/indexes:

- Partial unique index: one row per `turn_case_id` where `reviewer_type='automatic_llm'` and `review_scope='turn_case'`.
- `idx_case_reviews_turn_case(turn_case_id)`
- `idx_case_reviews_reviewer_type(reviewer_type)`
- `idx_case_reviews_created_at(created_at)`

Write-scope rule:

- `insert_case_review` / `upsert_case_review` may insert/update `case_reviews` and replace that review's child `case_findings`.
- It must never mutate `tool_outcome_reviews`, even when the request-level case review is force-refreshed.

### `case_findings`

Child outputs from request-level reviews.

Required columns:

- `id TEXT PRIMARY KEY`
- `case_review_id TEXT NOT NULL REFERENCES case_reviews(id) ON DELETE CASCADE`
- `turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE`
- `finding_type TEXT NOT NULL`
- `severity TEXT`
- `evidence_text TEXT`
- `evidence_source TEXT`
- `case_event_id TEXT REFERENCES case_events(id) ON DELETE SET NULL`
- `created_at TEXT NOT NULL`

Indexes:

- `idx_case_findings_review(case_review_id)`
- `idx_case_findings_turn_case(turn_case_id)`
- `idx_case_findings_type(finding_type)`

### `tool_outcome_cases`

Tool-level review targets derived from `tool_interactions`.

Required columns:

- `id TEXT PRIMARY KEY`
- `turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE`
- `tool_interaction_id TEXT NOT NULL REFERENCES tool_interactions(id) ON DELETE CASCADE`
- `turn_index INTEGER NOT NULL`
- `request_excerpt TEXT`
- `prior_response_excerpt TEXT`
- `following_response_excerpt TEXT`
- `caller_expectation_text TEXT`
- `caller_interpretation_text TEXT`
- `intent_source TEXT`
- `case_builder_version TEXT NOT NULL`
- `source_payload_json TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Constraints/indexes:

- `UNIQUE(tool_interaction_id)` — one tool-outcome case per normalized interaction in this refactor.
- `idx_tool_outcome_cases_turn_case(turn_case_id)`
- `idx_tool_outcome_cases_tool_interaction(tool_interaction_id)`

### `tool_outcome_reviews`

Judgments over `tool_outcome_cases`. This table replaces both old labels and old predictions.

Required columns:

- `id TEXT PRIMARY KEY`
- `tool_outcome_case_id TEXT NOT NULL REFERENCES tool_outcome_cases(id) ON DELETE CASCADE`
- `turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE` — denormalized from the parent case for guard/audit queries.
- `reviewer_type TEXT NOT NULL` — `human`, `human_correction`, `automatic_llm`, `ml_model`, `rule`, `imported`.
- `reviewer_name TEXT`
- `reviewer_version TEXT`
- `review_source_detail TEXT` — examples: `human_correction`, `ml_model_defer`, `ml_model_budget_fallback`.
- `outcome_label TEXT NOT NULL` — `problem`, `ok`, `unsure`.
- `reason_code TEXT` — `execution_error`, `empty_output`, `invalid_tool_input`, `wrong_or_bad_output`, `other`.
- `confidence REAL`
- `uncertainty REAL`
- `evidence_summary TEXT`
- `evidence_json TEXT`
- `training_eligible INTEGER NOT NULL DEFAULT 0`
- `training_weight REAL`
- `needs_llm_review INTEGER NOT NULL DEFAULT 0`
- `llm_review_budget_available INTEGER`
- `budget_fallback INTEGER NOT NULL DEFAULT 0`
- `created_at TEXT NOT NULL`

Constraints/indexes:

- `idx_tool_outcome_reviews_case(tool_outcome_case_id)`
- `idx_tool_outcome_reviews_turn_case(turn_case_id)`
- `idx_tool_outcome_reviews_reviewer_type(reviewer_type)`
- `idx_tool_outcome_reviews_training(training_eligible)`

Latest-row semantics:

- Dashboard/API should expose latest human-like review, latest ML review, latest training-eligible review, and latest displayed review as separate concepts.
- Do not collapse ML review, human correction, and automatic LLM review into one ambiguous `latest_label` field.

Automatic LLM guard contract:

- Automatic LLM tool-outcome review candidate selection must exclude any `tool_outcome_case` whose `turn_case_id` already has a `case_reviews` row with `reviewer_type='automatic_llm'` and `review_scope='turn_case'`.
- Re-check the same exclusion immediately before making an automatic LLM tool-outcome reviewer call. This protects scheduler/CLI races where a case review is written after candidate selection.
- Existing `tool_outcome_reviews` are append-only review records for this cleanup. Writing or refreshing a `case_review` never deletes, resets, relabels, invalidates, or marks stale previous `tool_outcome_reviews`.
- Human, imported, rule, and ML-model `tool_outcome_reviews` are not blocked by the automatic LLM guard. The guard only prevents additional automatic LLM calls for a turn case already reviewed at request level.

### `tool_outcome_reviewer_models`

Classifier model metadata for ML tool-outcome reviews.

Required columns:

- `id TEXT PRIMARY KEY`
- `model_name TEXT NOT NULL`
- `model_version TEXT NOT NULL`
- `artifact_path TEXT NOT NULL`
- `feature_schema_version TEXT NOT NULL` — `tool_outcome_features_v1`.
- `review_schema_version TEXT NOT NULL` — `tool_outcome_review_v1`.
- `training_summary_json TEXT`
- `promoted_at TEXT`
- `created_at TEXT NOT NULL`

Artifact policy:

- `incident-models/` becomes `tool-outcome-reviewer-models/`.
- `incident-model.pkl` becomes `tool-outcome-reviewer-model.pkl`.
- `incident_features_v1` becomes `tool_outcome_features_v1`.
- `ariadne_ml_first_incident_model_v1` becomes `ariadne_tool_outcome_reviewer_model_v1`.
- New code must not load old incident artifacts. Delete/retrain artifacts after the clean refactor.

### Operational tables

Rename operational tables directly:

| Current table | New table | Notes |
|---|---|---|
| `eval_feedback` | `review_feedback` | Feedback can target cases, reviews, findings, tool-outcome cases, or tool-outcome reviews. |
| `eval_tasks` | `review_jobs` | Scheduled work definition. |
| `eval_runs` | `review_runs` | One execution of a review job. |
| `eval_task_cursors` | `review_job_cursors` | Cursor state for review jobs. |
| `eval_state` | `review_state` | Sidecar operational key/value state. |

---

## Old-to-New Table Mapping

| Current table | New table |
|---|---|
| repeated source-session fields on `eval_units` | `source_sessions` |
| `eval_units` | `turn_cases` |
| `trace_events` | `case_events` plus normalized `tool_interactions` |
| `deterministic_signals` | `case_signals` |
| `llm_evals` | `case_reviews` |
| `anomalies` | `case_findings` |
| `incident_eval_examples` | `tool_outcome_cases` |
| `incident_labels` | `tool_outcome_reviews` |
| `incident_predictions` | `tool_outcome_reviews` with `reviewer_type='ml_model'` |
| `incident_models` | `tool_outcome_reviewer_models` |
| `eval_feedback` | `review_feedback` |
| `eval_tasks` | `review_jobs` |
| `eval_runs` | `review_runs` |
| `eval_task_cursors` | `review_job_cursors` |
| `eval_state` | `review_state` |

---

## Python/API Renames

| Current function/constant pattern | New pattern |
|---|---|
| `normalize_eval_units` | `build_turn_cases` |
| `normalize_incident_examples` | `build_tool_interactions` + `build_tool_outcome_cases` |
| `RequestBoundary` | `UserTurnBoundary` |
| `detect_request_boundaries` | `detect_user_turn_boundaries` |
| `upsert_eval_unit` | `upsert_turn_case` |
| `list_due_units` | `list_due_turn_cases` |
| `insert_llm_eval` | `insert_case_review` / `upsert_case_review` |
| `list_llm_evals` | `list_case_reviews` |
| `insert_incident_label` | `insert_tool_outcome_review` |
| `list_incident_examples` | `list_tool_outcome_cases` |
| `insert_incident_prediction` | `insert_tool_outcome_review` with `reviewer_type='ml_model'` |
| `REQUEST_HEALTH_LABELS` | `TURN_OUTCOME_STATUSES` |
| `INCIDENT_LABELS` | `TOOL_OUTCOME_LABELS` |
| `INCIDENT_REASON_CODES` | `TOOL_OUTCOME_REASON_CODES` |
| `incident_features.py` | `tool_outcome_features.py` |
| `incident_model.py` | `tool_outcome_reviewer_model.py` |
| `incident_routing.py` | `tool_outcome_routing.py` |
| `incident_taxonomy.py` | `tool_outcome_taxonomy.py` |

No old Python wrappers or compatibility imports should remain in active source.

---

## CLI Rename Pass

| Current command/output | New command/output |
|---|---|
| `agent-health units` | `agent-health cases` |
| `agent-health show` | `agent-health cases show` |
| `agent-health list` | `agent-health reviews list` |
| `agent-health summary` | `agent-health reviews summary` |
| `agent-health eval --due` | `agent-health review --due` |
| `agent-health signals` | `agent-health case-signals` |
| `agent-health schedule ...` | `agent-health review-jobs ...` |
| `agent-health incidents` | `agent-health tool-outcomes` |
| `agent-health incident examples` | `agent-health tool-outcomes cases` |
| `agent-health incident label` | `agent-health tool-outcomes review` |
| `agent-health incident judge-label` | `agent-health tool-outcomes llm-review` |
| `agent-health incident predict` | `agent-health tool-outcomes predict` |
| `agent-health incident train` | `agent-health tool-outcomes train-reviewer` |
| `agent-health incident export-training` | `agent-health tool-outcomes export-training` |
| `evaluated_units` metric | `reviewed_cases` |
| `selected_units` metric | `selected_cases` |
| `incident_labels` metric | `tool_outcome_reviews` |
| `judge_calls_used` metric | `llm_review_calls_used` |

CLI rules:

- No old command aliases.
- No old help text examples.
- No output JSON keys named `eval_unit`, `llm_eval`, `incident`, `anomaly`, or `deterministic_signal`.

---

## Dashboard/API Rename Pass

| Current route/key | New route/key |
|---|---|
| `/units/{eval_unit_id}` | `/cases/{turn_case_id}` |
| `/eval-tasks` | `/review-jobs` |
| `/eval-tasks/{task_id}/run-now` | `/review-jobs/{job_id}/run-now` |
| `/labels/incidents` | `/tool-outcome-reviews` |
| `/incident-models` | `/tool-outcome-reviewer-models` |
| `/incident-models/{model_id}/promote` | `/tool-outcome-reviewer-models/{model_id}/promote` |
| `/incident-models/retrain` | `/tool-outcome-reviewer-models/retrain` |
| `eval_units` | `turn_cases` |
| `units` | `cases` |
| `llm_evals` | `case_reviews` |
| `anomalies` | `case_findings` |
| `incident_examples` | `tool_outcome_cases` |
| `incident_labels` | `tool_outcome_reviews` |

Dashboard/frontend rules:

- Identify whether `dashboard_plugin/dashboard/dist/index*.js` files are shipped source or generated output.
- If shipped, update bundled route names, payload keys, and labels directly or regenerate them from the true frontend source in the same change.
- Dashboard labels should use `Turn Cases`, `Case Reviews`, `Findings`, `Tool Interactions`, `Tool Outcome Cases`, and `Tool Outcome Reviews`.
- Read-only browsing routes remain read-only. Import, judging, review-job execution, and model training/retraining must only happen through explicit action endpoints/buttons.

---

## Prompt Schema Rename Pass

### Request-level reviewer

Current prompt/result: `instruction_health_v1`, `health_status`, `anomalies`.

New prompt/result:

```json
{
  "schema_version": "turn_case_review_v1",
  "outcome_status": "succeed|failed|mishandled|prolonged",
  "confidence": "low|medium|high",
  "summary_reason": "...",
  "friction_score": 0.0,
  "findings": [
    {
      "finding_type": "tool_error|timeout|quality_gate|...",
      "severity": "low|medium|high",
      "evidence_text": "...",
      "case_event_id": "optional"
    }
  ]
}
```

Validator requirements:

- Require `schema_version='turn_case_review_v1'`.
- Reject old `instruction_health_v1` in active code paths.
- Preserve `findings[].case_event_id`.
- Reject old `anomalies` result shape in active code paths.

### Tool-outcome reviewer

Current prompt/result: `incident_eval_v1`, `incident|not_incident|unsure`.

New prompt/result:

```json
{
  "schema_version": "tool_outcome_review_v1",
  "tool_outcome_case_id": "...",
  "outcome_label": "problem|ok|unsure",
  "reason_code": "execution_error|empty_output|invalid_tool_input|wrong_or_bad_output|other|null",
  "confidence": 0.0,
  "evidence_summary": "..."
}
```

Validator requirements:

- Require `schema_version='tool_outcome_review_v1'`.
- Reject old `incident_eval_v1` in active code paths.
- Reject old `incident|not_incident` labels in active code paths.

---

## Implementation Tasks

### Task 1: Replace schema with the clean target model

**Files:** `src/agent_health/db.py`, schema/DB tests.

- Replace `SCHEMA_SQL` with the target tables above.
- Remove old compatibility migration logic entirely.
- Remove old table constants and field-list constants.
- Add `source_sessions`, `tool_interactions`, and the renamed review-domain tables.
- Add partial unique index for automatic LLM `case_reviews` per `turn_case_id`.
- Add `turn_case_id` to `tool_outcome_reviews` and populate it from the parent `tool_outcome_case` on insert.
- Delete old local development DB after this change before running integration flows: `$HERMES_HOME/instruction-health/evals.db` or the configured sidecar DB path.
- Add fresh-DB schema introspection tests proving only the new table names exist.

### Task 2: Rewrite source-session, turn-case, and tool-interaction builders

**Files:** `src/agent_health/normalize.py`, `src/agent_health/adapters/hermes.py`, `tests/test_normalize.py`.

- Replace request-boundary source code with `UserTurnBoundary` / `detect_user_turn_boundaries`.
- Emit `source_session` rows with normalized session metadata.
- Emit `turn_case` dicts with `request_text`, `response_text`, `turn_index`, `tool_interaction_count`, and `source_session_api_interaction_count`.
- Build persisted `tool_interaction` dicts by pairing assistant tool-call messages with tool-result messages.
- Emit `tool_outcome_case` dicts that reference `tool_interaction_id` instead of duplicating pairing logic.
- Add tests for source-session metadata, turn-case counts, tool-interaction pairing, and tool-outcome case derivation.

### Task 3: Rewrite DB APIs around the new domain

**Files:** `src/agent_health/db.py`, DB tests.

- Implement `upsert_source_session`, `upsert_turn_case`, `upsert_case_event`, `upsert_tool_interaction`, `upsert_case_signal`, `insert_case_review`, `insert_case_finding`, `insert_tool_outcome_review`, and `list_tool_outcome_cases`.
- Replace `list_due_units` with `list_due_turn_cases`.
- Replace old latest-label/latest-prediction joins with explicit latest-review concepts for tool outcomes.
- Add DB tests proving `insert_case_review` / case-review refresh only touches `case_reviews` and `case_findings`; existing `tool_outcome_reviews` remain present and unchanged.

### Task 4: Rewrite judge contracts and validators

**Files:** `src/agent_health/judge.py`, `src/agent_health/prompts/*.txt`, `tests/test_judge_contract.py`.

- Rename request-level result schema to `turn_case_review_v1` with `schema_version`.
- Rename anomalies to findings.
- Preserve `findings[].case_event_id`.
- Rename tool-outcome prompt/schema to `tool_outcome_review_v1` with `schema_version`.
- Rename label values from `incident/not_incident` to `problem/ok`.
- Add tests that old schema versions and old label values are rejected.

### Task 5: Rewrite automatic LLM guard in CLI and scheduler paths

**Files:** `src/agent_health/cli.py`, `src/agent_health/scheduler.py`, scheduler/CLI tests.

- Enforce the automatic LLM guard in tool-outcome candidate selection.
- Re-check the guard immediately before each automatic LLM tool-outcome reviewer call.
- Keep request-level and tool-outcome LLM costs separate.
- Use `selected_cases`, `reviewed_cases`, `tool_outcome_reviews`, and `llm_review_calls_used` in metrics.
- Add tests showing request-level automatic LLM review suppresses future automatic LLM tool-outcome review for the same `turn_case_id`.
- Add tests showing human, rule, imported, and ML-model tool-outcome reviews remain allowed.
- Add tests showing refreshing a `case_review` preserves existing `tool_outcome_reviews` unchanged.

### Task 6: Rewrite tool-outcome ML/routing modules

**Files:** `tool_outcome_features.py`, `tool_outcome_reviewer_model.py`, `tool_outcome_routing.py`, `tool_outcome_taxonomy.py`, related tests.

- Rename files directly; delete old incident-named modules in the same change.
- Replace all symbols, JSON keys, feature schema names, artifact names, and route labels.
- Preserve ML routing semantics with explicit fields: `review_source_detail`, `needs_llm_review`, `llm_review_budget_available`, `budget_fallback`, `uncertainty`, and `training_eligible`.
- Do not load old incident model artifacts.
- Add tests for confident ML review, ML defer, and ML budget-fallback behavior.

### Task 7: Rewrite CLI/dashboard/API surfaces

**Files:** `src/agent_health/cli.py`, `src/agent_health/dashboard_queries.py`, `src/agent_health/dashboard_plugin/dashboard/plugin_api.py`, dashboard frontend artifacts, tests.

- Apply the CLI rename table exactly.
- Apply the dashboard/API rename table exactly.
- Remove old command aliases, routes, payload keys, and help examples.
- Update dashboard labels to the canonical names.
- Verify dashboard read routes remain read-only and work actions remain explicit.
- Add CLI help tests and dashboard route/payload contract tests.

### Task 8: Rewrite docs/truth after code behavior changes

**Files:** `docs/design.md`, `docs/truth/**`, `docs/truthmark/areas/**`, `README.md` for active behavior documentation.

- Rewrite active architecture docs with the new vocabulary and relationship model.
- Update routed truth docs after code changes.
- Update source-of-truth paths if files are renamed.
- Run `truthmark check`.
- Do not update historical `docs/plans/**`. Historical documents promoted to active behavior documentation must first move into an active docs path, then use the active vocabulary.

---

## Verification Commands

Run after the atomic schema/builder/judge/API rewrite is complete:

```bash
python -m unittest discover -s tests
```

Search active runtime and active truth surfaces only:

```bash
rg "eval_unit|llm_eval|incident_|anomal|deterministic_signal" \
  src tests docs/design.md docs/truth docs/truthmark README.md
```

Expected: no active-runtime matches. Historical plans under `docs/plans/**` are not part of this grep gate. Promote a historical plan into an active docs path before applying the active-vocabulary gate to it.

After docs/truth updates:

```bash
truthmark check
```

---

## Acceptance Criteria

- Fresh DB has only the new table names, including `source_sessions`, `turn_cases`, `case_events`, `tool_interactions`, `case_signals`, `case_reviews`, `case_findings`, `tool_outcome_cases`, `tool_outcome_reviews`, and `tool_outcome_reviewer_models`.
- No old DB migration, old compatibility view, old CLI alias, old JSON duplicate key, or old Python wrapper remains in active code.
- CLI help contains `cases`, `review`, `case-signals`, `review-jobs`, and `tool-outcomes`; it does not contain `units`, `eval --due`, or `incident` commands.
- Request-level judging stores `case_reviews` and `case_findings`.
- Tool-layer judging stores `tool_outcome_reviews` and never writes `case_findings` directly.
- `tool_interactions` are persisted and `tool_outcome_cases` reference them.
- Automatic LLM review guard prevents any new automatic LLM `tool_outcome_review` call for a `tool_outcome_case` whose parent `turn_case_id` already has an automatic LLM `case_review`.
- Refreshing, replacing, or force-updating a `case_review` preserves existing `tool_outcome_reviews` unchanged; the guard blocks new automatic LLM calls but never performs cleanup/deletion of old tool-outcome reviews.
- ML tool-outcome reviews preserve defer, budget fallback, uncertainty, and training-eligibility semantics.
- Dashboard read routes remain read-only; import, judging, review-job execution, and model training/retraining only happen through explicit action endpoints/buttons.
- Active grep command returns no old vocabulary matches in active runtime/truth surfaces.
- `python -m unittest discover -s tests` passes.
- `truthmark check` passes after docs are updated.
