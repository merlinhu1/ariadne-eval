from __future__ import annotations

import json
import re
from typing import Any

FEATURE_SCHEMA_VERSION = "incident_features_v1"


def _text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    text = str(value)
    return text[:limit]


def _json_obj(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _find_exit_code(value: Any) -> int | None:
    parsed = _json_obj(value)
    if isinstance(parsed, dict):
        for key in ("exit_code", "returncode", "status"):
            if key in parsed:
                try:
                    return int(parsed[key])
                except Exception:
                    pass
    text = _text(value)
    match = re.search(r"\b(?:exit_code|returncode|exit status)\s*[:=]?\s*(-?\d+)\b", text, re.I)
    return int(match.group(1)) if match else None


def _error_fields(value: Any) -> dict[str, Any]:
    parsed = _json_obj(value)
    if isinstance(parsed, dict):
        return {
            "error": parsed.get("error"),
            "stderr": parsed.get("stderr"),
            "exception": parsed.get("exception"),
        }
    text = _text(value)
    return {
        "error": text if re.search(r"\b(error|exception|traceback|failed|permission denied)\b", text, re.I) else None,
        "stderr": None,
        "exception": None,
    }


def build_incident_features(example: dict[str, Any]) -> dict[str, Any]:
    tool_name = _text(example.get("tool_name"), 200)
    tool_arguments = _text(example.get("tool_arguments"), 4000)
    tool_result = _text(example.get("tool_result"), 6000)
    insufficient = not (example.get("assistant_tool_call_message_id") and example.get("result_message_id") and example.get("tool_call_id"))
    exit_code = _find_exit_code(tool_result)
    errors = _error_fields(tool_result)
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "example_id": example.get("id"),
        "tool_name": tool_name,
        "tool_arguments_text": tool_arguments,
        "tool_result_text": tool_result,
        "tool_result_length": len(str(example.get("tool_result") or "")),
        "tool_result_empty": not bool(tool_result.strip()),
        "tool_result_truncated": len(str(example.get("tool_result") or "")) > len(tool_result),
        "exit_code": exit_code,
        "exit_code_nonzero": exit_code is not None and exit_code != 0,
        "error": errors["error"],
        "stderr": errors["stderr"],
        "exception": errors["exception"],
        "user_request_excerpt": _text(example.get("user_request_excerpt"), 2000),
        "prior_assistant_visible_text": _text(example.get("prior_assistant_visible_text"), 2000),
        "following_assistant_visible_text": _text(example.get("following_assistant_visible_text"), 2000),
        "explicit_caller_expectation": example.get("explicit_caller_expectation"),
        "explicit_caller_interpretation": example.get("explicit_caller_interpretation"),
        "insufficient_for_classification": insufficient,
    }


def incident_feature_text(features: dict[str, Any]) -> str:
    return "\n".join(
        str(features.get(key) or "")
        for key in (
            "tool_name",
            "tool_arguments_text",
            "tool_result_text",
            "user_request_excerpt",
            "prior_assistant_visible_text",
            "following_assistant_visible_text",
        )
    )
