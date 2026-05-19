import unittest

from agent_health.normalize import normalize_session


class NormalizeSessionTest(unittest.TestCase):
    def test_creates_one_eval_unit_per_user_request_with_reaction_and_tool_events(self):
        session = {
            "id": "s1",
            "source": "discord",
            "model": "gpt-test",
            "title": "Demo",
            "parent_session_id": None,
            "api_call_count": 3,
            "input_tokens": 100,
            "output_tokens": 50,
        }
        messages = [
            {"id": 1, "role": "user", "content": "Create a file", "timestamp": 1.0},
            {"id": 2, "role": "assistant", "content": "", "tool_calls": '[{"id":"tc1","function":{"name":"write_file","arguments":"{}"}}]', "timestamp": 2.0},
            {"id": 3, "role": "tool", "content": '{"success": true}', "tool_name": "write_file", "tool_call_id": "tc1", "timestamp": 3.0},
            {"id": 4, "role": "assistant", "content": "Created it.", "timestamp": 4.0},
            {"id": 5, "role": "user", "content": "No, you did not create it", "timestamp": 5.0},
            {"id": 6, "role": "assistant", "content": "Sorry, I will try again.", "timestamp": 6.0},
        ]

        units = normalize_session(session, messages)

        self.assertEqual(len(units), 2)
        first = units[0]
        self.assertEqual(first["id"], "hermes:s1:turn:1")
        self.assertEqual(first["user_request"], "Create a file")
        self.assertEqual(first["assistant_response"], "Created it.")
        self.assertEqual(first["next_user_reaction_text"], "No, you did not create it")
        self.assertEqual(first["tool_call_count"], 1)
        self.assertEqual(first["trace_events"][0]["tool_name"], "write_file")
        self.assertEqual(first["assistant_message_id"], "4")

    def test_previous_context_is_capped_to_recent_pairs(self):
        session = {"id": "s2", "source": "cli", "model": "m", "api_call_count": 0}
        messages = []
        ts = 1.0
        mid = 1
        for i in range(5):
            messages.append({"id": mid, "role": "user", "content": f"u{i}", "timestamp": ts}); mid += 1; ts += 1
            messages.append({"id": mid, "role": "assistant", "content": f"a{i}", "timestamp": ts}); mid += 1; ts += 1

        units = normalize_session(session, messages, previous_turn_pairs=2)

        self.assertIn("u2", units[4]["previous_context_summary"])
        self.assertIn("a3", units[4]["previous_context_summary"])
        self.assertNotIn("u0", units[4]["previous_context_summary"])

    def test_skips_context_compaction_and_runtime_user_messages(self):
        session = {"id": "s3", "source": "discord", "model": "m", "api_call_count": 0}
        messages = [
            {"id": 1, "role": "user", "content": "[Merlin] Fix the budget issue", "timestamp": 1.0},
            {"id": 2, "role": "assistant", "content": "I fixed it.", "timestamp": 2.0},
            {"id": 3, "role": "user", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted", "timestamp": 3.0},
            {"id": 4, "role": "assistant", "content": "Compaction loaded.", "timestamp": 4.0},
            {"id": 5, "role": "user", "content": "[Your active task list was preserved across context compression]\n- [>] run tests", "timestamp": 5.0},
            {"id": 6, "role": "assistant", "content": "Task list restored.", "timestamp": 6.0},
            {"id": 7, "role": "user", "content": "You've reached the maximum number of tool-calling iterations allowed. Please continue", "timestamp": 7.0},
            {"id": 8, "role": "assistant", "content": "Continuing.", "timestamp": 8.0},
            {"id": 9, "role": "user", "content": "[Merlin] Now show the eval summary", "timestamp": 9.0},
            {"id": 10, "role": "assistant", "content": "Here is the summary.", "timestamp": 10.0},
        ]

        units = normalize_session(session, messages)

        self.assertEqual([u["user_request"] for u in units], ["[Merlin] Fix the budget issue", "[Merlin] Now show the eval summary"])
        self.assertEqual(units[0]["next_user_reaction_text"], "[Merlin] Now show the eval summary")
        self.assertNotIn("CONTEXT COMPACTION", units[1]["previous_context_summary"])
        self.assertNotIn("active task list", units[1]["previous_context_summary"])

    def test_skips_replayed_document_upload_before_context_compaction(self):
        session = {"id": "s4", "source": "discord", "model": "m", "api_call_count": 0}
        replayed_doc = "[The user sent a text document: 'agent_instruction_health_evaluator_design1.md'. Its content has been included below.]"
        messages = [
            {"id": 1, "role": "user", "content": replayed_doc, "timestamp": 1.0},
            {"id": 2, "role": "assistant", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted", "timestamp": 1.02},
            {"id": 3, "role": "user", "content": "[Merlin] Continue with the real request", "timestamp": 2.0},
            {"id": 4, "role": "assistant", "content": "Continuing the real request.", "timestamp": 3.0},
        ]

        units = normalize_session(session, messages)

        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["user_request"], "[Merlin] Continue with the real request")
        self.assertNotIn("agent_instruction_health_evaluator_design1", units[0]["previous_context_summary"])

    def test_preserves_real_document_upload_when_it_receives_real_response(self):
        session = {"id": "s5", "source": "discord", "model": "m", "api_call_count": 0}
        doc = "[The user sent a text document: 'design.md'. Its content has been included below.]\n[Merlin] This is my implementation plan. The repository is available locally."
        messages = [
            {"id": 1, "role": "user", "content": doc, "timestamp": 1.0},
            {"id": 2, "role": "assistant", "content": "I will inspect the repo and start from the plan.", "timestamp": 2.0},
        ]

        units = normalize_session(session, messages)

        self.assertEqual(len(units), 1)
        self.assertIn("implementation plan", units[0]["user_request"])
    def test_tool_result_error_null_is_not_marked_error(self):
        session = {"id": "s4", "source": "discord", "model": "m", "api_call_count": 0}
        messages = [
            {"id": 1, "role": "user", "content": "run checks", "timestamp": 1.0},
            {"id": 2, "role": "tool", "content": '{"output": "", "exit_code": 0, "error": null}', "tool_name": "terminal", "timestamp": 2.0},
            {"id": 3, "role": "assistant", "content": "passed", "timestamp": 3.0},
        ]

        units = normalize_session(session, messages)

        self.assertFalse(units[0]["trace_events"][0]["result_error"])


if __name__ == "__main__":
    unittest.main()
