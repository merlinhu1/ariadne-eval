from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from agent_health.signals import DEFAULT_THRESHOLDS, _event_error


def _one_line(value: object, limit: int = 220) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[: limit - 1] + "…" if len(text) > limit else text


def _incident(
    unit: dict[str, Any],
    incident_type: str,
    *,
    severity: str = "medium",
    source: str = "trace",
    evidence: str,
    related_event_id: str | None = None,
    tool_name: str | None = None,
) -> dict[str, Any]:
    return {
        "eval_unit_id": unit.get("id"),
        "source_session_id": unit.get("source_session_id"),
        "source_turn_index": unit.get("source_turn_index"),
        "started_at": unit.get("started_at"),
        "incident_type": incident_type,
        # Backward-compatible alias for existing consumers and historical rows.
        "bump_type": incident_type,
        "severity": severity,
        "source": source,
        "evidence": evidence,
        "related_event_id": related_event_id,
        "tool_name": tool_name,
        "user_request": unit.get("user_request"),
    }


def _completion_claimed(text: object) -> bool:
    lower = str(text or "").lower()
    return any(p in lower for p in ["created", "done", "completed", "sent", "uploaded", "fixed", "i have"])


def extract_incident_events(unit: dict[str, Any], thresholds: dict[str, int | float] | None = None) -> list[dict[str, Any]]:
    """Return deterministic event-level incidents for one eval unit.

    Incidents are not final LLM judgements. They are concrete trace anomalies.
    A single unit can contain many incidents; each tool-error event is
    intentionally represented as its own incident so failure counts do not
    collapse into one vague turn-level rating.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    events = list(unit.get("trace_events") or [])
    incidents: list[dict[str, Any]] = []

    error_event_ids: list[str] = []
    for index, event in enumerate(events, start=1):
        if not _event_error(event):
            continue
        event_id = str(event.get("id") or event.get("source_event_id") or f"event:{index}")
        error_event_ids.append(event_id)
        tool_name = str(event.get("tool_name") or "tool")
        preview = _one_line(event.get("result_preview"), 180)
        severity = "high" if bool(event.get("result_error")) else "medium"
        incidents.append(
            _incident(
                unit,
                "tool_error",
                severity=severity,
                source="trace_event",
                related_event_id=event_id,
                tool_name=tool_name,
                evidence=f"{tool_name} event {event_id} looked like an error: {preview}",
            )
        )

    grouped: defaultdict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if not event.get("tool_name"):
            continue
        key = (event.get("tool_name"), event.get("args_hash"), event.get("args_preview"))
        grouped[key].append(event)
    for (tool_name, _args_hash, args_preview), group in grouped.items():
        if len(group) >= int(th["repeated_same_tool_same_args"]):
            first_id = str(group[0].get("id") or group[0].get("source_event_id") or "") or None
            incidents.append(
                _incident(
                    unit,
                    "repeated_tool_loop",
                    severity="high" if len(group) >= int(th["repeated_same_tool_same_args"]) + 2 else "medium",
                    source="trace_event",
                    related_event_id=first_id,
                    tool_name=str(tool_name or "tool"),
                    evidence=f"{tool_name or 'tool'} was called {len(group)} times with the same arguments: {_one_line(args_preview, 140)}",
                )
            )

    tool_call_count = int(unit.get("tool_call_count") or len(events) or 0)
    if tool_call_count >= int(th["prolonged_tool_calls"]):
        incidents.append(
            _incident(
                unit,
                "excessive_tool_calls",
                severity="high" if tool_call_count >= int(th["prolonged_tool_calls"]) * 2 else "medium",
                source="deterministic_signal",
                evidence=f"{tool_call_count} tool events in this eval unit",
            )
        )

    api_call_count = int(unit.get("api_call_count") or 0)
    if api_call_count >= int(th["prolonged_api_calls"]):
        incidents.append(
            _incident(
                unit,
                "excessive_api_calls",
                severity="medium",
                source="session_metadata",
                evidence=f"{api_call_count} API calls recorded on the source session; this is currently session-level metadata",
            )
        )

    if unit.get("started_at") is not None and unit.get("ended_at") is not None:
        duration = max(0.0, float(unit.get("ended_at") or 0) - float(unit.get("started_at") or 0))
        if duration >= float(th["prolonged_turn_minutes"]) * 60:
            incidents.append(
                _incident(
                    unit,
                    "excessive_duration",
                    severity="high" if duration >= float(th["prolonged_turn_minutes"]) * 120 else "medium",
                    source="timestamp",
                    evidence=f"Turn timestamp span is {int(duration)} seconds",
                )
            )

    if not str(unit.get("assistant_response") or "").strip():
        incidents.append(
            _incident(
                unit,
                "interrupted_or_incomplete",
                severity="medium",
                source="message_boundary",
                evidence="No assistant response was captured for this eval unit",
            )
        )

    if error_event_ids and _completion_claimed(unit.get("assistant_response")):
        incidents.append(
            _incident(
                unit,
                "completion_claim_after_tool_error",
                severity="medium",
                source="trace_and_assistant_response",
                related_event_id=error_event_ids[0],
                evidence="Assistant response appears to claim completion even though at least one tool event looked like an error",
            )
        )

    return incidents


def summarize_incident_events(incidents: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(i.get("incident_type") or i.get("bump_type") or "unknown") for i in incidents)
    severity_counts = Counter(str(i.get("severity") or "medium") for i in incidents)
    return {
        "total_incidents": len(incidents),
        "total_bumps": len(incidents),  # Backward-compatible alias.
        "by_type": dict(counts.most_common()),
        "by_severity": dict(severity_counts.most_common()),
    }


# Backward-compatible API names.
extract_bump_events = extract_incident_events
summarize_bump_events = summarize_incident_events
