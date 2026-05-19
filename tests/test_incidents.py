import unittest

from agent_health.incidents import extract_incident_events, summarize_incident_events


class IncidentExtractionTest(unittest.TestCase):
    def test_each_tool_error_becomes_one_incident_event(self):
        unit = {
            "id": "hermes:s1:turn:1",
            "source_session_id": "s1",
            "source_turn_index": 1,
            "user_request": "run tests",
            "assistant_response": "I ran them",
            "tool_call_count": 3,
            "api_call_count": 1,
            "trace_events": [
                {"id": "e1", "event_type": "tool", "tool_name": "terminal", "args_preview": "pytest", "result_preview": "exit_code: 1\nfailed", "result_error": True},
                {"id": "e2", "event_type": "tool", "tool_name": "terminal", "args_preview": "pytest -q", "result_preview": "Traceback", "result_error": False},
                {"id": "e3", "event_type": "tool", "tool_name": "read_file", "args_preview": "README.md", "result_preview": "ok", "result_error": False},
            ],
        }

        incidents = extract_incident_events(unit)

        tool_errors = [i for i in incidents if i["incident_type"] == "tool_error"]
        self.assertEqual(len(tool_errors), 2)
        self.assertEqual(tool_errors[0]["related_event_id"], "e1")
        self.assertEqual(tool_errors[1]["related_event_id"], "e2")
        self.assertIn("terminal", tool_errors[0]["evidence"])
        self.assertNotIn("bump_type", tool_errors[0])

    def test_incidents_include_loops_excess_and_incomplete_turns(self):
        unit = {
            "id": "hermes:s1:turn:2",
            "source_session_id": "s1",
            "source_turn_index": 2,
            "user_request": "fix it",
            "assistant_response": "",
            "tool_call_count": 9,
            "api_call_count": 6,
            "trace_events": [
                {"id": "r1", "event_type": "tool", "tool_name": "terminal", "args_hash": "sha:a", "args_preview": "pytest", "result_preview": "fail", "result_error": False},
                {"id": "r2", "event_type": "tool", "tool_name": "terminal", "args_hash": "sha:a", "args_preview": "pytest", "result_preview": "fail", "result_error": False},
                {"id": "r3", "event_type": "tool", "tool_name": "terminal", "args_hash": "sha:a", "args_preview": "pytest", "result_preview": "fail", "result_error": False},
            ],
        }

        incidents = extract_incident_events(unit)
        types = [i["incident_type"] for i in incidents]

        self.assertIn("repeated_tool_loop", types)
        self.assertIn("excessive_tool_calls", types)
        self.assertIn("excessive_api_calls", types)
        self.assertIn("interrupted_or_incomplete", types)
    def test_successful_tool_output_with_error_null_is_not_a_tool_error(self):
        unit = {
            "id": "hermes:s1:turn:3",
            "source_session_id": "s1",
            "source_turn_index": 3,
            "user_request": "run checks",
            "assistant_response": "Checks passed",
            "tool_call_count": 2,
            "api_call_count": 1,
            "trace_events": [
                {"id": "ok1", "event_type": "tool", "tool_name": "terminal", "result_preview": '{"output": "", "exit_code": 0, "error": null}', "result_error": False},
                {"id": "ok2", "event_type": "tool", "tool_name": "terminal", "result_preview": "Truthmark check completed with 0 error diagnostics.", "result_error": False},
                {"id": "ok3", "event_type": "tool", "tool_name": "read_file", "result_preview": '{"content": "Exception and failed: are just source text, not tool failure"', "result_error": False},
            ],
        }

        incidents = extract_incident_events(unit)

        self.assertEqual([i for i in incidents if i["incident_type"] == "tool_error"], [])

    def test_incident_summary_uses_incident_language(self):
        summary = summarize_incident_events([
            {"incident_type": "tool_error", "severity": "high"},
            {"incident_type": "tool_error", "severity": "medium"},
        ])

        self.assertEqual(summary["total_incidents"], 2)
        self.assertEqual(summary["by_type"], {"tool_error": 2})
        self.assertNotIn("total_bumps", summary)


if __name__ == "__main__":
    unittest.main()
