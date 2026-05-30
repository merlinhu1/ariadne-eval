import unittest

from agent_health.tool_outcome_reviewer_model import ToolOutcomeDecision
from agent_health.tool_outcome_routing import route_tool_outcome_decision


class ToolOutcomeRoutingTest(unittest.TestCase):
    def test_confident_tool_outcome_and_ok_are_final_ml_predictions(self):
        tool_outcome = route_tool_outcome_decision(
            ToolOutcomeDecision("problem", True, None, None, 0.93, 0.07, "ml_model", evidence_summary="top_label_margin=0.20"),
            llm_review_budget_available=True,
        )
        ok = route_tool_outcome_decision(
            ToolOutcomeDecision("ok", False, None, None, 0.81, 0.19, "ml_model", evidence_summary="top_label_margin=0.20"),
            llm_review_budget_available=True,
        )

        self.assertEqual(tool_outcome.decision_source, "ml_model")
        self.assertFalse(tool_outcome.should_defer_to_llm)
        self.assertEqual(ok.label, "ok")
        self.assertFalse(ok.should_defer_to_llm)

    def test_low_confidence_defers_with_budget_and_falls_back_without_budget(self):
        decision = ToolOutcomeDecision("problem", True, None, None, 0.55, 0.45, "ml_model", evidence_summary="top_label_margin=0.01")

        deferred = route_tool_outcome_decision(decision, llm_review_budget_available=True)
        fallback = route_tool_outcome_decision(decision, llm_review_budget_available=False)

        self.assertEqual(deferred.label, "unsure")
        self.assertTrue(deferred.should_defer_to_llm)
        self.assertFalse(deferred.budget_fallback)
        self.assertEqual(fallback.label, "problem")
        self.assertEqual(fallback.decision_source, "ml_model_budget_fallback")
        self.assertFalse(fallback.should_defer_to_llm)
        self.assertTrue(fallback.budget_fallback)

    def test_unsure_falls_back_to_ok_without_llm_budget(self):
        decision = ToolOutcomeDecision("unsure", None, None, None, 0.4, 0.6, "ml_model", evidence_summary="top_label_margin=0.01")

        fallback = route_tool_outcome_decision(decision, llm_review_budget_available=False)

        self.assertEqual(fallback.label, "ok")
        self.assertFalse(fallback.is_tool_outcome)
        self.assertTrue(fallback.budget_fallback)


if __name__ == "__main__":
    unittest.main()
