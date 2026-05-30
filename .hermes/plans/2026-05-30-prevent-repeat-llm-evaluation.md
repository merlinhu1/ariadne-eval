# Fail-Closed Automatic LLM Judge Eligibility Plan

> **For Hermes:** Use `subagent-driven-development` for implementation, but keep this change small, test-first, and fail-closed. This is a safety fix for LLM spend prevention, not a broad redesign.

**Goal:** Ariadne Eval must never call an automatic LLM judge for a `turn_case` or `tool_outcome_case` that already has automatic LLM judgment history **or an automatic LLM judge claim**. A CLI flag, scheduler loop, stale selected list, batch retry, deferred ML path, or concurrent worker must not bypass this rule.

**Why now:** The last-48-hours repeat-judging failure spent LLM calls on work that already had LLM coverage. That is unacceptable. This fix treats prior automatic LLM review history and prior automatic LLM judge claims as hard safety boundaries, not as selection preferences.

**Architecture:** Add an atomic DB-backed automatic LLM judge claim before every automatic LLM call, centralize fail-closed eligibility in `EvalDB`, enforce it at selection and final pre-call sites, add future-write barriers for duplicate automatic LLM reviews, and remove/neutralize any `reevaluate` behavior that can cause automatic LLM rejudge. Human correction, imported labels, and local ML predictions remain allowed; automatic LLM judging does not.

**Tech Stack:** Python, SQLite, `unittest`, Ariadne Eval modules under `src/agent_health/`.

---

## Agreement With Review Finding

Yes: the review finding is correct. The previous plan's pre-call read checks plus post-call unique indexes were not enough to guarantee zero duplicate LLM spend under concurrency.

The unsafe race was:

1. process A checks `is_*_llm_eligible()` and sees eligible,
2. process B checks the same row and sees eligible,
3. both processes call the LLM,
4. only after the spend does one write lose to a unique constraint.

That protects storage but not budget. The fixed design must acquire a one-way DB claim **before** any LLM request is made. If the claim cannot be acquired, the code must skip without calling the judge.

---

## Non-Negotiable Design Decisions

Use **fail-closed automatic LLM eligibility with atomic claims**:

1. A `turn_case` with any prior `case_reviews` row where `reviewer_type='automatic_llm'` and `review_scope='turn_case'` is never eligible for another automatic LLM turn-case review.
2. A `turn_case` with any prior automatic LLM judge claim is never eligible for another automatic LLM turn-case review, even if no review row was written.
3. A `tool_outcome_case` with any prior `tool_outcome_reviews` row where `reviewer_type='automatic_llm'` is never eligible for another automatic LLM tool-outcome review.
4. A `tool_outcome_case` with any prior automatic LLM judge claim is never eligible for another automatic LLM tool-outcome review, even if no review row was written.
5. A `tool_outcome_case` whose parent `turn_case_id` has an automatic LLM `case_review` or automatic LLM judge claim is never eligible for automatic LLM tool-outcome review.
6. Selection queries may filter early, but the final authorization for spend is `EvalDB.claim_automatic_llm_review(...)`. No claim means no LLM call.
7. Claims are one-way spend barriers. A failed, crashed, interrupted, or partially completed automatic LLM attempt still blocks automatic retry. Coverage gaps are safer than duplicate spend.
8. Batch LLM calls must claim every item immediately before batch construction. If no claims are acquired, do not call the LLM. If a batch LLM call was attempted, do not retry missing/failed rows one-by-one in the same automatic path; treat them as spent/attempted and leave them blocked.
9. `--reevaluate`, scheduler config, retry logic, and ML deferred judging do not override this rule.
10. Human review/correction and imported historical labels are still allowed. Local ML `tool_outcome_reviews` are still allowed. The ban applies to automatic LLM calls only.
11. A future manual rejudge feature would need a separate design with an audited reason, hard cap, warning, and different reviewer identity. Do not add that escape hatch in this fix.

---

## Safety Boundaries For This Implementation

This plan must not break existing local data or broad CLI behavior while closing the spend bug.

- Do not delete, rewrite, quarantine, or relabel historical review rows in this patch.
- Do not clear `tool_outcome_reviews` when a parent `case_review` changes.
- Do not make local ML prediction, human review, import, export, dashboard, or browsing paths depend on LLM claims.
- Do not disable review jobs blindly. Inspect the active DB path first, snapshot before any operational mitigation, and only disable enabled automatic jobs if mitigation is needed.
- Do not add a migration that fails startup merely because a local pre-existing DB already contains duplicate automatic LLM review rows. Add future-write barriers that work even when historical duplicates exist, and report historical duplicates for follow-up.
- Do not rely on an in-memory row, stale selected list, or CLI argument for eligibility. The DB is authoritative.

