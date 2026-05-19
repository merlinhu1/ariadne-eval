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


if __name__ == "__main__":
    unittest.main()
