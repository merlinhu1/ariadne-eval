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
  - ../../../src/agent_health/scheduler.py
  - ../../../src/agent_health/scheduler_bootstrap.py
  - ../../../src/agent_health/judge.py
  - ../../../src/agent_health/incident_model.py
  - ../../../src/agent_health/incident_routing.py
---

# CLI And Sidecar Storage

## Purpose

This behavior gives users a local command-line workflow and sidecar SQLite database for inspecting, importing, judging, querying request evaluation units, and managing ML-first tool-call incident examples.

## Scope

This doc owns CLI commands, Hermes-home initialization, judge config defaults, sidecar database schema, and local runtime behavior.

## Current Behavior

- The CLI exposes `init`, `inspect hermes`, `import hermes`, `units`, `incidents`, `signals`, ML-first `incident examples`, `incident export-training`, `incident label`, `incident judge-label`, `incident predict`, `incident train`, `eval`, `list`, `show`, `summary`, `dashboard install`, `scheduler`, and `schedule` commands.
- `init` creates the instruction-health home under the Hermes profile and migrates the sidecar eval database.
- `init` prints that Hermes dashboard support is available through an explicit opt-in plugin install.
- `init` also prints the judge provider locality caveat because the judge uses Hermes provider/model resolution by default.
- `import hermes` reads Hermes sessions, normalizes request eval units, stores trace events and deterministic signals, and also upserts normalized incident examples for assistant tool-call/immediate-result pairs.
- `units` lists recently imported eval units from the sidecar database.
- `incidents` lists canonical tool-call incident examples with their latest authoritative label and latest prediction without calling the LLM judge; `incidents --summary` reports counts by canonical labels and predictions.
- `signals` recomputes and stores deterministic signals for one eval unit.
- `eval --due` loads imported due units, recomputes deterministic evidence signals, applies deterministic priority prefiltering, builds aggressively trimmed judge payloads with a configurable judgement threshold (`strict`, `balanced`, or `relaxed`; default `strict`), requires the request-level LLM judge to return `request_friction_score`, stores `llm_evals` plus `anomalies` rows, and records provider-reported judge token usage. It then uses any remaining `--max-judge-calls` budget to label unlabeled tool-call incident examples through batched incident-specific judge calls and stores those labels in `incident_labels`, not request-level eval/anomaly tables. The default run considers at most 10 request candidates, skips priority-0 units, and is budget-gated to at most 5 total judge calls across both layers.
- `incident export-training` is the default ML-first incident training export and emits only accepted incident-specific labels from `incident_labels`; it excludes request anomaly tables, legacy incident reviews, deterministic signals, legacy rule labels, and ML fallback predictions.
- `incident label` inserts accepted human or human-correction labels for a stored incident example by example id or composite source key. Human labels receive higher training weights than incident LLM labels.
- `incident judge-label` sends incident examples to the incident-specific judge in configurable batches, stores accepted incident labels in `incident_labels`, supports `--since` current-window targeting, can prioritize budget-fallback/deferred/missing/low-confidence prediction gaps, and retries rows missing from a batch result one-by-one by default; it does not write request-level `llm_evals` or `anomalies`.
- `incident predict` loads the promoted ML-first incident model or an explicit model path, writes `incident_predictions` with routed LLM budget availability, optionally judges deferred examples while incident judge budget remains, and stores unavailable/over-budget best-effort ML fallback predictions without creating labels or leaving them marked as deferred.
- `incident train` trains from accepted incident labels, writes the model artifact, smoke-checks the artifact before recording an `incident_models` row, and auto-promotes by default only when the candidate has more accepted training records than the currently promoted model. `--no-auto-promote` disables promotion.
- Deterministic incident subtypes, the old `ml` command group, and the old separate incident-review table are removed rather than preserved for compatibility.
- Judge routing inherits Hermes models by trying configured `auxiliary.approval` first, then the Hermes main provider/model.
- `list`, `show`, and `summary` query latest judged results from the sidecar database; `list --details` prints request, next-user reaction, observed outcome, and anomaly evidence context for each row.
- The sidecar database exposes recent-unit listing plus a session-scoped unit lookup that filters by `source_session_id` and `since` before applying its limit, allowing dashboard session-detail views to inspect an older session without being crowded out by newer units from other sessions.
- The V1 sidecar SQLite schema includes eval units, trace events, deterministic signals, LLM evals, anomalies, incident eval examples, incident labels, incident predictions, incident model registry rows, eval state tables, eval task rows, and eval run rows. The canonical incident source of truth is `incident_eval_examples` plus `incident_labels` and `incident_predictions`.
- `dashboard install` copies the bundled dashboard plugin into `<hermes-home>/plugins/ariadne-eval/dashboard`. By default it also installs `<hermes-home>/scripts/ariadne_eval_scheduler_watchdog.py` and creates or updates a local-output Hermes cron job named `Ariadne Eval scheduler watchdog` on `every 10m`; the watchdog starts a scheduler daemon with `--poll-seconds 600` so dashboard-created eval tasks have a supervised, low-frequency scheduler consumer when Hermes cron is active. `--no-scheduler-watchdog` preserves tab-only installation for users who supervise `agent-health scheduler run` themselves.
- `schedule list`, `schedule show`, and `schedule runs` inspect eval task and run state without importing sessions, calling the judge, or creating eval runs. `schedule set` changes task configuration. `schedule pause` disables an existing task, `schedule resume` enables an existing task and marks it due at the current wall-clock time, and `schedule run-now` explicitly enables an existing task and marks it due at the current wall-clock time.
- Eval task updates by displayed task id or by task name mutate the original task row. Task creation and updates reject unsupported schedule kinds, including `cron`, and accept only `interval` and `continuous` schedules until cron parsing is implemented. Numeric scheduler limits and intervals are validated before storage so negative intervals, negative budgets, zero candidate limits, and invalid boolean controls do not enter eval task configuration.
- `scheduler tick` claims due enabled eval tasks and runs one scheduler pass. `scheduler run` polls for due work at `--poll-seconds` intervals and prints run summaries when work is performed.

