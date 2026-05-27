---
status: active
doc_type: behavior
truth_kind: behavior
last_reviewed: 2026-05-19
source_of_truth:
  - ../../truthmark/areas/repository.md
---

# Repository Overview

## Purpose

<!-- State why this feature exists, the user or system outcome it protects, and the problem it solves. Keep roadmap or implementation plans out of this section. -->

Describe why the default repository behavior surface exists and what outcome it protects.

## Scope

This bounded leaf truth doc owns the default repository behavior surface created by Truthmark.

<!--
This doc must own one coherent behavior surface.
Split into another leaf doc when content introduces:
- a distinct user or system outcome
- a separate lifecycle or state machine
- an unrelated rule family
- a different external contract
- code that should route through a different owner
Keep README.md files as indexes only.
-->

This doc was created from the editable behavior-doc template at docs/templates/behavior-doc.md.

## Current Behavior

<!-- Describe implemented behavior in present tense. Do not include desired future behavior. -->

- Document current behavior here when implementation changes make repository truth incomplete.

## Core Rules

<!-- Capture stable business rules, invariants, precedence rules, validation rules, and must-never constraints. Omit incidental implementation details. -->

- Truth README files are indexes; behavior truth belongs in bounded leaf docs.

## Flows And States

<!-- Use for route switches, state transitions, lifecycle stages, retries, fallbacks, and important error paths. Write 'None beyond current behavior.' when no distinct flow or state model exists. -->

- None beyond current behavior.

## Contracts

<!-- Capture user-visible or integration contracts: CLI/API shape, inputs, outputs, diagnostics, files, events, permissions, or links to canonical contract docs. Avoid duplicating a separate canonical contract doc. -->

- External contracts should link to the nearest canonical contract doc when one exists.

## Product Decisions

<!-- Keep active decisions only. Replace stale decisions instead of appending historical logs. -->

- Decision (2026-05-24): Truth README files are indexes; behavior truth belongs in bounded leaf docs.

## Rationale

<!-- Explain why the current behavior and active decisions are this way, including tradeoffs. -->

Bounded leaf docs keep agent context focused and prevent large products from accumulating unreviewable feature manuals.

## Non-Goals

<!-- Name adjacent behavior this doc intentionally does not own, especially tempting future expansions. -->

- This doc is not a catch-all for unrelated repository behavior.

## Maintenance Notes

<!-- List related tests, routing cautions, migration notes, and common drift risks for future agents. Keep this operational, not historical. -->

- Update this doc when routed implementation changes alter current behavior, rules, contracts, or decisions.
