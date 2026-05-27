# ML-First Tool-Call Incident Eval Implementation Plan

## Goal

Implement the ML-first tool-call incident evaluator described in `research/ml_first_incident_eval_design.md` as a separate incident layer over Hermes `state.db` tool-call/result records.

The implementation must preserve two layers:

- **Request anomaly eval**: one user request / assistant response unit, trained from request-level LLM evals and request-level reviews, using request/anomaly models or judges.
- **Tool-call incident eval**: one agent tool call plus its immediate result, trained from accepted incident-specific LLM/human labels, using the incident ML model.

Request-level anomaly labels must not become incident labels. Tool-call incidents may become evidence for request-level anomaly evaluation, but they are not the same training target.

The default mature path is ML-first incident evaluation. Deterministic and rule-labeled rows remain legacy/bootstrap only. Accepted incident-specific LLM labels and human labels are the default training data. When the incident LLM budget is exhausted, default behavior is best-effort ML fallback with `budget_fallback=true`, not conservative `unsure`.

## Architecture

Hermes `state.db` remains the source of truth for raw session messages. Ariadne Eval adds incident-specific normalization, storage, labeling, prediction, and model artifact tracking beside the existing request-level `eval_units` / `llm_evals` / `anomalies` path.

Target flow:

```text
Hermes state.db messages
  -> join assistant tool_calls to immediate tool result messages
  -> incident_eval_examples
  -> feature builder
  -> local incident ML model
       -> confident not_incident/incident: persist prediction
       -> unsure and budget available: incident LLM judge, persist accepted incident label
       -> unsure and budget exhausted: persist best-effort ML prediction with budget_fallback=true
  -> accepted incident labels export
  -> train incident model artifact
  -> smoke-check artifact
  -> auto-promote when accepted incident training_record_count increases
```

Deterministic request signals can provide evidence, but operational incident training and prediction use only `incident_eval_examples`, `incident_labels`, and `incident_predictions`.

## Tech Stack

- Python 3.11+.
- SQLite sidecar database under the existing Hermes `instruction-health/evals.db` path.
- Standard library `unittest` tests.
- Optional `scikit-learn` extra from `ariadne-eval[ml]` for TF-IDF plus calibrated logistic regression.
- Existing Hermes provider/model routing in `judge.py`, with a new incident-specific prompt/schema separate from request anomaly judging.

## Milestone 1: Incident Example Normalization And Persistence

### Task 1.1: Add incident example schema

Objective: Create incident-specific tables without overloading request-level eval, trace, or anomaly tables.

Files to create/modify:

- Modify `src/agent_health/db.py`.
- Modify `tests/test_db_and_signals.py`.

Tests to add/modify:

- Add a migration test asserting these tables and indexes exist:
  - `incident_eval_examples`
  - `incident_labels`
  - `incident_predictions`
  - `incident_models`
- Assert `incident_eval_examples` has a uniqueness constraint over `(source_session_id, assistant_tool_call_message_id, result_message_id, tool_call_id)`.
- Assert request-level tables still exist unchanged.

Implementation notes:

- Add schema columns for `incident_eval_examples`:
  - `id TEXT PRIMARY KEY`
  - `framework TEXT NOT NULL`
  - `source_session_id TEXT NOT NULL`
  - `source_event_id TEXT NOT NULL`
  - `eval_unit_id TEXT`
  - `source_turn_index INTEGER`
  - `assistant_tool_call_message_id TEXT NOT NULL`
  - `result_message_id TEXT NOT NULL`
  - `tool_call_id TEXT NOT NULL`
  - `tool_name TEXT`
  - `tool_arguments TEXT`
  - `tool_result TEXT`
  - `result_timestamp REAL`
  - `user_request_excerpt TEXT`
  - `prior_assistant_visible_text TEXT`
  - `following_assistant_visible_text TEXT`
  - `explicit_caller_expectation TEXT`
  - `explicit_caller_interpretation TEXT`
  - `upstream_intent_source TEXT`
  - `normalization_version TEXT NOT NULL`
  - `raw_payload_json TEXT`
  - `created_at REAL NOT NULL`
  - `updated_at REAL NOT NULL`
