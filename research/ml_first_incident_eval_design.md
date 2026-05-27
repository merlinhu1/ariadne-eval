# ML-First Tool-Call Incident Evaluation Design

Status: draft for review  
Date: 2026-05-23  
Scope: incident eval only; anomaly eval is intentionally out of scope.

## Executive Summary

Ariadne Eval should support a tool-call-level incident evaluator where the primary long-term decision maker is a local lightweight ML model, not deterministic rules. The evaluator classifies one agent tool call and its immediate tool response into four decision labels: `ok`, `incident`, `unsure`, or `not_evaluable`.

`unsure` cases are routed to an incident-specific LLM judge when budget is available. If the LLM budget is exhausted, the evaluator uses the ML model's best-effort decision by default and marks that prediction as a budget fallback. The user configured the budget, so budget exhaustion should not silently convert the product into a conservative no-op.

The system must not train the ML-first incident model on old deterministic labels. Training labels for this path should come from incident-specific LLM judge decisions and optional human labels, with human labels receiving higher training weight. Existing deterministic/rule-labeled training commands are a legacy/bootstrap path and should be separate from the ML-first incident-training path.

Ariadne Eval must keep two evaluation layers explicit:

| Layer | Unit | Primary question | Training data | Model |
| --- | --- | --- | --- | --- |
| Request anomaly eval | one user request / assistant response unit | Did the agent mishandle the user's request or produce broader task-quality anomalies? | request-level LLM evals and request-level reviews | request/anomaly model or judge |
| Tool-call incident eval | one agent tool call plus its immediate result | Did this individual tool call/result show a tool-level incident? | incident-specific LLM labels and human labels | incident ML model |

These layers are different granularities. They should not share labels as if they were the same concept. Request-level anomalies may use tool-call incidents as evidence, but they are not incident labels. Tool-call incidents may explain request-level anomalies, but they are not request-level anomaly labels.

Target mature-state outcome: reduce incident LLM judge calls by approximately 90% while keeping the local tool-call incident evaluator useful under explicit user budgets.

## Source Data Boundary

The first source for incident examples is Hermes `state.db`, specifically assistant tool-call messages and the corresponding tool-result messages. Ariadne's existing sidecar database can store derived examples, labels, predictions, and model metadata, but the ML-first incident evaluator should add incident-specific persistence rather than overloading existing deterministic signal or request-level evaluation tables.

`tool_call_id` must not be treated as globally unique. The source key for one incident-eval record should be composite:

```text
source_session_id
assistant_tool_call_message_id
result_message_id
tool_call_id
```

or an internally generated `source_event_id` derived from those fields.

Use `assistant_tool_call_message_id` for the assistant message that issued the tool call. Do not confuse it with a later final assistant response message.

## Classification Unit

The unit of incident classification is:

```text
one agent tool call
+ the immediate response/result for that tool call
+ compact visible caller context
+ optional explicit non-hidden caller expectation fields when the source runtime provides them
```

Minimum fields:

```json
{
  "source_session_id": "20260523_...",
  "assistant_tool_call_message_id": 123,
  "result_message_id": 124,
  "tool_call_id": "call_...",
  "tool_name": "terminal",
  "tool_arguments": "...",
  "tool_result": "...",
  "result_timestamp": 1779518072.5
}
```

Recommended contextual fields:

```json
{
  "user_request_excerpt": "...",
  "prior_assistant_visible_text": "...",
  "following_assistant_visible_text": "...",
  "explicit_caller_expectation": "...",
  "explicit_caller_interpretation": "..."
}
```

Do not fabricate caller-intent fields in Ariadne Eval. If explicit expectation fields are absent, record them as absent. Do not synthesize them from tool args, tool results, or hidden reasoning.

## Tool-Call Join Requirement

Incident examples require a reliable join between:

1. the assistant message that emitted one or more `tool_calls`; and
2. the tool-role result message for a specific `tool_call_id`.

The normalizer must recover:

- `assistant_tool_call_message_id`;
- `result_message_id`;
- `tool_call_id`;
- `tool_name`;
- `tool_arguments`;
- immediate `tool_result`;
- source session identity;
- local visible context.

