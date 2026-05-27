from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from agent_health.signals import _event_error

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
            content = _text(msg.get("content"), 6000)
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
    return _event_error({"result_preview": text, "result_error": False})


def _parse_tool_calls(raw: Any) -> list[dict[str, Any]]:
    if raw in (None, "", "[]"):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [call for call in raw if isinstance(call, dict) and call.get("id")]


def _tool_call_name_and_args(call: dict[str, Any]) -> tuple[str | None, str | None]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = call.get("name") or function.get("name")
    args = call.get("arguments") if "arguments" in call else function.get("arguments")
    if isinstance(args, (dict, list)):
        args = json.dumps(args, ensure_ascii=False)
    return (str(name) if name is not None else None, str(args) if args is not None else None)


def _find_tool_result_for_call(messages: list[dict[str, Any]], assistant_idx: int, tool_call_id: str) -> dict[str, Any] | None:
    for msg in messages[assistant_idx + 1:]:
        role = msg.get("role")
        if role == "user" or role == "assistant":
            return None
        if role == "tool" and str(msg.get("tool_call_id") or "") == tool_call_id:
            return msg
    return None


def _previous_visible_assistant(messages: list[dict[str, Any]], before_idx: int) -> str | None:
    for msg in reversed(messages[:before_idx]):
        if msg.get("role") == "assistant" and _text(msg.get("content")).strip():
            return _text(msg.get("content"), 2000)
    return None


