import sqlite3
import tempfile
import unittest
from contextlib import closing
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

            with closing(sqlite3.connect(Path(tmp) / "evals.db")) as con:
                tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
                self.assertTrue({"eval_units", "trace_events", "deterministic_signals", "llm_evals", "anomalies", "incident_eval_examples", "incident_labels", "eval_state"}.issubset(tables))
                row = con.execute("select user_request, assistant_response from eval_units").fetchone()
                columns = {r[1] for r in con.execute("pragma table_info(llm_evals)")}
            self.assertEqual(row, ("Do it", "Done"))
            self.assertTrue({"judge_prompt_tokens", "judge_completion_tokens", "judge_total_tokens", "judge_call_count", "request_friction_score"}.issubset(columns))

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
        self.assertEqual(by_name["reaction"]["signal_value"], "correction")
        self.assertEqual(
            set(by_name),
            {
                "tool_call_count",
                "api_call_count",
                "turn_duration_seconds",
                "tool_error_count",
                "same_tool_repeat_count",
                "reaction",
                "assistant_claimed_completion",
            },
        )

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
                eval_data={"health_status": "succeed", "confidence": "high", "primary_reason": "ok", "request_friction_score": 0.0, "anomalies": []},
            )
            due_after_eval = db.list_due_units(limit=10, cooldown_seconds=120, now=1000.0)
            self.assertEqual([row["id"] for row in due_after_eval], ["hermes:reacted:turn:1"])

    def test_due_units_skip_any_prior_judgement_unless_reevaluate(self):
        unit = {
            "id": "hermes:error:turn:1",
            "framework": "hermes",
            "source_session_id": "error",
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
            "user_request": "Do it",
            "assistant_response": "Done",
            "previous_context_summary": "",
            "next_user_reaction_text": "Thanks",
            "tool_call_count": 0,
            "api_call_count": 1,
            "input_tokens": 1,
            "output_tokens": 2,
            "normalization_version": "normalization_v1",
            "trace_events": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.upsert_eval_unit(unit)
            db.insert_llm_eval(
                "hermes:error:turn:1",
                prompt_version="instruction_health_v1",
                judge_provider=None,
                judge_model=None,
                eval_data={"health_status": "failed", "confidence": "low", "primary_reason": "judge failed", "request_friction_score": 1.0, "anomalies": []},
                evaluator_error="provider failed",
            )

            self.assertEqual(db.list_due_units(limit=10, cooldown_seconds=0, now=10.0), [])
            self.assertEqual([row["id"] for row in db.list_due_units(limit=10, cooldown_seconds=0, now=10.0, reevaluate=True)], ["hermes:error:turn:1"])
    def test_human_incident_labels_export_high_quality_training_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            example_id = db.upsert_incident_example({
                "id": "incident:1",
                "framework": "hermes",
                "source_session_id": "s1",
                "source_event_id": "s1|a|t|tc1",
                "eval_unit_id": "hermes:s1:turn:1",
                "source_turn_index": 1,
                "assistant_tool_call_message_id": "a",
                "result_message_id": "t",
                "tool_call_id": "tc1",
                "tool_name": "terminal",
                "tool_arguments": "cat /root/secret",
                "tool_result": "Permission denied",
                "result_timestamp": 1.5,
                "user_request_excerpt": "Open the secret file",
                "normalization_version": "normalization_v1",
            })
            label_id = db.insert_incident_label(
                example_id,
                label="incident",
                reason_code="execution_error",
                reason_confidence=0.95,
                label_source="human_correction",
                accepted_for_training=True,
                comment="This is auth/permissions, not generic tool error.",
                reviewer="merlin",
            )
            rows = db.export_accepted_incident_training()

        self.assertEqual(rows[0]["id"], label_id)
        self.assertEqual(rows[0]["label"], "incident")
        self.assertEqual(rows[0]["reason_code"], "execution_error")
        self.assertEqual(rows[0]["label_source"], "human_correction")
        self.assertGreaterEqual(rows[0]["weight"], 3.0)
        self.assertIn("Permission denied", rows[0]["text"])
        self.assertIn("Open the secret file", rows[0]["text"])

    def test_human_not_incident_label_exports_negative_training_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            example_id = db.upsert_incident_example({
                "id": "incident:1",
                "framework": "hermes",
                "source_session_id": "s1",
                "source_event_id": "s1|a|t|tc1",
                "eval_unit_id": "hermes:s1:turn:1",
                "source_turn_index": 1,
                "assistant_tool_call_message_id": "a",
                "result_message_id": "t",
                "tool_call_id": "tc1",
                "tool_name": "read_file",
                "tool_result": "{\"content\":\"TimeoutError docs example\"}",
                "user_request_excerpt": "Read docs",
                "normalization_version": "normalization_v1",
            })
            db.insert_incident_label(example_id, label="not_incident", label_source="human", accepted_for_training=True, reviewer="merlin")
            rows = db.export_accepted_incident_training()

        self.assertEqual(rows[0]["label"], "not_incident")
        self.assertGreaterEqual(rows[0]["weight"], 3.0)
        self.assertIn("TimeoutError docs example", rows[0]["text"])

    def test_llm_eval_requires_request_friction_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.upsert_eval_unit({
                "id": "hermes:s1:turn:1",
                "framework": "hermes",
                "source_session_id": "s1",
                "source_turn_index": 1,
                "user_message_id": "u",
                "assistant_message_id": "a",
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
            })
            with self.assertRaises(ValueError):
                db.insert_llm_eval(
                    "hermes:s1:turn:1",
                    prompt_version="instruction_health_v1",
                    judge_provider="test",
                    judge_model="test",
                    eval_data={"health_status": "succeed", "confidence": "high", "primary_reason": "ok", "anomalies": []},
                )
            db.insert_llm_eval(
                "hermes:s1:turn:1",
                prompt_version="instruction_health_v1",
                judge_provider="test",
                judge_model="test",
                eval_data={"health_status": "succeed", "confidence": "high", "primary_reason": "ok", "request_friction_score": 0.2, "anomalies": []},
            )
            self.assertEqual(db.summary()["friction"]["avg_request_friction_score"], 0.2)

    def test_canonical_incident_examples_include_latest_label_prediction_and_friction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.upsert_eval_unit({
                "id": "hermes:s1:turn:1",
                "framework": "hermes",
                "source_session_id": "s1",
                "source_turn_index": 1,
                "user_message_id": "u",
                "assistant_message_id": "a",
                "next_user_message_id": None,
                "started_at": 1.0,
                "ended_at": 2.0,
                "source": "cli",
                "model": "m",
                "title": "t",
                "parent_session_id": None,
                "user_request": "Run it",
                "assistant_response": "Done",
                "previous_context_summary": "",
                "next_user_reaction_text": None,
                "tool_call_count": 1,
                "api_call_count": 1,
                "input_tokens": 1,
                "output_tokens": 2,
                "normalization_version": "normalization_v1",
                "trace_events": [],
            })
            db.insert_llm_eval(
                "hermes:s1:turn:1",
                prompt_version="instruction_health_v1",
                judge_provider="test",
                judge_model="test-model",
                eval_data={"health_status": "mishandled", "confidence": "high", "primary_reason": "bad", "request_friction_score": 0.75, "anomalies": []},
            )
            example_id = db.upsert_incident_example({
                "id": "incident:1",
                "framework": "hermes",
                "source_session_id": "s1",
                "source_event_id": "e1",
                "eval_unit_id": "hermes:s1:turn:1",
                "source_turn_index": 1,
                "assistant_tool_call_message_id": "a",
                "result_message_id": "t",
                "tool_call_id": "tc1",
                "tool_name": "terminal",
                "tool_result": "exit 1",
                "result_timestamp": 2.0,
                "normalization_version": "normalization_v1",
            })
            db.insert_incident_label(example_id, label="not_incident", label_source="incident_llm_judge")
            db.insert_incident_label(example_id, label="incident", reason_code="execution_error", label_source="human", reviewer="merlin")
            db.insert_incident_prediction(example_id, label="incident", decision_source="ml_model", reason_code="execution_error")

            rows = db.list_canonical_incident_examples(source_session_id="s1", limit=10)

        self.assertEqual(rows[0]["id"], "incident:1")
        self.assertEqual(rows[0]["eval_unit_id"], "hermes:s1:turn:1")
        self.assertEqual(rows[0]["tool_name"], "terminal")
        self.assertEqual(rows[0]["label"], "incident")
        self.assertEqual(rows[0]["label_source"], "human")
        self.assertEqual(rows[0]["prediction_label"], "incident")
        self.assertEqual(rows[0]["reason_code"], "execution_error")
        self.assertEqual(rows[0]["request_friction_score"], 0.75)


if __name__ == "__main__":
    unittest.main()
