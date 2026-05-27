# Ariadne Dashboard Session Detail Drilldown Design

**Status:** research proposal / review draft  
**Date:** 2026-05-20  
**Scope:** Hermes dashboard plugin UI for Ariadne Eval inspection workflows  
**Primary requirement:** Users must be able to start from session-level anomaly/incident summaries and drill down into the exact session, turn, tool-call, judge, and raw-source evidence needed to understand what happened.

---

## Problem

The current Ariadne dashboard is good at showing a session-grouped health overview, but it is not yet good enough as an investigation tool.

The dashboard currently shows:

- aggregate health stats;
- top incidents and anomalies;
- timeline;
- session cards grouped by incident/anomaly counts;
- compact evidence rows on cards.

But users need to answer deeper questions:

- Which turn caused this session to be marked problematic?
- Which tool call failed?
- What exact error/result text did the tool return?
- What did the assistant claim after the tool result?
- What did the judge see, and why did it assign this status?
- Was the evidence deterministic, judge-derived, model-derived, or user-reaction-derived?
- Is the displayed string only a preview, and can I retrieve/copy the full original source?

Ariadne’s core value is not just scoring sessions; it is making agent failures inspectable. Detail drilldown is therefore a core product requirement, not a nice-to-have.

---

## Existing Hermes plugin patterns

### Kanban dashboard plugin

The best existing Hermes precedent is the bundled Kanban dashboard plugin.

Relevant pattern:

- compact board cards remain summary-only;
- selecting a card opens a right-side drawer;
- the drawer fetches detail lazily from a dedicated backend endpoint;
- the drawer contains structured sections for metadata, diagnostics, comments, event history, worker logs, and run history;
- large blobs are shown in copyable `<pre>`/`<code>` blocks;
- event/run metadata is hidden behind expandable `<details>` blocks when large;
- the detail view does not render as a panel underneath the card grid.

This maps very well to Ariadne. Ariadne should use the same broad interaction model: summary cards in the main grid, explicit detail drawer for deep inspection.

### Core Hermes web ToolCall component

The main Hermes web UI has an expandable `ToolCall` component for live/historical tool rows.

Relevant pattern:

- header row shows tool name, context, status, and elapsed time;
- click expands to show context, streaming preview, final result, error, or inline diff;
- error rows default-open;
- result/error text is rendered in whitespace-preserving copyable blocks.

Ariadne trace events should use the same concept: a compact trace-event row that expands into args/result/raw payload detail, with error events open by default.

### Achievements plugin

The Achievements plugin uses dialog/modal-style UI. It is useful as a precedent for overlays, but less suitable for Ariadne’s dense diagnostic data. Ariadne should prefer a drawer over a modal because session and tool-call detail is hierarchical, scroll-heavy, and often needs side-by-side relationship to the summary grid.

---

## Current Ariadne affordances

Ariadne already has backend APIs that support this design:

```text
GET /api/plugins/ariadne-eval/summary
GET /api/plugins/ariadne-eval/sessions/{source_session_id}
GET /api/plugins/ariadne-eval/units/{eval_unit_id}
```

Important session-detail contract:

> `/sessions/{source_session_id}` must filter by `source_session_id` and `since` before applying `unit_limit`.

Do not implement the drawer against a global newest-N unit query that is filtered by session afterward. That can make a visible older session open to partial or empty detail when the global result set is crowded by newer sessions. If the current query helper does global `list_units(limit=...)` first, Phase 1 should include a small backend/query fix that adds a session-scoped unit lookup without changing the schema.

The session detail endpoint already returns:

```text
source_session_id
title
last_started_at
eval_units
evaluated_turns
incident_count
anomaly_count
statuses
anomaly_types
incident_types
severities
units[]
```

Each `units[]` item already contains:

```text
unit
trace_events
signals
incidents
latest_eval
```

Trace events currently expose:

```text
id
eval_unit_id
source_event_id
event_type
timestamp
tool_name
args_hash
args_preview
result_hash
result_preview
result_error
duration_ms
raw_payload_json
```