This join is a hard prerequisite for the ML-first incident layer. Existing request-level `eval_units` and `trace_events` are useful context, but they are not sufficient as the authoritative incident-example contract unless they preserve the composite key and recovered tool-call metadata.

## Caller Expectation Boundary

The evaluator cannot reliably infer private tool purpose from only tool name, tool arguments, and immediate tool response. Those fields often explain what was invoked, but not necessarily why the caller chose it or what private success condition the caller expected.

Current repository policy does not allow storing or depending on hidden chain-of-thought or provider reasoning fields. This design does not use hidden reasoning.

Active policy:

1. Use visible context, tool arguments, and tool responses.
2. Treat expectation-sensitive labels as `not_evaluable` or `unsure` unless explicit non-hidden expectation fields are provided by the source runtime.
3. Accept optional future upstream fields such as `tool_call_purpose` or `expected_success_condition` only if Hermes or another source runtime records them explicitly as non-hidden data.
4. Do not have Ariadne generate synthetic intent fields internally.

Example of a future upstream field shape, not a field Ariadne should generate:

```json
{
  "tool_call_purpose": "Run the focused test that should fail before the parser fix.",
  "expected_success_condition": "The command exits nonzero with the old parser assertion failure.",
  "expectation_source": "hermes_runtime_explicit_intent"
}
```

## Incident Definition

An incident is a tool-call-level problem visible from the tool call and its immediate response, including:

- the tool ran and returned an explicit failure;
- the tool call did not complete because it was skipped, cancelled, timed out, blocked, denied, or unavailable;
- the tool request itself was invalid or malformed;
- the tool completed but returned an unusable, malformed, empty, truncated, or incomplete result.

Expectation-satisfaction failures are not part of the active current-data scope unless reliable expectation context is present from the source runtime.

This is separate from anomaly eval. Anomaly eval may evaluate broader semantic task quality, user-goal satisfaction, unsupported final claims, or cross-turn behavior at the user-request level. Do not mix anomaly labels into this incident classifier.

## Label Set

Use a deliberately tiny decision taxonomy. The production target is not an incident subtype ontology; it is whether this tool call should be treated as an incident, safely ignored, deferred, or excluded.

Active decision labels:

```text
ok
incident
unsure
not_evaluable
```

Category meanings:

- `ok`: the tool call completed normally and the immediate response does not show a tool-level incident.
- `incident`: the immediate tool call/result shows an operational tool-level failure.
- `unsure`: the model cannot decide confidently and should defer to the incident LLM judge if budget is available.
- `not_evaluable`: the record lacks the minimum fields needed to classify the tool call/result.

Incident flavor should be stored as optional metadata, not as the main label:

```text
reason_code = execution_error | no_result | bad_request | bad_output | other | null
```

Reason-code meanings:

- `execution_error`: nonzero exit, exception, failed command, or structured error result.
- `no_result`: skipped, cancelled, timed out, blocked, denied, unavailable, or interrupted.
- `bad_request`: malformed call, invalid args, unknown/unavailable tool, or schema/contract violation before meaningful execution.
- `bad_output`: completed response is unusable: malformed, empty when content is required, truncated, partial, or missing required fields.
- `other`: visible tool-level incident that does not fit the above buckets.
- `null`: no reason code applies, usually for `ok`, `unsure`, or `not_evaluable`.

The model may predict `reason_code` as an auxiliary task, but routing should optimize the four-label decision first.

## Reason Code Generation

`reason_code` should not be produced by a brittle string-match table. It should be a secondary output generated from the same normalized example used by the main incident model.

Recommended approach:

1. Extract typed, lossless features from the tool call/result, such as parsed `exit_code`, structured `error` fields, timeout/cancel status, schema-validation errors, response parseability, response length, truncation flags, and missing required fields.
2. Build text features from normalized tool name, arguments, stderr/stdout snippets, exception names, structured error messages, and compact result summaries.
3. Train the main model to predict the primary label: `ok`, `incident`, `unsure`, or `not_evaluable`.
4. Train a secondary auxiliary classifier, or a multi-output head, to predict `reason_code` for examples whose authoritative label is `incident`.
5. Emit the best available `reason_code` whenever the primary decision is `incident`. Reason-code confidence is useful for diagnostics and learning, but it should not gate emission.

