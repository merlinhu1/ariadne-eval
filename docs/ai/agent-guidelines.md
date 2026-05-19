---
status: active
doc_type: ai-guidance
last_reviewed: 2026-05-19
source_of_truth:
  - ../design.md
  - ../../research/agent_instruction_health_evaluator_design1.md
---

# AI Agent Guidelines

## Purpose

This note gives future coding agents a small amount of project-specific context before they edit Ariadne Eval.

## Current Priorities

- Build the Hermes-first MVP before adding broad multi-agent abstractions.
- Keep the evaluator local-first: Hermes `state.db` is the primary source, and sidecar data lives under the Hermes profile.
- Preserve hook behavior as passive and fail-open; hooks should not call an LLM or mutate agent behavior.
- Do not store or depend on hidden provider reasoning fields.
- Keep CLI outputs useful before adding dashboards or richer visualizations.

## Working Notes

- Treat `research/agent_instruction_health_evaluator_design1.md` as the original design reference.
- Treat `docs/design.md` as the human-readable design copy for normal project navigation.
- Prefer focused changes with small tests; the current test suite uses Python `unittest`.