---

## Current Evidence In Working Tree

Observed in the current naming-refactor workspace:

- `src/agent_health/db.py` has `list_due_turn_cases(..., reevaluate=False, ...)`; when `reevaluate=True`, it can remove the prior-review filter. That is an automatic LLM rejudge path and must be closed.
- `src/agent_health/db.py` inserts automatic `case_reviews` with conflict/update behavior in the current working tree. Any silent duplicate automatic LLM write/update must be replaced with clear rejection or no-op skip after a failed claim.
- `src/agent_health/db.py` has a partial unique index for automatic turn-case reviews, but write-time storage safety alone does not prevent duplicate LLM spend.
- `src/agent_health/db.py` has `list_tool_outcome_cases(...)`; it can exclude parent automatic case reviews, but it needs a single claim/eligibility contract that also excludes prior automatic `tool_outcome_reviews` and prior claims.
- `src/agent_health/scheduler.py` calls `judge.evaluate_unit(...)` and `judge.evaluate_tool_outcome(...)`; both need final claim acquisition immediately before the call.
- `src/agent_health/cli.py` has automatic LLM paths for `agent-health review`, `agent-health tool-outcomes llm-review`, and `agent-health tool-outcomes predict --judge-deferred`; all must share the same claim contract.
- The current parser uses `agent-health tool-outcomes llm-review`; keep the plan and tests aligned with that command name.

---

## Task 0: Immediate Operational Mitigation

**Objective:** Stop additional budget drain until code has the fail-closed guards.

**Files:** none, unless an operator intentionally chooses to disable an enabled job in the live DB.

**Step 1: Inspect the active DB path using the same application resolver used by the CLI**

```bash
PYTHONPATH=src python3 - <<'PY'
from agent_health.config import default_eval_db_path
from agent_health.db import EvalDB
from pathlib import Path
home = Path('/opt/data/.hermes')
db_path = default_eval_db_path(home)
print(f'db_path={db_path}')
db = EvalDB(db_path)
for row in db.list_review_jobs(limit=20):
    print(row['id'], row.get('enabled'), row.get('name'), row.get('next_due_at'))
PY
```

**Step 2: Snapshot before any operational write**

Only run if the inspection shows an enabled automatic review job that can still spend LLM calls:

```bash
PYTHONPATH=src python3 - <<'PY'
from agent_health.config import default_eval_db_path
from pathlib import Path
import shutil, time
home = Path('/opt/data/.hermes')
db_path = default_eval_db_path(home)
snapshot = db_path.with_suffix(db_path.suffix + f'.pre-llm-claim-fix.{int(time.time())}.bak')
shutil.copy2(db_path, snapshot)
print(snapshot)
PY
```

**Step 3: Disable enabled automatic jobs only after snapshot**

Use the same resolved DB path; do not hard-code a different path:

```bash
PYTHONPATH=src python3 - <<'PY'
from agent_health.config import default_eval_db_path
from pathlib import Path
import sqlite3, time
home = Path('/opt/data/.hermes')
db_path = default_eval_db_path(home)
con = sqlite3.connect(db_path)
con.execute('UPDATE review_jobs SET enabled = 0, updated_at = ? WHERE enabled = 1', (time.time(),))
con.commit()
print(con.execute('SELECT id, enabled, name FROM review_jobs ORDER BY id').fetchall())
PY
```

**Acceptance:** No enabled scheduled review job can spend automatic LLM calls while this fix is pending, and the inspection/mitigation path is one resolved DB path.

---

## Task 1: Add RED Tests For Atomic Claim Semantics

**Objective:** Prove two workers cannot both authorize an automatic LLM call for the same target.

**Files:**

- Modify: `tests/test_db_and_signals.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_cli.py`

**Step 1: DB claim is atomic and one-way for turn cases**

Create one `turn_case`, then call the new claim helper twice:

```python
claim_1 = db.claim_automatic_llm_review(target_type='turn_case', target_id=turn_case_id, run_id='run-a')
claim_2 = db.claim_automatic_llm_review(target_type='turn_case', target_id=turn_case_id, run_id='run-b')
self.assertIsNotNone(claim_1)
self.assertIsNone(claim_2)
self.assertFalse(db.is_turn_case_llm_eligible(turn_case_id))
```

Add a second test where an automatic `case_review` already exists before the claim attempt. Expected: claim returns `None` and eligibility is false.

Add a third test for a nonexistent `turn_case_id`. Expected: claim returns `None` and eligibility is false.

**Step 2: DB claim is atomic and one-way for tool-outcome cases**

Create one `tool_outcome_case`, then call the claim helper twice:

```python
claim_1 = db.claim_automatic_llm_review(target_type='tool_outcome_case', target_id=tool_outcome_case_id, run_id='run-a')
claim_2 = db.claim_automatic_llm_review(target_type='tool_outcome_case', target_id=tool_outcome_case_id, run_id='run-b')
self.assertIsNotNone(claim_1)
self.assertIsNone(claim_2)
self.assertFalse(db.is_tool_outcome_case_llm_eligible(tool_outcome_case_id))
```

Add tests where the claim returns `None` when:

- the `tool_outcome_case` does not exist,
- the tool outcome already has an automatic LLM `tool_outcome_review`,
- the parent `turn_case` already has an automatic LLM `case_review`,
- the parent `turn_case` already has an automatic LLM claim.

**Step 3: Race-shaped test without real threads**

Use two `EvalDB` instances connected to the same temporary SQLite file. Both attempt `claim_automatic_llm_review(...)` for the same target. Assert exactly one succeeds. This tests DB-level atomicity rather than Python object state.

**Step 4: Threaded smoke test if stable in this test suite**

If the repository already has reliable threaded SQLite tests, add a small two-thread barrier test around the same helper and assert exactly one claim. If adding threads would make tests flaky, keep the two-connection deterministic test and document why.

---

## Task 2: Add RED Tests For Turn-Case Rejudge Blocking

**Objective:** Prove `--reevaluate`, stale selection, or any direct caller cannot re-call the turn-case LLM judge after prior automatic LLM review or claim.

**Files:**

- Modify: `tests/test_db_and_signals.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_scheduler.py`

**Step 1: DB eligibility test**

Create one `turn_case`, insert an automatic `case_review`, then assert:

```python
self.assertTrue(db.turn_case_has_automatic_case_review(turn_case_id))
self.assertFalse(db.is_turn_case_llm_eligible(turn_case_id))
self.assertEqual(db.list_due_turn_cases(limit=10, reevaluate=False), [])
self.assertEqual(db.list_due_turn_cases(limit=10, reevaluate=True), [])
self.assertIsNone(db.claim_automatic_llm_review('turn_case', turn_case_id, run_id='test'))
```

Expected before implementation: the `reevaluate=True` and claim assertions fail or helper is missing.

**Step 2: CLI review test**

Add a test around `cmd_eval` / `agent-health review` with:

- one already automatically reviewed `turn_case`,
- `args.reevaluate=True`,
- fake `HermesLLMJudgeClient.evaluate_unit` that raises `AssertionError` if called.

Expected after implementation: command exits without calling the fake judge and reports no LLM-eligible turn cases.

**Step 3: Scheduler stale-list test**

Add a test that selects a case, then inserts an automatic claim or automatic `case_review` before the call via a fake DB wrapper or monkeypatch. Fake judge raises if called.

Expected after implementation: scheduler skips the case and records zero `reviewed_cases` / zero LLM calls for that item.

**Step 4: Scheduler claim conflict test**

Make the DB wrapper return an eligible selected row, then make `claim_automatic_llm_review(...)` return `None`. Fake judge raises if called.

Expected after implementation: scheduler treats claim failure as a skip, not an error and not a judge call.

---

## Task 3: Add RED Tests For Tool-Outcome Rejudge Blocking

**Objective:** Prove a `tool_outcome_case` is never sent to automatic LLM if the tool outcome itself or its parent turn case has LLM history or a claim.

**Files:**

- Modify: `tests/test_db_and_signals.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_scheduler.py`

**Step 1: Own prior automatic tool-outcome review**

Create one `tool_outcome_case`, insert a `tool_outcome_review` with `reviewer_type='automatic_llm'`, then assert:

```python
self.assertTrue(db.tool_outcome_case_has_automatic_llm_review(tool_outcome_case_id))
self.assertFalse(db.is_tool_outcome_case_llm_eligible(tool_outcome_case_id))
self.assertEqual(db.list_tool_outcome_cases(limit=10, llm_eligible_only=True), [])
self.assertIsNone(db.claim_automatic_llm_review('tool_outcome_case', tool_outcome_case_id, run_id='test'))
```

**Step 2: Parent prior automatic case review**

Create one `turn_case`, one linked `tool_outcome_case`, insert an automatic `case_review` for the parent, then assert `is_tool_outcome_case_llm_eligible(...)` is false, `list_tool_outcome_cases(..., llm_eligible_only=True)` excludes it, and the claim helper returns `None`.

**Step 3: Parent prior automatic claim**

Create one `turn_case`, one linked `tool_outcome_case`, acquire an automatic LLM claim for the parent `turn_case`, then assert the child tool outcome is ineligible and cannot acquire a child claim.

**Step 4: CLI single/batch paths**

Use fake judge methods that raise if called. Cover:

- `_label_tool_outcome_cases_with_judge(..., reevaluate=True, batch_size=1)`,
- `_label_tool_outcome_cases_with_judge(..., reevaluate=True, batch_size=10)`,
- `cmd_tool_outcome_predict --judge-deferred`.

Expected after implementation: no fake LLM calls for previously automatic-LLM-reviewed, previously claimed, or parent-reviewed/parent-claimed tool-outcome cases.

**Step 5: Batch retry prevention test**

Create two eligible tool-outcome cases. Let `_label_tool_outcome_cases_with_judge` acquire claims and send a batch to a fake batch judge that returns a partial/missing result or raises after call entry. The fake one-by-one judge path must raise if called.

Expected after implementation: once the batch LLM call has been attempted for claimed items, the code must not retry those same items one-by-one. It should mark claims failed/partial as appropriate and report skipped/failed without extra LLM spend.

**Step 6: Scheduler tool-outcome stale-list test**

Create a tool-outcome case that appears eligible at list time. Insert an automatic claim or automatic tool-outcome review before `judge.evaluate_tool_outcome`. Fake judge raises if called.

Expected after implementation: scheduler skips it and records zero new `tool_outcome_reviews` / zero LLM calls for that item.

---

## Task 4: Add The Automatic LLM Judge Claim Table

**Objective:** Provide the atomic budget guard used by scheduler, CLI, and future callers.

**Files:**

- Modify: `src/agent_health/db.py`
- Modify tests in `tests/test_db_and_signals.py`

**Schema:**

Add a non-destructive table in `migrate()`:

```sql
CREATE TABLE IF NOT EXISTS automatic_llm_review_claims (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK(target_type IN ('turn_case', 'tool_outcome_case')),
    target_id TEXT NOT NULL,
    parent_turn_case_id TEXT,
    run_id TEXT,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'claimed',
    claimed_at REAL NOT NULL,
    llm_started_at REAL,
    completed_at REAL,
    error_message TEXT,
    metadata_json TEXT,
    UNIQUE(target_type, target_id)
);
```

Add indexes for inspection:

```sql
CREATE INDEX IF NOT EXISTS idx_automatic_llm_review_claims_status
ON automatic_llm_review_claims(status, claimed_at);

CREATE INDEX IF NOT EXISTS idx_automatic_llm_review_claims_parent
ON automatic_llm_review_claims(parent_turn_case_id);
```

**Claim statuses:**

- `claimed`: DB claim acquired, LLM call not yet marked started.
- `llm_started`: code is about to call or has called the LLM; assume spend may have happened.
- `review_inserted`: automatic LLM review row was written.
- `failed_before_review`: LLM call started or may have started, but no review row was written.
- `failed_before_call`: reserved for failures provably before LLM call entry. This status still blocks automatic retry in this fix.

Statuses are operational diagnostics only. Any row in this table blocks future automatic LLM judging for the target.

**Claim helper contract:**

Add:

```python
def claim_automatic_llm_review(
    self,
    target_type: str,
    target_id: str,
    *,
    run_id: str | None = None,
    source: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object] | None:
    ...
```

The helper must call `self.migrate()` and perform a single atomic `INSERT ... SELECT ... WHERE ...` so existence, prior review checks, parent checks, and claim acquisition happen in the same DB statement. It returns the inserted claim row on success and `None` on any ineligible/missing/already-claimed target.

**Turn-case claim SQL shape:**

```sql
INSERT OR IGNORE INTO automatic_llm_review_claims (
    id, target_type, target_id, parent_turn_case_id, run_id, source, status, claimed_at, metadata_json
)
SELECT ?, 'turn_case', tc.id, tc.id, ?, ?, 'claimed', ?, ?
FROM turn_cases tc
WHERE tc.id = ?
  AND NOT EXISTS (
      SELECT 1 FROM case_reviews cr
      WHERE cr.turn_case_id = tc.id
        AND cr.reviewer_type = 'automatic_llm'
        AND cr.review_scope = 'turn_case'
  )
  AND NOT EXISTS (
      SELECT 1 FROM automatic_llm_review_claims c
      WHERE c.target_type = 'turn_case'
        AND c.target_id = tc.id
  );
```

**Tool-outcome claim SQL shape:**

