import tempfile
import unittest
from pathlib import Path

from agent_health.dashboard_queries import dashboard_summary, eval_unit_detail
from agent_health.db import EvalDB
from agent_health.incidents import summarize_incident_events


def _unit(unit_id, *, session="s1", turn=1, started_at=100.0, ended_at=110.0, request="Do it", response="Done", trace_events=None):
    return {
        "id": unit_id,
        "framework": "hermes",
        "source_session_id": session,
        "source_turn_index": turn,
        "user_message_id": f"{unit_id}:u",
        "assistant_message_id": f"{unit_id}:a",
        "next_user_message_id": None,
        "started_at": started_at,
        "ended_at": ended_at,
        "source": "discord",
        "model": "m",
        "title": "Session title",
        "parent_session_id": None,
        "user_request": request,
        "assistant_response": response,
        "previous_context_summary": "",
        "next_user_reaction_text": None,
        "tool_call_count": len(trace_events or []),
        "api_call_count": 1,
        "input_tokens": 1,
        "output_tokens": 2,
        "normalization_version": "normalization_v1",
        "trace_events": trace_events or [],
    }


class DashboardQueriesTest(unittest.TestCase):
    def test_dashboard_summary_combines_statuses_incidents_anomalies_sessions_and_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            failing_tool = {
                "event_type": "tool",
                "timestamp": 101.0,
                "tool_name": "terminal",
                "args_hash": "sha256:a",
                "result_error": True,
                "result_preview": "exit_code=1",
            }
            db.upsert_eval_unit(_unit("u1", session="s1", turn=1, trace_events=[failing_tool]))
            db.upsert_eval_unit(_unit("u2", session="s1", turn=2, started_at=200.0, ended_at=205.0, request="Second"))
            db.insert_llm_eval(
                "u1",
                prompt_version="instruction_health_v1",
                judge_provider="test",
                judge_model="test-model",
                eval_data={
                    "health_status": "mishandled",
                    "confidence": "high",
                    "primary_reason": "tool failed",
                    "anomalies": [{"type": "tool_failure_unresolved", "severity": "high", "evidence": "terminal failed"}],
                },
                judge_prompt_tokens=10,
                judge_completion_tokens=5,
                judge_total_tokens=15,
                judge_call_count=1,
            )
            db.insert_llm_eval(
                "u2",
                prompt_version="instruction_health_v1",
                judge_provider="test",
                judge_model="test-model",
                eval_data={"health_status": "succeed", "confidence": "medium", "primary_reason": "ok", "anomalies": []},
                judge_total_tokens=7,
                judge_call_count=1,
            )

            summary = dashboard_summary(db, since=0.0, bucket_seconds=3600, unit_limit=100)

            self.assertEqual(summary["totals"]["eval_units"], 2)
            self.assertEqual(summary["totals"]["evaluated_turns"], 2)
            self.assertEqual(summary["totals"]["incidents"], 2)
            self.assertEqual(summary["totals"]["anomalies"], 1)
            self.assertEqual(summary["statuses"], {"mishandled": 1, "succeed": 1})
            self.assertEqual(summary["top_incidents"][0]["incident_type"], "tool_error")
            self.assertEqual(summary["top_anomalies"], [{"anomaly_type": "tool_failure_unresolved", "count": 1}])
            self.assertEqual(summary["judge_tokens"]["total_tokens"], 22)
            self.assertEqual(summary["hot_sessions"][0]["source_session_id"], "s1")
            self.assertEqual(summary["hot_sessions"][0]["incident_count"], 2)
            self.assertTrue(summary["timeline"])

    def test_eval_unit_detail_includes_latest_eval_trace_events_signals_incidents_and_anomalies(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            event = {"event_type": "tool", "timestamp": 11.0, "tool_name": "terminal", "args_hash": "sha256:a", "result_error": True, "result_preview": "failed"}
            db.upsert_eval_unit(_unit("u1", started_at=10.0, ended_at=12.0, trace_events=[event]))
            db.insert_llm_eval(
                "u1",
                prompt_version="instruction_health_v1",
                judge_provider="test",
                judge_model="test-model",
                eval_data={"health_status": "mishandled", "confidence": "high", "primary_reason": "failed", "anomalies": [{"type": "tool_failure_unresolved", "severity": "high"}]},
            )

            detail = eval_unit_detail(db, "u1")

            self.assertEqual(detail["unit"]["id"], "u1")
            self.assertEqual(len(detail["trace_events"]), 1)
            self.assertEqual(detail["latest_eval"]["health_status"], "mishandled")
            self.assertEqual(detail["latest_eval"]["anomalies"][0]["anomaly_type"], "tool_failure_unresolved")
            self.assertEqual(detail["incidents"][0]["incident_type"], "tool_error")
            self.assertEqual(summarize_incident_events(detail["incidents"])["total_incidents"], 2)
            self.assertTrue(any(s["signal_name"] == "tool_error_count" for s in detail["signals"]))


if __name__ == "__main__":
    unittest.main()