So the first UI implementation can use existing read-only APIs after the session-scoped query-limit fix above. The stored `result_preview` cap is acceptable for now. Later, if users still need source beyond the capped previews, add an optional read-only source-message endpoint backed by Hermes `state.db`.

---

## Proposed interaction model

### Main page remains summary-first

Keep the current page structure:

- stat cards;
- status bars;
- top incidents/anomalies;
- timeline;
- session card grid.

Session cards should remain compact and scannable. They are not the right place for full detail.

### Session card actions

Each session card should have two separate interactions:

1. **Fold/expand summary**
   - A dedicated `Fold`/`Expand` button, or a clearly bounded collapse-only region, toggles the compact card summary.
   - This preserves the recently requested behavior.

2. **Open detail drawer**
   - A clearly labeled `Details` button opens the session drawer.
   - Clicking an evidence row can also open the drawer and jump to the matching turn/event.

Interaction rule:

> Preserve the current session-card feel: the upper summary area can remain the large fold/expand target, and nested copy/detail controls may live inside the card header as long as they stop propagation and remain independently focusable.

The existing collapse-zone pattern is acceptable because it gives users a large click target and keeps dense cards easy to scan. Do not replace it with a tiny fold-only button unless there is a concrete usability reason. The important constraints are:

- the collapse zone must remain keyboard-activatable with `Enter`/`Space`;
- text selection should not accidentally collapse the card;
- nested action controls such as copy/detail must call `stopPropagation()` so they do not toggle collapse;
- evidence rows should be keyboard-activatable if they open the drawer;
- do not wrap long copyable diagnostic text in native `<button>` elements.

Recommended card DOM shape:

```text
article|div.ae-session-card
  div.ae-session-collapse-zone[role="button"][tabIndex=0]
    header.ae-session-card-head
      title / metadata / risk score
      button.ae-details-button        # stops propagation
      button.ae-copy-button           # stops propagation
      span.ae-card-fold               # visual Fold/Expand label
    div.ae-session-metrics
  div.ae-session-card-body
    button|div[role="button"].ae-evidence-row
```

Use native `<button type="button">` for explicit actions such as Details, Copy, and evidence navigation when practical. If an evidence row must be a `div role="button"`, it must have `tabIndex=0` and handle `Enter` and `Space`.

Important rule:

> Never render a large inspector as a sibling panel beneath the session grid. Detail inspection must be an overlay/drawer, not an attached lower panel.

This avoids reintroducing the previous “weird panel under the card” problem.

---

## Proposed UI: `SessionDrawer`

Open from:

- session card `Details` button;
- evidence row click;
- future top-anomaly/session links.

Suggested dimensions:

```text
width: min(920px, 92vw)
position: fixed right
height: 100vh
scroll body independently
```

Header content:

- session title;
- `source_session_id`;
- copy session ID button;
- last activity timestamp;
- eval units count;
- judged turns count;
- incident count;
- anomaly count;
- highest severity / risk score;
- close button and `Esc` close.

The drawer should use local component state:

```js
const [drawerSessionId, setDrawerSessionId] = useState(null);
const [drawerData, setDrawerData] = useState(null);
const [selectedUnitId, setSelectedUnitId] = useState(null);
const [selectedEventId, setSelectedEventId] = useState(null);
const [activeTab, setActiveTab] = useState("overview");
```

On open:

```js
fetchPluginJSON(`/api/plugins/ariadne-eval/sessions/${encodeURIComponent(sessionId)}?since=${encodeURIComponent(since)}&unit_limit=500`)
```

Fetches should be race-safe. Use an `AbortController` or monotonically increasing request id so a slower response from a previously selected session cannot overwrite the drawer for the currently selected session.

---

## Drawer tabs

### 1. Overview

Purpose: answer “why is this session hot?” quickly.

Sections:

- status distribution;
- anomaly type counts;
- incident type counts;
- severity counts;
- top evidence rows;
- quick links to jump to matching turn/tool event.

Actions:

- `Copy session ID`;
- `Copy overview JSON`;
- `Open latest unit`;
- `Show errors only` toggle.

### 2. Turns

Purpose: inspect each normalized eval unit in the session.

Each turn row should show:

```text
Turn N · status · confidence · started_at
user request preview
assistant response preview
next user reaction preview
tool count / api count / token counts
incident badges
anomaly badges
[Open turn]
```

Expanded turn detail should show:

- full user request;
- assistant response;
- previous context summary;
- next user reaction;
- deterministic signals;
- incidents for the turn;
- latest judge evaluation summary;
- trace events for the turn.

Text blocks must be selectable and copyable.

### 3. Tool calls

Purpose: inspect exact tool-level evidence.

This is the most important new tab.

Render each `trace_event` as an expandable row, inspired by Hermes core `ToolCall`:

Header:

```text
▸ tool_name or event_type
turn N
status: error/done
result_error badge
incident/anomaly badges
elapsed/duration if available
```

Expanded body:

```text
Args
<pre>{args_preview}</pre>

Result
<pre>{result_preview}</pre>

Raw payload
<pre>{pretty raw_payload_json}</pre>
```

Behavior:

- error/result_error rows default-open;
- rows linked to incidents/anomalies are visually highlighted;
- when `selectedEventId` is set, scroll the matching trace row into view after drawer data renders and expand it;
- allow text selection;
- include copy buttons for args, result, raw JSON, event ID;
- parse `raw_payload_json` defensively; malformed JSON should render as copyable raw text instead of breaking the drawer;
- show a “preview is capped” note only if/when truncation metadata is available; the current 6000-character preview cap is acceptable without adding Phase 2 source retrieval.

Filters:

```text
All
Errors only
Incidents only
Anomalies only
Tool: [select]
Turn: [select]
Search: [text]
```

Clicking an incident/anomaly badge should scroll to or open the relevant trace event if `related_event_id` is available.

### 4. Judge eval

Purpose: answer “why did the judge mark this as succeed/prolonged/mishandled/etc.?”

For each evaluated unit, show:

- health status;
- confidence;
- primary reason;
- judge provider/model;
- prompt/completion/total tokens;
- judge call count;
- anomalies list;
- raw `eval_json`.

Use expandable raw JSON blocks for power users.

### 5. Raw

Purpose: maximum transparency and debugging.

Show:

- full session detail JSON;
- selected unit JSON;
- selected trace event JSON;
- copy buttons.

This tab is especially useful while the dashboard UI is still evolving.

---

## Evidence-row behavior from session cards

Current session cards show compact evidence rows. These should become navigational affordances.

Summary evidence row contract:

```text
eval_unit_id
source_turn_index
started_at
severity
source
related_event_id optional
evidence/detail fields
```

Incident rows should continue to include `source`, `tool_name`, `related_event_id`, `result_preview`, and `args_preview` when available. Deterministic incident rows are the reliable Phase 1 source for event-level deep links.

Anomaly rows should expose `source` and optional `related_event_id` only when that link is actually present in stored anomaly data. `related_event_id` remains optional. Most current judge anomalies are judge-only or assistant-response-level and have no single trace event; those rows should still open the drawer at the relevant eval unit and default to the Judge eval tab instead of pretending there is a tool event to jump to.

For each evidence row:

- click opens `SessionDrawer`;
- sets `selectedUnitId = row.eval_unit_id`;
- if `related_event_id` exists, sets `selectedEventId = row.related_event_id`;
- switches to `Tool calls` for deterministic tool/trace incidents and event-linked anomalies;
- switches to `Judge eval` for judge-only anomalies without `related_event_id`.

This gives a direct path from summary evidence to the best available source evidence without requiring every anomaly to be event-linked.

If LLM judge anomalies should deep-link to trace events in a later increment, the judge path must preserve the link end-to-end: include stable trace event ids in the judge input, allow optional `related_event_id` in the prompt schema, preserve it in validation, store it in `anomalies.related_event_id`, and return it in dashboard summary rows. Do not count judge-anomaly event links as Phase 1 complete until all hops are verified.

---

## Optional full-source retrieval extension

The existing sidecar database stores `result_preview`, currently capped during normalization. A 6000-character capped preview is acceptable for the current drawer implementation. Full-source retrieval is an optional later extension only if real investigation workflows prove that the cap is insufficient.

