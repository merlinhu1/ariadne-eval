# Tiny Incident Classifier

Previous version: 0.2.0
New version: 0.3.0
Diff basis: current pyproject baseline when no release tag exists plus working tree
Version action: minor
SemVer rationale: Adds backward-compatible public CLI commands/options and a new non-LLM incident-classification capability.

Release payload:
- Adds high-precision incident taxonomy/rules for timeout, permission/auth denial, approval denial, rate limits, dependency/path/network/resource failures, test failures, quality-gate failures, and git rejections.
- Adds optional scikit-learn tiny classifier training/inference via `ariadne-eval[ml]`, `agent-health ml export-training`, `agent-health ml train`, and `agent-health ml classify`.
- Allows trained classifiers to contribute model-derived incident evidence to `incidents` output and `eval --due` prefilter signals without replacing the LLM judge's final status role.

User-facing release text:
- Ariadne Eval can now classify common trace failure modes more specifically and can optionally train a tiny non-LLM incident classifier from weak labels, while keeping LLM judging reserved for final semantic health status.

Verification:
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'` → `Ran 48 tests`, `OK`.
- `npx --yes truthmark check --json` → no diagnostics.
- `npx --yes truthmark index --json` → 0 error diagnostics and 0 review diagnostics.
