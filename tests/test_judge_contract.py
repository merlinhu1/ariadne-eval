import unittest
import json
import sys
import types
from pathlib import Path
from unittest import mock

from agent_health import incident_taxonomy, judge


ROOT = Path(__file__).resolve().parents[1]


class JudgeContractTest(unittest.TestCase):
    def test_request_and_incident_label_sets_are_canonical(self):
        self.assertEqual(judge.HEALTH_STATUSES, {"succeed", "failed", "mishandled", "prolonged"})
        self.assertEqual(judge.INCIDENT_LABELS, {"incident", "not_incident", "unsure"})
        self.assertEqual(incident_taxonomy.INCIDENT_DECISION_LABELS, {"incident", "not_incident", "unsure"})

        for label in ("incident", "not_incident", "unsure"):
            with self.subTest(label=label):
                self.assertEqual(incident_taxonomy.validate_incident_decision_label(label), label)

    def test_judge_routes_prefer_auxiliary_approval_before_main(self):
        routes = judge.build_judge_routes(
            {"provider": "custom", "model": "approval-model"},
            main_provider="openrouter",
            main_model="main-model",
        )

        self.assertEqual([route.name for route in routes], ["auxiliary.approval", "main"])
        self.assertEqual(routes[0].task, "approval")
        self.assertEqual(routes[0].provider, "custom")
        self.assertEqual(routes[0].model, "approval-model")
        self.assertEqual(routes[1].task, None)
        self.assertEqual(routes[1].provider, "openrouter")
        self.assertEqual(routes[1].model, "main-model")

    def test_judge_routes_auto_fallback_uses_approval_task(self):
        routes = judge.build_judge_routes({}, main_provider=None, main_model=None)

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].name, "auto")
        self.assertEqual(routes[0].task, "approval")
        self.assertEqual(routes[0].provider, "auto")
        self.assertIsNone(routes[0].model)

    def test_judge_client_resolves_auxiliary_approval_config(self):
        config_mod = types.ModuleType("hermes_cli.config")
        setattr(config_mod, "load_config", lambda: {
            "auxiliary": {"approval": {"provider": "custom", "model": "approval-model"}},
            "model": {"provider": "main-provider", "model": "main-model"},
        })
        hermes_mod = types.ModuleType("hermes_cli")
        agent_mod = types.ModuleType("agent")
        aux_mod = types.ModuleType("agent.auxiliary_client")
        setattr(aux_mod, "_read_main_provider", lambda: None)
        setattr(aux_mod, "_read_main_model", lambda: None)

        with mock.patch.dict(sys.modules, {
            "hermes_cli": hermes_mod,
            "hermes_cli.config": config_mod,
            "agent": agent_mod,
            "agent.auxiliary_client": aux_mod,
        }):
            routes = judge.HermesLLMJudgeClient(Path("/tmp/hermes-test")).resolve_routes()

        self.assertEqual([route.name for route in routes], ["auxiliary.approval", "main"])
        self.assertEqual(routes[0].task, "approval")
        self.assertEqual(routes[0].model, "approval-model")
        self.assertEqual(routes[1].provider, "main-provider")
        self.assertEqual(routes[1].model, "main-model")

    def test_request_friction_score_is_required_and_bounded(self):
        base = {
            "health_status": "succeed",
            "confidence": "high",
            "primary_reason": "completed",
            "request_friction_score": 0.0,
            "anomalies": [],
        }

        self.assertEqual(judge.validate_eval_json(base)["request_friction_score"], 0.0)
        for score in (-0.1, 1.1):
            with self.subTest(score=score):
                payload = dict(base, request_friction_score=score)
                with self.assertRaises(ValueError):
                    judge.validate_eval_json(payload)
        with self.assertRaises(ValueError):
            payload = dict(base)
            del payload["request_friction_score"]
            judge.validate_eval_json(payload)

    def test_dashboard_plugin_copy_is_request_first_and_tri_state(self):
        dist = ROOT / "src" / "agent_health" / "dashboard_plugin" / "dashboard" / "dist"
        required = [
            "Request friction",
            "Requests needing attention",
            "request_friction_score",
            "friction_band",
            "Anomaly timeline",
            "Incident",
            "Not incident",
            "Unsure",
        ]

        failures = []
        for path in sorted(dist.glob("index*.js")):
            text = path.read_text()
            for term in required:
                if term not in text:
                    failures.append(f"{path.name} missing {term}")

        self.assertEqual(failures, [])

    def test_dashboard_truth_doc_matches_manifest_assets(self):
        manifest_path = ROOT / "src" / "agent_health" / "dashboard_plugin" / "dashboard" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        doc = (ROOT / "docs" / "truth" / "dashboard" / "hermes-dashboard-plugin.md").read_text()

        self.assertIn(manifest["entry"], doc)
        self.assertIn(manifest["css"], doc)


if __name__ == "__main__":
    unittest.main()
