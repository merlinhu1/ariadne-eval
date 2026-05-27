import unittest

import agent_health.incident_model as incident_model
import agent_health.incident_taxonomy as incident_taxonomy


class IncidentModelTest(unittest.TestCase):
    def test_incident_decision_labels_are_incident_not_incident_or_unsure_only(self):
        self.assertEqual(incident_taxonomy.INCIDENT_DECISION_LABELS, {"incident", "not_incident", "unsure"})
        for label in ("incident", "not_incident", "unsure"):
            with self.subTest(label=label):
                self.assertEqual(incident_taxonomy.validate_incident_decision_label(label), label)
                decision = incident_model.IncidentDecision(label, False, None, None, 0.9, 0.1, "test")
                self.assertEqual(decision.label, label)


if __name__ == "__main__":
    unittest.main()
