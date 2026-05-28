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
- eval-unit normalization changes
- reaction classification, deterministic signal, canonical incident-example, incident-label, or incident-model behavior changes
- judge prompt or health-status schema changes

## Dashboard Visualization

Area files:
- docs/truthmark/areas/dashboard-visualization.md

Code surface:
- src/agent_health/dashboard_queries.py
- src/agent_health/dashboard_plugin/**
- tests/test_dashboard_queries.py

Update truth when:
- Dashboard summary/detail payloads change
- Hermes dashboard plugin install, manifest, API, or UI behavior changes

## Local Runtime

Area files:
- docs/truthmark/areas/local-runtime.md

Code surface:
- src/agent_health/cli.py
- src/agent_health/config.py
- src/agent_health/db.py
- src/agent_health/scheduler.py
- src/agent_health/scheduler_bootstrap.py
- src/agent_health/__init__.py
- examples/**
- tests/test_db_and_signals.py
- tests/test_cli.py
- tests/test_scheduler.py
- tests/test_scheduler_bootstrap.py

Update truth when:
- CLI commands, local paths, judge config, or sidecar database schema change
