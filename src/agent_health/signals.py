from __future__ import annotations

from collections import Counter
import json
import re
from typing import Any

from agent_health.reactions import classify_reaction

DEFAULT_THRESHOLDS = {
    "prolonged_tool_calls": 8,
    "prolonged_api_calls": 4,
    "prolonged_turn_minutes": 10,
    "repeated_same_tool_same_args": 3,
    "long_tool_result_chars": 8000,
}


def _severity(count: int | float, threshold: int | float) -> str | None:
    if count >= threshold:
        return "high"
    if count >= threshold * 0.75:
        return "medium"
    return None


def _event_error(event: dict[str, Any]) -> bool:
    if bool(event.get("output_error")):
        return True
    preview = str(event.get("output_preview") or "")
    lower = preview.lower()
    try:
        parsed = json.loads(preview)
    except Exception:
        parsed = None
    stripped = lower.lstrip()
    if parsed is None and (
        stripped.startswith('{"content"')
        or stripped.startswith("{'content'")
        or stripped.startswith('{"matches"')
        or stripped.startswith('{"total_count"')
    ):
        return False
    if isinstance(parsed, dict):
        exit_code = parsed.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return True
        if isinstance(exit_code, str) and exit_code.strip() not in {"", "0"}:
            return True
        error = parsed.get("error")
        if error not in (None, "", False):
            return True
        output = str(parsed.get("output") or "").lower()
        if "traceback" in output or re.search(r"\b(exception|command failed|failed:)\b", output):
            return True
        return False
    if re.search(r"exit[_ -]?code[\"']?\s*[:=]\s*[1-9]", lower):
        return True
    if "traceback" in lower:
        return True
    if re.search(r"\b(exception|command failed|failed:)\b", lower):
        return True
    return False


def _signal(name: str, value: Any, severity: str | None = None, evidence: str | None = None) -> dict[str, str | None]:
    return {
        "signal_type": name,
        "signal_value": str(value),
        "score": None,
        "severity": severity,
        "evidence_text": evidence,
    }


def extract_case_signals(
    unit: dict[str, Any],
    thresholds: dict[str, int | float] | None = None,
) -> list[dict[str, str | None]]:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    events = unit.get("case_events") or []
    signals: list[dict[str, str | None]] = []

    tool_interaction_count = int(unit.get("tool_interaction_count") or len(events) or 0)
    source_session_api_interaction_count = int(unit.get("source_session_api_interaction_count") or 0)
    signals.append(_signal("tool_interaction_count", tool_interaction_count, _severity(tool_interaction_count, th["prolonged_tool_calls"]), f"{tool_interaction_count} tool events in turn case"))
    signals.append(_signal("source_session_api_interaction_count", source_session_api_interaction_count, _severity(source_session_api_interaction_count, th["prolonged_api_calls"]), f"{source_session_api_interaction_count} API calls recorded on source session"))

    started = unit.get("started_at")
    ended = unit.get("ended_at")
    if started is not None and ended is not None:
        duration = max(0.0, float(ended) - float(started))
        signals.append(_signal("turn_duration_seconds", int(duration), _severity(duration, float(th["prolonged_turn_minutes"]) * 60), f"turn timestamp span is {int(duration)} seconds"))

    error_count = sum(1 for e in events if _event_error(e))
    signals.append(_signal("tool_error_count", error_count, "medium" if error_count else None, f"{error_count} tool events looked like errors"))

    repeated_counts = Counter((e.get("tool_name"), e.get("input_hash"), e.get("input_preview")) for e in events if e.get("tool_name"))
    same_tool_repeat_count = max(repeated_counts.values(), default=0)
    signals.append(_signal(
        "same_tool_repeat_count",
        same_tool_repeat_count,
        _severity(same_tool_repeat_count, th["repeated_same_tool_same_args"]),
        "maximum repeats of the same tool with the same input hash/preview",
    ))

    reaction = classify_reaction(unit.get("next_request_text"), unit.get("request_text"))
    signals.append(_signal("reaction", reaction, "medium" if reaction in {"correction", "complaint", "repeated_request"} else None, unit.get("next_request_text")))

    response_text = str(unit.get("response_text") or "").lower()
    claimed = any(p in response_text for p in ["created", "done", "completed", "sent", "uploaded", "fixed", "i have"])
    signals.append(_signal("assistant_claimed_completion", str(claimed).lower(), None, None))

    return signals
