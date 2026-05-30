import unittest

import agent_health.tool_outcome_reviewer_model as tool_outcome_reviewer_model
import agent_health.tool_outcome_taxonomy as tool_outcome_taxonomy


class ToolOutcomeModelTest(unittest.TestCase):
    def test_tool_outcome_decision_labels_are_tool_outcome_ok_or_unsure_only(self):
        self.assertEqual(tool_outcome_taxonomy.TOOL_OUTCOME_DECISION_LABELS, {"problem", "ok", "unsure"})
        for label in ("problem", "ok", "unsure"):
            with self.subTest(label=label):
                self.assertEqual(tool_outcome_taxonomy.validate_tool_outcome_decision_label(label), label)
                decision = tool_outcome_reviewer_model.ToolOutcomeDecision(label, False, None, None, 0.9, 0.1, "test")
                self.assertEqual(decision.label, label)


if __name__ == "__main__":
    unittest.main()
