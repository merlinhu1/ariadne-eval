from __future__ import annotations

from agent_health.incidents import extract_incident_events, summarize_incident_events

# Backward-compatible module for the old deterministic "bump" terminology.
extract_bump_events = extract_incident_events
summarize_bump_events = summarize_incident_events
