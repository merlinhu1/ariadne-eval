---
status: active
doc_type: standard
last_reviewed: 2026-05-19
source_of_truth:
  - ../../pyproject.toml
  - change-notes.md
  - https://semver.org/
---

# Versioning

## Trigger

Use this standard when a task changes or asks whether to change:

- the `[project].version` value in `pyproject.toml`;
- release/version policy;
- a `changes/` note with `Version action: patch`, `minor`, `major`, or `none`;
- published package behavior that should be classified before release.

Do not load this standard for ordinary implementation work unless a package version decision, release note, public-surface change, or user-facing compatibility question is in scope.

## Goal

Choose Ariadne Eval's package version using Semantic Versioning 2.0.0, adapted for a Python package whose maintained version source is `pyproject.toml`.

Normal committed versions use `MAJOR.MINOR.PATCH`. Until the project reaches `1.0.0`, public compatibility may still change, but version actions should still describe the impact honestly so release notes and downstream users are not surprised.

## First Gate

If the change does not alter published package behavior, do not bump the package version.

No-bump examples:

- internal repository standards or agent instructions;
- documentation topology/routing changes with no published package behavior change;
- tests, refactors, formatting, or cleanup with no behavior change;
- Truthmark truth/docs corrections that only align docs to already-shipped behavior.

## Decision Table

| Change | Version action |
| --- | --- |
| Backward-incompatible public CLI, Python API, persisted schema, or output-contract change after that surface has shipped | `MAJOR + 1`, reset `MINOR` and `PATCH` to `0`; before `1.0.0`, use at least `MINOR + 1` unless the project owner explicitly wants a major reset |
| Backward-compatible public CLI/Python API addition, new evaluator capability, new output field, new storage feature, deprecation, or substantial user-visible improvement | `MINOR + 1`, reset `PATCH` to `0` |
| Backward-compatible bug fix, diagnostic correction, packaging fix, or published documentation correction | `PATCH + 1` |
| Internal-only maintenance with no published package behavior change | no version change |

Do not use prerelease or build metadata in the committed package version unless the release task explicitly asks for it.

## Public API For Bump Decisions

Treat these as published package behavior once released or documented for users:

- `agent-health` command names, options, exit behavior, stdout/stderr shapes, and JSON/result fields;
- Python package/module imports that users may reasonably call directly;
- sidecar SQLite layout, table names, persisted field names, and migration behavior;
- judge prompt/output contracts including statuses, finding fields, evaluator-error records, and token/call accounting fields;
- configuration keys, environment behavior, default paths, and Hermes-home resolution;
- package metadata and console script entry points.

Truthmark docs and README examples can also force a bump when they document behavior that users rely on and the implementation changes to match or break that documented contract.

## Version Change Procedure

Before changing a version number:

1. Identify the previous released version from the latest lower `release/<version>` tag if one exists.
2. If no `release/*` tag exists, use the current `pyproject.toml` version as the unreleased baseline and inspect the complete payload intended for the next release.
3. Inspect all pending release payload since that previous version: committed branch diff plus staged, unstaged, and untracked files that will ship.
4. Classify the payload with the decision table above before editing `pyproject.toml`.
5. If the requested new version is lower than the required bump, block the edit and report the required version. Do not accept a patch request for a minor or major payload.

When changing a version number:

1. State the previous version, requested version if any, required bump class, and SemVer rationale in the handoff, PR, or release note.
2. Create or update the matching `changes/` note from [change-notes.md](change-notes.md), covering the full release payload since the previous released version or baseline.
3. Update `[project].version` in `pyproject.toml`.
4. Run relevant unit tests and Truthmark verification.
5. If build tooling is available, build or inspect the Python package metadata before publishing.

## Verification

Minimum useful checks for a versioned release payload:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
npx --yes truthmark check --json
npx --yes truthmark index --json
```

If packaging/build tooling is installed, also run the repository's Python build check, for example:

```bash
python3 -m build
```

Report skipped checks explicitly with the reason.

## Agent Output

When reporting a package version decision, state only:

- chosen version action;
- one-line SemVer rationale;
- whether the requested version matched the required bump;
- files changed or intentionally left unchanged;
- matching change note path when a version changes;
- verification run or explicitly skipped.