- Add `incident_labels` with authoritative training labels only:
  - `example_id TEXT NOT NULL REFERENCES incident_eval_examples(id) ON DELETE CASCADE`
  - `label TEXT NOT NULL`
  - `reason_code TEXT`
  - `reason_confidence REAL`
  - `label_source TEXT NOT NULL`
  - `label_source_version TEXT`
  - `accepted_for_training INTEGER NOT NULL DEFAULT 0`
  - `weight REAL NOT NULL DEFAULT 1.0`
  - `reviewer TEXT`
  - `comment TEXT`
  - `created_at REAL NOT NULL`
- Add `incident_predictions` for ML, LLM decision records, and budget fallbacks:
  - `example_id TEXT NOT NULL REFERENCES incident_eval_examples(id) ON DELETE CASCADE`
  - `label TEXT NOT NULL`
  - `is_incident INTEGER`
  - `reason_code TEXT`
  - `reason_confidence REAL`
  - `confidence REAL`
  - `uncertainty REAL`
  - `decision_source TEXT NOT NULL`
  - `model_name TEXT`
  - `model_version TEXT`
  - `should_defer_to_llm INTEGER NOT NULL DEFAULT 0`
  - `llm_budget_available INTEGER`
  - `budget_fallback INTEGER NOT NULL DEFAULT 0`
  - `evidence_json TEXT`
  - `created_at REAL NOT NULL`
- Add `incident_models`:
  - `id TEXT PRIMARY KEY`
  - `model_name TEXT NOT NULL`
  - `model_version TEXT NOT NULL`
  - `artifact_path TEXT NOT NULL`
  - `training_record_count INTEGER NOT NULL`
  - `accepted_label_count INTEGER NOT NULL`
  - `metrics_json TEXT`
  - `promoted INTEGER NOT NULL DEFAULT 0`
  - `created_at REAL NOT NULL`
  - `promoted_at REAL`
- Valid labels: `incident`, `not_incident`, `unsure`.
- Valid reason codes: `execution_error`, `no_result`, `bad_request`, `bad_output`, `other`, `NULL`.

Verification commands:

```sh
python -m unittest tests.test_db_and_signals
```

Expected result: database migration tests pass and request-level persistence behavior remains unchanged.

### Task 1.2: Add DB accessors for incident examples, labels, predictions, and model records

Objective: Provide explicit persistence APIs so later code does not write SQL ad hoc.

Files to create/modify:

- Modify `src/agent_health/db.py`.
- Modify `tests/test_db_and_signals.py`.

Tests to add/modify:

- `upsert_incident_example()` is idempotent on the composite source key.
- `list_incident_examples()` filters by `source_session_id`, `limit`, and unlabeled/prediction status as needed by later CLI tasks.
- `insert_incident_label()` rejects request anomaly sources and legacy deterministic sources for accepted ML-first training labels.
- `export_accepted_incident_training()` returns only `accepted_for_training=1` rows from allowed sources.
- `insert_incident_prediction()` persists `ml_model_budget_fallback` with `budget_fallback=1` and does not create a training label.
- `record_incident_model()` and `promote_incident_model()` maintain exactly one promoted model.

Implementation notes:

- Allowed accepted training `label_source` values:
  - `incident_llm_judge`
  - `human`
  - `human_correction`
- Disallowed accepted training `label_source` values:
  - `request_anomaly_label`
  - `ml_self_prediction`
  - `old_deterministic_label`
  - `deterministic_rule`
- Apply weights in DB helpers:
  - `human_correction`: `3.5`
  - `human`: `3.0`
  - high-confidence incident LLM: `1.5`
  - normal incident LLM: `1.0`
- Dashboard feedback writes accepted human labels directly into `incident_labels`.

Verification commands:

```sh
python -m unittest tests.test_db_and_signals
```

Expected result: DB helper tests prove accepted training rows come only from incident-specific LLM/human labels.

### Task 1.3: Normalize assistant tool calls to immediate tool results

Objective: Recover one incident example per assistant tool call/result pair from Hermes messages.

Files to create/modify:

- Modify `src/agent_health/normalize.py`.
- Modify `tests/test_normalize.py`.

Tests to add/modify:

- Assistant message with two `tool_calls` and two following tool messages creates two incident examples with distinct composite source IDs.
- `tool_call_id` reused in different sessions does not collide.
- The example stores `assistant_tool_call_message_id`, `result_message_id`, `tool_call_id`, `tool_name`, `tool_arguments`, and `tool_result`.
- Missing result creates no complete incident example.
- Existing request-level `normalize_session()` behavior remains unchanged.
- Hidden reasoning/provider fields are never read or copied.

