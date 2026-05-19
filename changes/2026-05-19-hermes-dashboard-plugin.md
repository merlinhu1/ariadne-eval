# Hermes Dashboard Plugin

Previous version: 0.1.0
New version: 0.2.0
Diff basis: current pyproject baseline when no release tag exists
Version action: minor
SemVer rationale: The pending payload adds backward-compatible public CLI and packaged dashboard plugin surfaces.

Release payload:
- Add Ariadne Eval versioning and change-note standards for future package release decisions.
- Add a read-only Hermes dashboard plugin tab for visualizing sidecar eval data.
- Add dashboard summary/detail query helpers for statuses, incidents, anomalies, timeline buckets, hot sessions, and judge-token totals.
- Add `agent-health dashboard install` to copy the bundled plugin into a Hermes home.
- Package dashboard plugin assets with the Python wheel.

User-facing release text:
- Ariadne Eval now includes an optional Hermes dashboard tab. Run `agent-health --hermes-home <home> dashboard install`, then reload the Hermes dashboard to view statuses, incidents, anomalies, and hot sessions from local `evals.db` data.

Verification:
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'` — 37 tests passed.
- `node --check src/agent_health/dashboard_plugin/dashboard/dist/index.js` — passed.
- Dashboard query smoke against `/opt/data/instruction-health/evals.db` — summary payload produced totals, timeline, and hot sessions.
- `npx --yes truthmark check --json` and `npx --yes truthmark index --json` — passed with no diagnostics.
- Python package build check skipped because this environment does not have the `build` or `hatchling` modules installed.
