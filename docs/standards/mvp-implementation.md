---
status: active
doc_type: standard
last_reviewed: 2026-05-19
source_of_truth:
  - ../design.md
---

# MVP Implementation Standards

## Keep V1 State.db-Ingested

- Use Hermes `state.db` as the only V1 ingestion source.
- Do not require users to install a passive Hermes hook plugin before Ariadne Eval is useful; the dashboard plugin must remain optional and read-only.
- Do not create or depend on `events.jsonl` in V1.

## Trigger The Judge With The CLI

- V1 should trigger judging through an explicit CLI batch command, `agent-health eval --due`.
- Do not add a resident scheduler or daemon to V1.
- Keep default judge batches budget-safe: score due candidates with deterministic signals, skip low-priority candidates by default, trim bulky documents/code/images/tool previews before provider calls, cap judge calls per invocation, skip any previously judged unit unless `--reevaluate` is explicit, defer no-reaction last turns behind a cooldown, and record provider-reported judge token usage.
- If periodic runs are needed later, use cron/systemd as a thin wrapper around the CLI command.

## Keep The LLM Judge In V1

- V1 must have an LLM judge path for final health ratings.
- Deterministic signals are evidence and fallback inspection data, not a replacement for the judge.
- The judge should use Hermes' existing provider/model configuration by default rather than a separate evaluator API key. It should prefer configured `auxiliary.compression` first, then fall back to the Hermes main provider/model.
- Judge output should be strict JSON with status, confidence, primary reason, and anomalies; malformed JSON should get one repair attempt before storing an evaluator error.

## Keep It Local And Inspectable

- Store evaluator state under the Hermes profile in `instruction-health/`.
- Use SQLite for queryable local eval data.
- Store judge provider/model metadata with every LLM eval.

## Preserve Evidence Quality

- Exclude hidden chain-of-thought or provider reasoning fields from normalized records.
- Prefer deterministic signals before relying on LLM judgment.
- Treat the next user message as evidence, not ground truth.

## Keep The Product Narrow

- Evaluate per user request, not only whole sessions.
- Keep feedback loops, prompt patches, schedulers, passive hook plugins, standalone dashboards, and non-Hermes adapters out of V1 unless the state.db evaluator and judge path are already working. The Hermes dashboard tab is allowed as a read-only visualization over `evals.db`.

## Verification

- Add or update tests for normalization, signal extraction, schema behavior, and judge JSON contracts when those surfaces change.
- Use the CLI against a real Hermes profile for smoke checks when practical.