## Core Rules

- Evaluator state lives under the Hermes profile in `instruction-health/`.
- V1 initialization creates `config.yaml`, `evals.db`, and `logs/`; it does not create `events.jsonl`.
- Judge routing belongs to the Hermes runtime: Ariadne should prefer `auxiliary.approval` when configured and then fall back to the Hermes main provider/model.
- Due selection should avoid budget spam: no realtime judging, no calls from import/list/show/signals, no re-eval after any prior request judgement unless `--reevaluate` is explicit, no-reaction units wait for the cooldown, deterministic prefiltering prioritizes corrections/tool errors/loops/prolonged runs, preflight trimming removes bulky low-value evidence before provider calls, and `--max-judge-calls` caps each invocation across request-level judging plus incident-specific labeling.
- Scheduler due selection is explicit: listing tasks or runs is read-only, while `schedule run-now` and `schedule resume` only mark enabled tasks due for the scheduler rather than running evaluation inline.
- CLI errors should return a non-zero command result and print a concise error message.
- Accepted ML-first incident training labels may come only from `incident_llm_judge`, `human`, or `human_correction`. Request anomaly labels, ML self-predictions, and legacy deterministic/rule labels are rejected as accepted incident training rows.

## Flows And States

- Init flow: resolve Hermes home, create config/log paths, migrate SQLite, print the dashboard opt-in note and judge provider caveat.
- Import flow: discover sessions, normalize each session into request units and incident examples, upsert units and trace events, replace deterministic evidence signals, and upsert incident examples.
- Eval flow: a manual `agent-health eval --due` command loads due units, extracts deterministic evidence signals, applies deterministic priority and max-call budget gates, trims bulky judge evidence, calls the request-level judge, and stores `llm_evals` plus `anomalies` rows. If request judging does not consume the invocation budget, the same command spends the remaining judge-call budget on batched incident-specific judging for unlabeled incident examples and stores accepted labels in `incident_labels`.
- ML-first incident flow: `incident export-training` writes accepted incident-label JSONL rows, `incident train` writes model artifacts below `instruction-health/incident-models/<model_version>/` by default, `incident predict` stores prediction records, and `incident judge-label` or deferred prediction judging stores accepted incident labels.
- Scheduler flow: an eval task stores schedule configuration, next due time, cursor state, and budget limits. `schedule run-now` marks a task due now without creating a run; the next scheduler tick claims the task, imports Hermes sessions oldest-first for scheduler cursor safety, evaluates due units with one shared task budget for request judging and incident labeling, records an eval run, and advances the next due time. Running scheduler workers heartbeat their lease between long phases, and a stale worker cannot later finalize a run that has already failed or been reclaimed.

