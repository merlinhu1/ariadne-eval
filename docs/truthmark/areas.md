---
status: active
doc_type: route-index
last_reviewed: 2026-05-19
source_of_truth:
  - ../../.truthmark/config.yml
---

# Truthmark Areas

## Hermes Integration

Area files:
- docs/truthmark/areas/hermes-integration.md

Code surface:
- src/agent_health/adapters/**
- tests/test_hermes_reader.py

Update truth when:
- Hermes state.db reading changes
- Hidden reasoning exclusion changes

## Evaluation Model

Area files:
- docs/truthmark/areas/evaluation-model.md

Code surface:
- src/agent_health/normalize.py
- src/agent_health/reactions.py
- src/agent_health/signals.py
- src/agent_health/incidents.py
- src/agent_health/judge.py
- src/agent_health/prompts/**
- tests/test_normalize.py
- tests/test_db_and_signals.py
- tests/test_incidents.py
- tests/test_judge.py

Update truth when:
- eval-unit normalization changes
- reaction classification, deterministic signal, or incident-extraction behavior changes
- judge prompt or health-status schema changes

## Local Runtime

Area files:
- docs/truthmark/areas/local-runtime.md

Code surface:
- src/agent_health/cli.py
- src/agent_health/config.py
- src/agent_health/db.py
- src/agent_health/__init__.py
- examples/**
- tests/test_db_and_signals.py
- tests/test_cli.py

Update truth when:
- CLI commands, local paths, judge config, or sidecar database schema change
