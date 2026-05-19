from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROMPT_VERSION = "instruction_health_v1"
EVAL_SCHEMA_VERSION = "instruction_health_eval_v1"

HEALTH_STATUSES = {"succeed", "failed", "mishandled", "prolonged", "not_evaluable"}
CONFIDENCES = {"high", "medium", "low"}
REACTION_TYPES = {"acceptance", "continuation", "clarification", "correction", "complaint", "repeated_request", "scope_change", "unrelated", "unknown", "none"}
BARRIER_TYPES = {
    "tool_error", "repeated_tool_loop", "unnecessary_tool_use", "missing_tool_use",
    "bad_tool_selection", "external_action_not_verified", "action_misrepresentation",
    "misread_instruction", "missed_requirement", "unsupported_claim", "format_mismatch",
    "vague_or_incomplete_response", "over_refusal", "under_clarification",
    "user_correction", "user_repeated_request", "interrupted_or_incomplete",
    "excessive_duration", "excessive_api_calls", "excessive_tool_calls", "context_loss",
}


@dataclass(frozen=True)
class JudgeRoute:
    name: str
    task: str | None
    provider: str | None
    model: str | None


@dataclass
class JudgeResult:
    eval_data: dict[str, Any]
    judge_provider: str | None
    judge_model: str | None
    raw_output: str
    evaluator_error: str | None = None


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _compression_config_is_real(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict) or not config:
        return False
    for key in ("provider", "model", "base_url", "api_key", "api_mode"):
        value = _non_empty(config.get(key))
        if value and not (key == "provider" and value.lower() == "auto"):
            return True
    return False


def build_judge_routes(
    compression_config: dict[str, Any] | None,
    *,
    main_provider: str | None,
    main_model: str | None,
) -> list[JudgeRoute]:
    """Return judge model routes in Ariadne's V1 priority order.

    Priority is intentionally inherited from Hermes: first use the user's
    configured ``auxiliary.compression`` model/provider if one exists, then fall
    back to the configured main provider/model.  The caller executes each route
    with Hermes' own auxiliary runtime so auth, custom providers, OAuth adapters,
    and provider quirks stay centralized in Hermes.
    """
    routes: list[JudgeRoute] = []
    compression_config = compression_config if isinstance(compression_config, dict) else {}
    if _compression_config_is_real(compression_config):
        routes.append(JudgeRoute(
            name="auxiliary.compression",
            task="compression",
            provider=_non_empty(compression_config.get("provider")) or "auto",
            model=_non_empty(compression_config.get("model")),
        ))
    provider = _non_empty(main_provider)
    model = _non_empty(main_model)
    if provider or model:
        routes.append(JudgeRoute(name="main", task=None, provider=provider, model=model))
    if not routes:
        routes.append(JudgeRoute(name="auto", task="compression", provider="auto", model=None))
    return routes


def _ensure_hermes_import_path() -> None:
    candidates = [os.environ.get("HERMES_REPO"), "/opt/hermes"]
    for candidate in candidates:
        if candidate and Path(candidate).exists() and candidate not in sys.path:
            sys.path.insert(0, candidate)


