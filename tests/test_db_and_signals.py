import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_health.db import EvalDB
from agent_health.reactions import classify_reaction
from agent_health.signals import extract_deterministic_signals


class EvalDbAndSignalsTest(unittest.TestCase):
    def test_migration_creates_queryable_tables_and_upserts_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.migrate()
            unit = {
                "id": "hermes:s1:turn:1",
                "framework": "hermes",
                "source_session_id": "s1",
                "source_turn_index": 1,
                "user_message_id": "1",
                "assistant_message_id": "2",
                "next_user_message_id": None,
                "started_at": 1.0,
                "ended_at": 2.0,
                "source": "cli",
                "model": "m",
                "title": "t",
                "parent_session_id": None,
                "user_request": "Do it",
                "assistant_response": "Done",
                "previous_context_summary": "",
                "next_user_reaction_text": None,
                "tool_call_count": 0,
                "api_call_count": 1,
                "input_tokens": 1,
                "output_tokens": 2,
                "normalization_version": "normalization_v1",
                "trace_events": [],
            }
            db.upsert_eval_unit(unit)

            with sqlite3.connect(Path(tmp) / "evals.db") as con:
                tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
                self.assertTrue({"eval_units", "trace_events", "deterministic_signals", "llm_evals", "barriers", "eval_state"}.issubset(tables))
                row = con.execute("select user_request, assistant_response from eval_units").fetchone()
            self.assertEqual(row, ("Do it", "Done"))

    def test_signals_detect_tool_errors_repeats_duration_and_reaction(self):
        unit = {
            "id": "u1",
            "started_at": 0.0,
            "ended_at": 650.0,
            "tool_call_count": 9,
            "api_call_count": 5,
            "next_user_reaction_text": "No, you didn't create the file.",
            "trace_events": [
                {"event_type": "tool", "tool_name": "terminal", "args_hash": "sha256:a", "result_error": False, "result_preview": "ok"},
                {"event_type": "tool", "tool_name": "terminal", "args_hash": "sha256:a", "result_error": True, "result_preview": "exit_code 1"},
                {"event_type": "tool", "tool_name": "terminal", "args_hash": "sha256:a", "result_error": False, "result_preview": "ok"},
            ],
        }

        signals = extract_deterministic_signals(unit)
        by_name = {s["signal_name"]: s for s in signals}

        self.assertEqual(by_name["tool_error_count"]["signal_value"], "1")
        self.assertEqual(by_name["same_tool_repeat_count"]["signal_value"], "3")
        self.assertEqual(by_name["turn_duration_seconds"]["severity"], "high")
        self.assertEqual(by_name["next_user_reaction_type"]["signal_value"], "correction")

    def test_reaction_classifier_distinguishes_acceptance_scope_change_and_repeated_request(self):
        self.assertEqual(classify_reaction("Thanks, that works"), "acceptance")
        self.assertEqual(classify_reaction("Now add tests too"), "scope_change")
        self.assertEqual(classify_reaction("Can you create the file?", previous_request="Create the file"), "repeated_request")

    def test_due_units_are_budget_gated_by_reaction_or_cooldown(self):
        def unit(unit_id, *, ended_at, reaction=None):
            return {
                "id": unit_id,
                "framework": "hermes",
                "source_session_id": unit_id.split(":")[1],
                "source_turn_index": 1,
                "user_message_id": unit_id + ":u",
                "assistant_message_id": unit_id + ":a",
                "next_user_message_id": unit_id + ":next" if reaction else None,
                "started_at": ended_at - 10,
                "ended_at": ended_at,
                "source": "cli",
                "model": "m",
                "title": "t",
                "parent_session_id": None,
                "user_request": "Do it",
                "assistant_response": "Done",
                "previous_context_summary": "",
                "next_user_reaction_text": reaction,
                "tool_call_count": 0,
                "api_call_count": 1,
                "input_tokens": 1,
                "output_tokens": 2,
                "normalization_version": "normalization_v1",
                "trace_events": [],
            }

        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.upsert_eval_unit(unit("hermes:recent:turn:1", ended_at=990.0))
            db.upsert_eval_unit(unit("hermes:reacted:turn:1", ended_at=995.0, reaction="No, that is wrong"))
            db.upsert_eval_unit(unit("hermes:old:turn:1", ended_at=100.0))

            due = db.list_due_units(limit=10, cooldown_seconds=120, now=1000.0)
            self.assertEqual([row["id"] for row in due], ["hermes:old:turn:1", "hermes:reacted:turn:1"])

            db.insert_llm_eval(
                "hermes:old:turn:1",
                prompt_version="instruction_health_v1",
                judge_provider="test",
                judge_model="test-model",
                eval_data={"health_status": "succeed", "confidence": "high", "primary_reason": "ok", "barriers": []},
            )
            due_after_eval = db.list_due_units(limit=10, cooldown_seconds=120, now=1000.0)
            self.assertEqual([row["id"] for row in due_after_eval], ["hermes:reacted:turn:1"])


if __name__ == "__main__":
    unittest.main()