If this extension is needed later, add this read-only primary endpoint:

```text
GET /api/plugins/ariadne-eval/events/{trace_event_id}/source
```

The server owns id resolution. The frontend should pass the Ariadne sidecar `trace_event_id`; it should not need to know whether sidecar trace ids, Hermes message ids, and source event ids are interchangeable.

Resolution path:

```text
trace_event_id
  -> trace_events row
  -> eval_unit_id
  -> eval_units.source_session_id
  -> trace_events.source_event_id
  -> Hermes state.db messages.id within that source_session_id
```

If a direct source-message endpoint is added, make it session-scoped:

```text
GET /api/plugins/ariadne-eval/source-sessions/{source_session_id}/messages/{message_id}
```

Do not expose an unscoped `/source-messages/{message_id}` endpoint; it makes the frontend depend on Hermes message-id semantics and is easier to misuse.

Recommended response:

```json
{
  "trace_event_id": "hermes:20260519_232741_db7f1e31:turn:3:event:1",
  "eval_unit_id": "hermes:20260519_232741_db7f1e31:turn:3",
  "source_session_id": "20260519_232741_db7f1e31",
  "source_event_id": "13995",
  "message": {
    "id": "13995",
    "session_id": "20260519_232741_db7f1e31",
    "role": "tool",
    "tool_name": "terminal",
    "tool_call_id": "call_...",
    "timestamp": 1779237876.9948144,
    "content": "full source message content..."
  },
  "content_truncated": false,
  "original_chars": 12345,
  "returned_chars": 12345,
  "hidden_fields_omitted": [
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items"
  ]
}
```

Server-side rules:

- resolve the sidecar trace event from `evals.db`, then read the full source message from Hermes `state.db`;
- do not call the judge;
- do not import sessions;
- do not mutate `evals.db`;
- do not mutate Hermes `state.db`;
- continue excluding hidden provider reasoning fields;
- cap extremely large payloads with explicit truncation metadata;
- preserve copyable diagnostic strings.

Error handling:

- return 404 if the sidecar trace event is missing;
- return 404 if the resolved source message is missing from the resolved Hermes session;
- return 422, or a clear 404-style not-available response, if the trace event has no `source_event_id`;
- never fall back to scanning every session for a matching message id.

If the optional endpoint is added later, the frontend button should be:

```text
Load full source
```

This should appear only when the optional source endpoint exists and the trace event has a `source_event_id`, or in Raw/Tool-call detail behind an explicit user action. Do not block the current drawer on this; capped previews are acceptable for now.

---

## Data/API changes

### Phase 1: no backend schema changes

Use existing endpoints and one query-semantics fix:

```text
GET /summary
GET /sessions/{source_session_id}
GET /units/{eval_unit_id}
```

The `/sessions/{source_session_id}` route should be backed by a session-scoped lookup that applies `source_session_id` and `since` before `unit_limit`. This changes query behavior, not schema or storage.

This is enough for:

- drawer;
- turn detail;
- trace-event rows;
- judge tab;
- raw JSON tab.

### Optional future phase: add source-message retrieval

Add endpoint in:

```text
src/agent_health/dashboard_plugin/dashboard/plugin_api.py
```

Likely helper lives near Hermes adapter/state reader code:

```text
src/agent_health/adapters/hermes.py
```

Possible helper:

```python
HermesStateReader.get_message(session_id: str, message_id: str) -> dict[str, Any]
```

Also add a dashboard-query helper that resolves a sidecar trace event to its eval unit, source session, and source event id before calling `HermesStateReader`.

The endpoint should use `default_hermes_home()` unless `hermes_home` override is provided, matching existing dashboard plugin API patterns. It must remain read-only over both sidecar and Hermes databases.

---

## Frontend component plan

Suggested new components in:

```text
src/agent_health/dashboard_plugin/dashboard/dist/index.js
```

Components:

```text
SessionDrawer
DrawerTabs
SessionOverviewTab
SessionTurnsTab
TurnDetail
ToolCallsTab
TraceEventRow
JudgeEvalTab
RawJsonBlock
SafeJsonBlock
CopyButton
```