Implementation notes:

- Add a new helper such as `normalize_incident_examples(session, messages, *, ...)`.
- Parse assistant `tool_calls` JSON defensively.
- Match result messages by `tool_call_id` and choose the immediate next tool-role result for that call.
- Use `assistant_tool_call_message_id` for the assistant message that emitted `tool_calls`, not the later final assistant response.
- Derive `source_event_id` from session id, assistant tool-call message id, result message id, and tool-call id.
- Include visible context only:
  - current user request excerpt
  - previous visible assistant text
  - following visible assistant text
- Set explicit expectation fields only when already present as non-hidden source runtime fields. Do not synthesize caller intent.

Verification commands:

```sh
python -m unittest tests.test_normalize
```

Expected result: incident examples are produced from the assistant-tool-call/result join while request eval-unit normalization tests still pass.

### Task 1.4: Import incident examples from Hermes

Objective: Store normalized incident examples during Hermes import without changing the request anomaly import path semantics.

Files to create/modify:

- Modify `src/agent_health/adapters/hermes.py`.
- Modify `src/agent_health/cli.py`.
- Modify `tests/test_hermes_reader.py`.
- Modify `tests/test_cli.py`.

Tests to add/modify:

- Hermes reader continues excluding reasoning fields.
- `import-hermes` upserts request `eval_units` and incident examples.
- CLI output reports incident example import counts without embedding local DB row counts in docs.
- Existing import tests and parser tests still pass.

Implementation notes:

- Add `HermesAdapter.normalize_incident_examples(raw_source)`.
- In `cmd_import_hermes`, after request-unit import, call the new incident normalizer and `EvalDB.upsert_incident_example()`.
- Do not make the dashboard import or judge anything.
- Keep the Hermes dashboard read-only over `evals.db` except explicit review writes already present.

Verification commands:

```sh
python -m unittest tests.test_hermes_reader tests.test_cli tests.test_db_and_signals
```

Expected result: import persists both request eval units and incident examples, while hidden reasoning exclusion remains enforced.

Commit boundary: `incident example schema and Hermes normalization`.

## Milestone 2: Incident Labeling Path

### Task 2.1: Add incident taxonomy constants for four-label decisions

Objective: Define the ML-first incident decision labels and reason codes independently from legacy incident-type strings.

Files to create/modify:

- Modify `src/agent_health/incident_taxonomy.py`.
- Modify `tests/test_incident_model.py` or add `tests/test_incident_taxonomy.py`.

Tests to add/modify:

- Assert decision labels are exactly `incident`, `not_incident`, `unsure`.
- Assert reason codes are exactly `execution_error`, `no_result`, `bad_request`, `bad_output`, `other`.
- Assert deleted legacy labels are rejected.

Implementation notes:

- Add helpers such as `validate_incident_decision_label()` and `validate_reason_code()`.

Verification commands:

```sh
python -m unittest tests.test_incident_model
```

Expected result: taxonomy tests pass and deleted legacy incident labels are rejected.

### Task 2.2: Add incident-specific LLM judge prompt and validator

Objective: Create a judge path that labels tool-call incidents only, separate from request anomaly judging.

Files to create/modify:

- Modify `src/agent_health/judge.py`.
- Create `src/agent_health/prompts/incident_judge.md`.
- Modify `tests/test_judge.py`.

Tests to add/modify:

- `build_incident_judge_payload(example, prediction)` includes tool arguments, immediate tool result, ML prediction, uncertainty, compact visible context, and explicit expectation fields only if present.
- Payload does not include request-level anomaly labels.
- Payload does not include hidden reasoning fields.
- `validate_incident_eval_json()` accepts strict JSON with `label`, optional `reason_code`, `confidence`, and evidence summary.
- Validator rejects labels outside `incident`, `not_incident`, `unsure`.
- Validator rejects request anomaly types as incident labels.

Implementation notes:

- Keep existing `validate_eval_json()` for request anomaly eval unchanged.
- Add a separate prompt version constant for incident judging.
- Require JSON output only.
- Treat `reason_code` as nullable metadata, not as the primary label.

Verification commands:

```sh
python -m unittest tests.test_judge
```

