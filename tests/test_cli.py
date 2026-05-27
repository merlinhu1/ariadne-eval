import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import agent_health.cli as cli
from agent_health.cli import build_parser, judge_call_budget, select_priority_units
from agent_health.db import EvalDB
from agent_health.judge import JudgeBatchResult, JudgeResult, TokenUsage


class CliBudgetTest(unittest.TestCase):
    def test_eval_defaults_are_budget_safe(self):
        parser = build_parser()
        args = parser.parse_args(["eval", "--due"])

        self.assertEqual(args.limit, 10)
        self.assertEqual(args.max_judge_calls, 5)
        self.assertEqual(args.cooldown_minutes, 120)
        self.assertEqual(args.min_priority_score, 1)
        self.assertEqual(args.judgement_threshold, "strict")

    def test_incidents_command_exists_for_event_level_failures(self):
        parser = build_parser()
        args = parser.parse_args(["incidents", "--since", "5h", "--limit", "10"])

        self.assertEqual(args.since, "5h")
        self.assertEqual(args.limit, 10)
        self.assertTrue(callable(args.func))

    def test_dashboard_install_command_exists(self):
        parser = build_parser()
        args = parser.parse_args(["dashboard", "install"])

        self.assertEqual(args.dashboard_command, "install")
        self.assertTrue(callable(args.func))

    def test_incident_label_command_records_human_label_for_export(self):
        parser = build_parser()
        args = parser.parse_args(["incident", "label", "--example-id", "e", "--label", "not_incident", "--correction"])
        self.assertEqual(args.incident_command, "label")
        self.assertEqual(args.label, "not_incident")
        self.assertTrue(args.correction)
        self.assertTrue(callable(args.func))

    def test_judge_call_budget_caps_large_requested_limits(self):
        self.assertEqual(judge_call_budget(limit=100, max_judge_calls=10), 10)
        self.assertEqual(judge_call_budget(limit=3, max_judge_calls=10), 3)
        self.assertEqual(judge_call_budget(limit=10, max_judge_calls=0), 0)

    def test_priority_selection_skips_low_priority_by_default(self):
        candidates = [
            ({"id": "boring"}, [{"signal_name": "reaction", "signal_value": "continuation", "severity": None}]),
            ({"id": "bad"}, [{"signal_name": "tool_error_count", "signal_value": "2", "severity": "medium"}]),
            ({"id": "corrected"}, [{"signal_name": "reaction", "signal_value": "correction", "severity": "medium"}]),
        ]
        selected = select_priority_units(candidates, budget=2, min_priority_score=1)
        self.assertEqual([row["id"] for row, _, _ in selected], ["corrected", "bad"])

    def test_priority_selection_can_include_low_priority_when_requested(self):
        candidates = [
            ({"id": "boring"}, [{"signal_name": "reaction", "signal_value": "continuation", "severity": None}]),
        ]
        selected = select_priority_units(candidates, budget=1, min_priority_score=0)
        self.assertEqual([row["id"] for row, _, _ in selected], ["boring"])

    def test_eval_due_labels_incidents_with_remaining_judge_budget(self):
        class FakeJudge:
            request_calls = 0
            incident_calls = 0

            def __init__(self, home, max_tokens=0, judgement_threshold="strict"):
                pass

            def resolve_routes(self):
                return []

            def evaluate_unit(self, unit, signals):
                FakeJudge.request_calls += 1
                return JudgeResult(
                    eval_data={
                        "health_status": "succeed",
                        "confidence": "high",
                        "primary_reason": "request ok",
                        "request_friction_score": 0.0,
                        "anomalies": [],
                    },
                    judge_provider="test",
                    judge_model="request-model",
                    raw_output="{}",
                    token_usage=TokenUsage(calls=1),
                )

            def evaluate_incident(self, example, prediction=None):
                FakeJudge.incident_calls += 1
                return JudgeResult(
                    eval_data={
                        "label": "incident",
                        "reason_code": "execution_error",
                        "confidence": 0.9,
                        "evidence_summary": "tool failed",
                    },
                    judge_provider="test",
                    judge_model="incident-model",
                    raw_output="{}",
                    token_usage=TokenUsage(calls=1),
                )

            def evaluate_incidents_batch(self, items):
                return JudgeBatchResult(
                    results={
                        str(example["id"]): self.evaluate_incident(example, prediction)
                        for example, prediction in items
                    },
                    missing_example_ids=[],
                    judge_provider="test",
                    judge_model="incident-model",
                    raw_output="{}",
                    token_usage=TokenUsage(calls=1),
                )

        def unit(unit_id):
            return {
                "id": unit_id,
                "framework": "hermes",
                "source_session_id": "s1",
                "source_turn_index": 1,
                "user_message_id": "u",
                "assistant_message_id": "a",
                "next_user_message_id": "next",
                "started_at": 1.0,
                "ended_at": 2.0,
                "source": "cli",
                "model": "m",
                "title": "t",
                "parent_session_id": None,
                "user_request": "Run it",
                "assistant_response": "Done",
                "previous_context_summary": "",
                "next_user_reaction_text": "thanks",
                "tool_call_count": 1,
                "api_call_count": 1,
                "input_tokens": 1,
                "output_tokens": 2,
                "normalization_version": "normalization_v1",
                "trace_events": [],
            }

        def example(example_id, result_message_id):
            return {
                "id": example_id,
                "framework": "hermes",
                "source_session_id": "s1",
                "source_event_id": example_id,
                "eval_unit_id": "hermes:s1:turn:1",
                "source_turn_index": 1,
                "assistant_tool_call_message_id": "a",
                "result_message_id": result_message_id,
                "tool_call_id": result_message_id,
                "tool_name": "terminal",
                "tool_arguments": '{"cmd":"false"}',
                "tool_result": "exit 1",
                "result_timestamp": 2.0,
                "user_request_excerpt": "Run it",
                "prior_assistant_visible_text": None,
                "following_assistant_visible_text": "Done",
                "normalization_version": "normalization_v1",
            }

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            db = EvalDB(home / "instruction-health" / "evals.db")
            db.upsert_eval_unit(unit("hermes:s1:turn:1"))
            db.upsert_incident_example(example("incident:1", "t1"))
            db.upsert_incident_example(example("incident:2", "t2"))
            original_judge = cli.HermesLLMJudgeClient
            cli.HermesLLMJudgeClient = FakeJudge
            try:
                args = build_parser().parse_args([
                    "--hermes-home", str(home),
                    "eval", "--due",
                    "--max-judge-calls", "3",
                    "--min-priority-score", "0",
                ])
                self.assertEqual(cli.cmd_eval(args), 0)
            finally:
                cli.HermesLLMJudgeClient = original_judge
            with closing(sqlite3.connect(home / "instruction-health" / "evals.db")) as con:
                request_eval_count = con.execute("select count(*) from llm_evals").fetchone()[0]
                incident_label_count = con.execute("select count(*) from incident_labels").fetchone()[0]
                incident_sources = {
                    row[0] for row in con.execute("select distinct label_source from incident_labels").fetchall()
                }

        self.assertEqual(FakeJudge.request_calls, 1)
        self.assertEqual(FakeJudge.incident_calls, 2)
        self.assertEqual(request_eval_count, 1)
        self.assertEqual(incident_label_count, 2)
        self.assertEqual(incident_sources, {"incident_llm_judge"})


if __name__ == "__main__":
    unittest.main()
