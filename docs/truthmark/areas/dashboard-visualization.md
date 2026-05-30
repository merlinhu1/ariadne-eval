---
status: active
doc_type: area-route
last_reviewed: 2026-05-24
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
- tests/test_dashboard_config_api.py

Update truth when:
- Dashboard summary/detail payload fields change
- Dashboard aggregation rules for request friction, request ranking, statuses, tool outcomes, findings, timeline buckets, session groups, or tokens change
- Hermes dashboard plugin install, manifest, API route, static asset, or UI behavior changes
- The dashboard gains or changes explicit write/control actions, including label capture, retraining, import, or eval behavior
