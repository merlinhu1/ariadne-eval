import unittest

from agent_health.incident_model import IncidentDecision
from agent_health.incident_routing import route_incident_decision


class IncidentRoutingTest(unittest.TestCase):
    def test_confident_incident_and_not_incident_are_final_ml_predictions(self):
        incident = route_incident_decision(
            IncidentDecision("incident", True, None, None, 0.93, 0.07, "ml_model", evidence_summary="top_label_margin=0.20"),
            llm_budget_available=True,
        )
        not_incident = route_incident_decision(
            IncidentDecision("not_incident", False, None, None, 0.81, 0.19, "ml_model", evidence_summary="top_label_margin=0.20"),
            llm_budget_available=True,
        )

        self.assertEqual(incident.decision_source, "ml_model")
        self.assertFalse(incident.should_defer_to_llm)
        self.assertEqual(not_incident.label, "not_incident")
        self.assertFalse(not_incident.should_defer_to_llm)

    def test_low_confidence_defers_with_budget_and_falls_back_without_budget(self):
        decision = IncidentDecision("incident", True, None, None, 0.55, 0.45, "ml_model", evidence_summary="top_label_margin=0.01")

        deferred = route_incident_decision(decision, llm_budget_available=True)
        fallback = route_incident_decision(decision, llm_budget_available=False)

        self.assertEqual(deferred.label, "unsure")
        self.assertTrue(deferred.should_defer_to_llm)
        self.assertFalse(deferred.budget_fallback)
        self.assertEqual(fallback.label, "incident")
        self.assertEqual(fallback.decision_source, "ml_model_budget_fallback")
        self.assertFalse(fallback.should_defer_to_llm)
        self.assertTrue(fallback.budget_fallback)

    def test_unsure_falls_back_to_not_incident_without_llm_budget(self):
        decision = IncidentDecision("unsure", None, None, None, 0.4, 0.6, "ml_model", evidence_summary="top_label_margin=0.01")

        fallback = route_incident_decision(decision, llm_budget_available=False)

        self.assertEqual(fallback.label, "not_incident")
        self.assertFalse(fallback.is_incident)
        self.assertTrue(fallback.budget_fallback)


if __name__ == "__main__":
    unittest.main()
