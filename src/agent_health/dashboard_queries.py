from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from agent_health.db import EvalDB
from agent_health.incidents import extract_incident_events, summarize_incident_events
from agent_health.signals import extract_deterministic_signals


def _bucket_start(timestamp: float | None, bucket_seconds: int) -> float | None:
    if timestamp is None:
        return None
    bucket = max(1, int(bucket_seconds or 1))
    return float(int(float(timestamp) // bucket) * bucket)


def dashboard_summary(
    db: EvalDB,
    *,
    since: float | None = None,
    bucket_seconds: int = 3600,
    unit_limit: int = 1000,
) -> dict[str, Any]:
    """Build a read-only dashboard payload from the local eval sidecar.

    This function intentionally does not import sessions or call the judge. It
    summarizes already-imported eval units and already-stored judge rows so the
    web dashboard remains an inspection surface, not another evaluator path.
    """
    units = db.list_units(limit=max(1, int(unit_limit)), since=since)
    summary = db.summary(since=since)
    eval_rows = db.list_llm_evals(statuses=None, limit=max(1, int(unit_limit)), since=since)

    incidents: list[dict[str, Any]] = []
    for row in units:
        try:
            unit = db.get_unit_with_trace(row["id"])
        except KeyError:
            continue
        incidents.extend(extract_incident_events(unit))

    incident_summary = summarize_incident_events(incidents)
    anomaly_total = sum(len(row.get("anomalies") or []) for row in eval_rows)

    timeline_map: dict[float, dict[str, Any]] = {}
    for row in eval_rows:
        bucket = _bucket_start(row.get("started_at"), bucket_seconds)
        if bucket is None:
            continue
        entry = timeline_map.setdefault(
            bucket,
            {"bucket_start": bucket, "evaluated_turns": 0, "statuses": {}, "incidents": 0, "anomalies": 0},
        )
        entry["evaluated_turns"] += 1
        status = str(row.get("health_status") or "unknown")
        entry["statuses"][status] = entry["statuses"].get(status, 0) + 1
        entry["anomalies"] += len(row.get("anomalies") or [])
    for incident in incidents:
        bucket = _bucket_start(incident.get("started_at"), bucket_seconds)
        if bucket is None:
            continue
        entry = timeline_map.setdefault(
            bucket,
            {"bucket_start": bucket, "evaluated_turns": 0, "statuses": {}, "incidents": 0, "anomalies": 0},
        )
        entry["incidents"] += 1

    sessions: dict[str, dict[str, Any]] = {}
    for unit in units:
        session_id = str(unit.get("source_session_id") or "unknown")
        entry = sessions.setdefault(
            session_id,
            {
                "source_session_id": session_id,
                "title": unit.get("title"),
                "eval_units": 0,
                "evaluated_turns": 0,
                "incident_count": 0,
                "anomaly_count": 0,
                "statuses": {},
                "last_started_at": unit.get("started_at"),
            },
        )
        entry["eval_units"] += 1
        if unit.get("started_at") is not None:
            entry["last_started_at"] = max(entry.get("last_started_at") or unit["started_at"], unit["started_at"])
    for incident in incidents:
        session_id = str(incident.get("source_session_id") or "unknown")
        sessions.setdefault(
            session_id,
            {"source_session_id": session_id, "title": None, "eval_units": 0, "evaluated_turns": 0, "incident_count": 0, "anomaly_count": 0, "statuses": {}, "last_started_at": None},
        )["incident_count"] += 1
    for row in eval_rows:
        session_id = str(row.get("source_session_id") or "unknown")
        entry = sessions.setdefault(
            session_id,
            {"source_session_id": session_id, "title": None, "eval_units": 0, "evaluated_turns": 0, "incident_count": 0, "anomaly_count": 0, "statuses": {}, "last_started_at": None},
        )
        entry["evaluated_turns"] += 1
        status = str(row.get("health_status") or "unknown")
        entry["statuses"][status] = entry["statuses"].get(status, 0) + 1
        entry["anomaly_count"] += len(row.get("anomalies") or [])

    hot_sessions = sorted(
        sessions.values(),
        key=lambda row: (-(int(row.get("incident_count") or 0) + int(row.get("anomaly_count") or 0)), -(row.get("last_started_at") or 0), row.get("source_session_id") or ""),
    )[:20]

    top_incidents = [
        {"incident_type": incident_type, "count": count}
        for incident_type, count in (incident_summary.get("by_type") or {}).items()
    ]

    return {
        "totals": {
            "eval_units": len(units),
            "evaluated_turns": int(summary.get("evaluated_turns") or 0),
            "incidents": int(incident_summary.get("total_incidents") or 0),
            "anomalies": anomaly_total,
        },
        "statuses": dict(summary.get("statuses") or {}),
        "top_incidents": top_incidents,
        "incident_severities": dict(incident_summary.get("by_severity") or {}),
        "top_anomalies": list(summary.get("top_anomalies") or []),
        "judge_tokens": dict(summary.get("judge_tokens") or {}),
        "timeline": [timeline_map[k] for k in sorted(timeline_map)],
        "hot_sessions": hot_sessions,
    }


def eval_unit_detail(db: EvalDB, eval_unit_id: str) -> dict[str, Any]:
    """Return the dashboard detail payload for one eval unit."""
    unit = db.get_unit_with_trace(eval_unit_id)
    latest_eval = db.get_latest_llm_eval(eval_unit_id)
    signals = extract_deterministic_signals(unit)
    incidents = extract_incident_events(unit)
    trace_events = unit.pop("trace_events", [])
    return {
        "unit": unit,
        "trace_events": trace_events,
        "signals": signals,
        "incidents": incidents,
        "latest_eval": latest_eval,
    }
