import tempfile
import unittest
from pathlib import Path

from agent_health.db import EvalDB
from agent_health.judge import HermesLLMJudgeClient, JudgeRoute, TokenUsage, build_eval_payload, build_judge_routes, judgement_threshold_policy, validate_eval_json


class JudgePreflightTrimTest(unittest.TestCase):
    def test_payload_trims_large_documents_code_and_image_noise(self):
        long_doc = "[The user sent a text document: 'huge.md']\n[Content of huge.md]:\n" + ("important details\n" * 800)
        long_code = "```python\n" + ("print('noise')\n" * 500) + "```"
        data_image = "![screenshot](data:image/png;base64," + ("A" * 5000) + ")"
        unit = {
            "id": "u1",
            "framework": "hermes",
            "user_request": f"Please evaluate this.\n{long_doc}\n{long_code}\n{data_image}",
            "assistant_response": "Done. " + ("verbose output " * 600),
            "previous_context_summary": "context " * 1000,
            "next_user_reaction_text": "No, you missed the point",
            "trace_events": [
                {
                    "tool_name": "browser_vision",
                    "args_preview": data_image,
                    "result_preview": long_doc + long_code,
                    "result_error": False,
                    "duration_ms": 10,
                }
            ],
        }

        payload = build_eval_payload(unit, [])
        encoded = str(payload)

        self.assertLess(len(payload["user_request"]), 3500)
        self.assertLess(len(payload["assistant_response"]), 4500)
        self.assertLess(len(payload["previous_context_summary"]), 2800)
        self.assertLess(len(payload["trace_summary"]["tool_sequence"][0]["result_summary"]), 1800)
        self.assertIn("[trimmed", encoded)
        self.assertIn("[image omitted", encoded)
        self.assertNotIn("data:image/png;base64", encoded)


class JudgeThresholdPolicyTest(unittest.TestCase):
    def test_payload_includes_configurable_threshold_policy(self):
        unit = {"id": "u", "framework": "hermes", "user_request": "Do it", "assistant_response": "Done", "trace_events": []}

        payload = build_eval_payload(unit, [], judgement_threshold="strict")

        self.assertEqual(payload["judgement_threshold"]["level"], "strict")
        self.assertIn("Do not treat natural follow-up", payload["judgement_threshold"]["policy"])
        self.assertIn("trace", payload["judgement_threshold"]["policy"].lower())

    def test_unknown_threshold_falls_back_to_balanced(self):
        policy = judgement_threshold_policy("unknown")

        self.assertEqual(policy["level"], "balanced")

    def test_eval_json_accepts_anomalies_as_judge_output_name(self):
        result = validate_eval_json({
            "schema_version": "instruction_health_eval_v1",
            "health_status": "mishandled",
            "confidence": "high",
            "primary_reason": "The agent over-claimed completion.",
            "user_reaction": {"type": "correction", "used_as_evidence": True, "evidence": "No"},
            "anomalies": [
                {"type": "unsupported_claim", "severity": "high", "source": "assistant_response", "evidence": "Claimed done without evidence"}
            ],
        })

        self.assertEqual(result["anomalies"][0]["type"], "unsupported_claim")
        self.assertEqual(result["barriers"][0]["type"], "unsupported_claim")


class JudgeRouteTest(unittest.TestCase):
    def test_routes_prefer_auxiliary_compression_then_main_model(self):
        routes = build_judge_routes(
            {"provider": "openrouter", "model": "google/gemini-flash"},
            main_provider="openai-codex",
            main_model="gpt-5.5-codex",
        )

        self.assertEqual(routes[0], JudgeRoute(name="auxiliary.compression", task="compression", provider="openrouter", model="google/gemini-flash"))
        self.assertEqual(routes[1], JudgeRoute(name="main", task=None, provider="openai-codex", model="gpt-5.5-codex"))

    def test_routes_use_main_when_compression_has_no_override(self):
        routes = build_judge_routes({}, main_provider="openrouter", main_model="anthropic/claude-sonnet")

        self.assertEqual(routes, [JudgeRoute(name="main", task=None, provider="openrouter", model="anthropic/claude-sonnet")])


