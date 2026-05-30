import tempfile
import unittest
from pathlib import Path

import agent_health.scheduler as scheduler
from agent_health.db import EvalDB
from agent_health.judge import JudgeResult, TokenUsage
from agent_health.scheduler import EvalRunBudget, import_hermes_for_task, run_review_job, schedule_next_task_run


class SchedulerTaskDbTest(unittest.TestCase):
    def test_task_crud_increments_version_and_claim_snapshots_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            task = db.upsert_review_job(
                "nightly",
                {
                    "enabled": True,
                    "schedule_kind": "interval",
                    "interval_seconds": 60,
                    "candidate_limit": 10,
                    "max_judge_calls": 2,
                    "params_json": {"mode": "first"},
                    "next_due_at": 100.0,
                },
            )
            updated = db.upsert_review_job("nightly", {"candidate_limit": 20})
            self.assertEqual(updated["config_version"], task["config_version"] + 1)

            due = db.list_due_review_jobs(now=100.0)
            run = db.claim_review_job(due[0]["id"], lease_owner="worker-a", lease_seconds=30, now=100.0)
            self.assertIsNotNone(run)
            self.assertEqual(run["effective_config_version"], updated["config_version"])
            self.assertEqual(run["effective_params_json"]["mode"], "first")
            self.assertEqual(run["candidate_limit"], 20)

            db.upsert_review_job("nightly", {"candidate_limit": 30})
            history = db.list_review_runs(task_id=task["id"])
            self.assertEqual(history[0]["effective_config_version"], updated["config_version"])
            self.assertEqual(history[0]["effective_params_json"]["candidate_limit"], 20)

    def test_per_task_concurrency_and_stale_lease_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            task = db.upsert_review_job("continuous", {"enabled": True, "schedule_kind": "continuous", "next_due_at": 10.0})
            first = db.claim_review_job(task["id"], lease_owner="a", lease_seconds=30, now=10.0)
            self.assertIsNotNone(first)
            self.assertIsNone(db.claim_review_job(task["id"], lease_owner="b", lease_seconds=30, now=20.0))
            recovered = db.claim_review_job(task["id"], lease_owner="b", lease_seconds=30, now=41.0)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["lease_owner"], "b")

    def test_cursor_get_set_round_trips_json_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            task = db.upsert_review_job("cursor-task", {"enabled": True})
            db.set_review_job_cursor(task["id"], "hermes", "state", {"since": 123.0})
            self.assertEqual(db.get_review_job_cursor(task["id"], "hermes", "state"), {"since": 123.0})

    def test_upsert_by_existing_id_mutates_original_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            task = db.upsert_review_job("dashboard", {"enabled": True})

            updated = db.upsert_review_job(task["id"], {"enabled": False})

            self.assertEqual(updated["id"], task["id"])
            self.assertFalse(updated["enabled"])
            self.assertEqual([row["id"] for row in db.list_review_jobs()], [task["id"]])

    def test_finish_stale_reclaimed_run_does_not_overwrite_task_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            task = db.upsert_review_job("continuous", {"enabled": True, "schedule_kind": "continuous", "next_due_at": 10.0})
            stale = db.claim_review_job(task["id"], lease_owner="a", lease_seconds=5, now=10.0)
            reclaimed = db.claim_review_job(task["id"], lease_owner="b", lease_seconds=30, now=16.0)

            result = db.finish_review_run(
                stale["id"],
                status="succeeded",
                stop_reason="completed",
                next_due_at=999.0,
                metrics={"reviewed_cases": 1},
                now=17.0,
            )
            current = db.get_review_job(task["id"])
            stale_after = db.list_review_runs(task_id=task["id"], limit=10)[1]

            self.assertEqual(result["status"], "failed")
            self.assertEqual(stale_after["id"], stale["id"])
            self.assertEqual(stale_after["status"], "failed")
            self.assertEqual(current["last_run_id"], reclaimed["id"])
            self.assertEqual(current["next_due_at"], 10.0)

    def test_rejects_unsupported_or_invalid_task_schedule_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")

            for updates in (
                {"schedule_kind": "cron"},
                {"schedule_kind": "other"},
                {"interval_seconds": -1},
                {"idle_backoff_seconds": 0},
                {"candidate_limit": 0},
                {"max_judge_calls": -1},
                {"max_review_total_tokens": -1},
                {"max_tokens_per_call": 0},
                {"cooldown_minutes": -1},
                {"min_priority_score": -1},
                {"no_gap": "yes"},
            ):
                with self.subTest(updates=updates):
                    with self.assertRaises(ValueError):
                        db.upsert_review_job("bad", updates)

