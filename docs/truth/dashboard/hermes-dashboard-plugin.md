---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-19
source_of_truth:
  - ../../truthmark/areas/dashboard-visualization.md
  - ../../../src/agent_health/dashboard_queries.py
  - ../../../src/agent_health/dashboard_plugin/
---

# Hermes Dashboard Plugin

## Purpose

Ariadne Eval provides a read-only Hermes dashboard tab so local instruction-health data can be visualized without creating a separate UI server.

## Scope

This doc owns dashboard query payload behavior, the bundled Hermes dashboard plugin files, and plugin API/UI contracts.

## Current Behavior

- `dashboard_summary(db, since, bucket_seconds, unit_limit)` reads the local `evals.db` sidecar and returns one JSON-compatible payload for the dashboard.
- The summary payload includes total eval units, judged turns, deterministic incidents, judge anomalies, latest health-status counts, top incident types, top anomaly types, judge token totals, timeline buckets, and hot sessions.
- Incident counts are derived by loading eval units with trace events and running the deterministic incident extractor; the dashboard does not persist a second incident table.
- Anomaly/status/token counts are derived from the latest stored LLM eval per eval unit, matching CLI summary behavior.
- `eval_unit_detail(db, eval_unit_id)` returns one eval unit's unit metadata, trace events, deterministic signals, deterministic incidents, and latest LLM eval with anomalies.
- The bundled dashboard plugin manifest registers the Hermes tab as `ariadne-eval` at `/ariadne-eval` and points to `dist/index.js`, `dist/style.css`, and `plugin_api.py`.
- The plugin API exposes read-only `GET /summary` and `GET /units/{eval_unit_id}` routes under Hermes' `/api/plugins/ariadne-eval/` mount.
- The browser tab visualizes the summary as status bars, top incident/anomaly chips, judge-token totals, and hot sessions.
- The dashboard path is read-only: it does not import Hermes sessions, call the LLM judge, mutate eval rows, or schedule evaluation.

## Core Rules

- The Hermes dashboard plugin must visualize existing sidecar data rather than becoming another evaluator control plane.
- Dashboard API routes should be thin wrappers around `dashboard_queries.py`.
- The plugin is opt-in; state.db ingestion and CLI evaluation must continue working without dashboard installation.
- Dashboard assets are copied into `<hermes-home>/plugins/ariadne-eval/dashboard` by the CLI install command.

## Contracts

- Plugin name: `ariadne-eval`.
- Tab path: `/ariadne-eval`.
- Plugin API summary route: `/api/plugins/ariadne-eval/summary`.
- Plugin API detail route: `/api/plugins/ariadne-eval/units/{eval_unit_id}`.
- Default dashboard window: `24h`.
- Default timeline bucket size: `3600` seconds.

## Product Decisions

- Decision (2026-05-19): The first web visualization is a Hermes dashboard plugin tab, not a standalone UI.
- Decision (2026-05-19): Dashboard routes are read-only and operate over already-imported/evaluated sidecar data.

## Rationale

Hermes already provides the dashboard shell, plugin tab registry, static asset mounting, and plugin API routing. Reusing that shell keeps Ariadne Eval local-first and avoids a separate server while still making the visualization first-class.

## Maintenance Notes

- Update this doc when dashboard query fields, plugin manifest fields, API routes, or install behavior change.
- Related tests currently include `tests/test_dashboard_queries.py` and `tests/test_dashboard_plugin.py`.
