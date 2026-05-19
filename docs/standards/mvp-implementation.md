---
status: active
doc_type: standard
last_reviewed: 2026-05-19
source_of_truth:
  - ../design.md
---

# MVP Implementation Standards

## Keep It Local And Inspectable

- Store evaluator state under the Hermes profile in `instruction-health/`.
- Use SQLite for queryable local eval data.
- Avoid extra evaluator-specific cloud services or API credentials.

## Preserve Evidence Quality

- Use Hermes session records as the primary source of truth.
- Keep supplemental hook records small, append-only, and capped.
- Exclude hidden chain-of-thought or provider reasoning fields from normalized records.
- Prefer deterministic signals before relying on LLM judgment.

## Keep The Product Narrow

- Evaluate per user request, not only whole sessions.
- Classify with one primary status: `succeed`, `failed`, `mishandled`, `prolonged`, or `not_evaluable`.
- Keep feedback loops, prompt patches, dashboards, and non-Hermes adapters out of the MVP unless the core evaluator is already working.

## Verification

- Add or update tests for normalization, signal extraction, and schema behavior when those surfaces change.
- Use the CLI against a real Hermes profile for smoke checks when practical.