Expected result: request judge tests still pass and incident judge schema tests prove the layer separation.

### Task 2.3: Add CLI command to label incident examples with the incident judge

Objective: Route selected incident examples to the incident-specific LLM judge and persist accepted labels.

Files to create/modify:

- Modify `src/agent_health/cli.py`.
- Modify `src/agent_health/judge.py`.
- Modify `tests/test_cli.py`.
- Modify `tests/test_judge.py`.
- Modify `tests/test_db_and_signals.py`.

Tests to add/modify:

- Parser exposes an incident-specific command, for example `agent-health incident label`.
- Command uses incident examples, not request `eval_units`, as its work queue.
- Command honors a max incident judge call budget.
- Successful judge result creates `incident_labels` with `label_source='incident_llm_judge'` and `accepted_for_training=1`.
- Failed or malformed judge result stores no accepted training label.
- Existing `agent-health eval --due` request anomaly judge behavior remains unchanged.

Implementation notes:

- Prefer a new command namespace such as `incident examples`, `incident label`, `incident predict`, `incident train`, `incident promote`.
- Do not reuse `ml export-training` defaults for this path.
- Store token usage if practical, but do not mix incident label rows into request `llm_evals`.

Verification commands:

```sh
python -m unittest tests.test_cli tests.test_judge tests.test_db_and_signals
```

Expected result: incident LLM labeling exists and is separate from request-level `eval --due`.

### Task 2.4: Add human label ingestion for incident examples

Objective: Allow human labels and corrections to become high-weight accepted incident training rows.

Files to create/modify:

- Modify `src/agent_health/cli.py`.
- Modify `src/agent_health/db.py`.
- Modify `tests/test_cli.py`.
- Modify `tests/test_db_and_signals.py`.

Tests to add/modify:

- Parser supports human label insertion by `example_id` or composite source key.
- Human label creates `label_source='human'`, `accepted_for_training=1`, `weight=3.0`.
- Human correction creates `label_source='human_correction'`, `accepted_for_training=1`, `weight=3.5`.
- Request anomaly labels cannot be inserted through this command.

Implementation notes:

- Dashboard feedback and CLI labeling write accepted labels to `incident_labels`.

Verification commands:

```sh
python -m unittest tests.test_cli tests.test_db_and_signals
```

Expected result: human/correction labels export as accepted incident training records with higher weights.

Commit boundary: `incident-specific judge and accepted label persistence`.

## Milestone 3: Baseline Local Incident ML

### Task 3.1: Add feature builder for incident examples

Objective: Build structured and text features from normalized incident examples without brittle label-generating rules.

Files to create/modify:

- Create `src/agent_health/incident_features.py`.
- Add `tests/test_incident_features.py`.

Tests to add/modify:

- Feature builder includes `tool_name`, normalized arguments text, result text snippets, parsed exit code, structured error fields, truncation/empty result flags, and result length.
- Feature builder does not include hidden reasoning.
- Feature builder does not synthesize caller expectation.
- Feature builder marks missing required fields as insufficient for classification.

Implementation notes:

- Structured evidence such as `exit_code != 0` is a feature, not a deterministic label.
- Keep result parsing conservative and lossless.
- Return a serializable feature dict suitable for model adapters and judge payloads.

Verification commands:

```sh
python -m unittest tests.test_incident_features
```

Expected result: feature tests show structured/tool text features are available without converting rules into labels.

### Task 3.2: Replace tiny classifier contract with ML-first incident prediction contract

Objective: Add the three-label prediction output contract.

Files to create/modify:

- Modify `src/agent_health/incident_model.py`.
- Modify `tests/test_incident_model.py`.

Tests to add/modify:

- `IncidentPrediction` or a new `IncidentDecision` includes:
  - `label`
  - `is_incident`
  - `reason_code`
  - `reason_confidence`
  - `confidence`
  - `uncertainty`
  - `decision_source`
  - `model_name`
  - `model_version`
  - `should_defer_to_llm`
  - `budget_fallback`
  - `evidence_summary`
- Label validation rejects legacy subtype labels in the ML-first decision object.

Implementation notes:

- Introduce a stable adapter interface such as:

```python
class IncidentModel:
    model_name: str
    model_version: str
    def train(self, dataset_path: str, output_dir: str) -> ModelArtifact: ...
    def predict(self, features: dict) -> IncidentDecision: ...
    def evaluate(self, dataset_path: str) -> EvaluationReport: ...
```

