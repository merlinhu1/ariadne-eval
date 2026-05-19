---
status: active
doc_type: area-route
last_reviewed: 2026-05-19
source_of_truth:
  - ../../../.truthmark/config.yml
  - ../../ai/repo-rules.md
---

# Hermes Integration Areas

## Hermes State.db Ingestion

Truth documents:
```yaml
truth_documents:
  - path: docs/truth/hermes-integration/state-db-ingestion.md
    kind: behavior
```

Code surface:
- src/agent_health/adapters/**
- tests/test_hermes_reader.py

Update truth when:
- Hermes state.db schema reading changes
- hidden reasoning exclusion changes
- session/message ordering or field selection changes
