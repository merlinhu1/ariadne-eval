import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_health.db import EvalDB


def _turn_case(case_id="hermes:s1:turn:1"):
    return {
        "id": case_id,
        "framework": "hermes",
        "source_session_id": "s1",
        "turn_index": 1,
        "request_text": "Do it",
        "response_text": "Done",
        "next_request_text": "No, fix it",
        "tool_interaction_count": 1,
        "case_builder_version": "normalization_v1",
        "case_events": [],
    }


def _tool_outcome_case(case_id="tool_outcome:1"):
    return {
        "id": case_id,
        "framework": "hermes",
        "source_session_id": "s1",
        "turn_case_id": "hermes:s1:turn:1",
        "turn_index": 1,
        "tool_call_id": "tc1",
        "tool_name": "terminal",
        "tool_result": "exit 1",
        "result_timestamp": 2.0,
        "case_builder_version": "normalization_v1",
    }


def _case_review_data():
    return {
        "outcome_status": "succeed",
        "confidence": "high",
        "summary_reason": "ok",
        "friction_score": 0.0,
        "case_findings": [],
    }


class AutomaticLlmReviewGuardTest(unittest.TestCase):
    def _db(self, tmp):
        db = EvalDB(Path(tmp) / "evals.db")
        db.upsert_turn_case(_turn_case())
        db.upsert_tool_outcome_case(_tool_outcome_case())
        return db

    def test_child_tool_outcome_claim_blocks_parent_turn_case_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            self.assertIsNotNone(db.claim_automatic_llm_review("tool_outcome_case", "tool_outcome:1"))

            self.assertFalse(db.is_turn_case_llm_eligible("hermes:s1:turn:1"))
            self.assertIsNone(db.claim_automatic_llm_review("turn_case", "hermes:s1:turn:1"))

    def test_child_tool_outcome_review_blocks_parent_turn_case_claim_and_review_insert(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            db.insert_tool_outcome_review("tool_outcome:1", outcome_label="problem", reviewer_type="automatic_llm")

            self.assertFalse(db.is_turn_case_llm_eligible("hermes:s1:turn:1"))
            self.assertIsNone(db.claim_automatic_llm_review("turn_case", "hermes:s1:turn:1"))
            with self.assertRaises(RuntimeError):
                db.insert_case_review(
                    "hermes:s1:turn:1",
                    prompt_version="turn_case_review_v1",
                    judge_provider="test",
                    judge_model="test",
                    eval_data=_case_review_data(),
                )

    def test_child_tool_outcome_claim_blocks_parent_claim_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            parent = db.claim_automatic_llm_review("turn_case", "hermes:s1:turn:1")
            self.assertIsNotNone(parent)
            with closing(db.connect()) as con:
                con.execute(
                    """
                    INSERT INTO automatic_llm_review_claims
                        (id, target_type, target_id, parent_turn_case_id, status, claimed_at)
                    VALUES (?, 'tool_outcome_case', 'tool_outcome:1', 'hermes:s1:turn:1', 'claimed', 1.0)
                    """,
                    ("child-claim",),
                )
                con.commit()

            self.assertFalse(db.mark_automatic_llm_claim_started(parent["id"]))


if __name__ == "__main__":
    unittest.main()
