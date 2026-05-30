---
status: active
doc_type: area-route
last_reviewed: 2026-05-30
source_of_truth:
  - ../../../.truthmark/config.yml
  - ../../ai/repo-rules.md
---

# Evaluation Model Areas

## Turn Cases, Case Signals, And Review Contracts

Truth documents:
```yaml
truth_documents:
  - path: docs/truth/evaluation/turn-cases-and-case-signals.md
    kind: behavior
```

Code surface:
- src/agent_health/normalize.py
- src/agent_health/reactions.py
- src/agent_health/signals.py
- src/agent_health/tool_outcome_taxonomy.py
- src/agent_health/tool_outcome_features.py
- src/agent_health/tool_outcome_reviewer_model.py
- src/agent_health/tool_outcome_routing.py
- src/agent_health/judge.py
- src/agent_health/prompts/**
- tests/test_normalize.py
- tests/test_db_review_guards.py
- tests/test_tool_outcome_reviewer_model.py
- tests/test_tool_outcome_features.py
- tests/test_tool_outcome_routing.py
- tests/test_judge_contract.py

Update truth when:
- user-turn boundary logic or turn-case building changes
- case-event, tool-interaction, or case-signal extraction changes
- tool outcome taxonomy, features, ML review routing, or reviewer model behavior changes
- turn-case or tool-outcome review prompt/schema validation changes
