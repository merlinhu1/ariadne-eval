---
status: active
doc_type: standard
last_reviewed: 2026-05-19
source_of_truth:
  - versioning.md
  - ../ai/repo-rules.md
---

# Change Notes

## Trigger

Use this standard when a change needs PR text, release text, handoff text, or a package version decision.

Required triggers:

- `[project].version` in `pyproject.toml` changes;
- the user asks for PR, release, changelog, or handoff text;
- the working change alters published package behavior;
- a version decision must be recorded as `patch`, `minor`, `major`, or `none`.

Optional trigger:

- internal-only maintenance that would benefit from a reusable PR summary.

## Folder

Write change notes under `changes/`.

Use one file per cohesive working change:

```text
changes/YYYY-MM-DD-short-slug.md
```

Use the current local session date. Keep slugs short, lowercase, and grep-friendly.

## Required Shape

Each change note must use this structure when `Version action` is `none`:

```markdown
# Short Change Title

Version action: none

## PR Summary
- Concise maintainer-facing summary.

## Release Note
- None; internal-only change.

## Verification
- Command run, or skipped check with reason.
```

Each change note must use this structure when `Version action` is `patch`, `minor`, or `major`:

```markdown
# Short Change Title

Previous version: MAJOR.MINOR.PATCH
New version: MAJOR.MINOR.PATCH
Diff basis: release/MAJOR.MINOR.PATCH..HEAD plus working tree, or current pyproject baseline when no release tag exists
Version action: patch|minor|major
SemVer rationale: One sentence explaining why the pending release payload requires this bump.

Release payload:
- Concise maintainer-facing summary of actual release-worthy changes.

User-facing release text:
- User-visible release note.

Verification:
- Command run, or skipped check with reason.
```

## Rules

- `Version action` must match [versioning.md](versioning.md).
- If `Version action` is `patch`, `minor`, or `major`, `pyproject.toml` must change in the same working change.
- If `pyproject.toml` version changes, a matching change note for the new version is required.
- Versioned change notes must describe all release-worthy pending changes since the previous release tag or unreleased baseline, not only the package metadata edit.
- Versioned change notes must name the previous version, new version, diff basis, SemVer rationale, release payload, user-facing release text, and verification.
- Verification entries should report final useful checks only; do not include red-test scaffolding, agent mistakes, or process chatter in release-facing change notes.
- Internal-only repository standards may use `Version action: none`.
- Release notes describe published package behavior, not private repo maintenance.
- Keep notes compact; they are source material for PR and release descriptions, not canonical product truth.

## Agent Output

When reporting change-note work, state only:

- change note path;
- version action;
- whether release text is present or intentionally `None`.
