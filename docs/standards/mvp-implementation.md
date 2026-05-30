---
status: active
doc_type: standard
last_reviewed: 2026-05-24
source_of_truth:
  - ../design.md
---

# MVP Implementation Standards

## Keep V1 State.db-Ingested

- Use Hermes `state.db` as the only V1 ingestion source.
- Do not require users to install a passive Hermes hook plugin before Ariadne Eval is useful; the dashboard plugin must remain optional, explicit-action driven, and backed by local sidecar state.
- Do not create or depend on `events.jsonl` in V1.

## Trigger The Judge Explicitly

- V1 may trigger judging through an explicit CLI batch command, `agent-health review --due`, or through explicitly configured recurring review jobs processed by `agent-health scheduler tick` / `agent-health scheduler run --poll-seconds N`.
- Recurring tasks must be stored in `evals.db`, use per-task leases, snapshot effective config into each run, and keep page-load behavior inert.
- Keep default judge batches budget-safe: score due candidates with case signals, including specific rule/model finding categories when available; skip low-priority candidates by default; trim bulky documents/code/images/tool previews before provider calls; cap judge calls per invocation; skip any previously judged unit unless `--reevaluate` is explicit; defer no-reaction last turns behind a cooldown; and record provider-reported judge token usage.
- Scheduler budget caps must be shared across request-level judging and incident-specific labeling.

## Keep The LLM Judge In V1

- Ariadne Eval must keep an LLM judge path for final health ratings beyond V1.
- Case signals and tiny non-LLM classifier outputs are evidence and fallback inspection data, not replacements for the judge; local models are expected to leave some semantic cases below high-confidence coverage.
- The judge should use Hermes' existing provider/model configuration by default rather than a separate evaluator API key. It should prefer configured `auxiliary.approval` first, then fall back to the Hermes main provider/model.
- Judge output should be strict JSON with status, confidence, primary reason, and findings; malformed JSON should get one repair attempt before storing an evaluator error.

## Keep It Local And Inspectable

- Store evaluator state under the Hermes profile in `instruction-health/`.
- Use SQLite for queryable local eval data.
- Store judge provider/model metadata with every LLM review.

## Preserve Evidence Quality

- Exclude hidden chain-of-thought or provider reasoning fields from normalized records.
- Prefer case signals before relying on LLM judgment.
- Prefer tiny non-LLM classifiers for noisy incident categorization only when rules are insufficient or a trained model is explicitly configured.
- Treat the next user message as evidence, not ground truth.

## Keep The Product Narrow

- Evaluate per user request, not only whole sessions.
- Keep prompt patches, passive hook plugins, standalone dashboards, and non-Hermes adapters out of V1 unless the state.db evaluator and judge path are already working. The scheduler and Hermes dashboard tab may include explicit actions over `evals.db`, including recurring-task controls and browser-triggered retraining, as long as those actions are user-initiated and auditable.

## Verification

- Add or update tests for normalization, signal extraction, schema behavior, and judge JSON contracts when those surfaces change.
- Use the CLI against a real Hermes profile for smoke checks when practical.
