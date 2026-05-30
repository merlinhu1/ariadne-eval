---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-24
source_of_truth:
  - ../../truthmark/areas/dashboard-visualization.md
  - ../../../src/agent_health/dashboard_queries.py
  - ../../../src/agent_health/dashboard_plugin/
  - ../../../src/agent_health/cli.py
  - ../../../src/agent_health/scheduler_bootstrap.py
---

# Hermes Dashboard Plugin

## Purpose

Ariadne Eval provides a local-first Hermes dashboard tab so instruction-health data can be visualized without creating a separate UI server, and tool outcome reviews can be corrected directly into the canonical ML-first training source.

## Scope

This doc owns dashboard query payload behavior, the bundled Hermes dashboard plugin files, and plugin API/UI contracts.

## Current Behavior

- `dashboard_summary(db, since, bucket_seconds, unit_limit, session_limit, session_offset)` reads the local `evals.db` sidecar and returns one JSON-compatible payload for the dashboard.
- The summary payload includes total turn cases, judged turns, canonical tool outcome cases, judge findings, latest health-status counts, top finding types, judge token totals, timeline buckets, request-level friction summaries, friction anchors, globally ranked requests, paginated session-grouped request/tool outcome/finding data, and `session_pagination` metadata.
- Session groups include per-session turn/evaluated counts, status counts, finding counts, latest turn-case handles, capped inspectable finding rows, canonical tool outcome case rows, and max/average request friction scores. The summary query sorts all session groups by friction/finding recency, slices only the `session_groups` array by normalized `session_limit`/`session_offset`, and keeps globally ranked `requests` computed from all visible sessions rather than just the current session page.
- Tool outcome rows come from the canonical `tool_outcome_cases` table enriched with latest human/LLM review and ML prediction concepts from `tool_outcome_reviews`; all dashboard feedback is also recorded in `review_feedback` so request, tool-call, tool outcome case, and LLM-judge corrections have one audit trail.
- Finding/status/token counts are derived from the latest stored case review per turn case, matching CLI summary behavior.
- `turn_case_detail(db, turn_case_id)` returns one turn case's metadata, trace events, case signals, canonical tool outcome cases, and latest case review with findings.
- `session_detail(db, source_session_id, since, unit_limit)` returns one source agent session's inspectable cases with context, trace events, case signals, canonical tool outcome cases, latest judge review, and aggregate status/finding/tool outcome counts. Session detail filters by `source_session_id` and `since` before applying `unit_limit`, so older visible sessions are not dropped by newer cases from other sessions.
- The bundled dashboard plugin manifest registers the Hermes tab as `ariadne-eval` at `/ariadne-eval` and points to `dist/index.v20260520b.js`, `dist/style.v20260520b.css`, and `plugin_api.py`.
- The plugin API exposes inspection routes, currently `GET /summary`, `GET /sessions/{source_session_id}`, and `GET /cases/{turn_case_id}`, plus explicit write/control routes for generic feedback, tool outcome reviews, and review job management under Hermes' `/api/plugins/ariadne-eval/` mount. Summary requests accept `session_limit` (`1..100`, default `24`) and `session_offset` (`>=0`, default `0`) query parameters for session-card pagination.
- The browser tab visualizes the summary with a top request-friction section, a visible friction-anchor legend, a request-first "Requests needing attention" list sorted by `friction_score`, status bars, aggregated tool outcome review chips, top finding chips, judge-token totals, a finding-only timeline, and secondary paginated session-grouped finding/tool outcome cards.
- The browser tab exposes a header `Configure` button that opens a wide in-dashboard configuration panel, not a hidden route. Opening or refreshing the panel lists scheduler task configuration, available trained tool outcome ML models, the promoted model, and read-only Hermes LLM judging route guidance without importing sessions, judging cases, training models, or creating review runs.
- The configuration panel can create or update recurring review jobs with name, enabled state, interval or continuous scheduling, interval hours, no-gap behavior, idle backoff, candidate limit, priority score floor, cooldown, judgement threshold, judge call limits, total judge token budget, and per-call token cap. Existing tasks can be selected into the form, saved with explicit `POST`/`PATCH`, or controlled with explicit Run now, Pause, and Resume buttons.
- The configuration panel lists trained tool outcome ML model records, lets the user promote one explicitly, and exposes an explicit Retrain button that trains from accepted tool outcome reviews, smoke-checks the artifact, records model metadata, and refreshes the panel. The panel shows Hermes route priority for LLM judging but does not claim to edit arbitrary provider/auth settings that remain owned by Hermes.
- The Agent sessions panel renders one API-provided session page at a time, shows the total from `session_pagination.total`, and provides Prev/Next controls using the same `24`-session page size as the API default. Changing the time window resets session pagination to the first page.
- Session cards expose an explicit Details action that lazily opens a right-side drawer rather than rendering any inline lower inspector panel. The drawer uses the session detail API, aborts stale in-flight fetches when the selected session changes, and can close with `Esc`.
- The session drawer contains Overview, Turns, Tool calls, Judge review, and Raw tabs. It exposes per-turn context, case signals, canonical tool outcome cases, latest judge status/reason/findings/raw review JSON, and raw session/turn/event JSON for copyable inspection.
- Tool-call drawer rows render input previews, output previews, and defensively parsed source payload JSON in copyable whitespace-preserving blocks. Tool rows with `output_error` default open, selected linked events are highlighted and scrolled into view, and malformed `source_payload_json` remains visible as raw text instead of breaking rendering.
- Session-card evidence rows are keyboard-activatable navigation affordances. Rows with `related_event_id` open the drawer to the Tool calls tab and selected event; judge-only finding rows without an event link open the Judge review path for the relevant turn case.
- Request cards, turn details, tool-call rows, tool outcome evidence rows, and Judge review rows expose visibly labeled `Feedback` controls. Request and Judge review controls write one of `succeed`, `failed`, `mishandled`, or `prolonged`; tool-call and tool outcome case controls write one of `problem`, `ok`, or `unsure`. Every feedback action writes a `review_feedback` row; tool outcome case feedback also writes an accepted human review into `tool_outcome_reviews`. Feedback writes do not import sessions, call the judge, or retrain as an implicit side effect.
- Session cards, evidence rows, IDs, tool outcome details, and trace details use copyable text containers instead of large text-bearing button wrappers; explicit copy controls remain for session IDs and long diagnostic details, while evidence row unit IDs stay selectable without a per-row Copy unit button.
- Dashboard timestamps from source rows are rendered as browser-local `<time>` labels using the user's current locale and time zone in session cards, evidence rows, and timeline hover titles.
- Raw `source_session_id` and `turn_case_id` values remain visible in session/evidence/detail metadata for technical inspection and copying.
- Session cards use dynamic-height masonry columns so cards with more tool outcome/finding evidence can grow without forcing neighboring cards to the same height.
- Clicking or keyboard-activating the upper session-card summary area above the Findings/Tool Outcomes facets folds/expands that card; folding hides the facet and evidence body while preserving the header, metrics, local time, and copyable metadata. The collapse handler ignores active text selections, and nested copy controls keep normal click behavior.
- The session-card list does not render a separate session-inspector/detail panel below the cards; session detail API routes remain available for future UI surfaces but the dashboard tab avoids an extra panel that visually attaches to the bottom of masonry cards.
- Session-card finding and tool outcome facet counts are stacked vertically, with smaller medium-weight labels for readability in dense cards.
- Session-card, evidence, metric, secondary session evidence-count, and stat-label typography uses sentence case and moderate weights so dense dashboard cards do not read as overly heavy or shouty. The Ariadne plugin explicitly overrides inherited Hermes dashboard uppercase transforms inside its page, and enum-style labels such as `ok` or `external_action_not_verified` are displayed with spaces for readability while preserving raw values in tooltips/copyable JSON.
- Plugin API calls use a local JSON fetch helper that sends `X-Hermes-Session-Token` when a Hermes session token is available, parses response text explicitly, and reports a dashboard/plugin API route restart hint when Hermes returns HTML or non-JSON instead of leaking raw JSON parser messages such as `Unexpected token <`.
- When the summary window reloads, the browser refreshes the session groups directly without auto-selecting a session or rendering stale detail state.
- The dashboard path is inspection-first: browsing and listing routes do not import Hermes sessions, call the LLM judge, mutate turn-case/judge/finding rows, create review runs, or schedule evaluation. Explicit browser actions may write feedback rows and canonical tool outcome reviews, may manage review job state, and may trigger retraining as user-initiated actions over the same local sidecar paths used by the CLI.
- Plugin review job controls mirror the CLI scheduler controls: list/get/config routes are read-only; pause disables an existing task; resume enables an existing task and marks it due at the current wall-clock time; run-now explicitly enables an existing task and marks it due at the current wall-clock time without running evaluation inline. Item updates by displayed task id mutate the original task row, missing pause/resume/run-now targets return 404, and invalid scheduler payloads return compact validation errors instead of creating replacement tasks.
- Plugin tool outcome model controls expose read-only model listing plus explicit promote and retrain routes. Promotion only changes the promoted model pointer for an existing model id. Retraining reuses the in-process TF-IDF tool outcome model training, smoke-check, model-record, and optional promotion behavior rather than shelling out to the CLI.

