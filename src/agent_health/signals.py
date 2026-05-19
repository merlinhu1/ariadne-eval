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
    if bool(event.get("result_error")):
        return True
    preview = str(event.get("result_preview") or "")
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
        "signal_name": name,
        "signal_value": str(value),
        "severity": severity,
        "evidence": evidence,
    }


def extract_deterministic_signals(unit: dict[str, Any], thresholds: dict[str, int | float] | None = None) -> list[dict[str, str | None]]:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    events = unit.get("trace_events") or []
    signals: list[dict[str, str | None]] = []

    tool_call_count = int(unit.get("tool_call_count") or len(events) or 0)
    api_call_count = int(unit.get("api_call_count") or 0)
    signals.append(_signal("tool_call_count", tool_call_count, _severity(tool_call_count, th["prolonged_tool_calls"]), f"{tool_call_count} tool events in eval unit"))
    signals.append(_signal("api_call_count", api_call_count, _severity(api_call_count, th["prolonged_api_calls"]), f"{api_call_count} API calls recorded on source session"))

    started = unit.get("started_at")
    ended = unit.get("ended_at")
    if started is not None and ended is not None:
        duration = max(0.0, float(ended) - float(started))
        signals.append(_signal("turn_duration_seconds", int(duration), _severity(duration, float(th["prolonged_turn_minutes"]) * 60), f"turn timestamp span is {int(duration)} seconds"))

    error_count = sum(1 for e in events if _event_error(e))
    signals.append(_signal("tool_error_count", error_count, "medium" if error_count else None, f"{error_count} tool events looked like errors"))

    repeated_counts = Counter((e.get("tool_name"), e.get("args_hash"), e.get("args_preview")) for e in events if e.get("tool_name"))
    same_tool_repeat_count = max(repeated_counts.values(), default=0)
    signals.append(_signal(
        "same_tool_repeat_count",
        same_tool_repeat_count,
        _severity(same_tool_repeat_count, th["repeated_same_tool_same_args"]),
        "maximum repeats of the same tool with the same args hash/preview",
    ))

    reaction = classify_reaction(unit.get("next_user_reaction_text"), unit.get("user_request"))
    signals.append(_signal("next_user_reaction_type", reaction, "medium" if reaction in {"correction", "complaint", "repeated_request"} else None, unit.get("next_user_reaction_text")))

    assistant_response = str(unit.get("assistant_response") or "").lower()
    claimed = any(p in assistant_response for p in ["created", "done", "completed", "sent", "uploaded", "fixed", "i have"])
    signals.append(_signal("assistant_claimed_completion", str(claimed).lower(), None, None))

    return signals
