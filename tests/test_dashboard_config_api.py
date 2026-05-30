import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


from agent_health.db import EvalDB, default_eval_db_path
from agent_health.dashboard_plugin.dashboard import plugin_api


class DashboardConfigApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.db = EvalDB(default_eval_db_path(self.home))

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_tool_outcome_reviewer_models_with_promoted_model_first_and_decoded_metrics(self):
        first_id = self.db.record_tool_outcome_reviewer_model({
            "model_name": "tfidf_tool_outcome",
            "model_version": "1",
            "artifact_path": "/models/1/model.joblib",
            "training_record_count": 4,
            "accepted_label_count": 4,
            "metrics_json": {"accuracy": 0.75},
        })
        second_id = self.db.record_tool_outcome_reviewer_model({
            "model_name": "tfidf_tool_outcome",
            "model_version": "2",
            "artifact_path": "/models/2/model.joblib",
            "training_record_count": 8,
            "accepted_label_count": 8,
            "metrics_json": {"accuracy": 0.875},
        })
        self.db.promote_tool_outcome_reviewer_model(first_id)

        rows = self.db.list_tool_outcome_reviewer_models()

        self.assertEqual([row["id"] for row in rows], [first_id, second_id])
        self.assertTrue(rows[0]["promoted"])
        self.assertEqual(rows[0]["metrics_json"], {"accuracy": 0.75})

    def test_config_options_are_read_only_and_include_tasks_models_and_judge_route_info(self):
        self.db.upsert_review_job("nightly", {"enabled": True, "interval_seconds": 600})
        model_id = self.db.record_tool_outcome_reviewer_model({
            "model_name": "tfidf_tool_outcome",
            "model_version": "1",
            "artifact_path": "/models/1/model.joblib",
            "training_record_count": 4,
            "accepted_label_count": 4,
            "metrics_json": {},
        })
        self.db.promote_tool_outcome_reviewer_model(model_id)

        with patch.object(plugin_api, "train_tfidf_tool_outcome_reviewer_model") as train:
            payload = plugin_api.get_config_options(hermes_home=str(self.home))

        train.assert_not_called()
        self.assertEqual(payload["tasks"][0]["name"], "nightly")
        self.assertEqual(payload["promoted_tool_outcome_reviewer_model"]["id"], model_id)
        self.assertEqual(payload["tool_outcome_reviewer_models"][0]["id"], model_id)
        self.assertEqual(payload["review_job_options"]["schedule_kinds"], ["interval", "continuous"])
        self.assertIn("Hermes", payload["llm_judging"]["route_priority"])

    def test_promotes_existing_tool_outcome_reviewer_model(self):
        model_id = self.db.record_tool_outcome_reviewer_model({
            "model_name": "tfidf_tool_outcome",
            "model_version": "1",
            "artifact_path": "/models/1/model.joblib",
            "training_record_count": 4,
            "accepted_label_count": 4,
            "metrics_json": {},
        })

        payload = plugin_api.post_tool_outcome_reviewer_model_promote(model_id, hermes_home=str(self.home))

        self.assertEqual(payload["promoted_tool_outcome_reviewer_model"]["id"], model_id)
        self.assertTrue(self.db.get_promoted_tool_outcome_reviewer_model()["promoted"])

    def test_promoting_missing_tool_outcome_reviewer_model_returns_404(self):
        with self.assertRaises(plugin_api.HTTPException) as raised:
            plugin_api.post_tool_outcome_reviewer_model_promote("missing", hermes_home=str(self.home))

        self.assertEqual(raised.exception.status_code, 404)

    def test_retrain_records_and_returns_artifact_without_shelling_to_cli(self):
        artifact = SimpleNamespace(
            model_name="tfidf_tool_outcome",
            model_version="v-test",
            artifact_path=str(self.home / "models" / "v-test" / "model.joblib"),
            training_record_count=3,
            accepted_label_count=3,
            metrics={"label_count": 3},
        )
        model = SimpleNamespace(save=lambda output_dir: artifact)
        self.db.export_tool_outcome_review_training = lambda limit=10000: [
            {"label": "problem", "tool_name": "bash", "tool_result": "failed"},
            {"label": "ok", "tool_name": "bash", "tool_result": "ok"},
            {"label": "problem", "tool_name": "python", "tool_result": "traceback"},
        ]

        with patch.object(plugin_api, "_db", return_value=self.db), \
             patch.object(plugin_api, "train_tfidf_tool_outcome_reviewer_model", return_value=model) as train, \
             patch.object(plugin_api, "smoke_check_tool_outcome_reviewer_model", return_value=True) as smoke:
            payload = plugin_api.post_tool_outcome_reviewer_model_retrain({"model_version": "v-test", "auto_promote": True}, hermes_home=str(self.home))

        train.assert_called_once()
        smoke.assert_called_once_with(artifact.artifact_path)
        self.assertEqual(payload["model"]["model_version"], "v-test")
        self.assertEqual(payload["artifact"]["artifact_path"], artifact.artifact_path)
        self.assertTrue(payload["promoted"])
        self.assertEqual(self.db.get_promoted_tool_outcome_reviewer_model()["id"], payload["model"]["id"])

    def test_retrain_without_auto_promote_returns_new_unpromoted_model(self):
        existing_id = self.db.record_tool_outcome_reviewer_model({
            "model_name": "tfidf_tool_outcome",
            "model_version": "old",
            "artifact_path": "/models/old/model.joblib",
            "training_record_count": 2,
            "accepted_label_count": 2,
            "metrics_json": {},
        })
        self.db.promote_tool_outcome_reviewer_model(existing_id)
        artifact = SimpleNamespace(
            model_name="tfidf_tool_outcome",
            model_version="new",
            artifact_path=str(self.home / "models" / "new" / "model.joblib"),
            training_record_count=4,
            accepted_label_count=4,
            metrics={},
        )
        model = SimpleNamespace(save=lambda output_dir: artifact)
        self.db.export_tool_outcome_review_training = lambda limit=10000: [
            {"label": "problem", "tool_name": "bash", "tool_result": "failed"},
            {"label": "ok", "tool_name": "bash", "tool_result": "ok"},
            {"label": "problem", "tool_name": "python", "tool_result": "traceback"},
            {"label": "ok", "tool_name": "python", "tool_result": "ok"},
        ]

        with patch.object(plugin_api, "_db", return_value=self.db), \
             patch.object(plugin_api, "train_tfidf_tool_outcome_reviewer_model", return_value=model), \
             patch.object(plugin_api, "smoke_check_tool_outcome_reviewer_model", return_value=True):
            payload = plugin_api.post_tool_outcome_reviewer_model_retrain({"model_version": "new"}, hermes_home=str(self.home))

        self.assertFalse(payload["promoted"])
        self.assertEqual(payload["model"]["model_version"], "new")
        self.assertFalse(payload["model"]["promoted"])
        self.assertEqual(self.db.get_promoted_tool_outcome_reviewer_model()["id"], existing_id)


if __name__ == "__main__":
    unittest.main()