```sql
INSERT OR IGNORE INTO automatic_llm_review_claims (
    id, target_type, target_id, parent_turn_case_id, run_id, source, status, claimed_at, metadata_json
)
SELECT ?, 'tool_outcome_case', toc.id, toc.turn_case_id, ?, ?, 'claimed', ?, ?
FROM tool_outcome_cases toc
WHERE toc.id = ?
  AND NOT EXISTS (
      SELECT 1 FROM tool_outcome_reviews tor
      WHERE tor.tool_outcome_case_id = toc.id
        AND tor.reviewer_type = 'automatic_llm'
  )
  AND NOT EXISTS (
      SELECT 1 FROM case_reviews cr
      WHERE cr.turn_case_id = toc.turn_case_id
        AND cr.reviewer_type = 'automatic_llm'
        AND cr.review_scope = 'turn_case'
  )
  AND NOT EXISTS (
      SELECT 1 FROM automatic_llm_review_claims c
      WHERE c.target_type = 'tool_outcome_case'
        AND c.target_id = toc.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM automatic_llm_review_claims parent_claim
      WHERE parent_claim.target_type = 'turn_case'
        AND parent_claim.target_id = toc.turn_case_id
  );
```

After the insert, fetch by `id`; if no row was inserted, return `None`.

**Claim update helpers:**

Add small helpers:

```python
def mark_automatic_llm_claim_started(self, claim_id: str) -> None: ...
def mark_automatic_llm_claim_review_inserted(self, claim_id: str) -> None: ...
def mark_automatic_llm_claim_failed(self, claim_id: str, *, before_call: bool, error_message: str) -> None: ...
```

These helpers must never delete claims.

---

## Task 5: Centralize Eligibility In `EvalDB`

**Objective:** Provide readable eligibility helpers for selection, dry-run messaging, and tests. These helpers are not spend authorization; claims are.

**Files:**

- Modify: `src/agent_health/db.py`

**Add or update helpers near existing review/listing methods:**

```python
def turn_case_has_automatic_case_review(self, turn_case_id: str) -> bool:
    ...

def turn_case_has_automatic_llm_claim(self, turn_case_id: str) -> bool:
    ...

def tool_outcome_case_has_automatic_llm_review(self, tool_outcome_case_id: str) -> bool:
    ...

def tool_outcome_case_has_automatic_llm_claim(self, tool_outcome_case_id: str) -> bool:
    ...

def is_turn_case_llm_eligible(self, turn_case_id: str) -> bool:
    # False for blank/missing IDs.
    # False unless the target row exists.
    # False if prior automatic LLM review exists.
    # False if prior automatic LLM claim exists.
    ...

def is_tool_outcome_case_llm_eligible(self, tool_outcome_case_id: str) -> bool:
    # False for blank/missing IDs.
    # False unless the target row exists.
    # False if prior automatic LLM tool-outcome review exists.
    # False if prior automatic LLM tool-outcome claim exists.
    # False if parent turn_case has automatic LLM review or claim.
    ...
```

**Implementation details:**

- Helpers must call `self.migrate()` and use direct SQL.
- Missing IDs are ineligible (`False`), not eligible and not exceptions at guard sites.
- `is_tool_outcome_case_llm_eligible` must fetch the parent `turn_case_id` from `tool_outcome_cases` instead of trusting caller-provided row data.
- Keep human/imported/ML review rows out of automatic LLM exclusion except where explicitly parent automatic case review blocks child automatic tool-outcome review.
- Use these helpers for diagnostics and list filtering, but use `claim_automatic_llm_review(...)` as the final spend gate.

---

## Task 6: Make Selection Queries Fail Closed

**Objective:** Ensure the normal query APIs do not feed already judged or claimed artifacts into automatic LLM call sites.

**Files:**

- Modify: `src/agent_health/db.py`
- Update affected call sites/tests.

**Step 1: Turn cases**

Change `list_due_turn_cases` so it always excludes prior automatic LLM case reviews and prior turn-case automatic LLM claims:

```sql
AND NOT EXISTS (
    SELECT 1 FROM case_reviews cr
    WHERE cr.turn_case_id = u.id
      AND cr.reviewer_type = 'automatic_llm'
      AND cr.review_scope = 'turn_case'
)
AND NOT EXISTS (
    SELECT 1 FROM automatic_llm_review_claims c
    WHERE c.target_type = 'turn_case'
      AND c.target_id = u.id
)
```

Do not let `reevaluate=True` remove this automatic LLM exclusion. If `reevaluate` remains, it may only affect non-automatic rows such as human/imported reviews; document that in the method docstring and CLI help. For this fix, neutralize the dangerous behavior first.

**Step 2: Tool outcomes**

Add or update `llm_eligible_only: bool = False` on `list_tool_outcome_cases`. When true, append all four filters:

