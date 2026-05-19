---
status: active
doc_type: ai-instructions
last_reviewed: 2026-05-19
source_of_truth:
  - ../../.truthmark/config.yml
  - ../architecture/system-overview.md
  - ../standards/mvp-implementation.md
  - ../../research/agent_instruction_health_evaluator_design1.md
---

# Repository Rules For AI Agents

## Purpose

This is the repository instruction authority for Ariadne Eval agents. `AGENTS.md` and `CLAUDE.md` should stay small and point here for project-specific rules.

## Read First

- Read `.truthmark/config.yml` before changing Truthmark-controlled docs or routing.
- Read `docs/truthmark/areas.md` and the relevant files under `docs/truthmark/areas/` before deciding where truth belongs.
- Use `research/agent_instruction_health_evaluator_design1.md` as the original product-design reference.
- Use `docs/design.md` as the normal design copy for project navigation.

## Truthmark Structure

- Repository instruction authority lives in `docs/ai/repo-rules.md`.
- Standards live under `docs/standards/`.
- Architecture docs live under `docs/architecture/` and should describe structure, boundaries, persistence, runtime topology, and generated-surface ownership.
- Current behavior truth lives under `docs/truth/` and should be kept in bounded leaf docs, not README indexes.
- Route ownership lives in `docs/truthmark/areas.md` and `docs/truthmark/areas/**/*.md`.

## Implementation Rules

- Build the Hermes-first MVP before adding broad adapter abstractions.
- Keep Hermes hook code passive, fast, and fail-open.
- Do not call LLMs from hooks.
- Do not mutate prompts, tool outputs, memory, skills, or configuration from the plugin.
- Do not store or depend on hidden chain-of-thought or provider reasoning fields.
- Prefer deterministic evidence before LLM judgment.
- Keep the CLI useful before adding dashboards.

## Verification Rules

- For code changes, run the relevant Python `unittest` targets before finishing.
- When functional behavior changes, update or verify the routed Truthmark docs before reporting completion.
- For docs-only changes, run `truthmark check` and fix diagnostics unless they are intentionally deferred and reported.
- Report skipped checks explicitly with the reason.
