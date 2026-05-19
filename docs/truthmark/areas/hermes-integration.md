---
status: active
doc_type: area-route
last_reviewed: 2026-05-19
source_of_truth:
  - ../../../.truthmark/config.yml
  - ../../ai/repo-rules.md
---

# Hermes Integration Areas

## Hermes Session Ingestion And Hooks

Truth documents:
```yaml
truth_documents:
  - path: docs/truth/hermes-integration/session-ingestion-and-hooks.md
    kind: behavior
```

Code surface:
- src/agent_health/adapters/**
- src/agent_health/hermes_plugin/**
- src/agent_health/events.py
- tests/test_hermes_reader.py

Update truth when:
- Hermes state.db schema reading changes
- hidden reasoning exclusion changes
- hook event capture, event previews, hashing, or fail-open behavior changes