```sql
AND NOT EXISTS (
    SELECT 1 FROM tool_outcome_reviews tor
    WHERE tor.tool_outcome_case_id = e.id
      AND tor.reviewer_type = 'automatic_llm'
)
AND NOT EXISTS (
    SELECT 1 FROM automatic_llm_review_claims c
    WHERE c.target_type = 'tool_outcome_case'
      AND c.target_id = e.id
)
AND NOT EXISTS (
    SELECT 1 FROM case_reviews cr
    WHERE cr.turn_case_id = e.turn_case_id
      AND cr.reviewer_type = 'automatic_llm'
      AND cr.review_scope = 'turn_case'
)
AND NOT EXISTS (
    SELECT 1 FROM automatic_llm_review_claims parent_claim
    WHERE parent_claim.target_type = 'turn_case'
      AND parent_claim.target_id = e.turn_case_id
)
```

Then update automatic LLM call sites to pass `llm_eligible_only=True`. Keep dashboard/non-LLM browsing free to list historical rows.

---

## Task 7: Enforce Claims Immediately Before Every LLM Call

**Objective:** Close stale-list and concurrent-worker windows.

**Files:**

- Modify: `src/agent_health/scheduler.py`
- Modify: `src/agent_health/cli.py`

**Rule:** The final line of defense before an automatic LLM call is a successful DB claim. A boolean eligibility check is useful but not sufficient.

**Scheduler turn-case loop:**

Immediately before `judge.evaluate_unit(unit, signals)`:

```python
claim = db.claim_automatic_llm_review(
    'turn_case',
    str(unit.get('id') or ''),
    run_id=str(run.get('id') or '') if run else None,
    source='scheduler.review.turn_case',
)
if claim is None:
    continue
try:
    db.mark_automatic_llm_claim_started(str(claim['id']))
    result = judge.evaluate_unit(unit, signals)
    # insert automatic case_review...
    db.mark_automatic_llm_claim_review_inserted(str(claim['id']))
except Exception as exc:
    db.mark_automatic_llm_claim_failed(str(claim['id']), before_call=False, error_message=str(exc))
    raise
```

If this code cannot prove the exception occurred before the LLM call was entered, use `before_call=False` and keep the claim blocking automatic retry.

**Scheduler tool-outcome loop:**

Immediately before `judge.evaluate_tool_outcome(example)`:

```python
claim = db.claim_automatic_llm_review(
    'tool_outcome_case',
    str(example.get('id') or ''),
    run_id=str(run.get('id') or '') if run else None,
    source='scheduler.review.tool_outcome_case',
)
if claim is None:
    continue
try:
    db.mark_automatic_llm_claim_started(str(claim['id']))
    result = judge.evaluate_tool_outcome(example)
    # insert automatic tool_outcome_review...
    db.mark_automatic_llm_claim_review_inserted(str(claim['id']))
except Exception as exc:
    db.mark_automatic_llm_claim_failed(str(claim['id']), before_call=False, error_message=str(exc))
    raise
```

**CLI `agent-health review`:**

- `list_due_turn_cases(..., reevaluate=args.reevaluate)` must still return no automatic-LLM-reviewed or automatic-LLM-claimed rows.
- Acquire `claim_automatic_llm_review('turn_case', unit['id'], source='cli.review')` immediately before `judge.evaluate_unit`.
- If `args.reevaluate` is true and every candidate is skipped due prior automatic LLM coverage or claims, print: `No LLM-eligible turn cases; automatic LLM reviews are never re-run.`

**CLI `agent-health tool-outcomes llm-review`:**

- `_label_tool_outcome_cases_with_judge` calls `list_tool_outcome_cases(..., llm_eligible_only=True)`.
- Single-item path claims immediately before `evaluate_tool_outcome`.
- Batch path claims every item immediately before building the LLM batch payload.
- If claim acquisition empties the batch, do not call the LLM.
- Once a batch LLM call has been attempted, do not retry missing or failed batch items one-by-one in automatic LLM mode. Mark claims failed/partial and report them.

**CLI `agent-health tool-outcomes predict --judge-deferred`:**

Before `cmd_tool_outcome_predict` calls `judge.evaluate_tool_outcome(...)`, acquire `claim_automatic_llm_review('tool_outcome_case', example['id'], source='cli.tool_outcomes.predict.judge_deferred')`. If claim returns `None`, insert only the local ML review/prediction row when appropriate and do not spend an LLM call.

**Dry-run behavior:**

Dry-run must never create claims and never call the LLM. It may report rows that would be skipped because they are already reviewed/claimed, but it must label them as ineligible.

---

## Task 8: Make Duplicate Automatic LLM Writes Structurally Impossible Without Breaking Historical DBs

**Objective:** If a future caller forgets the claim guard, the DB layer must reject duplicate automatic LLM writes and parent-reviewed tool-outcome writes instead of updating or hiding them. Existing historical duplicate rows must not make startup fail.

**Files:**

- Modify: `src/agent_health/db.py`
- Modify tests in `tests/test_db_and_signals.py`

**Step 1: Add preflight duplicate inspection**

Add a helper for diagnostics and tests:

```python
def list_duplicate_automatic_llm_reviews(self) -> dict[str, list[dict[str, object]]]: ...
```

It should report:

- `case_reviews` duplicate groups by `turn_case_id` where `reviewer_type='automatic_llm' AND review_scope='turn_case'`,
- `tool_outcome_reviews` duplicate groups by `tool_outcome_case_id` where `reviewer_type='automatic_llm'`.

Do not delete or rewrite rows in this helper.

**Step 2: Keep or create future-write barriers for turn-case reviews**

The clean schema should keep this index when the table has no historical duplicates:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_case_reviews_automatic_llm_turn_case
ON case_reviews(turn_case_id)
WHERE reviewer_type = 'automatic_llm' AND review_scope = 'turn_case';
```

For existing DBs where this index already exists, no change is needed. If a local DB has historical duplicates that would prevent creating a new unique index, do not delete data in `migrate()`. Instead, create a trigger that rejects future duplicate automatic LLM inserts/updates. SQLite triggers can be created even when old duplicates exist.

**Step 3: Add future-write barriers for tool-outcome reviews**

On clean DBs, add:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_outcome_reviews_automatic_llm_case
ON tool_outcome_reviews(tool_outcome_case_id)
WHERE reviewer_type = 'automatic_llm';
```

On DBs with historical duplicate automatic tool-outcome rows, do not destructively dedupe in this patch. Create triggers that reject future duplicate automatic LLM inserts/updates.

**Trigger shape:**

```sql
CREATE TRIGGER IF NOT EXISTS trg_tool_outcome_reviews_no_duplicate_auto_llm_insert
BEFORE INSERT ON tool_outcome_reviews
WHEN NEW.reviewer_type = 'automatic_llm'
 AND EXISTS (
     SELECT 1 FROM tool_outcome_reviews existing
     WHERE existing.tool_outcome_case_id = NEW.tool_outcome_case_id
       AND existing.reviewer_type = 'automatic_llm'
 )
BEGIN
    SELECT RAISE(ABORT, 'duplicate automatic LLM tool outcome review');
END;
```

Add corresponding `BEFORE UPDATE` triggers if updates can change `reviewer_type` or `tool_outcome_case_id`.

**Step 4: Reject automatic child tool-outcome review when parent is reviewed or claimed**

Add DB-layer write checks in `insert_tool_outcome_review(...)` for `reviewer_type='automatic_llm'`:

- reject if the target `tool_outcome_case` is missing,
- reject if the target already has an automatic LLM `tool_outcome_review`,
- reject if parent `turn_case` has an automatic LLM `case_review`,
- reject if parent `turn_case` has an automatic LLM claim and the current child claim is not valid for this write.

Add a trigger if practical:

```sql
CREATE TRIGGER IF NOT EXISTS trg_tool_outcome_reviews_no_auto_llm_after_parent_review
BEFORE INSERT ON tool_outcome_reviews
WHEN NEW.reviewer_type = 'automatic_llm'
 AND EXISTS (
     SELECT 1
     FROM tool_outcome_cases toc
     JOIN case_reviews cr ON cr.turn_case_id = toc.turn_case_id
     WHERE toc.id = NEW.tool_outcome_case_id
       AND cr.reviewer_type = 'automatic_llm'
       AND cr.review_scope = 'turn_case'
 )
BEGIN
    SELECT RAISE(ABORT, 'parent turn_case already has automatic LLM review');
END;
```

Also add an update trigger if updates can turn a non-automatic row into automatic.

**Step 5: Replace silent upsert/update behavior**

Change automatic LLM write paths so they do **not** use `ON CONFLICT ... DO UPDATE` for protected automatic review rows. A duplicate must raise a clear exception or be caught by the caller after no claim was acquired. It must not silently overwrite the existing review.

**Step 6: Do not block human/ML/imported updates**

Tests must show these still work:

- multiple `tool_outcome_reviews` with `reviewer_type='ml_model'` for the same case are allowed if current training/prediction semantics need that history,
- human correction remains allowed,
- imported review rows remain allowed.

---

## Task 9: Remove Dangerous `reevaluate` Semantics From User-Facing Help

**Objective:** Make it impossible for an operator to believe `--reevaluate` will re-run automatic LLM judging on prior automatic LLM-reviewed or claimed artifacts.

**Files:**

- Modify: `src/agent_health/cli.py`
- Modify CLI tests/docs.

**Required behavior:**

- `agent-health review --reevaluate` must not include prior automatic LLM `case_reviews` or claims.
- `agent-health tool-outcomes llm-review --reevaluate` must not include prior automatic LLM `tool_outcome_reviews`, prior tool-outcome claims, children of automatic LLM-reviewed parent turn cases, or children of automatic LLM-claimed parent turn cases.
- `agent-health tool-outcomes predict --judge-deferred --reevaluate` must not use LLM for ineligible/claimed cases.
- Help text should state: `--reevaluate can revisit non-automatic review state only; automatic LLM reviews and claims are never re-run by this command.`

