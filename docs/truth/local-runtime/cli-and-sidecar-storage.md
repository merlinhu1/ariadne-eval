---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-19
source_of_truth:
  - ../../truthmark/areas/local-runtime.md
  - ../../../src/agent_health/cli.py
  - ../../../src/agent_health/config.py
  - ../../../src/agent_health/db.py
  - ../../../src/agent_health/judge.py
---

# CLI And Sidecar Storage

## Purpose

This behavior gives users a local command-line workflow and sidecar SQLite database for inspecting, importing, judging, and querying instruction-health evaluation units.

## Scope

This doc owns CLI commands, Hermes-home initialization, judge config defaults, sidecar database schema, and local runtime behavior.

## Current Behavior

- The CLI exposes `init`, `inspect hermes`, `import hermes`, `units`, `incidents`, `signals`, `eval`, `list`, `show`, `summary`, and `dashboard install` commands.
- `init` creates the instruction-health home under the Hermes profile and migrates the sidecar eval database.
- `init` prints that Hermes dashboard support is available through an explicit opt-in plugin install.
- `init` also prints the judge provider locality caveat because the judge uses Hermes provider/model resolution by default.
- `import hermes` reads Hermes sessions, normalizes eval units, stores trace events, and stores deterministic signals.
- `units` lists recently imported eval units from the sidecar database.
- `incidents` lists deterministic event-level incidents without calling the LLM judge; `incidents --summary` reports counts by incident type and severity.
- `signals` recomputes and stores deterministic signals for one eval unit.
- `eval --due` loads imported due units, recomputes deterministic signals, applies deterministic priority prefiltering, builds aggressively trimmed judge payloads with a configurable judgement threshold (`strict`, `balanced`, or `relaxed`; default `strict`), calls the LLM judge for selected units, stores `llm_evals` plus `anomalies` rows, and records provider-reported judge token usage. The default run considers at most 10 candidates, skips priority-0 units, and is budget-gated to at most 5 judge calls.
- Event-level incident listing is deterministic and separate from LLM judging: one failed/error-looking tool event produces one `tool_error` incident.
- Judge routing inherits Hermes models by trying configured `auxiliary.compression` first, then the Hermes main provider/model.
- `list`, `show`, and `summary` query latest judged results from the sidecar database; `list --details` prints request, next-user reaction, observed outcome, and anomaly evidence context for each row.
- The V1 sidecar SQLite schema includes eval units, trace events, deterministic signals, LLM evals, anomalies, and eval state tables.
- `dashboard install` copies the bundled dashboard plugin into `<hermes-home>/plugins/ariadne-eval/dashboard`.

## Core Rules

- Evaluator state lives under the Hermes profile in `instruction-health/`.
- V1 initialization creates `config.yaml`, `evals.db`, and `logs/`; it does not create `events.jsonl`.
- Judge routing belongs to the Hermes runtime: Ariadne should prefer `auxiliary.compression` when configured and then fall back to the Hermes main provider/model.
- Due selection should avoid budget spam: no realtime judging, no calls from import/list/show/signals, no re-eval after any prior judgement unless `--reevaluate` is explicit, no-reaction units wait for the cooldown, deterministic prefiltering prioritizes corrections/tool errors/loops/prolonged runs, preflight trimming removes bulky low-value evidence before provider calls, and `--max-judge-calls` caps each invocation.
- CLI errors should return a non-zero command result and print a concise error message.

## Flows And States

- Init flow: resolve Hermes home, create config/log paths, migrate SQLite, print the dashboard opt-in note and judge provider caveat.
- Import flow: discover sessions, normalize each session into units, upsert units and trace events, replace deterministic signals.
- Eval flow: a manual `agent-health eval --due` command loads due units, extracts signals, applies deterministic priority and max-call budget gates, trims bulky judge evidence, calls the judge, and stores `llm_evals` plus `anomalies` rows.
- Scheduling is not a V1 runtime behavior; future cron/systemd automation may invoke the same manual eval command.

## Contracts

- The placeholder console command is `agent-health`.
- The default eval DB path is `<hermes-home>/instruction-health/evals.db`.
- The recorded eval schema version is `eval_schema_v1`.

## Product Decisions

- Decision (2026-05-19): The MVP uses a local SQLite sidecar database rather than JSONL for evaluations.
- Decision (2026-05-19): Manual CLI batches trigger the judge in V1; scheduled background evaluation is only optional later automation around the same command.
- Decision (2026-05-19): LLM eval and anomaly tables are part of V1 because judged ratings are the core output.

## Rationale

A CLI plus SQLite keeps the MVP inspectable and useful without committing to a scheduler, passive hook plugin, standalone dashboard, or hosted observability system. The opt-in Hermes dashboard plugin reuses that same SQLite data for visualization. Keeping judge/anomaly tables in the schema supports the core rating workflow without requiring the dashboard path.

## Non-Goals

- This doc does not own future Hermes hook internals.
- This doc owns the install command for the Hermes dashboard plugin but not the dashboard query/API/UI behavior.

## Maintenance Notes

- Update this doc when CLI commands, database tables, judge config, or initialization paths change.
- Related tests currently include `tests/test_db_and_signals.py`, `tests/test_cli.py`, and `tests/test_judge.py`.