## Contracts

- The placeholder console command is `agent-health`.
- The default eval DB path is `<hermes-home>/instruction-health/evals.db`.
- The recorded eval schema version is `eval_schema_v1`.
- Incident example uniqueness is enforced over `(source_session_id, assistant_tool_call_message_id, result_message_id, tool_call_id)`.
- Incident model registry rows persist across normal sidecar migrations, can be listed for dashboard selection, and promotion keeps exactly one promoted model row while preserving previous artifact registry rows for rollback.

## Product Decisions

- Decision (2026-05-19): The MVP uses a local SQLite sidecar database rather than JSONL for evaluations.
- Decision (2026-05-24): Recurring evaluation uses explicit local eval tasks and scheduler commands. Listing task state is read-only; run-now/resume only mark tasks due, and scheduler ticks perform the actual import/evaluation work.
- Decision (2026-05-19): LLM eval and anomaly tables are part of V1 because judged ratings are the core output.
- Decision (2026-05-23): ML-first incident training uses accepted incident-specific LLM/human labels by default. Rule-labeled and reviewed legacy classifier commands were removed rather than preserved as compatibility surfaces.
- Decision (2026-05-23): Incident model auto-promotion is count-driven and disableable: a smoke-checked candidate is promoted by default when its accepted incident training-record count exceeds the current promoted model, while `--no-auto-promote` records the smoke-checked candidate without promotion.
- Decision (2026-05-23): The normal `eval --due` batch orchestrates request-level anomaly judging first, then incident-specific LLM labeling with the remaining judge budget, while keeping the two storage outputs separate.

- Decision (2026-05-20): User incident reviews are captured as explicit sidecar labels and only affect model behavior after an auditable retraining command; no online learning happens during import, eval, or dashboard browsing.

## Rationale

A CLI plus SQLite keeps the MVP inspectable and useful without requiring passive hook capture, a standalone dashboard, or hosted observability system. The opt-in Hermes dashboard plugin reuses that same SQLite data for visualization, and the scheduler reuses the explicit local CLI import/eval path through stored eval tasks rather than realtime background capture. Keeping request judge/anomaly tables separate from incident examples, labels, predictions, and models preserves the different training targets while supporting both workflows in one sidecar database.

## Non-Goals

- This doc does not own future Hermes hook internals.
- This doc owns the install command for the Hermes dashboard plugin but not the dashboard query/API/UI behavior.

## Maintenance Notes

- Update this doc when CLI commands, scheduler behavior, database tables, judge config, model registry behavior, or initialization paths change.
- Related tests currently include `tests/test_db_and_signals.py`, `tests/test_cli.py`, `tests/test_scheduler.py`, `tests/test_scheduler_cli_and_plugin.py`, `tests/test_incident_model.py`, `tests/test_incident_features.py`, `tests/test_incident_routing.py`, and `tests/test_judge_contract.py`.