## Core Rules

- The Hermes dashboard plugin must stay anchored in existing sidecar data rather than becoming another ingestion or judge control plane.
- Dashboard feedback writes must be explicit user actions. Generic request/tool/judge feedback writes to `review_feedback`; tool outcome case feedback additionally writes canonical `tool_outcome_reviews`. Feedback must not mutate turn cases, judge outputs, finding rows, or trigger retraining automatically. A separate explicit retrain control may trigger model training from accepted reviews when implemented.
- Dashboard review job controls must remain explicit user actions. Task listing must not create review runs, and run-now/resume must only update task due state for the scheduler. Task controls must not create a new task when the caller supplied an unknown displayed task id.
- Dashboard configuration browsing must remain read-only. Only Save/Create, Promote, Retrain, Run now, Pause, and Resume controls may write sidecar state.
- Dashboard API routes should be thin wrappers around `dashboard_queries.py`; session detail queries should remain session-scoped before applying per-session limits.
- The plugin is opt-in; state.db ingestion and CLI evaluation must continue working without dashboard installation.
- Dashboard assets are copied into `<hermes-home>/plugins/ariadne-eval/dashboard` by the CLI install command. By default the same install also writes `<hermes-home>/scripts/ariadne_eval_scheduler_watchdog.py` and creates or updates a local-output Hermes cron job named `Ariadne Eval scheduler watchdog` on `every 10m`; the watchdog starts `agent-health --hermes-home <hermes-home> scheduler run --poll-seconds 600` when no scheduler daemon PID is alive. Dashboard task interval editing is hour-based and converts to stored `interval_seconds` on save. `dashboard install --no-scheduler-watchdog` skips this scheduler watchdog wiring for users who supervise `agent-health scheduler run` themselves.

