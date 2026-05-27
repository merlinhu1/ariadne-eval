# Session Dashboard Inspector

Previous version: 0.3.0
New version: 0.4.0
Diff basis: current pyproject baseline when no release tag exists plus working tree
Version action: minor
SemVer rationale: Adds backward-compatible dashboard API payload fields, a session-detail API route, and a substantially richer user-visible dashboard visualization.

Release payload:
- Groups dashboard anomalies and deterministic incidents by source agent session with per-session status, severity, anomaly-type, and incident-type counts.
- Adds inspectable anomaly and incident evidence rows with eval-unit handles, turn indexes, severity, evidence, primary reason, user-request previews, and longer incident result-preview details for error diagnosis.
- Adds a read-only `GET /api/plugins/ariadne-eval/sessions/{source_session_id}` dashboard API route for session-level inspection.
- Upgrades the bundled Hermes dashboard UI with session cards, an incident/anomaly timeline, and an expandable session inspector showing context, judge anomalies, deterministic incidents, and expandable/copyable trace details.
- Makes long dashboard evidence/session text copyable by avoiding button wrappers around copyable content, adding explicit copy controls, and using expandable full-detail blocks for incident and trace previews.
- Renders dashboard source timestamps as browser-local time labels while keeping raw session and eval-unit IDs visible for copying and inspection.
- Adds explicit Fold/Expand controls to session cards so evidence/facet bodies can be collapsed without losing the header, metrics, local time, or selection behavior.
- Uses the selected session-card visual shell for the loaded session inspector so the active session card and full-width inspector share the same card treatment.
- Lightens session-card, evidence, metric, and risk-score typography.
- Replaces raw JSON parse failures such as `Unexpected token <` with a clear plugin API restart hint when the dashboard receives HTML/non-JSON from Ariadne API routes.
- Reconciles the selected session after dashboard window changes so the inspector does not request a stale session outside the new summary window.

User-facing release text:
- Ariadne Eval's Hermes dashboard now groups anomalies by agent session and lets you inspect the relevant session context, incident/anomaly evidence, judge result, and trace details directly from the dashboard.

Verification:
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'` → `Ran 59 tests`, `OK`.
- `node --check src/agent_health/dashboard_plugin/dashboard/dist/index.js` → passed.
- `PYTHONPATH=src python3 -m unittest tests/test_dashboard_plugin.py` → `Ran 7 tests`, `OK`.
- Browser smoke against local Hermes dashboard → session cards render as `DIV`, no `button.ae-evidence`, computed `user-select: text`, no `Unexpected token` text, local `<time>` labels render with ISO `datetime` attributes, Fold/Expand toggles hide/show session bodies, card header has keyboard selection role, and session inspector loads data after dashboard restart.
- `npx --yes truthmark check --json` → no diagnostics.
- `npx --yes truthmark index --json` → 0 error diagnostics and 0 review diagnostics.