class SchedulerBudgetTest(unittest.TestCase):
    def test_run_budget_debits_missing_token_usage_one_call_and_total_token_cap(self):
        budget = EvalRunBudget(max_judge_calls=3, max_review_total_tokens=10)
        self.assertTrue(budget.can_spend())
        budget.debit(TokenUsage())
        self.assertEqual(budget.calls_used, 1)
        budget.debit(TokenUsage(calls=1, total_tokens=7, prompt_tokens=3, completion_tokens=4))
        self.assertFalse(budget.can_spend(TokenUsage(calls=1, total_tokens=4)))
        self.assertEqual(budget.stop_reason, "budget_exhausted")

    def test_no_gap_schedules_immediately_for_backlog_or_budget_and_backs_off_when_empty(self):
        task = {"schedule_kind": "interval", "interval_seconds": 60, "no_gap": 1, "idle_backoff_seconds": 300}
        self.assertEqual(schedule_next_task_run(task, finished_at=100.0, backlog_remaining=True, stop_reason="completed"), 100.0)
        self.assertEqual(schedule_next_task_run(task, finished_at=100.0, backlog_remaining=False, stop_reason="budget_exhausted"), 100.0)
        self.assertEqual(schedule_next_task_run(task, finished_at=100.0, backlog_remaining=False, stop_reason="no_due"), 400.0)
        interval = {"schedule_kind": "interval", "interval_seconds": 60, "no_gap": 0, "idle_backoff_seconds": 300}
        self.assertEqual(schedule_next_task_run(interval, finished_at=100.0, backlog_remaining=True, stop_reason="completed"), 160.0)

    def test_scheduler_budget_is_shared_by_request_and_tool_outcome_judges(self):
        class FakeJudge:
            request_calls = 0
            tool_outcome_calls = 0

            def __init__(self, home, max_tokens=0, judgement_threshold="strict"):
                pass

            def evaluate_unit(self, unit, signals):
                FakeJudge.request_calls += 1
                return JudgeResult(
                    eval_data={"outcome_status": "succeed", "confidence": "high", "summary_reason": "ok", "friction_score": 0.0, "case_findings": []},
                    judge_provider="test",
                    judge_model="request",
                    raw_output="{}",
                    token_usage=TokenUsage(),
                )

            def evaluate_tool_outcome(self, example, prediction=None):
                FakeJudge.tool_outcome_calls += 1
                return JudgeResult(
                    eval_data={"outcome_label": "problem", "reason_code": "execution_error", "confidence": 0.9, "evidence_summary": "failed"},
                    judge_provider="test",
                    judge_model="problem",
                    raw_output="{}",
                    token_usage=TokenUsage(),
                )

        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.upsert_review_job("budget", {"enabled": True, "next_due_at": 1.0, "max_judge_calls": 1, "candidate_limit": 10, "min_priority_score": 0})
            db.upsert_turn_case(_unit("hermes:s1:turn:1"))
            db.upsert_tool_outcome_case(_tool_outcome("tool_outcome:1"))
            original_judge = scheduler.HermesLLMJudgeClient
            original_import = scheduler.import_hermes_for_task
            scheduler.HermesLLMJudgeClient = FakeJudge
            scheduler.import_hermes_for_task = lambda db, hermes_home, task: 0
            try:
                run = scheduler.run_review_job(db, tmp, "budget", now=1.0)
            finally:
                scheduler.HermesLLMJudgeClient = original_judge
                scheduler.import_hermes_for_task = original_import

            self.assertEqual(FakeJudge.request_calls, 1)
            self.assertEqual(FakeJudge.tool_outcome_calls, 0)
            self.assertEqual(run["llm_review_calls_used"], 1)
            self.assertEqual(run["stop_reason"], "budget_exhausted")

    def test_scheduler_skips_turn_case_when_claim_cannot_be_acquired(self):
        class FakeJudge:
            calls = 0

            def __init__(self, home, max_tokens=0, judgement_threshold="strict"):
                pass

            def evaluate_unit(self, unit, signals):
                FakeJudge.calls += 1
                raise AssertionError("claim failure still called LLM")

            def evaluate_tool_outcome(self, example, prediction=None):
                raise AssertionError("unexpected tool outcome LLM call")

        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.upsert_review_job("guard", {"enabled": True, "next_due_at": 1.0, "max_judge_calls": 1, "candidate_limit": 10, "min_priority_score": 0})
            db.upsert_turn_case(_unit("hermes:s1:turn:1"))
            db.claim_automatic_llm_review("turn_case", "hermes:s1:turn:1", run_id="existing")
            original_judge = scheduler.HermesLLMJudgeClient
            original_import = scheduler.import_hermes_for_task
            scheduler.HermesLLMJudgeClient = FakeJudge
            scheduler.import_hermes_for_task = lambda db, hermes_home, task: 0
            try:
                run = scheduler.run_review_job(db, tmp, "guard", now=1.0)
            finally:
                scheduler.HermesLLMJudgeClient = original_judge
                scheduler.import_hermes_for_task = original_import

        self.assertEqual(FakeJudge.calls, 0)
        self.assertEqual(run["reviewed_cases"], 0)

    def test_scheduler_skips_tool_outcome_when_parent_has_claim(self):
        class FakeJudge:
            tool_outcome_calls = 0

            def __init__(self, home, max_tokens=0, judgement_threshold="strict"):
                pass

            def evaluate_unit(self, unit, signals):
                return JudgeResult(
                    eval_data={"outcome_status": "succeed", "confidence": "high", "summary_reason": "ok", "friction_score": 0.0, "case_findings": []},
                    judge_provider="test",
                    judge_model="request",
                    raw_output="{}",
                    token_usage=TokenUsage(calls=0),
                )

            def evaluate_tool_outcome(self, example, prediction=None):
                FakeJudge.tool_outcome_calls += 1
                raise AssertionError("parent-claimed tool outcome reached LLM")

        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.upsert_review_job("guard", {"enabled": True, "next_due_at": 1.0, "max_judge_calls": 1, "candidate_limit": 10, "min_priority_score": 9})
            db.upsert_turn_case(_unit("hermes:s1:turn:1"))
            db.upsert_tool_outcome_case(_tool_outcome("tool_outcome:1"))
            db.claim_automatic_llm_review("turn_case", "hermes:s1:turn:1", run_id="parent")
            original_judge = scheduler.HermesLLMJudgeClient
            original_import = scheduler.import_hermes_for_task
            scheduler.HermesLLMJudgeClient = FakeJudge
            scheduler.import_hermes_for_task = lambda db, hermes_home, task: 0
            try:
                run = scheduler.run_review_job(db, tmp, "guard", now=1.0)
            finally:
                scheduler.HermesLLMJudgeClient = original_judge
                scheduler.import_hermes_for_task = original_import

        self.assertEqual(FakeJudge.tool_outcome_calls, 0)
        self.assertEqual(run["tool_outcome_reviews"], 0)

    def test_scheduler_import_cursor_does_not_skip_newest_first_backlog(self):
        class FakeAdapter:
            sessions = [
                {"id": "s5", "started_at": 5.0},
                {"id": "s4", "started_at": 4.0},
                {"id": "s3", "started_at": 3.0},
                {"id": "s2", "started_at": 2.0},
                {"id": "s1", "started_at": 1.0},
            ]

            def __init__(self, home):
                pass

            def discover_due_sources(self, since=None, limit=1000, *, oldest_first=False):
                rows = [row for row in self.sessions if since is None or row["started_at"] >= since]
                if oldest_first:
                    rows = list(reversed(rows))
                for row in rows[:limit]:
                    yield row["id"]

            def load_source(self, source_id):
                started = float(source_id[1:])
                return {"session": {"id": source_id, "source": "cli", "model": "m", "started_at": started, "ended_at": started + 0.5, "title": source_id, "tool_interaction_count": 0, "source_session_api_interaction_count": 1, "input_tokens": 1, "output_tokens": 1}, "messages": [{"id": f"{source_id}:u", "role": "user", "content": "Do it", "timestamp": started}, {"id": f"{source_id}:a", "role": "assistant", "content": "Done", "timestamp": started + 0.1}]}

            def normalize_turn_cases(self, raw_source):
                return [_unit(f"hermes:{raw_source['session']['id']}:turn:1", started_at=raw_source["session"]["started_at"], session_id=raw_source["session"]["id"])]

            def build_tool_outcome_cases(self, raw_source):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            task = db.upsert_review_job("import", {"candidate_limit": 2})
            original = scheduler.HermesAdapter
            scheduler.HermesAdapter = FakeAdapter
            try:
                counts = [import_hermes_for_task(db, tmp, task) for _ in range(3)]
            finally:
                scheduler.HermesAdapter = original

            self.assertEqual(counts, [2, 2, 1])
            self.assertEqual(len(db.list_due_turn_cases(limit=10, cooldown_seconds=0, now=10.0)), 5)

    def test_scheduler_import_cursor_keeps_equal_timestamp_sessions(self):
        class FakeAdapter:
            sessions = [
                {"id": "s3", "started_at": 2.0},
                {"id": "s2", "started_at": 1.0},
                {"id": "s1", "started_at": 1.0},
            ]

            def __init__(self, home):
                pass

            def discover_due_sources(self, since=None, limit=1000, *, oldest_first=False):
                rows = [row for row in self.sessions if since is None or row["started_at"] >= since]
                if oldest_first:
                    rows = sorted(rows, key=lambda row: (row["started_at"], row["id"]))
                for row in rows[:limit]:
                    yield row["id"]

            def load_source(self, source_id):
                started = 2.0 if source_id == "s3" else 1.0
                return {"session": {"id": source_id, "started_at": started}, "messages": []}

            def normalize_turn_cases(self, raw_source):
                return [_unit(f"hermes:{raw_source['session']['id']}:turn:1", started_at=raw_source["session"]["started_at"], session_id=raw_source["session"]["id"])]

            def build_tool_outcome_cases(self, raw_source):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            task = db.upsert_review_job("import", {"candidate_limit": 1})
            original = scheduler.HermesAdapter
            scheduler.HermesAdapter = FakeAdapter
            try:
                counts = [import_hermes_for_task(db, tmp, task) for _ in range(3)]
            finally:
                scheduler.HermesAdapter = original

            self.assertEqual(counts, [1, 1, 1])
            self.assertEqual(len(db.list_due_turn_cases(limit=10, cooldown_seconds=0, now=10.0)), 3)


