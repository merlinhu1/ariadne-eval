from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from agent_health.events import append_event, preview, sha256_text

_TOOL_STARTS: dict[str, float] = {}


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _events_path() -> Path:
    return _home() / "instruction-health" / "events.jsonl"


def _session_id(*args: Any, **kwargs: Any) -> str | None:
    for value in list(kwargs.values()) + list(args):
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for key in ("session_id", "id"):
                if value.get(key):
                    return str(value[key])
        if hasattr(value, "session_id"):
            return str(getattr(value, "session_id"))
        if hasattr(value, "id"):
            return str(getattr(value, "id"))
    return None


def _record(event_type: str, *args: Any, payload: dict[str, Any] | None = None, **kwargs: Any) -> None:
    try:
        append_event(_events_path(), framework="hermes", session_id=_session_id(*args, **kwargs), event_type=event_type, payload=payload or {"args_preview": preview(args), "kwargs_preview": preview(kwargs)})
    except Exception:
        # Hooks must fail open and never affect the agent loop.
        return


def on_session_start(*args: Any, **kwargs: Any) -> None:
    _record("session_start", *args, **kwargs)


def pre_llm_call(*args: Any, **kwargs: Any) -> None:
    _record("pre_llm_call", *args, **kwargs)


def post_llm_call(*args: Any, **kwargs: Any) -> None:
    _record("post_llm_call", *args, **kwargs)


def pre_tool_call(*args: Any, **kwargs: Any) -> None:
    tool_name = str(kwargs.get("tool_name") or kwargs.get("name") or "unknown")
    tool_args = kwargs.get("args") or kwargs.get("arguments") or kwargs
    key = sha256_text({"tool_name": tool_name, "args": tool_args})
    _TOOL_STARTS[key] = time.time()
    _record("tool_start", *args, payload={"tool_name": tool_name, "args_hash": sha256_text(tool_args), "args_preview": preview(tool_args), "start_key": key}, **kwargs)


def post_tool_call(*args: Any, **kwargs: Any) -> None:
    tool_name = str(kwargs.get("tool_name") or kwargs.get("name") or "unknown")
    tool_args = kwargs.get("args") or kwargs.get("arguments") or {}
    result = kwargs.get("result") or kwargs.get("response") or kwargs
    key = sha256_text({"tool_name": tool_name, "args": tool_args})
    started = _TOOL_STARTS.pop(key, None)
    duration_ms = int((time.time() - started) * 1000) if started else None
    result_text = preview(result)
    _record("tool_end", *args, payload={
        "tool_name": tool_name,
        "args_hash": sha256_text(tool_args),
        "args_preview": preview(tool_args),
        "result_hash": sha256_text(result),
        "result_preview": result_text,
        "result_error": any(p in result_text.lower() for p in ["error", "traceback", "exception", "failed", "exit_code 1"]),
        "duration_ms": duration_ms,
    }, **kwargs)


def on_session_end(*args: Any, **kwargs: Any) -> None:
    _record("session_end", *args, **kwargs)


def on_session_finalize(*args: Any, **kwargs: Any) -> None:
    _record("session_finalize", *args, **kwargs)


def post_approval_response(*args: Any, **kwargs: Any) -> None:
    _record("approval_response", *args, **kwargs)


def register(ctx: Any) -> None:
    for name, fn in [
        ("on_session_start", on_session_start),
        ("pre_llm_call", pre_llm_call),
        ("post_llm_call", post_llm_call),
        ("pre_tool_call", pre_tool_call),
        ("post_tool_call", post_tool_call),
        ("on_session_end", on_session_end),
        ("on_session_finalize", on_session_finalize),
        ("post_approval_response", post_approval_response),
    ]:
        ctx.register_hook(name, fn)
