from __future__ import annotations

INCIDENT_DECISION_LABELS = {"incident", "not_incident", "unsure"}
INCIDENT_REASON_CODES = {"execution_error", "no_result", "bad_request", "bad_output", "other"}


def validate_incident_decision_label(label: object) -> str:
    value = str(label or "").strip()
    if value not in INCIDENT_DECISION_LABELS:
        raise ValueError(f"invalid incident decision label {value!r}")
    return value


def validate_reason_code(reason_code: object | None) -> str | None:
    if reason_code is None or reason_code == "":
        return None
    value = str(reason_code).strip()
    if value not in INCIDENT_REASON_CODES:
        raise ValueError(f"invalid incident reason_code {value!r}")
    return value