High-precision structured features may still be used as features or sanity checks. For example, `exit_code != 0` is a strong feature for `execution_error`, and a JSON/schema validation failure is a strong feature for `bad_request`. These should be typed evidence, not a long keyword list pretending to be intelligence.

The incident LLM judge should return both `label` and optional `reason_code`; those rows become supervised training data for both outputs. Human corrections can update either field independently.

## Decision Flow

```text
Hermes state.db tool call/result
  -> join assistant tool_call to tool result
  -> normalize visible tool-call incident example
  -> build text and structured features
  -> local incident ML classifier
       -> confident incident: final ML decision
       -> confident ok: final ML decision
       -> unsure: call incident LLM judge if budget exists
             -> persist LLM label as incident training data
       -> unsure + no LLM budget: final best-effort ML budget-fallback decision
  -> persist prediction, source, confidence, model version, and evidence
  -> retrain after enough new incident labels exist
  -> automatically promote the newest valid model artifact according to training-record policy
```

## ML Output Contract

Every prediction should include:

```json
{
  "label": "incident",
  "is_incident": true,
  "reason_code": "execution_error",
  "reason_confidence": 0.88,
  "confidence": 0.94,
  "uncertainty": 0.06,
  "decision_source": "ml_model",
  "model_name": "tfidf_logreg",
  "model_version": "2026-05-23.1",
  "should_defer_to_llm": false,
  "budget_fallback": false,
  "evidence_summary": [
    "terminal result returned nonzero exit",
    "structured result includes an error field"
  ]
}
```

For uncertain cases while LLM budget remains:

```json
{
  "label": "unsure",
  "is_incident": null,
  "reason_code": null,
  "confidence": 0.48,
  "uncertainty": 0.52,
  "decision_source": "ml_model",
  "should_defer_to_llm": true,
  "budget_fallback": false
}
```

For over-budget fallback, use the model's best available non-`unsure` label when possible and preserve that it would have preferred LLM review:

```json
{
  "label": "incident",
  "is_incident": true,
  "reason_code": "bad_output",
  "reason_confidence": 0.57,
  "confidence": 0.61,
  "decision_source": "ml_model_budget_fallback",
  "should_defer_to_llm": true,
  "llm_budget_available": false,
  "budget_fallback": true
}
```

## Routing Policy

False positives matter, but the system should still produce best-effort decisions when the user-configured LLM budget is exhausted.

Starting threshold proposal:

```yaml
thresholds:
  incident_confident: 0.92
  ok_confident: 0.80
  minimum_top_label_margin: 0.12
```

Routing rule:

```text
if top_label == incident and confidence >= incident_confident and margin >= minimum_top_label_margin:
    final ML incident decision
elif top_label == ok and confidence >= ok_confident and margin >= minimum_top_label_margin:
    final ML ok decision
elif llm_budget_available:
    emit unsure and call incident LLM judge
else:
    emit best-effort ML label and mark budget_fallback=true
```

If over budget, the fallback prediction should be stored separately from authoritative training labels. Budget-fallback predictions can later be sampled for human or LLM review, but they must not train the model until confirmed.

## Supported Local ML Algorithms

The framework should support multiple algorithms through a stable model adapter interface. Because Ariadne Eval controls feature generation, dataset export, training, evaluation, and artifact loading, model choice can remain swappable.

Recommended adapter interface:

```python
class IncidentModel:
    model_name: str
    model_version: str

    def train(self, dataset_path: str, output_dir: str) -> ModelArtifact:
        ...

    def predict(self, features: dict) -> IncidentPrediction:
        ...

    def evaluate(self, dataset_path: str) -> EvaluationReport:
        ...
```

Supported model families should include:

1. **TF-IDF char/word n-grams + calibrated logistic regression**
   - Best first baseline.
   - Lightweight, local, strong on logs/tool outputs.
   - Supports probability calibration and interpretable top features.

2. **Linear SVM / SGD classifier with calibration**
   - Good for sparse text at larger scale.
   - Efficient local training.
   - Needs calibration for confidence thresholds.

