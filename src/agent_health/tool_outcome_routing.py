from __future__ import annotations

from dataclasses import replace

from agent_health.tool_outcome_reviewer_model import ToolOutcomeDecision

PROBLEM_CONFIDENT = 0.92
OK_CONFIDENT = 0.80
MINIMUM_TOP_LABEL_MARGIN = 0.12


def _margin(decision: ToolOutcomeDecision) -> float:
    evidence = decision.evidence_summary or ""
    if "top_label_margin=" in evidence:
        try:
            return float(evidence.split("top_label_margin=", 1)[1].split()[0])
        except Exception:
            pass
    return 1.0 - decision.uncertainty


def route_tool_outcome_decision(decision: ToolOutcomeDecision, *, llm_review_budget_available: bool) -> ToolOutcomeDecision:
    margin = _margin(decision)
    if decision.label == "problem" and decision.confidence >= PROBLEM_CONFIDENT and margin >= MINIMUM_TOP_LABEL_MARGIN:
        return replace(decision, decision_source="ml_model", should_defer_to_llm=False, budget_fallback=False)
    if decision.label == "ok" and decision.confidence >= OK_CONFIDENT and margin >= MINIMUM_TOP_LABEL_MARGIN:
        return replace(decision, decision_source="ml_model", should_defer_to_llm=False, budget_fallback=False)
    if llm_review_budget_available:
        return replace(
            decision,
            label="unsure",
            is_tool_outcome=None,
            decision_source="ml_model_defer",
            should_defer_to_llm=True,
            budget_fallback=False,
        )
    fallback_label = decision.label if decision.label != "unsure" else "ok"
    return replace(
        decision,
        label=fallback_label,
        is_tool_outcome=True if fallback_label == "problem" else False if fallback_label == "ok" else None,
        decision_source="ml_model_budget_fallback",
        should_defer_to_llm=False,
        budget_fallback=True,
    )
