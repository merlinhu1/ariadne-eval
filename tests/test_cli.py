import unittest

from agent_health.cli import build_parser, judge_call_budget, select_priority_units


class CliBudgetTest(unittest.TestCase):
    def test_eval_defaults_are_budget_safe(self):
        parser = build_parser()
        args = parser.parse_args(["eval", "--due"])

        self.assertEqual(args.limit, 10)
        self.assertEqual(args.max_judge_calls, 5)
        self.assertEqual(args.cooldown_minutes, 120)
        self.assertEqual(args.min_priority_score, 1)
        self.assertEqual(args.judgement_threshold, "strict")

    def test_incidents_command_exists_for_event_level_failures(self):
        parser = build_parser()
        args = parser.parse_args(["incidents", "--since", "5h", "--limit", "10"])

        self.assertEqual(args.since, "5h")
        self.assertEqual(args.limit, 10)
        self.assertTrue(callable(args.func))

    def test_bumps_command_is_removed(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["bumps", "--since", "5h", "--limit", "10"])

    def test_dashboard_install_command_exists(self):
        parser = build_parser()
        args = parser.parse_args(["dashboard", "install"])

        self.assertEqual(args.dashboard_command, "install")
        self.assertTrue(callable(args.func))

    def test_judge_call_budget_caps_large_requested_limits(self):
        self.assertEqual(judge_call_budget(limit=100, max_judge_calls=10), 10)
        self.assertEqual(judge_call_budget(limit=3, max_judge_calls=10), 3)
        self.assertEqual(judge_call_budget(limit=10, max_judge_calls=0), 0)

    def test_priority_selection_skips_low_priority_by_default(self):
        candidates = [
            ({"id": "boring"}, [{"signal_name": "next_user_reaction_type", "signal_value": "continuation", "severity": None}]),
            ({"id": "bad"}, [{"signal_name": "tool_error_count", "signal_value": "2", "severity": "medium"}]),
            ({"id": "corrected"}, [{"signal_name": "next_user_reaction_type", "signal_value": "correction", "severity": "medium"}]),
        ]
        selected = select_priority_units(candidates, budget=2, min_priority_score=1)
        self.assertEqual([row["id"] for row, _, _ in selected], ["corrected", "bad"])

    def test_priority_selection_can_include_low_priority_when_requested(self):
        candidates = [
            ({"id": "boring"}, [{"signal_name": "next_user_reaction_type", "signal_value": "continuation", "severity": None}]),
        ]
        selected = select_priority_units(candidates, budget=1, min_priority_score=0)
        self.assertEqual([row["id"] for row, _, _ in selected], ["boring"])


if __name__ == "__main__":
    unittest.main()