3. **Gradient boosted trees over structured features**
   - Useful for structured features such as tool type, duration, result length, retry count, parsed status fields, and context flags.
   - May be ensembled with the text classifier.

4. **Other optional local models**
   - fastText-style classifiers, local embeddings, or small transformer fine-tunes can be added later if the baseline plateaus.
   - These are adapter extensions, not initial hard dependencies.

## Training Data Policy

Do not train the ML-first incident model from old deterministic labels.

Allowed training sources:

1. Incident-specific LLM judge labels.
2. Optional human labels.
3. Human corrections of previous incident ML/LLM outputs.
4. Previously budget-fallback ML cases only after later LLM or human confirmation.

Disallowed training sources:

1. Old deterministic labels.
2. Current deterministic signal rows used as labels.
3. Request-level anomaly labels used as tool-call incident labels.
4. ML self-predictions that have not been confirmed by LLM or human review.

Recommended label weights:

```yaml
label_weights:
  human: 3.0
  human_correction: 3.5
  incident_llm_judge_high_confidence: 1.5
  incident_llm_judge_normal: 1.0
  request_anomaly_label: 0.0
  ml_self_prediction: 0.0
  old_deterministic_label: 0.0
```

Legacy deterministic/rule-labeled training export may continue to exist for bootstrapping experiments, but it should be named and documented separately from the ML-first incident training path. The default ML-first incident training command should consume only accepted incident labels from `incident_labels`.

## Persistence Design

Add incident-specific persistence rather than overloading deterministic signal tables or request-level LLM eval tables.

Suggested tables:

### `incident_eval_examples`

Stores normalized examples eligible for prediction/training.

Key fields:

```text
id
source_session_id
assistant_tool_call_message_id
result_message_id
tool_call_id
tool_name
tool_arguments
tool_result
user_request_excerpt
previous_visible_context_excerpt
following_visible_context_excerpt
explicit_caller_expectation
explicit_caller_interpretation
upstream_tool_call_purpose
upstream_expected_success_condition
upstream_intent_source
created_at
normalization_version
```

`upstream_tool_call_purpose` and `upstream_expected_success_condition` are nullable optional fields. Ariadne should only populate them when the source runtime already provides explicit non-hidden intent. Ariadne should not infer or generate these fields internally.

Unique key:

```text
(source_session_id, assistant_tool_call_message_id, result_message_id, tool_call_id)
```

### `incident_labels`

Stores authoritative labels for incident training and review.

```text
id
example_id
label                     -- ok, incident, unsure, not_evaluable
is_incident
reason_code               -- optional incident flavor metadata
reason_confidence         -- optional confidence for reason_code
confidence
label_source              -- incident_llm_judge, human, human_correction
label_source_version
training_weight
rationale
evidence_json
accepted_for_training
created_at
```

### `incident_predictions`

Stores ML and fallback predictions.

```text
id
example_id
label                     -- ok, incident, unsure, not_evaluable
is_incident
reason_code               -- optional incident flavor metadata
reason_confidence         -- optional confidence for reason_code
confidence
uncertainty
decision_source           -- ml_model, incident_llm_judge, ml_model_budget_fallback
model_name
model_version
should_defer_to_llm
budget_fallback
llm_budget_available
evidence_json
created_at
```

### `incident_models`

Tracks trained model artifacts and auto-promotion.

```text
id
model_name
model_version
algorithm
artifact_path
training_record_count
training_data_hash
metrics_json
promoted
promoted_at
created_at
```

## Incident LLM Judge Role

The incident LLM judge is not the same as the request-level anomaly judge.

The incident LLM judge is used for:

- incident ML `unsure` cases when budget exists;
- incident label generation for incident-model training;
- periodic audit samples;
- drift/new-tool cases;
- optional review of budget-fallback cases.

Incident LLM judge input should include:

- tool name;
- tool arguments;
- immediate tool response;
- ML prediction and uncertainty;
- compact surrounding visible user request/context;
- explicit caller expectation/interpretation only if the source runtime already provides it.

Incident LLM judge output should be strict JSON matching the incident label set.

Request-level LLM evals remain a separate layer for anomaly and task-quality assessment. They must not be used directly as incident labels.

