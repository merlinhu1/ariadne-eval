from __future__ import annotations

from collections import Counter
from typing import Any

from agent_health.db import EvalDB
from agent_health.signals import extract_case_signals

FRICTION_ANCHORS = [
    {"score": 0.0, "label": "clean", "description": "No visible avoidable friction; the request was completed cleanly."},
    {"score": 0.25, "label": "minor", "description": "Minor retry, clarification, or detour while still completing the goal."},
    {"score": 0.5, "label": "moderate", "description": "Avoidable tool errors, detours, or corrections affected completion."},
    {"score": 0.75, "label": "severe", "description": "Significant mishandling, repeated retries, or prolonged work before partial/late completion."},
    {"score": 1.0, "label": "breakdown", "description": "The request failed, was substantially misrepresented, or required major user correction."},
]


def _bucket_start(timestamp: float | None, bucket_seconds: int) -> float | None:
    if timestamp is None:
        return None
    bucket = max(1, int(bucket_seconds or 1))
    return float(int(float(timestamp) // bucket) * bucket)


def _preview(value: object, limit: int = 280) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[: limit - 1] + "…" if len(text) > limit else text


def _friction(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    try:
        value = float(row.get("friction_score") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(1.0, value))


def _friction_band(value: float) -> str:
    if value >= 0.9:
        return "breakdown"
    if value >= 0.75:
        return "severe"
    if value >= 0.5:
        return "moderate"
    if value >= 0.25:
        return "minor"
    return "clean"


def _new_session_entry(session_id: str, *, title: str | None = None, started_at: float | None = None) -> dict[str, Any]:
    return {
        "source_session_id": session_id,
        "title": title,
        "turn_cases": 0,
        "evaluated_turns": 0,
        "finding_count": 0,
        "tool_outcome_case_count": 0,
        "statuses": {},
        "finding_types": {},
        "severities": {},
        "last_started_at": started_at,
        "latest_turn_case_id": None,
        "max_friction_score": 0.0,
        "avg_friction_score": 0.0,
        "findings": [],
        "tool_outcome_cases": [],
        "requests": [],
        "_requests_by_unit_id": {},
        "_friction_total": 0.0,
    }


def _session_entry(sessions: dict[str, dict[str, Any]], session_id: str, *, title: str | None = None, started_at: float | None = None) -> dict[str, Any]:
    entry = sessions.setdefault(session_id, _new_session_entry(session_id, title=title, started_at=started_at))
    if title and not entry.get("title"):
        entry["title"] = title
    if started_at is not None:
        current = entry.get("last_started_at")
        if current is None or float(started_at) >= float(current):
            entry["last_started_at"] = started_at
    return entry


def _bump_counter(mapping: dict[str, int], key: object, amount: int = 1) -> None:
    text = str(key or "unknown")
    mapping[text] = int(mapping.get(text) or 0) + amount


def _request_entry(entry: dict[str, Any], turn_case_id: object, *, unit: dict[str, Any] | None = None, row: dict[str, Any] | None = None) -> dict[str, Any]:
    unit = unit or {}
    row = row or {}
    unit_id = str(turn_case_id or row.get("turn_case_id") or unit.get("id") or "")
    by_id: dict[str, dict[str, Any]] = entry.setdefault("_requests_by_unit_id", {})
    request = by_id.get(unit_id)
    if request is None:
        request = {
            "turn_case_id": unit_id,
            "source_session_id": row.get("source_session_id") or unit.get("source_session_id") or entry.get("source_session_id"),
            "turn_index": row.get("turn_index") or unit.get("turn_index"),
            "started_at": row.get("started_at") if row.get("started_at") is not None else unit.get("started_at"),
            "request_text": _preview(row.get("request_text") or unit.get("request_text")),
            "outcome_status": row.get("outcome_status"),
            "confidence": row.get("confidence"),
            "summary_reason": row.get("summary_reason"),
            "friction_score": _friction(row),
            "friction_band": _friction_band(_friction(row)),
            "finding_count": 0,
            "tool_outcome_case_count": 0,
            "findings": [],
            "tool_outcome_cases": [],
        }
        by_id[unit_id] = request
        entry.setdefault("requests", []).append(request)
    else:
        if row.get("outcome_status"):
            request["outcome_status"] = row.get("outcome_status")
            request["confidence"] = row.get("confidence")
            request["summary_reason"] = row.get("summary_reason")
            request["friction_score"] = _friction(row)
            request["friction_band"] = _friction_band(_friction(row))
        if not request.get("request_text"):
            request["request_text"] = _preview(row.get("request_text") or unit.get("request_text"))
        if request.get("started_at") is None:
            request["started_at"] = row.get("started_at") if row.get("started_at") is not None else unit.get("started_at")
        if request.get("turn_index") is None:
            request["turn_index"] = row.get("turn_index") or unit.get("turn_index")
    return request


def _sort_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for request in requests:
        request["finding_count"] = len(request.get("findings") or [])
        request["tool_outcome_case_count"] = len(request.get("tool_outcome_cases") or [])
        request["findings"] = sorted(
            request.get("findings") or [],
            key=lambda row: (-(row.get("started_at") or 0), row.get("finding_type") or ""),
        )[:20]
        request["tool_outcome_cases"] = sorted(
            request.get("tool_outcome_cases") or [],
            key=lambda row: (-(row.get("result_timestamp") or 0), row.get("id") or ""),
        )[:20]
    return sorted(
        requests,
        key=lambda row: (
            -float(row.get("friction_score") or 0.0),
            -(int(row.get("finding_count") or 0) + int(row.get("tool_outcome_case_count") or 0)),
            -(row.get("started_at") or 0),
            row.get("source_session_id") or "",
            row.get("turn_index") or 0,
            row.get("turn_case_id") or "",
        ),
    )


def _finalize_session_groups(sessions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    for entry in sessions.values():
        evaluated = int(entry.get("evaluated_turns") or 0)
        total = float(entry.pop("_friction_total", 0.0))
        entry.pop("_requests_by_unit_id", None)
        entry["avg_friction_score"] = total / evaluated if evaluated else 0.0
        entry["findings"].sort(key=lambda row: (-(row.get("started_at") or 0), row.get("turn_index") or 0, row.get("finding_type") or ""))
        entry["findings"] = entry["findings"][:25]
        entry["tool_outcome_cases"].sort(key=lambda row: (-(row.get("result_timestamp") or 0), row.get("turn_index") or 0, row.get("id") or ""))
        entry["tool_outcome_cases"] = entry["tool_outcome_cases"][:25]
        entry["requests"] = _sort_requests(entry.get("requests") or [])
    return sorted(
        sessions.values(),
        key=lambda row: (
            -float(row.get("max_friction_score") or 0.0),
            -float(row.get("avg_friction_score") or 0.0),
            -int(row.get("finding_count") or 0),
            -(row.get("last_started_at") or 0),
            row.get("source_session_id") or "",
        ),
    )


def dashboard_summary(
    db: EvalDB,
    since: float | None = None,
    bucket_seconds: int = 3600,
    unit_limit: int = 1000,
    session_limit: int = 24,
    session_offset: int = 0,
) -> dict[str, Any]:
    cases = db.list_turn_cases(limit=max(1, int(unit_limit)), since=since)
    summary = db.summary(since=since)
    eval_rows = db.list_case_reviews(statuses=None, limit=max(1, int(unit_limit)), since=since)
    tool_outcome_cases = db.list_canonical_tool_outcome_cases(since=since, limit=max(1, int(unit_limit)))
    finding_total = sum(len(row.get("findings") or []) for row in eval_rows)

    timeline_map: dict[float, dict[str, Any]] = {}
    for row in eval_rows:
        bucket = _bucket_start(row.get("started_at"), bucket_seconds)
        if bucket is None:
            continue
        entry = timeline_map.setdefault(
            bucket,
            {"bucket_start": bucket, "evaluated_turns": 0, "statuses": {}, "findings": 0, "max_friction_score": 0.0, "avg_friction_score": 0.0, "_friction_total": 0.0},
        )
        entry["evaluated_turns"] += 1
        status = str(row.get("outcome_status") or "unknown")
        entry["statuses"][status] = entry["statuses"].get(status, 0) + 1
        entry["findings"] += len(row.get("findings") or [])
        friction = _friction(row)
        entry["_friction_total"] += friction
        entry["max_friction_score"] = max(float(entry["max_friction_score"]), friction)

    for entry in timeline_map.values():
        count = int(entry.get("evaluated_turns") or 0)
        total = float(entry.pop("_friction_total", 0.0))
        entry["avg_friction_score"] = total / count if count else 0.0

    sessions: dict[str, dict[str, Any]] = {}
    cases_by_id = {str(unit.get("id")): unit for unit in cases}
    for unit in cases:
        session_id = str(unit.get("source_session_id") or "unknown")
        entry = _session_entry(sessions, session_id, title=unit.get("title"), started_at=unit.get("started_at"))
        entry["turn_cases"] += 1
        _request_entry(entry, unit.get("id"), unit=unit)
        if entry.get("latest_turn_case_id") is None or (unit.get("started_at") or 0) >= (entry.get("last_started_at") or 0):
            entry["latest_turn_case_id"] = unit.get("id")

    for row in eval_rows:
        session_id = str(row.get("source_session_id") or "unknown")
        unit = cases_by_id.get(str(row.get("turn_case_id") or ""), {})
        entry = _session_entry(sessions, session_id, title=unit.get("title"), started_at=row.get("started_at"))
        request = _request_entry(entry, row.get("turn_case_id"), unit=unit, row=row)
        entry["evaluated_turns"] += 1
        friction = _friction(row)
        entry["_friction_total"] += friction
        entry["max_friction_score"] = max(float(entry.get("max_friction_score") or 0.0), friction)
        status = str(row.get("outcome_status") or "unknown")
        _bump_counter(entry["statuses"], status)
        findings = row.get("findings") or []
        entry["finding_count"] += len(findings)
        for finding in findings:
            finding_type = str(finding.get("finding_type") or finding.get("type") or "unknown")
            severity = str(finding.get("severity") or "medium")
            _bump_counter(entry["finding_types"], finding_type)
            _bump_counter(entry["severities"], severity)
            finding_row = {
                "turn_case_id": row.get("turn_case_id"),
                "turn_index": row.get("turn_index"),
                "started_at": row.get("started_at"),
                "finding_type": finding_type,
                "severity": severity,
                "source": finding.get("source"),
                "related_event_id": finding.get("related_event_id"),
                "evidence": finding.get("evidence"),
                "outcome_status": status,
                "confidence": row.get("confidence"),
                "summary_reason": row.get("summary_reason"),
                "friction_score": friction,
                "request_text": _preview(row.get("request_text") or unit.get("request_text")),
            }
            entry["findings"].append(finding_row)
            request["findings"].append(finding_row)

    for tool_outcome in tool_outcome_cases:
        session_id = str(tool_outcome.get("source_session_id") or "unknown")
        unit = cases_by_id.get(str(tool_outcome.get("turn_case_id") or ""), {})
        entry = _session_entry(sessions, session_id, started_at=tool_outcome.get("result_timestamp"))
        request = _request_entry(entry, tool_outcome.get("turn_case_id"), unit=unit)
        entry["tool_outcome_case_count"] += 1
        entry["tool_outcome_cases"].append(tool_outcome)
        request["tool_outcome_cases"].append(tool_outcome)

    session_groups = _finalize_session_groups(sessions)
    safe_session_limit = max(1, int(session_limit))
    safe_session_offset = max(0, int(session_offset))
    paged_session_groups = session_groups[safe_session_offset : safe_session_offset + safe_session_limit]
    requests = _sort_requests([request for group in session_groups for request in group.get("requests", [])])[:100]
    friction_summary = dict(summary.get("friction") or {})
    friction_summary["count"] = int(summary.get("evaluated_turns") or 0)
    return {
        "totals": {
            "turn_cases": len(cases),
            "evaluated_turns": int(summary.get("evaluated_turns") or 0),
            "findings": finding_total,
        },
        "statuses": dict(summary.get("statuses") or {}),
        "friction": friction_summary,
        "friction_anchors": FRICTION_ANCHORS,
        "requests": requests,
        "top_findings": list(summary.get("top_findings") or []),
        "tool_outcome_cases": tool_outcome_cases,
        "judge_tokens": dict(summary.get("judge_tokens") or {}),
        "timeline": [timeline_map[key] for key in sorted(timeline_map)],
        "session_groups": paged_session_groups,
        "session_pagination": {
            "limit": safe_session_limit,
            "offset": safe_session_offset,
            "total": len(session_groups),
            "has_next": safe_session_offset + safe_session_limit < len(session_groups),
            "has_prev": safe_session_offset > 0,
        },
        "hot_sessions": session_groups[:20],
    }


def turn_case_detail(db: EvalDB, turn_case_id: str) -> dict[str, Any]:
    unit = db.get_turn_case_with_trace(turn_case_id)
    latest_eval = db.get_latest_case_review(turn_case_id)
    signals = extract_case_signals(unit)
    case_events = unit.pop("case_events", [])
    return {
        "unit": unit,
        "case_events": case_events,
        "signals": signals,
        "latest_eval": latest_eval,
    }


def session_detail(
    db: EvalDB,
    source_session_id: str,
    since: float | None = None,
    unit_limit: int = 500,
) -> dict[str, Any]:
    cases = db.list_session_cases(str(source_session_id), limit=max(1, int(unit_limit)), since=since)
    cases.sort(key=lambda row: (-(row.get("started_at") or 0), row.get("turn_index") or 0, row.get("id") or ""))
    details: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    finding_types: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    finding_count = 0
    last_started_at = None
    friction_total = 0.0
    max_friction = 0.0
    evaluated_turns = 0
    tool_outcome_cases = db.list_canonical_tool_outcome_cases(source_session_id=str(source_session_id), since=since, limit=max(1, int(unit_limit)))
    tool_outcomes_by_unit: dict[str, list[dict[str, Any]]] = {}
    for tool_outcome in tool_outcome_cases:
        tool_outcomes_by_unit.setdefault(str(tool_outcome.get("turn_case_id") or ""), []).append(tool_outcome)

    for unit_row in cases:
        detail = turn_case_detail(db, str(unit_row["id"]))
        detail["tool_outcome_cases"] = tool_outcomes_by_unit.get(str(unit_row["id"]), [])
        unit = detail["unit"]
        if unit.get("started_at") is not None:
            last_started_at = max(last_started_at or unit["started_at"], unit["started_at"])
        latest_eval = detail.get("latest_eval") or {}
        friction = _friction(latest_eval)
        detail["friction_score"] = friction
        detail["friction_band"] = _friction_band(friction)
        if latest_eval:
            evaluated_turns += 1
            statuses[str(latest_eval.get("outcome_status") or "unknown")] += 1
            friction_total += friction
            max_friction = max(max_friction, friction)
            for finding in latest_eval.get("findings") or []:
                finding_count += 1
                finding_types[str(finding.get("finding_type") or finding.get("type") or "unknown")] += 1
                severities[str(finding.get("severity") or "medium")] += 1
        details.append(detail)

    avg_friction = friction_total / evaluated_turns if evaluated_turns else 0.0
    return {
        "source_session_id": str(source_session_id),
        "last_started_at": last_started_at,
        "turn_cases": len(details),
        "evaluated_turns": evaluated_turns,
        "finding_count": finding_count,
        "statuses": dict(statuses),
        "finding_types": dict(finding_types),
        "severities": dict(severities),
        "max_friction_score": max_friction,
        "avg_friction_score": avg_friction,
        "friction_anchors": FRICTION_ANCHORS,
        "tool_outcome_cases": tool_outcome_cases,
        "cases": details,
    }