def _unit(unit_id, *, started_at=1.0, session_id="s1"):
    return {
        "id": unit_id,
        "framework": "hermes",
        "source_session_id": session_id,
        "turn_index": 1,
        "request_message_id": unit_id + ":u",
        "response_message_id": unit_id + ":a",
        "next_request_message_id": unit_id + ":next",
        "started_at": started_at,
        "ended_at": started_at + 1.0,
        "source": "cli",
        "model": "m",
        "title": "t",
        "parent_session_id": None,
        "request_text": "Do it",
        "response_text": "Done",
        "prior_context_summary": "",
        "next_request_text": "No, fix it",
        "tool_interaction_count": 0,
        "source_session_api_interaction_count": 1,
        "input_tokens": 1,
        "output_tokens": 2,
        "case_builder_version": "normalization_v1",
        "case_events": [],
    }


def _tool_outcome(example_id):
    return {
        "id": example_id,
        "framework": "hermes",
        "source_session_id": "s1",
        "source_event_id": example_id,
        "turn_case_id": "hermes:s1:turn:1",
        "turn_index": 1,
        "assistant_tool_call_message_id": "a",
        "result_message_id": "tool",
        "tool_call_id": "tc1",
        "tool_name": "terminal",
        "tool_result": "exit 1",
        "result_timestamp": 2.0,
        "case_builder_version": "normalization_v1",
    }


if __name__ == "__main__":
    unittest.main()
