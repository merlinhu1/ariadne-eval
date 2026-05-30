from __future__ import annotations

TOOL_OUTCOME_DECISION_LABELS = {"problem", "ok", "unsure"}
TOOL_OUTCOME_REASON_CODES = {"execution_error", "empty_output", "invalid_tool_input", "wrong_or_bad_output", "other"}


def validate_tool_outcome_decision_label(label: object) -> str:
    value = str(label or "").strip()
    if value not in TOOL_OUTCOME_DECISION_LABELS:
        raise ValueError(f"invalid tool_outcome decision label {value!r}")
    return value


def validate_reason_code(reason_code: object | None) -> str | None:
    if reason_code is None or reason_code == "":
        return None
    value = str(reason_code).strip()
    if value not in TOOL_OUTCOME_REASON_CODES:
        raise ValueError(f"invalid tool_outcome reason_code {value!r}")
    return value
