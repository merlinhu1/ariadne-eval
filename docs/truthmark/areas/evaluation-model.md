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
- src/agent_health/incidents.py
- src/agent_health/bumps.py
- src/agent_health/judge.py
- src/agent_health/prompts/**
- tests/test_normalize.py
- tests/test_db_and_signals.py
- tests/test_bumps.py
- tests/test_judge.py

Update truth when:
- user-turn boundary logic changes
- trace/tool-message collection or context caps change
- deterministic signal thresholds, incident extraction, or reaction classification change
- judge prompt or health-status schema changes
