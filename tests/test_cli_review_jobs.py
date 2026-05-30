import unittest

from agent_health.cli import _schedule_update_from_args, build_parser


class ReviewJobsCliTest(unittest.TestCase):
    def test_max_judge_total_tokens_updates_review_total_token_budget(self):
        parser = build_parser()
        args = parser.parse_args(["review-jobs", "set", "nightly", "--max-judge-total-tokens", "123"])

        self.assertEqual(_schedule_update_from_args(args)["max_review_total_tokens"], 123)


if __name__ == "__main__":
    unittest.main()
