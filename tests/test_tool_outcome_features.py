import unittest

from agent_health.tool_outcome_features import build_tool_outcome_features


class ToolOutcomeFeaturesTest(unittest.TestCase):
    def test_features_include_structured_tool_result_evidence_without_labeling(self):
        features = build_tool_outcome_features({
            "id": "e1",
            "assistant_tool_call_message_id": "a",
            "result_message_id": "r",
            "tool_call_id": "tc",
            "tool_name": "terminal",
            "tool_arguments": '{"cmd":"pytest"}',
            "tool_result": '{"exit_code":1,"stderr":"failed"}',
            "request_text_excerpt": "run tests",
            "reasoning": "hidden",
        })

        self.assertEqual(features["tool_name"], "terminal")
        self.assertEqual(features["exit_code"], 1)
        self.assertTrue(features["exit_code_nonzero"])
        self.assertEqual(features["stderr"], "failed")
        self.assertFalse(features["insufficient_for_classification"])
        self.assertNotIn("label", features)
        self.assertNotIn("reasoning", str(features))

    def test_missing_required_source_fields_marks_insufficient(self):
        features = build_tool_outcome_features({"tool_name": "terminal", "tool_result": ""})

        self.assertTrue(features["insufficient_for_classification"])
        self.assertTrue(features["tool_result_empty"])


if __name__ == "__main__":
    unittest.main()
