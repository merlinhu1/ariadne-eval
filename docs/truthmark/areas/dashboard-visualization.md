---
status: active
doc_type: area-route
last_reviewed: 2026-05-19
source_of_truth:
  - ../../../.truthmark/config.yml
  - ../../ai/repo-rules.md
---

# Dashboard Visualization Areas

## Hermes Dashboard Plugin

Truth documents:
```yaml
truth_documents:
  - path: docs/truth/dashboard/hermes-dashboard-plugin.md
    kind: behavior
```

Code surface:
- src/agent_health/dashboard_queries.py
- src/agent_health/dashboard_plugin/**
- tests/test_dashboard_queries.py
- tests/test_dashboard_plugin.py

Update truth when:
- Dashboard summary/detail payload fields change
- Dashboard aggregation rules for statuses, incidents, anomalies, timeline buckets, hot sessions, or tokens change
- Hermes dashboard plugin install, manifest, API route, static asset, or UI behavior changes
- The dashboard stops being read-only or starts triggering import/eval behavior
