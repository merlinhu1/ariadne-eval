---
status: active
doc_type: area-route
last_reviewed: 2026-05-19
source_of_truth:
  - ../../../.truthmark/config.yml
  - ../../ai/repo-rules.md
---

# Local Runtime Areas

## CLI And Sidecar Storage

Truth documents:
```yaml
truth_documents:
  - path: docs/truth/local-runtime/cli-and-sidecar-storage.md
    kind: behavior
```

Code surface:
- src/agent_health/cli.py
- src/agent_health/config.py
- src/agent_health/db.py
- src/agent_health/scheduler.py
- src/agent_health/scheduler_bootstrap.py
- src/agent_health/__init__.py
- examples/**
- tests/test_db_review_guards.py
- tests/test_cli_review_jobs.py
- tests/test_scheduler.py
- tests/test_scheduler_watchdog_contracts.py

Update truth when:
- CLI commands or arguments change
- sidecar SQLite schema changes
- scheduler task/run behavior changes
- judge config defaults, trigger command, or storage behavior changes
- Hermes-home path resolution or initialization behavior changes
- CLI package entry behavior changes