Verification commands:

```sh
python -m unittest tests.test_incident_model
```

Expected result: ML-first decision contract is tested without breaking legacy tiny classifier behavior.

### Task 3.3: Add accepted-label dataset export

Objective: Export training data only from accepted incident-specific labels.

Files to create/modify:

- Modify `src/agent_health/db.py`.
- Modify `src/agent_health/cli.py`.
- Modify `tests/test_db_and_signals.py`.
- Modify `tests/test_cli.py`.

Tests to add/modify:

- `agent-health incident export-training` exports JSONL from `incident_labels` where `accepted_for_training=1`.
- Export includes label weights and reason-code targets.
- Export excludes `deterministic_signals`, `llm_evals.anomalies`, legacy/deleted labels, and unconfirmed ML budget fallbacks.
- Existing `agent-health ml export-training` remains available but its help text identifies it as legacy/bootstrap weak labels.

Implementation notes:

- This is the default ML-first training export.
- Keep request anomaly data completely out of the export.

Verification commands:

```sh
python -m unittest tests.test_cli tests.test_db_and_signals
```

Expected result: training export contains only accepted incident LLM/human labels.

### Task 3.4: Train TF-IDF plus calibrated logistic regression baseline

Objective: Train the first local incident model from accepted incident labels.

Files to create/modify:

- Modify `src/agent_health/incident_model.py`.
- Modify `tests/test_incident_model.py`.

Tests to add/modify:

- Training requires at least two distinct decision labels.
- Training accepts sample weights.
- Artifact stores model name, model version, label set, training record count, and feature schema version.
- If `scikit-learn` is missing, tests accept `IncidentModelUnavailable` as current optional-extra behavior.
- Prediction emits confidence, uncertainty, top-label margin, and optional reason-code output.

Implementation notes:

- First model family: TF-IDF char/word n-grams plus calibrated logistic regression.
- Prefer `CalibratedClassifierCV` where feasible.
- Train primary label first. Add auxiliary reason-code model only for incident-labeled examples when enough labels exist; otherwise emit `reason_code=None`.
- Do not train on old deterministic labels.

Verification commands:

```sh
python -m unittest tests.test_incident_model
```

Expected result: baseline trains or reports the missing optional ML extra cleanly.

Commit boundary: `accepted-label dataset export and baseline incident model`.

## Milestone 4: ML-First Routing And Budget Fallback

### Task 4.1: Add routing policy

Objective: Convert raw ML probabilities into final, defer, or best-effort fallback decisions.

Files to create/modify:

- Create `src/agent_health/incident_routing.py`.
- Add `tests/test_incident_routing.py`.

Tests to add/modify:

- Confident `incident` above `0.92` confidence and `0.12` margin returns final ML incident.
- Confident `not_incident` above `0.80` confidence and `0.12` margin returns final ML not-incident.
- Low-confidence case with budget available sets `should_defer_to_llm=true`, `budget_fallback=false`, and label `unsure`.
- Low-confidence case with budget exhausted emits best available non-`unsure` ML label and sets:
  - `decision_source='ml_model_budget_fallback'`
  - `should_defer_to_llm=true`
  - `llm_budget_available=false`
  - `budget_fallback=true`
- No test should encode `conservative_unsure` as the default over-budget policy.

Implementation notes:

- Default config:
  - `incident_confident=0.92`
  - `ok_confident=0.80`
  - `minimum_top_label_margin=0.12`
  - `over_budget_policy.mode='best_effort_ml'`
- Store fallbacks as predictions only.

Verification commands:

```sh
python -m unittest tests.test_incident_routing
```

Expected result: budget exhaustion defaults to best-effort ML fallback with explicit fallback metadata.

### Task 4.2: Add incident prediction CLI

Objective: Run the promoted incident model against stored incident examples and persist predictions.

Files to create/modify:

- Modify `src/agent_health/cli.py`.
- Modify `src/agent_health/db.py`.
- Modify `src/agent_health/incident_model.py`.
- Modify `src/agent_health/incident_routing.py`.
- Modify `tests/test_cli.py`.
- Modify `tests/test_db_and_signals.py`.
- Modify `tests/test_incident_model.py`.

Tests to add/modify:

- Parser supports `agent-health incident predict`.
- Prediction command loads the promoted model or an explicit model path.
- Confident predictions write `incident_predictions` with `decision_source='ml_model'`.
- Budget fallback predictions write `incident_predictions` with `decision_source='ml_model_budget_fallback'` and do not create labels.
- Command can optionally invoke incident LLM judge for deferred examples when budget remains.

Implementation notes:

- Keep request-level `agent-health incidents` behavior available as legacy deterministic event listing unless it is intentionally renamed in a later cleanup.
- Do not train from predictions.

Verification commands:

```sh
python -m unittest tests.test_cli tests.test_db_and_signals tests.test_incident_model tests.test_incident_routing
```

Expected result: incident examples can be predicted and stored through the ML-first routing policy.

### Task 4.3: Connect deferred incident predictions to incident LLM labeling

Objective: Complete the runtime path where ML `unsure` calls the incident judge only when budget exists.

Files to create/modify:

- Modify `src/agent_health/cli.py`.
- Modify `src/agent_health/judge.py`.
- Modify `tests/test_cli.py`.
- Modify `tests/test_judge.py`.

Tests to add/modify:

- When budget is available, deferred examples are judged and accepted incident labels are persisted.
- When budget is exhausted, no judge call is made and best-effort ML fallback prediction is persisted.
- Judge budget counters are incident-specific and do not consume request anomaly `eval --due` budget counters.

Implementation notes:

- Reuse existing Hermes route resolution but separate prompt/schema/storage.
- Keep all incident judge outputs in `incident_labels` and/or `incident_predictions`, not request-level `llm_evals`.

Verification commands:

```sh
python -m unittest tests.test_cli tests.test_judge tests.test_db_and_signals tests.test_incident_routing
```

Expected result: ML-first routing has the correct LLM budget behavior and persistence boundaries.

Commit boundary: `incident routing with best-effort ML budget fallback`.

## Milestone 5: Retraining And Auto-Promotion

### Task 5.1: Add model artifact registry and smoke checks

Objective: Track incident model artifacts and verify basic loadability before promotion.

Files to create/modify:

- Modify `src/agent_health/incident_model.py`.
- Modify `src/agent_health/db.py`.
- Add or modify `tests/test_incident_model.py`.
- Modify `tests/test_db_and_signals.py`.

Tests to add/modify:

- Artifact smoke check loads model.
- Artifact label set matches the four-label taxonomy.
- `predict()` succeeds on minimal smoke examples.
- Previous promoted model remains recorded for rollback.

Implementation notes:

- Metrics may be recorded in `metrics_json`, but they are not the initial promotion gate.
- Keep previous model artifacts count target at 3.

Verification commands:

```sh
python -m unittest tests.test_incident_model tests.test_db_and_signals
```

Expected result: artifact validity is enforced by basic smoke checks.

### Task 5.2: Add auto-promotion by accepted incident training-record count

Objective: Promote newer valid models primarily when they are trained from more accepted incident records than the current promoted model.

Files to create/modify:

- Modify `src/agent_health/incident_model.py`.
- Modify `src/agent_health/db.py`.
- Modify `src/agent_health/cli.py`.
- Modify `tests/test_incident_model.py`.
- Modify `tests/test_db_and_signals.py`.
- Modify `tests/test_cli.py`.

Tests to add/modify:

- Candidate with higher `training_record_count` and passing smoke checks is promoted.
- Candidate with equal or lower `training_record_count` is not promoted by default.
- Candidate failing load or label-set smoke checks is not promoted.
- Promotion does not require human approval.
- Metrics changes alone do not trigger promotion.

Implementation notes:

- Primary gate: `training_record_count_increased`.
- `require_human_approval=false`.
- `rollback_on_load_failure=true`.
- Keep previous promoted artifact available.

Verification commands:

```sh
python -m unittest tests.test_cli tests.test_db_and_signals tests.test_incident_model
```

Expected result: promotion behavior is count-driven with only basic artifact smoke checks.

### Task 5.3: Add retrain command for ML-first incident path

Objective: Train, register, smoke-check, and optionally promote a new model from accepted incident labels.

Files to create/modify:

- Modify `src/agent_health/cli.py`.
- Modify `src/agent_health/incident_model.py`.
- Modify `tests/test_cli.py`.
- Modify `tests/test_incident_model.py`.