Existing components to reuse/extend:

```text
ExpandedText
EvidenceList
SeverityTag
LocalTime
CountMap
```

CSS additions in:

```text
src/agent_health/dashboard_plugin/dashboard/dist/style.css
```

Suggested classes:

```text
ae-drawer-shade
ae-drawer
ae-drawer-head
ae-drawer-tabs
ae-drawer-body
ae-section
ae-turn-row
ae-turn-detail
ae-trace-event
ae-trace-event-error
ae-trace-event-linked
ae-trace-event-selected
ae-pre
ae-json-block
ae-filter-row
ae-details-button
ae-card-fold
ae-evidence-row
```

---

## Testing strategy

Tests should keep the existing static/structure checks, but drawer navigation needs at least fixture-backed API coverage and explicit manual browser smoke until a real frontend test harness exists.

Update:

```text
tests/test_dashboard_plugin.py
tests/test_dashboard_queries.py
```

Static frontend assertions:

- `SessionDrawer` exists;
- `TraceEventRow` exists;
- session cards render a `Details` button;
- card body fold/expand behavior remains separate from detail opening;
- current collapse-zone behavior remains intact: the upper summary area toggles fold/expand, nested copy/detail controls stop propagation, and long diagnostic text is not wrapped in native buttons;
- detail drawer calls `/sessions/{source_session_id}` lazily;
- drawer fetches are race-safe via abort/request-id logic;
- no old inline `.ae-inspector` or `SessionInspector` lower panel is reintroduced;
- tool-call rows render `args_preview`, `result_preview`, and `raw_payload_json` sections;
- error trace events default-open, not merely styled;
- malformed `raw_payload_json` is displayed safely as raw text;
- `Copy unit` button remains absent from evidence rows if that was intentionally removed.

Fixture-backed API assertions:

- create a fixture eval DB with one session, two turns, one trace event, one deterministic incident, and one anomaly whose `related_event_id` points to that trace event;
- assert `/sessions/{source_session_id}` returns both units and the linked trace event;
- assert session detail applies `source_session_id`/`since` before `unit_limit` by creating enough newer units in another session to prove the target session is not dropped by a global newest-N limit;
- assert incident and anomaly evidence rows expose `eval_unit_id`, `source`, and `related_event_id` when available;
- assert every returned `related_event_id` in the fixture matches an actual trace event id in the session detail payload;
- include a malformed `raw_payload_json` trace event and assert it remains data in the payload.

If Phase 2 source retrieval is added:

- API test for `GET /events/{trace_event_id}/source` resolving sidecar trace event -> eval unit -> source session -> Hermes message;
- optional API test for scoped `GET /source-sessions/{source_session_id}/messages/{message_id}` if that endpoint exists;
- hidden reasoning fields omitted;
- missing sidecar event returns 404;
- missing source message returns 404;
- trace event without `source_event_id` returns the chosen not-available error;
- large content returns explicit truncation metadata;
- endpoint does not mutate eval DB or Hermes state DB.