If maintaining `--reevaluate` creates ambiguity, make it a no-op for automatic LLM paths in this patch and open a follow-up to rename/remove it.

---

## Task 10: Update Truth Docs

**Objective:** Preserve the safety contract as active repository truth so future changes do not regress it.

**Files:**

- Read first: `docs/truthmark/areas.md`
- Likely modify: `docs/truth/evaluation/turn-cases-and-case-signals.md`
- Possibly modify: `docs/truth/local-runtime/cli-and-sidecar-storage.md`

**Required truth claim:**

> Automatic LLM judging is fail-closed. A `turn_case` with prior automatic LLM `case_review` or automatic LLM judge claim is never automatically judged by an LLM again. A `tool_outcome_case` with prior automatic LLM `tool_outcome_review` or automatic LLM judge claim, or whose parent `turn_case` has prior automatic LLM `case_review` or automatic LLM judge claim, is never automatically judged by an LLM again. Human correction, imported reviews, and local ML predictions remain allowed.

Because this plan will lead to behavior-bearing code changes, the implementation must run Truthmark sync/check according to repo rules after tests pass:

```bash
truthmark sync
truthmark check
```

If `truthmark sync` is not available in this environment, report the exact command failure and run `truthmark check` so the gap is visible.

---

## Task 11: Verification

Run focused tests first:

```bash
PYTHONPATH=src python3 -m unittest tests.test_db_and_signals tests.test_cli tests.test_scheduler -v
```

Run broader verification after focused tests pass:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src tests
git diff --check
rg "evaluate_unit\(|evaluate_tool_outcome\(|evaluate_tool_outcomes_batch\(" src/agent_health tests
rg "claim_automatic_llm_review|automatic_llm_review_claims|reevaluate" src/agent_health tests README.md docs/truth docs/design.md
truthmark sync
truthmark check
```

Manual review checklist for every LLM call match:

- there is an immediate pre-call DB claim,
- claim acquisition uses `EvalDB`, not stale in-memory row state only,
- no claim means no LLM call,
- batch construction happens after claim acquisition,
- claimed batch rows are not retried one-by-one after a batch LLM attempt,
- dry-run paths do not create claims,
- `--reevaluate` cannot include automatic LLM-reviewed or automatic LLM-claimed targets,
- write paths reject duplicate automatic LLM reviews and parent-reviewed child automatic LLM reviews,
- metrics count skipped/ineligible/claim-conflict items separately or at least do not count them as reviewed,
- duplicate historical rows are reported, not destructively rewritten.

---

## Acceptance Criteria

- No automatic LLM call can be made for a `turn_case` that already has automatic LLM `case_review` history.
- No automatic LLM call can be made for a `turn_case` that already has an automatic LLM judge claim.
- No automatic LLM call can be made for a `tool_outcome_case` that already has automatic LLM `tool_outcome_review` history.
- No automatic LLM call can be made for a `tool_outcome_case` that already has an automatic LLM judge claim.
- No automatic LLM call can be made for a `tool_outcome_case` whose parent `turn_case` already has automatic LLM `case_review` history or an automatic LLM judge claim.
- `--reevaluate` cannot override the prior five rules.
- Scheduler selection, CLI direct review, CLI tool-outcome LLM review, batch mode, failed/missing batch handling, and ML deferred LLM judging all share the same DB-backed claim gate.
- Duplicate automatic LLM writes are rejected by SQLite unique indexes on clean DBs and/or triggers on DBs with historical duplicates; they are not silently upserted.
- Existing historical duplicate rows are not deleted or rewritten by this safety patch.
- Eligibility helpers return false for nonexistent IDs.
- Regression tests include fake judges that raise if called for previously automatic-LLM-reviewed, claimed, or parent-reviewed/parent-claimed artifacts.
- Truth docs state the fail-closed automatic LLM spend contract.
- Relevant `unittest`, `compileall`, `git diff --check`, stale-call-site review, `truthmark sync`, and `truthmark check` have been run or explicitly reported.

## Follow-Up Items

- Audit and summarize the already-spent duplicate/overlap LLM review rows from the last 48 hours.
- Decide whether historical overlapping `tool_outcome_reviews` should be retained as provenance, excluded from training, or quarantined from training exports.
- Design a future audited manual rejudge path only if truly needed; it must use a distinct reviewer identity and visible hard caps, not `automatic_llm`.
- Consider renaming or removing `--reevaluate` in a separate cleanup once the safety fix has landed.