def normalize_incident_examples(session: dict[str, Any], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    messages = filter_synthetic_runtime_messages(messages)
    session_id = str(session.get("id"))
    current_user: dict[str, Any] | None = None
    turn_index = 0
    now = time.time()
    for idx, msg in enumerate(messages):
        if msg.get("role") == "user":
            turn_index += 1
            current_user = msg
            continue
        if msg.get("role") != "assistant":
            continue
        tool_calls = _parse_tool_calls(msg.get("tool_calls"))
        if not tool_calls:
            continue
        following_idx, following = _find_next_assistant_final(messages, idx + 1)
        del following_idx
        assistant_message_id = str(msg.get("id"))
        for call in tool_calls:
            tool_call_id = str(call.get("id"))
            result = _find_tool_result_for_call(messages, idx, tool_call_id)
            if result is None:
                continue
            result_message_id = str(result.get("id"))
            tool_name, tool_arguments = _tool_call_name_and_args(call)
            if not tool_name and result.get("tool_name"):
                tool_name = str(result.get("tool_name"))
            source_event_id = "|".join([session_id, assistant_message_id, result_message_id, tool_call_id])
            raw_payload = {
                "assistant_message_id": assistant_message_id,
                "result_message_id": result_message_id,
                "tool_call_id": tool_call_id,
                "tool_call": call,
            }
            for hidden_key in ("reasoning", "reasoning_content", "reasoning_details", "codex_reasoning_items", "codex_message_items"):
                raw_payload.pop(hidden_key, None)
            examples.append({
                "id": f"hermes:{session_id}:incident:{assistant_message_id}:{result_message_id}:{tool_call_id}",
                "framework": "hermes",
                "source_session_id": session_id,
                "source_event_id": source_event_id,
                "eval_unit_id": f"hermes:{session_id}:turn:{turn_index}" if turn_index else None,
                "source_turn_index": turn_index or None,
                "assistant_tool_call_message_id": assistant_message_id,
                "result_message_id": result_message_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_arguments": tool_arguments,
                "tool_result": _text(result.get("content"), 6000),
                "result_timestamp": result.get("timestamp"),
                "user_request_excerpt": _text(current_user.get("content"), 2000) if current_user else None,
                "prior_assistant_visible_text": _previous_visible_assistant(messages, idx),
                "following_assistant_visible_text": _text(following.get("content"), 2000) if following else None,
                "explicit_caller_expectation": msg.get("explicit_caller_expectation") or result.get("explicit_caller_expectation"),
                "explicit_caller_interpretation": msg.get("explicit_caller_interpretation") or result.get("explicit_caller_interpretation"),
                "upstream_intent_source": msg.get("upstream_intent_source") or result.get("upstream_intent_source"),
                "normalization_version": NORMALIZATION_VERSION,
                "raw_payload_json": json.dumps(raw_payload, ensure_ascii=False),
                "created_at": now,
                "updated_at": now,
            })
    return examples


SYNTHETIC_USER_PREFIXES = (
    "[context compaction",
    "[the user sent context compaction",
    "[your active task list was preserved across context compression]",
    "you've reached the maximum number of tool-calling iterations allowed",
    "you have reached the maximum number of tool-calling iterations allowed",
)

SYNTHETIC_CONTEXT_BOUNDARY_USER_PREFIXES = (
    "[context compaction",
    "[the user sent context compaction",
)

SYNTHETIC_ASSISTANT_PREFIXES = (
    "[context compaction",
)

SYNTHETIC_ASSISTANT_ACK_PREFIXES = (
    "task list restored",
    "continuing.",
    "continuing…",
    "continuing...",
)


def _is_synthetic_text(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return True
    return any(lowered.startswith(prefix) for prefix in SYNTHETIC_USER_PREFIXES + SYNTHETIC_ASSISTANT_PREFIXES)


def _is_document_upload_replay(message: dict[str, Any], following_messages: list[dict[str, Any]]) -> bool:
    """Detect gateway-replayed document uploads before a compaction handoff.

    Hermes state.db can contain the original Discord document-upload message at
    the start of later compacted sessions, immediately followed by a few restored
    assistant/tool rows and then a synthetic context-compaction message. Treat
    that replay as runtime context, not as a fresh user request; otherwise the
    same document becomes many eval units.
    """
    if message.get("role") != "user":
        return False
    text = _text(message.get("content")).strip().lower()
    if not text.startswith("[the user sent a text document:"):
        return False
    try:
        start_ts = float(message.get("timestamp") or 0)
    except Exception:
        start_ts = 0.0
    for next_message in following_messages[:8]:
        next_text = _text(next_message.get("content")).strip().lower()
        if next_message.get("role") == "user":
            if next_text.startswith("[context compaction"):
                try:
                    gap = abs(float(next_message.get("timestamp") or 0) - start_ts)
                except Exception:
                    gap = 999999
                return gap <= 10.0
            return False
        if next_text.startswith("[context compaction"):
            try:
                gap = abs(float(next_message.get("timestamp") or 0) - start_ts)
            except Exception:
                gap = 999999
            return gap <= 10.0
    return False


def is_synthetic_user_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    text = _text(message.get("content")).strip().lower()
    return any(text.startswith(prefix) for prefix in SYNTHETIC_USER_PREFIXES)


def is_context_boundary_user_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    text = _text(message.get("content")).strip().lower()
    return any(text.startswith(prefix) for prefix in SYNTHETIC_CONTEXT_BOUNDARY_USER_PREFIXES)


def is_synthetic_assistant_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "assistant":
        return False
    text = _text(message.get("content")).strip().lower()
    return any(text.startswith(prefix) for prefix in SYNTHETIC_ASSISTANT_PREFIXES)


def is_synthetic_assistant_ack(message: dict[str, Any]) -> bool:
    if message.get("role") != "assistant" or _has_tool_calls(message):
        return False
    text = _text(message.get("content")).strip().lower()
    return any(text.startswith(prefix) for prefix in SYNTHETIC_ASSISTANT_ACK_PREFIXES)


def filter_synthetic_runtime_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove gateway/runtime housekeeping user turns and their assistant acks.

    Context compaction handoff messages, preserved task lists, and max-iteration
    continuation notices are injected as user-role messages but are not user
    requests. Including them creates fake eval units and false next-user
    reactions, so normalization drops them and their immediate assistant replies.
    """
    filtered: list[dict[str, Any]] = []
    skip_until_next_user = False
    skip_next_runtime_ack = False
    for idx, message in enumerate(messages):
        role = message.get("role")
        following_messages = messages[idx + 1:]
        if _is_document_upload_replay(message, following_messages):
            skip_until_next_user = True
            skip_next_runtime_ack = False
            continue
        if role == "user":
            skip_until_next_user = False
            skip_next_runtime_ack = False
            if is_synthetic_user_message(message):
                skip_until_next_user = is_context_boundary_user_message(message)
                skip_next_runtime_ack = not skip_until_next_user
                continue
        elif is_synthetic_assistant_message(message):
            skip_until_next_user = True
            skip_next_runtime_ack = False
            continue
        elif skip_until_next_user and role in {"assistant", "tool"}:
            continue
        elif skip_next_runtime_ack:
            if is_synthetic_assistant_ack(message):
                skip_next_runtime_ack = False
                continue
            skip_next_runtime_ack = False
        filtered.append(message)
    return filtered


@dataclass(frozen=True)
class RequestBoundary:
    """Current V1 request boundary: one real user message and its response window.

    This is a preparatory seam for future multi-message request boundary logic.
    V1 intentionally preserves existing behavior: after synthetic/runtime messages
    are filtered, each real user-role message is exactly one request boundary.
    Indexes refer to the filtered message list returned by
    ``filter_synthetic_runtime_messages``.
    """

    turn_index: int
    user_index: int
    user_message_id: str
    assistant_index: int | None
    assistant_message_id: str | None
    next_user_index: int | None
    next_user_message_id: str | None


def detect_request_boundaries(messages: list[dict[str, Any]]) -> list[RequestBoundary]:
    """Return V1 request boundaries without changing normalization semantics."""

    filtered = filter_synthetic_runtime_messages(messages)
    boundaries: list[RequestBoundary] = []
    turn_index = 0
    for idx, msg in enumerate(filtered):
        if msg.get("role") != "user":
            continue
        turn_index += 1
        assistant_idx, assistant = _find_next_assistant_final(filtered, idx + 1)
        next_user_idx, next_user = _find_next_user_after(filtered, assistant_idx)
        boundaries.append(RequestBoundary(
            turn_index=turn_index,
            user_index=idx,
            user_message_id=str(msg.get("id")),
            assistant_index=assistant_idx,
            assistant_message_id=str(assistant.get("id")) if assistant else None,
            next_user_index=next_user_idx,
            next_user_message_id=str(next_user.get("id")) if next_user else None,
        ))
    return boundaries


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
    messages = filter_synthetic_runtime_messages(messages)
    boundaries = detect_request_boundaries(messages)
    for boundary in boundaries:
        idx = boundary.user_index
        msg = messages[idx]
        turn_index = boundary.turn_index
        assistant_idx = boundary.assistant_index
        assistant = messages[assistant_idx] if assistant_idx is not None else None
        next_user_idx = boundary.next_user_index
        next_user = messages[next_user_idx] if next_user_idx is not None else None
        assistant_content = _text(assistant.get("content"), max_assistant_response_chars) if assistant else None
        if assistant_content is not None and not assistant_content.strip():
            assistant_content = None
        trace_end_idx = assistant_idx if assistant_content is not None else (next_user_idx - 1 if next_user_idx is not None else None)
        trace_events = _tool_events_between(messages, idx, trace_end_idx)
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
            "assistant_response": assistant_content,
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
