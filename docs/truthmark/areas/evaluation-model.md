---
status: active
doc_type: area-route
last_reviewed: 2026-05-19
source_of_truth:
  - ../../../.truthmark/config.yml
  - ../../ai/repo-rules.md
---

# Evaluation Model Areas

## Eval Units, Signals, And Judge Contract

Truth documents:
```yaml
truth_documents:
  - path: docs/truth/evaluation/eval-units-and-signals.md
    kind: behavior
```

Code surface:
- src/agent_health/normalize.py
- src/agent_health/reactions.py
- src/agent_health/signals.py
- src/agent_health/incident_taxonomy.py
- src/agent_health/incident_features.py
- src/agent_health/incident_model.py
- src/agent_health/incident_routing.py
- src/agent_health/judge.py
- src/agent_health/prompts/**
- tests/test_normalize.py
- tests/test_db_and_signals.py
- tests/test_incident_model.py
- tests/test_incident_features.py
- tests/test_incident_routing.py
- tests/test_judge_contract.py

Update truth when:
- user-turn boundary logic changes
- trace/tool-message collection or context caps change
- deterministic signal thresholds, incident taxonomy, canonical incident examples/labels/predictions, or reaction classification change
- incident feature, ML decision, routing, or judge prompt/schema changes
- request judge prompt or health-status schema changes