class JudgePersistenceTest(unittest.TestCase):
    def test_persists_llm_eval_and_anomalies(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = EvalDB(Path(tmp) / "evals.db")
            db.upsert_eval_unit({
                "id": "hermes:s1:turn:1",
                "framework": "hermes",
                "source_session_id": "s1",
                "source_turn_index": 1,
                "user_message_id": "u1",
                "assistant_message_id": "a1",
                "next_user_message_id": "u2",
                "started_at": 1.0,
                "ended_at": 2.0,
                "source": "discord",
                "model": "main-model",
                "title": "Demo",
                "parent_session_id": None,
                "user_request": "Create a file",
                "assistant_response": "Done",
                "previous_context_summary": "",
                "next_user_reaction_text": "No, you didn't create it",
                "tool_call_count": 0,
                "api_call_count": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "normalization_version": "normalization_v1",
                "trace_events": [],
                "created_at": 1.0,
                "updated_at": 1.0,
            })

            eval_id = db.insert_llm_eval(
                "hermes:s1:turn:1",
                prompt_version="instruction_health_v1",
                judge_provider="auxiliary.compression",
                judge_model="judge-model",
                eval_data={
                    "schema_version": "instruction_health_eval_v1",
                    "health_status": "mishandled",
                    "confidence": "high",
                    "primary_reason": "The user says the requested file was not created.",
                    "anomalies": [
                        {"type": "action_misrepresentation", "severity": "high", "source": "user_reaction", "evidence": "No, you didn't create it"}
                    ],
                },
            )

            latest = db.get_latest_llm_eval("hermes:s1:turn:1")
            self.assertEqual(latest["id"], eval_id)
            self.assertEqual(latest["health_status"], "mishandled")
            self.assertEqual(latest["anomalies"][0]["anomaly_type"], "action_misrepresentation")
            self.assertEqual(latest["barriers"][0]["barrier_type"], "action_misrepresentation")
            self.assertEqual(db.list_due_units(limit=10), [])

    def test_client_records_token_usage_from_successful_and_repair_calls(self):
        class Usage:
            prompt_tokens = 10
            completion_tokens = 3
            total_tokens = 13

        class Message:
            content = "not json"

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]
            usage = Usage()

        calls = []

        def fake_call(route, messages, temperature=None, max_tokens=None):
            calls.append(messages)
            if len(calls) == 1:
                return Response()
            return '{"schema_version":"instruction_health_eval_v1","health_status":"succeed","confidence":"medium","goal_summary":"Answer","observed_outcome":"Answered","primary_reason":"The response addressed the request.","user_reaction":{"type":"none","used_as_evidence":false,"evidence":""},"barriers":[],"prolongation_evidence":{"tool_calls":0,"api_calls":0,"duration_seconds":1,"repeated_actions":[]},"missed_or_mishandled_requirements":[],"not_evaluable_reason":null}'

        client = HermesLLMJudgeClient(
            hermes_home=Path("/tmp/nonexistent"),
            routes=[JudgeRoute(name="main", task=None, provider="openai-codex", model="main")],
            call_func=fake_call,
        )
        result = client.evaluate_unit({"id": "u", "user_request": "Hi", "assistant_response": "Hello", "trace_events": []}, [])

        self.assertEqual(len(calls), 2)
        self.assertEqual(result.token_usage, TokenUsage(prompt_tokens=10, completion_tokens=3, total_tokens=13, calls=2))

    def test_client_falls_back_from_compression_to_main(self):
        calls = []

        def fake_call(route, messages, temperature=None, max_tokens=None):
            calls.append(route.name)
            if route.name == "auxiliary.compression":
                raise RuntimeError("compression unavailable")
            return '{"schema_version":"instruction_health_eval_v1","health_status":"succeed","confidence":"medium","goal_summary":"Answer","observed_outcome":"Answered","primary_reason":"The response addressed the request.","user_reaction":{"type":"none","used_as_evidence":false,"evidence":""},"barriers":[],"prolongation_evidence":{"tool_calls":0,"api_calls":0,"duration_seconds":1,"repeated_actions":[]},"missed_or_mishandled_requirements":[],"not_evaluable_reason":null}'

        client = HermesLLMJudgeClient(
            hermes_home=Path("/tmp/nonexistent"),
            routes=[
                JudgeRoute(name="auxiliary.compression", task="compression", provider="openrouter", model="judge"),
                JudgeRoute(name="main", task=None, provider="openai-codex", model="main"),
            ],
            call_func=fake_call,
        )
        result = client.evaluate_unit({"id": "u", "user_request": "Hi", "assistant_response": "Hello", "trace_events": []}, [])

        self.assertEqual(calls, ["auxiliary.compression", "main"])
        self.assertEqual(result.eval_data["health_status"], "succeed")
        self.assertEqual(result.judge_provider, "main")
        self.assertEqual(result.judge_model, "main")


if __name__ == "__main__":
    unittest.main()
