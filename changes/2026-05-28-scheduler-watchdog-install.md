# Scheduler Watchdog Install

Previous version: 0.4.0
New version: 0.5.0
Diff basis: current pyproject baseline; no release tag found
Version action: minor
SemVer rationale: The dashboard install command gains a backward-compatible scheduler watchdog installation so recurring eval tasks created from the dashboard can execute without separate manual daemon setup.

Release payload:
- Install a quiet Ariadne Eval scheduler watchdog script during `agent-health dashboard install` by default.
- Create or update a local-output Hermes cron job named `Ariadne Eval scheduler watchdog` on `every 10m` so the scheduler daemon is started when needed, with the scheduler daemon itself polling due tasks every 600 seconds by default.
- Show dashboard task intervals in hours and convert them to stored `interval_seconds` on save.
- Add `--no-scheduler-watchdog`, `--watchdog-schedule`, and `--scheduler-poll-seconds` for users who want external supervision or custom watchdog timing.

User-facing release text:
- `agent-health dashboard install` now installs the dashboard tab plus a scheduler watchdog by default, so eval schedules configured in the Ariadne Eval dashboard have a runtime consumer. The watchdog and scheduler use a low-frequency 10-minute cadence by default, and dashboard task intervals are edited in hours. Use `--no-scheduler-watchdog` if you supervise `agent-health scheduler run` yourself.

Verification:
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v` — 60 tests passed.
- `/opt/data/node/bin/truthmark check --json` — 0 error diagnostics; existing/generated-surface review diagnostics remain.
- `/opt/data/node/bin/truthmark index --json` — 0 error diagnostics; existing area-index review diagnostic remains for `tests/test_dashboard_config_api.py`.
- `python3 -m build` — skipped/unavailable because the active Python environment does not have the `build` module installed.