## Contracts

- Plugin name: `ariadne-eval`.
- Tab path: `/ariadne-eval`.
- Plugin API summary route: `/api/plugins/ariadne-eval/summary`.
- Plugin API summary pagination query parameters: `session_limit` defaults to `24` and is constrained to `1..100`; `session_offset` defaults to `0` and is constrained to `>=0`; responses include `session_pagination.limit`, `offset`, `total`, `has_next`, and `has_prev`.
- Plugin API session detail route: `/api/plugins/ariadne-eval/sessions/{source_session_id}`.
- Plugin API turn-case detail route: `/api/plugins/ariadne-eval/cases/{turn_case_id}`.
- Plugin API generic feedback route: `POST /api/plugins/ariadne-eval/feedback`.
- Plugin API tool outcome review routes: `GET /api/plugins/ariadne-eval/tool-outcome-reviews` and `POST /api/plugins/ariadne-eval/tool-outcome-reviews`.
- Plugin API configuration options route: `GET /api/plugins/ariadne-eval/config/options`.
- Plugin API review job collection routes: `GET /api/plugins/ariadne-eval/review-jobs` and `POST /api/plugins/ariadne-eval/review-jobs`.
- Plugin API review job item routes: `GET /api/plugins/ariadne-eval/review-jobs/{job_id}` and `PATCH /api/plugins/ariadne-eval/review-jobs/{job_id}`.
- Plugin API review job control routes: `POST /api/plugins/ariadne-eval/review-jobs/{job_id}/run-now`, `POST /api/plugins/ariadne-eval/review-jobs/{job_id}/pause`, and `POST /api/plugins/ariadne-eval/review-jobs/{job_id}/resume`.
- Plugin API review run list route: `GET /api/plugins/ariadne-eval/review-runs`.
- Plugin API tool outcome model list route: `GET /api/plugins/ariadne-eval/tool-outcome-reviewer-models`.
- Plugin API tool outcome model control routes: `POST /api/plugins/ariadne-eval/tool-outcome-reviewer-models/{model_id}/promote` and `POST /api/plugins/ariadne-eval/tool-outcome-reviewer-models/retrain`.
- Default dashboard window: `24h`.
- Default timeline bucket size: `3600` seconds.

## Product Decisions

- Decision (2026-05-19): The first web visualization is a Hermes dashboard plugin tab, not a standalone UI.
- Decision (2026-05-24): Dashboard routes are inspection-first, not read-only; explicit browser actions may write review labels and may trigger retraining while still operating over local sidecar data.
- Decision (2026-05-24): Dashboard review job controls may expose scheduler state management, but task/runs listing remains read-only and run-now/resume only mark tasks due for the scheduler.
- Decision (2026-05-24): Browser-triggered retraining is in scope as an explicit user action. Label capture itself must not silently retrain, but retraining no longer has to remain CLI-only.
- Decision (2026-05-25): Scheduler, tool outcome model, and judge-budget configuration belongs in a visible dashboard configuration panel so most local eval operation does not require command-line configuration.
- Decision (2026-05-23): The dashboard ranks requests first by normalized `friction_score`; session evidence counts remain secondary context rather than the primary risk concept.

## Rationale

Hermes already provides the dashboard shell, plugin tab registry, static asset mounting, and plugin API routing. Reusing that shell keeps Ariadne Eval local-first and avoids a separate server while still making the visualization first-class.

## Maintenance Notes

- Update this doc when dashboard query fields, plugin manifest fields, API routes, review job controls, or install behavior change.
- Related tests currently include `tests/test_dashboard_config_api.py`, `tests/test_judge_contract.py`, `tests/test_db_review_guards.py`, and `tests/test_scheduler.py`.
