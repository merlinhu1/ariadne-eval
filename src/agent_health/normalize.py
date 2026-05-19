from __future__ import annotations

import json
import time
from typing import Any

NORMALIZATION_VERSION = "normalization_v1"


def _text(value: Any, cap: int | None = None) -> str:
    if value is None:
        return ""
    result = str(value)
    if cap is not None and len(result) > cap:
        return result[: cap - 1] + "…"
    return result


def _has_tool_calls(message: dict[str, Any]) -> bool:
    raw = message.get("tool_calls")
    if raw in (None, "", "[]"):
        return False
    return True


def _is_final_assistant(message: dict[str, Any]) -> bool:
    if message.get("role") != "assistant":
        return False
    # Hermes can store an assistant tool-call planning message before tool results.
    # Treat content-bearing assistant messages with no pending tool_calls as final.
    return bool(_text(message.get("content")).strip()) and not _has_tool_calls(message)


def _find_next_assistant_final(messages: list[dict[str, Any]], start: int) -> tuple[int | None, dict[str, Any] | None]:
    fallback: tuple[int | None, dict[str, Any] | None] = (None, None)
    for idx in range(start, len(messages)):
        msg = messages[idx]
        if msg.get("role") == "user":
            break
        if _is_final_assistant(msg):
            return idx, msg
        if msg.get("role") == "assistant" and fallback[1] is None:
            fallback = (idx, msg)
    return fallback


def _find_next_user_after(messages: list[dict[str, Any]], start_idx: int | None) -> tuple[int | None, dict[str, Any] | None]:
    if start_idx is None:
        return None, None
    for idx in range(start_idx + 1, len(messages)):
        if messages[idx].get("role") == "user":
            return idx, messages[idx]
    return None, None


def _collect_previous_context(messages: list[dict[str, Any]], before: int, previous_turn_pairs: int, cap: int) -> str:
    context_msgs = [m for m in messages[:before] if m.get("role") in {"user", "assistant"} and _text(m.get("content")).strip()]
    # pair count = user+assistant turns, so retain 2 messages per pair.
    keep = max(previous_turn_pairs * 2, 0)
    if keep:
        context_msgs = context_msgs[-keep:]
    else:
        context_msgs = []
    lines = [f"{m.get('role')}: {_text(m.get('content'))}" for m in context_msgs]
    return _text("\n".join(lines), cap)


def _tool_events_between(messages: list[dict[str, Any]], start_idx: int, end_idx: int | None) -> list[dict[str, Any]]:
    stop = len(messages) if end_idx is None else end_idx + 1
    events: list[dict[str, Any]] = []
    for msg in messages[start_idx + 1 : stop]:
        if msg.get("role") == "tool" or msg.get("tool_name"):
            content = _text(msg.get("content"), 1500)
            events.append({
                "id": f"msg:{msg.get('id')}",
                "source_event_id": str(msg.get("id")),
                "event_type": "tool",
                "timestamp": msg.get("timestamp"),
                "tool_name": msg.get("tool_name"),
                "args_hash": None,
                "args_preview": None,
                "result_hash": None,
                "result_preview": content,
                "result_error": _looks_like_error(content),
                "duration_ms": None,
                "raw_payload_json": json.dumps({
                    "message_id": msg.get("id"),
                    "tool_call_id": msg.get("tool_call_id"),
                    "role": msg.get("role"),
                }, ensure_ascii=False),
            })
    return events


def _looks_like_error(text: str) -> bool:
    lowered = text.lower()
    patterns = ["error", "traceback", "exception", 'exit_code": 1', "exit_code 1", "failed"]
    return any(pattern in lowered for pattern in patterns)


def normalize_session(
    session: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    previous_turn_pairs: int = 3,
    max_previous_context_chars: int = 6000,
    max_user_request_chars: int = 4000,
    max_assistant_response_chars: int = 8000,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    turn_index = 0
    for idx, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        turn_index += 1
        assistant_idx, assistant = _find_next_assistant_final(messages, idx + 1)
        next_user_idx, next_user = _find_next_user_after(messages, assistant_idx)
        trace_events = _tool_events_between(messages, idx, assistant_idx)
        started_at = msg.get("timestamp")
        ended_at = assistant.get("timestamp") if assistant else None
        created = time.time()
        units.append({
            "id": f"hermes:{session.get('id')}:turn:{turn_index}",
            "framework": "hermes",
            "source_session_id": str(session.get("id")),
            "source_turn_index": turn_index,
            "user_message_id": str(msg.get("id")),
            "assistant_message_id": str(assistant.get("id")) if assistant else None,
            "next_user_message_id": str(next_user.get("id")) if next_user else None,
            "started_at": started_at,
            "ended_at": ended_at,
            "source": session.get("source"),
            "model": session.get("model"),
            "title": session.get("title"),
            "parent_session_id": session.get("parent_session_id"),
            "user_request": _text(msg.get("content"), max_user_request_chars),
            "assistant_response": _text(assistant.get("content"), max_assistant_response_chars) if assistant else None,
            "previous_context_summary": _collect_previous_context(messages, idx, previous_turn_pairs, max_previous_context_chars),
            "next_user_reaction_text": _text(next_user.get("content"), max_user_request_chars) if next_user else None,
            "tool_call_count": len(trace_events),
            "api_call_count": int(session.get("api_call_count") or 0),
            "input_tokens": int(session.get("input_tokens") or 0),
            "output_tokens": int(session.get("output_tokens") or 0),
            "normalization_version": NORMALIZATION_VERSION,
            "trace_events": trace_events,
            "created_at": created,
            "updated_at": created,
        })
    return units
