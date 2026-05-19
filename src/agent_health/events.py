from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any


def preview(value: Any, max_chars: int = 4000) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def sha256_text(value: Any) -> str:
    text = preview(value, max_chars=10_000_000)
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def append_event(events_path: Path, *, framework: str, session_id: str | None, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    event = {
        "event_id": "evt_" + uuid.uuid4().hex,
        "schema_version": "event_v1",
        "framework": framework,
        "session_id": session_id,
        "event_type": event_type,
        "timestamp": time.time(),
        "payload": payload,
    }
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    return event


def read_events(events_path: Path, session_id: str | None = None) -> list[dict[str, Any]]:
    if not events_path.exists():
        return []
    result = []
    with events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if session_id is None or event.get("session_id") == session_id:
                result.append(event)
    return result
