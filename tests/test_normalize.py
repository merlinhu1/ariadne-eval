import unittest

from agent_health.normalize import detect_user_turn_boundaries, normalize_session


class NormalizeSessionTest(unittest.TestCase):
    def test_detect_user_turn_boundaries_exposes_current_one_real_user_message_unit(self):
        messages = [
            {"id": 1, "role": "user", "content": "First real request", "timestamp": 1.0},
            {"id": 2, "role": "assistant", "content": "First response", "timestamp": 2.0},
            {"id": 3, "role": "user", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] old context", "timestamp": 3.0},
            {"id": 4, "role": "assistant", "content": "ignored", "timestamp": 4.0},
            {"id": 5, "role": "user", "content": "Second real request", "timestamp": 5.0},
            {"id": 6, "role": "assistant", "content": "Second response", "timestamp": 6.0},
        ]

        boundaries = detect_user_turn_boundaries(messages)

        self.assertEqual([b.turn_index for b in boundaries], [1, 2])
        self.assertEqual([b.request_message_id for b in boundaries], ["1", "5"])
        self.assertEqual([b.user_index for b in boundaries], [0, 2])
        self.assertEqual([b.assistant_index for b in boundaries], [1, 3])

    def test_creates_one_turn_case_per_request_text_with_reaction_and_tool_events(self):
        session = {
            "id": "s1",
            "source": "discord",
            "model": "gpt-test",
            "title": "Demo",
            "parent_session_id": None,
            "source_session_api_interaction_count": 3,
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

        cases = normalize_session(session, messages)

        self.assertEqual(len(cases), 2)
        first = cases[0]
        self.assertEqual(first["id"], "hermes:s1:turn:1")
        self.assertEqual(first["request_text"], "Create a file")
        self.assertEqual(first["response_text"], "Created it.")
        self.assertEqual(first["next_request_text"], "No, you did not create it")
        self.assertEqual(first["tool_interaction_count"], 1)
        self.assertEqual(first["case_events"][0]["tool_name"], "write_file")
        self.assertEqual(first["response_message_id"], "4")

    def test_previous_context_is_capped_to_recent_pairs(self):
        session = {"id": "s2", "source": "cli", "model": "m", "source_session_api_interaction_count": 0}
        messages = []
        ts = 1.0
        mid = 1
        for i in range(5):
            messages.append({"id": mid, "role": "user", "content": f"u{i}", "timestamp": ts}); mid += 1; ts += 1
            messages.append({"id": mid, "role": "assistant", "content": f"a{i}", "timestamp": ts}); mid += 1; ts += 1

        cases = normalize_session(session, messages, previous_turn_pairs=2)

        self.assertIn("u2", cases[4]["prior_context_summary"])
        self.assertIn("a3", cases[4]["prior_context_summary"])
        self.assertNotIn("u0", cases[4]["prior_context_summary"])

    def test_skips_context_compaction_and_runtime_user_messages(self):
        session = {"id": "s3", "source": "discord", "model": "m", "source_session_api_interaction_count": 0}
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

        cases = normalize_session(session, messages)

        self.assertEqual([u["request_text"] for u in cases], ["[Merlin] Fix the budget issue", "[Merlin] Now show the eval summary"])
        self.assertEqual(cases[0]["next_request_text"], "[Merlin] Now show the eval summary")
        self.assertNotIn("CONTEXT COMPACTION", cases[1]["prior_context_summary"])
        self.assertNotIn("active task list", cases[1]["prior_context_summary"])

    def test_skips_replayed_document_upload_before_context_compaction(self):
        session = {"id": "s4", "source": "discord", "model": "m", "source_session_api_interaction_count": 0}
        replayed_doc = "[The user sent a text document: 'agent_instruction_health_evaluator_design1.md'. Its content has been included below.]"
        messages = [
            {"id": 1, "role": "user", "content": replayed_doc, "timestamp": 1.0},
            {"id": 2, "role": "assistant", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted", "timestamp": 1.02},
            {"id": 3, "role": "user", "content": "[Merlin] Continue with the real request", "timestamp": 2.0},
            {"id": 4, "role": "assistant", "content": "Continuing the real request.", "timestamp": 3.0},
        ]

        cases = normalize_session(session, messages)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["request_text"], "[Merlin] Continue with the real request")
        self.assertNotIn("agent_instruction_health_evaluator_design1", cases[0]["prior_context_summary"])

    def test_preserves_real_document_upload_when_it_receives_real_response(self):
        session = {"id": "s5", "source": "discord", "model": "m", "source_session_api_interaction_count": 0}
        doc = "[The user sent a text document: 'design.md'. Its content has been included below.]\n[Merlin] This is my implementation plan. The repository is available locally."
        messages = [
            {"id": 1, "role": "user", "content": doc, "timestamp": 1.0},
            {"id": 2, "role": "assistant", "content": "I will inspect the repo and start from the plan.", "timestamp": 2.0},
        ]

        cases = normalize_session(session, messages)

        self.assertEqual(len(cases), 1)
        self.assertIn("implementation plan", cases[0]["request_text"])
    def test_tool_output_error_null_is_not_marked_error(self):
        session = {"id": "s4", "source": "discord", "model": "m", "source_session_api_interaction_count": 0}
        messages = [
            {"id": 1, "role": "user", "content": "run checks", "timestamp": 1.0},
            {"id": 2, "role": "tool", "content": '{"output": "", "exit_code": 0, "error": null}', "tool_name": "terminal", "timestamp": 2.0},
            {"id": 3, "role": "assistant", "content": "passed", "timestamp": 3.0},
        ]

        cases = normalize_session(session, messages)

        self.assertFalse(cases[0]["case_events"][0]["output_error"])

    def test_preserves_long_tool_error_details_for_dashboard_inspection(self):
        session = {"id": "s6", "source": "discord", "model": "m", "source_session_api_interaction_count": 0}
        sentinel = "DETAIL_SENTINEL_AFTER_2000_CHARS"
        long_error = "Traceback start " + ("x" * 2100) + sentinel + " final frame"
        messages = [
            {"id": 1, "role": "user", "content": "run failing command", "timestamp": 1.0},
            {"id": 2, "role": "tool", "content": long_error, "tool_name": "terminal", "timestamp": 2.0},
            {"id": 3, "role": "assistant", "content": "It failed.", "timestamp": 3.0},
        ]

        cases = normalize_session(session, messages)

        self.assertIn(sentinel, cases[0]["case_events"][0]["output_preview"])


if __name__ == "__main__":
    unittest.main()
