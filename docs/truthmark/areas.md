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
- src/agent_health/hermes_plugin/**
- src/agent_health/events.py
- tests/test_hermes_reader.py

Update truth when:
- Hermes state.db reading changes
- Hermes plugin hook capture or event-cache behavior changes

## Evaluation Model

Area files:
- docs/truthmark/areas/evaluation-model.md

Code surface:
- src/agent_health/normalize.py
- src/agent_health/reactions.py
- src/agent_health/signals.py
- src/agent_health/prompts/**
- tests/test_normalize.py
- tests/test_db_and_signals.py

Update truth when:
- eval-unit normalization changes
- reaction classification or deterministic signal behavior changes
- judge prompt inputs or expected output contracts change

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

Update truth when:
- CLI commands, local paths, or sidecar database schema change