Tests to add/modify:

- Parser supports `agent-health incident train`.
- Command consumes only `incident export-training` / accepted `incident_labels`.
- Command records `training_record_count`.
- Command auto-promotes only when count gate passes.
- Legacy `agent-health ml retrain-default` remains available but is help-text-marked as legacy/bootstrap if retained.

Implementation notes:

- Default output path should be under the existing Hermes `instruction-health/` area, for example `instruction-health/incident-models/<model_version>/`.
- Do not use local transient DB counts in docs. Runtime CLI output may report command results.

Verification commands:

```sh
python -m unittest tests.test_cli tests.test_incident_model tests.test_db_and_signals
```

Expected result: retraining uses accepted incident labels by default and promotion follows the accepted-record-count rule.

Commit boundary: `incident model retraining and count-based auto-promotion`.

## Milestone 6: Legacy Path Boundaries And Documentation Updates

### Task 6.1: Rename or document weak/rule-labeled ML commands as legacy/bootstrap

Objective: Prevent accidental use of deterministic labels as default ML-first incident training data.

Files to create/modify:

- Modify `src/agent_health/cli.py`.
- Modify `tests/test_cli.py`.
- Modify docs/truth files routed to Evaluation Model and Local Runtime after code changes.

Tests to add/modify:

- CLI help text for `ml export-training` and `ml retrain-default` includes `legacy` or `bootstrap`.
- Default incident training command is the new accepted-label path.
- No parser default points ML-first training at rule-labeled export.

Implementation notes:

- Do not remove legacy commands in the same change unless there is an explicit compatibility decision.
- Keep `export_rule_labeled_events()` as a bootstrap helper, not default training.

Verification commands:

```sh
python -m unittest tests.test_cli tests.test_incident_model
```

Expected result: CLI makes the default accepted-label path clear and leaves legacy rule-labeled export visibly separate.

### Task 6.2: Update current behavior Truthmark docs

Objective: Keep repository truth aligned after functional code changes.

Files to create/modify:

- Update the relevant docs under `docs/truth/`.
- Update route ownership under `docs/truthmark/areas/` only if new code surfaces need new routing.

Tests to add/modify:

- No code tests for docs-only updates.

Implementation notes:

- Likely routed areas:
  - Evaluation Model for normalization, incident model, incident judge, routing, taxonomy.
  - Local Runtime for CLI and sidecar DB behavior.
  - Hermes Integration for Hermes state.db reader/adapter behavior if import contracts change.
- Preserve the two-layer distinction in truth docs.
- Explicitly state that request anomaly labels are not incident training labels.
- Do not document transient local DB row counts.

Verification commands:

```sh
truthmark check
```

Expected result: Truthmark check passes or any intentionally deferred diagnostics are documented.

Commit boundary: `legacy command boundaries and truth documentation`.

## Final Verification And Gates

Run focused unit tests after each milestone, then run the full suite before the final implementation commit:

```sh
python -m unittest
```

Run targeted tests for the primary changed surfaces:

```sh
python -m unittest \
  tests.test_hermes_reader \
  tests.test_normalize \
  tests.test_db_and_signals \
  tests.test_incident_routing \
  tests.test_incident_model \
  tests.test_dashboard_queries \
  tests.test_judge \
  tests.test_cli
```

If new test modules are added, include them explicitly:

```sh
python -m unittest \
  tests.test_incident_features \
  tests.test_incident_routing
```

Run Truthmark after functional changes and truth doc updates:

```sh
truthmark check
```

If functional code changed and relevant tests pass, run the repository `truthmark-sync` skill before reporting completion. If routing cannot map new incident ML surfaces cleanly, run or request Truth Structure before syncing.

Release/version gate:

- Apply `docs/standards/versioning.md` before release-impacting changes.
- If a package version changes, add a matching change note under `changes/` using `docs/standards/change-notes.md`.

## Commit Boundaries

1. `incident example schema and Hermes normalization`
2. `incident-specific judge and accepted label persistence`
3. `accepted-label dataset export and baseline incident model`
4. `incident routing with best-effort ML budget fallback`
5. `incident model retraining and count-based auto-promotion`
6. `legacy command boundaries and truth documentation`

Each boundary should be independently testable. Do not mix request anomaly behavior changes into these commits except where tests explicitly prove the old request-level path is unchanged.