## LLM Budget Exhaustion

If ML says `unsure` and incident LLM budget is exhausted:

1. Use ML best-effort fallback by default.
2. Mark `budget_fallback=true`.
3. Store the result as a prediction, not an authoritative training label.
4. Prefer adding these cases to a later review queue.

Default policy:

```yaml
over_budget_policy:
  mode: best_effort_ml
```

## Automatic Model Replacement

New incident models should automatically replace old models when they are trained from more accepted incident training records than the currently promoted model and basic artifact checks pass.

This design intentionally uses training-record count as the primary promotion criterion. More training iterations and more accepted training records are assumed to improve the model over time. Metrics can be recorded for visibility, but they are not the promotion gate in this design.

Minimum artifact checks:

- model artifact loads;
- label set matches current incident taxonomy;
- `predict()` succeeds on smoke examples;
- previous model artifact remains available for rollback if the new artifact cannot load.

Promotion policy:

```yaml
promotion:
  automatic: true
  require_human_approval: false
  primary_gate: training_record_count_increased
  keep_previous_models: 3
  rollback_on_load_failure: true
```

## Evaluation Metrics

Metrics are for observability and iteration, not the initial automatic-promotion gate.

Primary metrics:

- incident precision;
- false-positive rate;
- ML coverage rate;
- LLM deferral rate;
- LLM reduction rate;
- budget-fallback rate;
- accepted incident training-record count.

Secondary metrics:

- incident recall;
- per-label precision/recall/F1;
- confusion matrix;
- unsure rate;
- drift/new-tool rate.

Core product question:

```text
How much incident traffic can the local ML model cover under the configured LLM budget?
```

## Implementation Milestones

### Milestone 1: State DB Incident Example Export

- Read Hermes `sessions` and `messages`.
- Join assistant `tool_calls` to tool-role result messages.
- Use composite source IDs, not global `tool_call_id` alone.
- Extract tool name and arguments from assistant `tool_calls` JSON.
- Store normalized incident examples.

### Milestone 2: Incident LLM Labeling Path

- Add incident-specific LLM judge prompt and strict JSON schema.
- Judge only selected incident examples.
- Persist judge labels in `incident_labels`.
- Do not use old deterministic labels for ML-first incident training.
- Do not use request-level anomaly labels as incident labels.

### Milestone 3: Baseline Local Incident ML

- Add dataset export from accepted `incident_labels`.
- Train TF-IDF + calibrated logistic regression.
- Emit `unsure` via confidence and margin thresholds.
- Store predictions in `incident_predictions`.

### Milestone 4: Routing

- Confident ML decisions are final.
- Unsure ML decisions call the incident LLM judge when budget exists.
- Over-budget cases use best-effort ML fallback by default.
- Persist all decision sources and model versions.

### Milestone 5: Automatic Retraining and Promotion

- Retrain after N new incident LLM/human labels or on schedule.
- Record training-record count for each model artifact.
- Auto-promote candidates trained on more accepted incident records than the current promoted model.
- Keep rollback artifacts.

### Milestone 6: Model Adapter Expansion

- Add SGD/SVM adapter.
- Add structured GBDT adapter.
- Add optional fastText/local embedding/transformer adapters only if needed.

## Open Design Knobs For Review

1. Exact confidence thresholds for confident `incident` and confident `ok` routing.
2. Whether to request upstream Hermes runtime instrumentation for explicit `tool_call_purpose` / `expected_success_condition`; Ariadne should not synthesize these fields itself.
3. Whether human labels should be stored in the same table as incident LLM labels or in a review-specific table with a materialized accepted-label view.
4. The N threshold for automatic retraining/promotion based on new accepted incident training records.

## Current Conclusion

The design is viable as a separate ML-first, tool-call-level incident layer. It should not be implemented by reusing request-level anomaly labels, old deterministic labels, or hidden reasoning. The first hard requirement is a reliable assistant-tool-call to tool-result join that produces stable incident examples with composite source identity. Once incident-specific LLM/human labels exist, the local ML model becomes the default decision maker, with best-effort ML fallback when user-configured LLM budget is exhausted.
