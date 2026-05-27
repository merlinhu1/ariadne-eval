from __future__ import annotations

from dataclasses import replace

from agent_health.incident_model import IncidentDecision

INCIDENT_CONFIDENT = 0.92
NOT_INCIDENT_CONFIDENT = 0.80
MINIMUM_TOP_LABEL_MARGIN = 0.12


def _margin(decision: IncidentDecision) -> float:
    evidence = decision.evidence_summary or ""
    if "top_label_margin=" in evidence:
        try:
            return float(evidence.split("top_label_margin=", 1)[1].split()[0])
        except Exception:
            pass
    return 1.0 - decision.uncertainty


def route_incident_decision(decision: IncidentDecision, *, llm_budget_available: bool) -> IncidentDecision:
    margin = _margin(decision)
    if decision.label == "incident" and decision.confidence >= INCIDENT_CONFIDENT and margin >= MINIMUM_TOP_LABEL_MARGIN:
        return replace(decision, decision_source="ml_model", should_defer_to_llm=False, budget_fallback=False)
    if decision.label == "not_incident" and decision.confidence >= NOT_INCIDENT_CONFIDENT and margin >= MINIMUM_TOP_LABEL_MARGIN:
        return replace(decision, decision_source="ml_model", should_defer_to_llm=False, budget_fallback=False)
    if llm_budget_available:
        return replace(
            decision,
            label="unsure",
            is_incident=None,
            decision_source="ml_model_defer",
            should_defer_to_llm=True,
            budget_fallback=False,
        )
    fallback_label = decision.label if decision.label != "unsure" else "not_incident"
    return replace(
        decision,
        label=fallback_label,
        is_incident=True if fallback_label == "incident" else False if fallback_label == "not_incident" else None,
        decision_source="ml_model_budget_fallback",
        should_defer_to_llm=False,
        budget_fallback=True,
    )