Verification commands:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_dashboard_plugin.py'
node --check src/agent_health/dashboard_plugin/dashboard/dist/index.js
npx --yes truthmark check --json
npx --yes truthmark index --json
```

Manual browser smoke:

1. Open Ariadne dashboard with fixture or known linked-evidence data.
2. Confirm session cards still fold/expand via the upper collapse zone and that `Details`/copy controls are separately focusable and do not trigger collapse.
3. Click `Details` on a hot session.
4. Confirm right drawer opens, not an inline lower panel.
5. Open `Tool calls` tab.
6. Confirm trace rows are visible and `result_error` rows are default-open.
7. Expand a trace row and copy result text.
8. Click an evidence row with `related_event_id` and confirm the drawer opens to `Tool calls`, selects the relevant unit, scrolls to the linked event, and expands/highlights that event.
9. Click a judge-only anomaly without `related_event_id` and confirm the drawer opens to the relevant unit's judge/eval evidence rather than a bogus event.
10. Confirm malformed `raw_payload_json` renders as raw copyable text instead of breaking the drawer.
11. Switch between two session `Details` buttons quickly and confirm stale fetches do not overwrite the latest drawer.
12. Confirm no `.ae-inspector` lower panel exists.

---

## Phased implementation recommendation

### Phase 1 — Drawer using existing sidecar details

Goal: restore deep inspection without changing ingestion/storage.

Tasks:

1. Fix session-detail query semantics so `/sessions/{source_session_id}` filters by session and `since` before applying `unit_limit`.
2. Add `Details` button to each `SessionCard` while preserving the current large upper collapse-zone behavior.
3. Keep nested copy/detail controls independently focusable and make them stop propagation so they do not toggle collapse.
4. Add dashboard state for selected session and selected unit/event.
5. Add `SessionDrawer` overlay.
6. Fetch `/sessions/{source_session_id}` lazily on drawer open with race-safe request handling.
7. Add Overview and Turns tabs.
8. Add Tool calls tab with expandable `TraceEventRow`.
9. Add Judge eval and Raw tabs.
10. Add evidence-row navigation that opens the drawer to the linked unit/event when `related_event_id` is available.
11. Add tests ensuring no inline lower inspector returns and fixture-backed linked-evidence payloads work.
12. Install plugin assets and browser-smoke.

### Optional Phase 2 — Better source retrieval

Goal: allow the user to go beyond the 6000-character stored previews only if investigation workflows prove that previews are insufficient.

Tasks:

1. Add `HermesStateReader.get_message(session_id, message_id)` with the same hidden-field exclusions as the existing reader.
2. Add read-only dashboard endpoint `GET /events/{trace_event_id}/source` that resolves sidecar trace id to Hermes source message server-side.
3. Optionally add session-scoped `GET /source-sessions/{source_session_id}/messages/{message_id}` if direct source-message debugging is still needed.
4. Add frontend `Load full source` action on trace event rows.
5. Add truncation metadata display.
6. Add tests for id resolution, hidden-field omission, missing event/message errors, truncation metadata, and read-only behavior.

### Phase 3 — Investigation polish

Goal: make repeated debugging fast.

Tasks:

1. Add filters and search in Tool calls tab.
2. Add jump links from overview evidence to trace event rows.
3. Add URL hash state, e.g. `#session=...&unit=...&event=...`.
4. Add keyboard shortcuts: `Esc` close, `/` search, `[`/`]` next/previous event.
5. Add “copy investigation bundle” action for session/unit/event.

---

## Acceptance criteria

A design-complete implementation should satisfy:

- session summary grid remains compact;
- detail inspection is available via explicit `Details` button;
- fold/expand remains available through the upper collapse zone, while Details, copy, and evidence-row interactions are independently focusable and do not accidentally toggle collapse;
- evidence rows are keyboard-activatable and can open the drawer to the relevant unit/event;
- detail view is a drawer/overlay, not an attached lower panel;
- user can inspect per-turn context;
- user can inspect per-tool-call args, result, raw payload;
- selected trace events scroll into view, highlight, and expand when opened from linked evidence;
- malformed raw payload JSON cannot break drawer rendering;
- user can inspect judge status/reason/anomalies/raw JSON;
- all diagnostic strings are selectable/copyable;
- errors are visually prominent and preferably default-expanded;
- dashboard remains read-only over `evals.db`;
- dashboard does not import sessions or call the judge;
- hidden provider reasoning fields remain excluded;
- optional future source retrieval, if added, is trace-event-first and resolves Hermes source messages server-side;
- tests guard against regression to the old inline inspector panel;
- fixture-backed coverage verifies linked evidence, session-scoped detail limit semantics, and optional source endpoint id-resolution behavior if that endpoint is added.

---

## Recommendation

Implement Phase 1 first. It gives the user real drilldown immediately using existing read-only APIs plus the small session-scoped query-limit fix, while avoiding source-storage expansion.

Keep source retrieval optional. The current 6000-character stored preview cap is fine for now; only implement the source-message endpoint if real investigations show that previews are still insufficient. If added later, keep it trace-event-first: frontend calls `/events/{trace_event_id}/source`, and the server resolves the Hermes source message.