def _extract_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    try:
        return str(response.choices[0].message.content or "")
    except Exception:
        pass
    if isinstance(response, dict):
        try:
            return str(response["choices"][0]["message"]["content"] or "")
        except Exception:
            pass
    return str(response)


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("empty judge response")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(stripped[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("judge response JSON was not an object")
    return data


def validate_eval_json(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized.setdefault("schema_version", EVAL_SCHEMA_VERSION)
    if normalized["schema_version"] != EVAL_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {normalized['schema_version']!r}")
    status = normalized.get("health_status")
    if status not in HEALTH_STATUSES:
        raise ValueError(f"invalid health_status {status!r}")
    confidence = normalized.get("confidence")
    if confidence not in CONFIDENCES:
        raise ValueError(f"invalid confidence {confidence!r}")
    primary_reason = _non_empty(normalized.get("primary_reason"))
    if not primary_reason:
        raise ValueError("primary_reason is required")
    normalized["primary_reason"] = primary_reason
    normalized.setdefault("goal_summary", "")
    normalized.setdefault("observed_outcome", "")
    reaction = normalized.get("user_reaction")
    if not isinstance(reaction, dict):
        reaction = {}
    reaction_type = reaction.get("type") if reaction.get("type") in REACTION_TYPES else "unknown"
    normalized["user_reaction"] = {
        "type": reaction_type,
        "used_as_evidence": bool(reaction.get("used_as_evidence", False)),
        "evidence": str(reaction.get("evidence") or ""),
    }
    barriers = normalized.get("barriers")
    if not isinstance(barriers, list):
        barriers = []
    cleaned_barriers = []
    for barrier in barriers:
        if not isinstance(barrier, dict):
            continue
        barrier_type = str(barrier.get("type") or "").strip()
        if barrier_type not in BARRIER_TYPES:
            continue
        severity = str(barrier.get("severity") or "medium").strip().lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        cleaned_barriers.append({
            "type": barrier_type,
            "severity": severity,
            "source": str(barrier.get("source") or "trace"),
            "evidence": str(barrier.get("evidence") or ""),
        })
    normalized["barriers"] = cleaned_barriers
    normalized.setdefault("prolongation_evidence", {"tool_calls": 0, "api_calls": 0, "duration_seconds": None, "repeated_actions": []})
    normalized.setdefault("missed_or_mishandled_requirements", [])
    normalized.setdefault("not_evaluable_reason", None)
    return normalized


def _signal_map(signals: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(s.get("signal_name")): s.get("signal_value") for s in signals}


FIELD_LIMITS = {
    "previous_context_summary": 2500,
    "user_request": 3000,
    "assistant_response": 4000,
    "next_user_reaction_text": 1000,
    "tool_args": 900,
    "tool_result": 1500,
}


def _middle_trim(text: str, limit: int, label: str) -> str:
    if len(text) <= limit:
        return text
    marker = f"\n[trimmed {label}: {len(text) - limit} chars omitted]\n"
    keep = max(0, limit - len(marker))
    head = max(1, int(keep * 0.65))
    tail = max(0, keep - head)
    return text[:head].rstrip() + marker + (text[-tail:].lstrip() if tail else "")


def preflight_trim_text(value: Any, *, limit: int, label: str) -> str | None:
    """Aggressively trim payload noise before sending evidence to the judge.

    The judge needs task intent, outcome, reactions, tool names, errors, and
    short evidence excerpts. Huge pasted docs, code fences, and image/base64
    blobs usually add cost without improving diagnosis, so summarize them before
    final field-level truncation.
    """
    if value is None:
        return None
    text = str(value)
    if not text:
        return text

    text = re.sub(
        r"!\[([^\]]*)\]\(data:image/[^)]{100,}\)",
        lambda m: f"[image omitted: markdown data URL alt={m.group(1)[:80]!r}]",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]{100,}",
        "[image omitted: base64 data URL]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"MEDIA:\S+\.(?:png|jpe?g|webp|gif|bmp|tiff?)\b",
        "[image omitted: MEDIA attachment path]",
        text,
        flags=re.IGNORECASE,
    )

    def _code_repl(match: re.Match[str]) -> str:
        lang = (match.group(1) or "").strip()
        body = match.group(2) or ""
        if len(body) <= 900:
            return match.group(0)
        first_lines = "\n".join(body.strip().splitlines()[:8])
        return f"```{lang}\n{first_lines}\n[trimmed code block: {max(0, len(body) - len(first_lines))} chars omitted]\n```"

    text = re.sub(r"```([^\n`]*)\n(.*?)```", _code_repl, text, flags=re.DOTALL)

    def _doc_repl(match: re.Match[str]) -> str:
        header = match.group(1)
        body = match.group(2)
        if len(body) <= 1600:
            return match.group(0)
        excerpt = body[:900].rstrip()
        return f"{header}{excerpt}\n[trimmed large document content: {len(body) - len(excerpt)} chars omitted]"

    text = re.sub(r"(\[Content of [^\n\]]+\]:\s*\n)(.*)", _doc_repl, text, flags=re.DOTALL)
    return _middle_trim(text, limit, label)


def build_trace_summary(unit: dict[str, Any], signals: list[dict[str, Any]]) -> dict[str, Any]:
    events = unit.get("trace_events") or []
    tool_sequence = []
    for idx, event in enumerate(events[:40], start=1):
        tool_sequence.append({
            "index": idx,
            "tool_name": event.get("tool_name"),
            "args_summary": preflight_trim_text(event.get("args_preview"), limit=FIELD_LIMITS["tool_args"], label="tool args"),
            "result_summary": preflight_trim_text(event.get("result_preview"), limit=FIELD_LIMITS["tool_result"], label="tool result"),
            "error": bool(event.get("result_error")),
            "duration_ms": event.get("duration_ms"),
        })
    return {
        "tool_sequence": tool_sequence,
        "deterministic_signals": signals,
        "timing": {
            "turn_duration_seconds": _signal_map(signals).get("turn_duration_seconds"),
            "api_call_count": unit.get("api_call_count"),
            "tool_call_count": unit.get("tool_call_count"),
        },
        "next_user_reaction": {
            "text": unit.get("next_user_reaction_text"),
        },
    }


def build_eval_payload(unit: dict[str, Any], signals: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "eval_unit_id": unit.get("id"),
        "framework": unit.get("framework"),
        "session": {
            "source_session_id": unit.get("source_session_id"),
            "source_turn_index": unit.get("source_turn_index"),
            "source": unit.get("source"),
            "model": unit.get("model"),
            "title": unit.get("title"),
        },
        "previous_context_summary": preflight_trim_text(
            unit.get("previous_context_summary"),
            limit=FIELD_LIMITS["previous_context_summary"],
            label="previous context",
        ),
        "user_request": preflight_trim_text(unit.get("user_request"), limit=FIELD_LIMITS["user_request"], label="user request"),
        "assistant_response": preflight_trim_text(
            unit.get("assistant_response"),
            limit=FIELD_LIMITS["assistant_response"],
            label="assistant response",
        ),
        "next_user_reaction_text": preflight_trim_text(
            unit.get("next_user_reaction_text"),
            limit=FIELD_LIMITS["next_user_reaction_text"],
            label="next user reaction",
        ),
        "preflight_trim_policy": {
            "large_documents": "trimmed to short excerpts",
            "large_code_blocks": "trimmed to leading lines plus omitted-character note",
            "images_and_data_urls": "omitted unless represented by surrounding text",
            "field_limits_chars": FIELD_LIMITS,
        },
        "trace_summary": build_trace_summary(unit, signals),
    }


def load_prompt_template() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "instruction_health_v1.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "You are evaluating one AI agent turn. Return strict JSON matching instruction_health_eval_v1."


class HermesLLMJudgeClient:
    def __init__(
        self,
        hermes_home: str | Path,
        *,
        routes: list[JudgeRoute] | None = None,
        call_func: Callable[[JudgeRoute, list[dict[str, str]], float | None, int | None], Any] | None = None,
        max_tokens: int = 1200,
        temperature: float | None = 0,
    ):
        self.hermes_home = Path(hermes_home).expanduser()
        self._routes = routes
        self._call_func = call_func
        self.max_tokens = max_tokens
        self.temperature = temperature

    def resolve_routes(self) -> list[JudgeRoute]:
        if self._routes is not None:
            return self._routes
        os.environ["HERMES_HOME"] = str(self.hermes_home)
        _ensure_hermes_import_path()
        compression_config: dict[str, Any] = {}
        main_provider = None
        main_model = None
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            aux = cfg.get("auxiliary", {}) if isinstance(cfg, dict) else {}
            compression_config = aux.get("compression", {}) if isinstance(aux, dict) else {}
            model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
            if isinstance(model_cfg, dict):
                main_provider = model_cfg.get("provider")
                main_model = model_cfg.get("default") or model_cfg.get("model")
            elif isinstance(model_cfg, str):
                main_model = model_cfg
        except Exception:
            pass
        try:
            from agent.auxiliary_client import _read_main_model, _read_main_provider
            main_provider = _read_main_provider() or main_provider
            main_model = _read_main_model() or main_model
        except Exception:
            pass
        return build_judge_routes(compression_config, main_provider=main_provider, main_model=main_model)

    def _call_hermes_llm(self, route: JudgeRoute, messages: list[dict[str, str]], temperature: float | None = None, max_tokens: int | None = None) -> Any:
        if self._call_func is not None:
            return self._call_func(route, messages, temperature, max_tokens)
        os.environ["HERMES_HOME"] = str(self.hermes_home)
        _ensure_hermes_import_path()
        from agent.auxiliary_client import call_llm
        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if route.task:
            kwargs["task"] = route.task
        else:
            kwargs["provider"] = route.provider
            kwargs["model"] = route.model
        return call_llm(**kwargs)

    def _messages_for_unit(self, unit: dict[str, Any], signals: list[dict[str, Any]]) -> list[dict[str, str]]:
        payload = build_eval_payload(unit, signals)
        prompt = load_prompt_template()
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2, default=str)},
        ]

    def _repair_messages(self, invalid_output: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "Repair the following evaluator output into exactly one valid JSON object matching schema_version instruction_health_eval_v1. Return JSON only."},
            {"role": "user", "content": invalid_output[:12000]},
        ]

    def evaluate_unit(self, unit: dict[str, Any], signals: list[dict[str, Any]]) -> JudgeResult:
        messages = self._messages_for_unit(unit, signals)
        errors: list[str] = []
        for route in self.resolve_routes():
            raw = ""
            try:
                response = self._call_hermes_llm(route, messages, self.temperature, self.max_tokens)
                raw = _extract_response_text(response)
                try:
                    data = validate_eval_json(extract_json_object(raw))
                except Exception as parse_err:
                    repair_response = self._call_hermes_llm(route, self._repair_messages(raw), self.temperature, self.max_tokens)
                    raw = _extract_response_text(repair_response)
                    data = validate_eval_json(extract_json_object(raw))
                return JudgeResult(
                    eval_data=data,
                    judge_provider=route.name if route.name in {"auxiliary.compression", "main", "auto"} else route.provider,
                    judge_model=route.model or data.get("judge_model"),
                    raw_output=raw,
                )
            except Exception as exc:
                errors.append(f"{route.name}: {exc}")
                continue
        error = "; ".join(errors) or "no judge routes available"
        data = {
            "schema_version": EVAL_SCHEMA_VERSION,
            "health_status": "not_evaluable",
            "confidence": "low",
            "goal_summary": str(unit.get("user_request") or "")[:160],
            "observed_outcome": "The evaluator could not obtain a valid LLM judge response.",
            "primary_reason": f"Evaluator error: {error}"[:500],
            "user_reaction": {"type": "unknown", "used_as_evidence": False, "evidence": ""},
            "barriers": [],
            "prolongation_evidence": {"tool_calls": unit.get("tool_call_count") or 0, "api_calls": unit.get("api_call_count") or 0, "duration_seconds": None, "repeated_actions": []},
            "missed_or_mishandled_requirements": [],
            "not_evaluable_reason": "judge_error",
        }
        return JudgeResult(data, judge_provider=None, judge_model=None, raw_output="", evaluator_error=error)
