from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROMPT_VERSION = "turn_case_review_v1"
EVAL_SCHEMA_VERSION = "turn_case_review_v1"
TOOL_OUTCOME_PROMPT_VERSION = "tool_outcome_review_v1"
TOOL_OUTCOME_REVIEW_SCHEMA_VERSION = "tool_outcome_review_v1"

HEALTH_STATUSES = {"succeed", "failed", "mishandled", "prolonged"}
CONFIDENCES = {"high", "medium", "low"}
REACTION_TYPES = {"acceptance", "continuation", "clarification", "correction", "complaint", "repeated_request", "scope_change", "unrelated", "unknown", "none"}
FINDING_TYPES = {
    "tool_error", "tool_timeout", "permission_denied", "approval_denied",
    "operation_cancelled", "rate_limited", "network_failure", "resource_exhausted",
    "dependency_missing", "path_not_found", "test_failure", "quality_gate_failure",
    "git_rejected", "repeated_tool_loop", "unnecessary_tool_use", "missing_tool_use",
    "bad_tool_selection", "external_action_not_verified", "action_misrepresentation",
    "misread_instruction", "missed_requirement", "unsupported_claim", "format_mismatch",
    "vague_or_incomplete_response", "over_refusal", "under_clarification",
    "user_correction", "user_repeated_request", "interrupted_or_incomplete",
    "excessive_duration", "excessive_api_calls", "excessive_tool_calls", "context_loss",
}
TOOL_OUTCOME_LABELS = {"problem", "ok", "unsure"}
TOOL_OUTCOME_REASON_CODES = {"execution_error", "empty_output", "invalid_tool_input", "wrong_or_bad_output", "other"}



@dataclass(frozen=True)
class JudgeRoute:
    name: str
    task: str | None
    provider: str | None
    model: str | None


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            calls=self.calls + other.calls,
        )


@dataclass
class JudgeResult:
    eval_data: dict[str, Any]
    judge_provider: str | None
    judge_model: str | None
    raw_output: str
    evaluator_error: str | None = None
    token_usage: TokenUsage = TokenUsage()


@dataclass
class JudgeBatchResult:
    results: dict[str, JudgeResult]
    missing_example_ids: list[str]
    judge_provider: str | None
    judge_model: str | None
    raw_output: str
    evaluator_error: str | None = None
    token_usage: TokenUsage = TokenUsage()


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _auxiliary_config_is_real(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict) or not config:
        return False
    for key in ("provider", "model", "base_url", "api_key", "api_mode"):
        value = _non_empty(config.get(key))
        if value and not (key == "provider" and value.lower() == "auto"):
            return True
    return False


def build_judge_routes(
    approval_config: dict[str, Any] | None,
    *,
    main_provider: str | None,
    main_model: str | None,
) -> list[JudgeRoute]:
    """Return judge model routes in Ariadne's V1 priority order.

    Priority is intentionally inherited from Hermes: first use the user's
    configured ``auxiliary.approval`` model/provider if one exists, then fall
    back to the configured main provider/model.  The caller executes each route
    with Hermes' own auxiliary runtime so auth, custom providers, OAuth adapters,
    and provider quirks stay centralized in Hermes.
    """
    routes: list[JudgeRoute] = []
    approval_config = approval_config if isinstance(approval_config, dict) else {}
    if _auxiliary_config_is_real(approval_config):
        routes.append(JudgeRoute(
            name="auxiliary.approval",
            task="approval",
            provider=_non_empty(approval_config.get("provider")) or "auto",
            model=_non_empty(approval_config.get("model")),
        ))
    provider = _non_empty(main_provider)
    model = _non_empty(main_model)
    if provider or model:
        routes.append(JudgeRoute(name="main", task=None, provider=provider, model=model))
    if not routes:
        routes.append(JudgeRoute(name="auto", task="approval", provider="auto", model=None))
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


def _extract_token_usage(response: Any, *, calls: int = 1) -> TokenUsage:
    usage = None
    if isinstance(response, dict):
        usage = response.get("usage")
    else:
        usage = getattr(response, "usage", None)
    if not usage:
        return TokenUsage(calls=calls)
    def get(name: str) -> int:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        try:
            return int(value or 0)
        except Exception:
            return 0
    prompt_tokens = get("prompt_tokens") or get("input_tokens")
    completion_tokens = get("completion_tokens") or get("output_tokens")
    total_tokens = get("total_tokens") or (prompt_tokens + completion_tokens)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        calls=calls,
    )


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
    deleted_fields = {"not_evaluable_reason", "request_smoothness", "smoothness_score"}
    present_deleted = sorted(field for field in deleted_fields if field in normalized)
    if present_deleted:
        raise ValueError(f"deleted request eval fields are not accepted: {', '.join(present_deleted)}")
    status = normalized.get("outcome_status")
    if status not in HEALTH_STATUSES:
        raise ValueError(f"invalid outcome_status {status!r}")
    confidence = normalized.get("confidence")
    if confidence not in CONFIDENCES:
        raise ValueError(f"invalid confidence {confidence!r}")
    summary_reason = _non_empty(normalized.get("summary_reason"))
    if not summary_reason:
        raise ValueError("summary_reason is required")
    normalized["summary_reason"] = summary_reason
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
    if "friction_score" not in normalized:
        raise ValueError("friction_score is required")
    try:
        friction = float(normalized["friction_score"])
    except (TypeError, ValueError) as exc:
        raise ValueError("friction_score must be between 0 and 1") from exc
    if friction < 0.0 or friction > 1.0:
        raise ValueError("friction_score must be between 0 and 1")
    normalized["friction_score"] = friction
    findings = normalized.get("findings")
    if not isinstance(findings, list):
        findings = []
    cleaned_findings = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_type = str(finding.get("finding_type") or "").strip()
        if finding_type not in FINDING_TYPES:
            continue
        severity = str(finding.get("severity") or "medium").strip().lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        cleaned_findings.append({
            "finding_type": finding_type,
            "severity": severity,
            "evidence_source": str(finding.get("evidence_source") or "trace"),
            "evidence_text": str(finding.get("evidence_text") or ""),
            "case_event_id": str(finding.get("case_event_id") or "") or None,
        })
    normalized["findings"] = cleaned_findings
    normalized.pop("barriers", None)
    normalized.setdefault("prolongation_evidence", {"tool_calls": 0, "api_calls": 0, "duration_seconds": None, "repeated_actions": []})
    normalized.setdefault("missed_or_mishandled_requirements", [])
    return normalized


def validate_tool_outcome_eval_json(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized.setdefault("schema_version", TOOL_OUTCOME_REVIEW_SCHEMA_VERSION)
    if normalized["schema_version"] != TOOL_OUTCOME_REVIEW_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {normalized['schema_version']!r}")
    label = str(normalized.get("outcome_label") or "").strip()
    if label not in TOOL_OUTCOME_LABELS:
        raise ValueError(f"invalid tool_outcome label {label!r}")
    reason_code = normalized.get("reason_code")
    if reason_code in ("", None):
        reason_code = None
    else:
        reason_code = str(reason_code).strip()
    if reason_code == "null":
        reason_code = None
    elif reason_code is not None and reason_code not in TOOL_OUTCOME_REASON_CODES:
        raise ValueError(f"invalid tool_outcome reason_code {reason_code!r}")
    confidence = float(normalized.get("confidence") or 0.0)
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError("tool_outcome confidence must be between 0 and 1")
    evidence = normalized.get("evidence_summary")
    if not str(evidence or "").strip():
        raise ValueError("evidence_summary is required")
    return {
        "schema_version": TOOL_OUTCOME_REVIEW_SCHEMA_VERSION,
        "outcome_label": label,
        "reason_code": reason_code,
        "confidence": confidence,
        "evidence_summary": str(evidence),
    }


def validate_tool_outcome_batch_eval_json(data: dict[str, Any], *, expected_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Validate a batched tool_outcome judge response and key results by example id."""
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("tool_outcome batch judge response requires a results list")
    expected = {str(example_id) for example_id in expected_ids}
    normalized: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("tool_outcome batch result rows must be JSON objects")
        example_id = str(item.get("tool_outcome_case_id") or "").strip()
        if not example_id:
            raise ValueError("tool_outcome batch result missing tool_outcome_case_id")
        if example_id not in expected:
            continue
        payload = dict(item)
        payload.setdefault("schema_version", TOOL_OUTCOME_REVIEW_SCHEMA_VERSION)
        normalized[example_id] = validate_tool_outcome_eval_json(payload)
    return normalized


def _signal_map(signals: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(s.get("signal_type")): s.get("signal_value") for s in signals}


FIELD_LIMITS = {
    "prior_context_summary": 2500,
    "request_text": 3000,
    "response_text": 4000,
    "next_request_text": 1000,
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
    events = unit.get("case_events") or []
    tool_sequence = []
    for idx, event in enumerate(events[:40], start=1):
        tool_sequence.append({
            "index": idx,
            "tool_name": event.get("tool_name"),
            "input_summary": preflight_trim_text(event.get("input_preview"), limit=FIELD_LIMITS["tool_args"], label="tool input"),
            "output_summary": preflight_trim_text(event.get("output_preview"), limit=FIELD_LIMITS["tool_result"], label="tool output"),
            "error": bool(event.get("output_error")),
            "duration_ms": event.get("duration_ms"),
        })
    return {
        "tool_sequence": tool_sequence,
        "case_signals": signals,
        "timing": {
            "turn_duration_seconds": _signal_map(signals).get("turn_duration_seconds"),
            "source_session_api_interaction_count": unit.get("source_session_api_interaction_count"),
            "tool_interaction_count": unit.get("tool_interaction_count"),
        },
        "next_user_reaction": {
            "text": unit.get("next_request_text"),
        },
    }


JUDGEMENT_THRESHOLDS = {
    "strict": {
        "level": "strict",
        "policy": (
            "Require concrete evidence from the trace between the request and response before assigning failed, mishandled, or prolonged. "
            "Do not treat natural follow-up, setup questions, new instructions, document uploads, or ambiguous continuation as findings by themselves. "
            "A next-user message is supporting evidence only when it explicitly corrects/complains/repeats the same request and is consistent with trace or assistant-response evidence. "
            "Prefer succeed when the response reasonably handled the request and no concrete failure/mishandling/prolongation evidence is visible."
        ),
    },
    "balanced": {
        "level": "balanced",
        "policy": (
            "Use both trace evidence and user reaction. Do not mark natural follow-ups or new requests as failures, but allow explicit correction/complaint to support an finding "
            "when it matches the assistant response or trace."
        ),
    },
    "relaxed": {
        "level": "relaxed",
        "policy": (
            "Flag likely friction even when evidence is indirect, but still separate natural follow-up and scope change from real agent failure."
        ),
    },
}


def judgement_threshold_policy(level: str | None) -> dict[str, str]:
    key = str(level or "balanced").strip().lower().replace("_", "-")
    if key in {"conservative", "high", "hard"}:
        key = "strict"
    elif key in {"normal", "medium", "standard"}:
        key = "balanced"
    elif key in {"low", "loose"}:
        key = "relaxed"
    return dict(JUDGEMENT_THRESHOLDS.get(key, JUDGEMENT_THRESHOLDS["balanced"]))


def build_eval_payload(unit: dict[str, Any], signals: list[dict[str, Any]], *, judgement_threshold: str | None = "strict") -> dict[str, Any]:
    return {
        "turn_case_id": unit.get("id"),
        "framework": unit.get("framework"),
        "session": {
            "source_session_id": unit.get("source_session_id"),
            "turn_index": unit.get("turn_index"),
            "source": unit.get("source"),
            "model": unit.get("model"),
            "title": unit.get("title"),
        },
        "prior_context_summary": preflight_trim_text(
            unit.get("prior_context_summary"),
            limit=FIELD_LIMITS["prior_context_summary"],
            label="previous context",
        ),
        "request_text": preflight_trim_text(unit.get("request_text"), limit=FIELD_LIMITS["request_text"], label="user request"),
        "response_text": preflight_trim_text(
            unit.get("response_text"),
            limit=FIELD_LIMITS["response_text"],
            label="assistant response",
        ),
        "next_request_text": preflight_trim_text(
            unit.get("next_request_text"),
            limit=FIELD_LIMITS["next_request_text"],
            label="next user reaction",
        ),
        "preflight_trim_policy": {
            "large_documents": "trimmed to short excerpts",
            "large_code_blocks": "trimmed to leading lines plus omitted-character note",
            "images_and_data_urls": "omitted unless represented by surrounding text",
            "field_limits_chars": FIELD_LIMITS,
        },
        "judgement_threshold": judgement_threshold_policy(judgement_threshold),
        "trace_summary": build_trace_summary(unit, signals),
    }


def build_tool_outcome_judge_payload(example: dict[str, Any], prediction: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "tool_outcome_case_id": example.get("id"),
        "schema_version": TOOL_OUTCOME_REVIEW_SCHEMA_VERSION,
        "source": {
            "framework": example.get("framework"),
            "source_session_id": example.get("source_session_id"),
            "turn_index": example.get("turn_index"),
            "assistant_tool_call_message_id": example.get("assistant_tool_call_message_id"),
            "result_message_id": example.get("result_message_id"),
            "tool_call_id": example.get("tool_call_id"),
        },
        "tool_call": {
            "tool_name": example.get("tool_name"),
            "tool_arguments": preflight_trim_text(example.get("tool_arguments"), limit=FIELD_LIMITS["tool_args"], label="tool args"),
            "immediate_tool_result": preflight_trim_text(example.get("tool_result"), limit=FIELD_LIMITS["tool_result"], label="tool result"),
        },
        "visible_context": {
            "request_text_excerpt": preflight_trim_text(example.get("request_text_excerpt"), limit=1200, label="user request"),
            "prior_assistant_visible_text": preflight_trim_text(example.get("prior_assistant_visible_text"), limit=900, label="prior assistant"),
            "following_assistant_visible_text": preflight_trim_text(example.get("following_assistant_visible_text"), limit=900, label="following assistant"),
        },
        "ml_prediction": prediction or {},
    }
    if example.get("explicit_caller_expectation"):
        payload["explicit_caller_expectation"] = example.get("explicit_caller_expectation")
    if example.get("explicit_caller_interpretation"):
        payload["explicit_caller_interpretation"] = example.get("explicit_caller_interpretation")
    return payload


def build_tool_outcome_batch_judge_payload(items: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    return {
        "schema_version": "tool_outcome_batch_eval_v1",
        "examples": [build_tool_outcome_judge_payload(example, prediction) for example, prediction in items],
        "expected_output": {
            "schema_version": "tool_outcome_batch_eval_v1",
            "results": [
                {
                    "tool_outcome_case_id": "copy from input tool_outcome_case_id",
                    "outcome_label": "ok|problem|unsure",
                    "reason_code": "execution_error|empty_output|invalid_tool_input|wrong_or_bad_output|other|null",
                    "confidence": 0.0,
                    "evidence_summary": "short visible evidence summary",
                }
            ],
        },
    }


def load_tool_outcome_prompt_template() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "tool_outcome_review.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Evaluate one tool-call tool_outcome example. Return strict JSON matching tool_outcome_review_v1."


def load_tool_outcome_batch_prompt_template() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "tool_outcome_review_batch.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Evaluate each tool-call tool_outcome example independently. Return strict JSON matching tool_outcome_batch_eval_v1."


def load_prompt_template() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "turn_case_review_v1.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "You are evaluating one AI agent turn. Return strict JSON matching turn_case_review_v1."


class HermesLLMJudgeClient:
    def __init__(
        self,
        hermes_home: str | Path,
        *,
        routes: list[JudgeRoute] | None = None,
        call_func: Callable[[JudgeRoute, list[dict[str, str]], float | None, int | None], Any] | None = None,
        max_tokens: int = 1200,
        temperature: float | None = 0,
        judgement_threshold: str | None = "strict",
    ):
        self.hermes_home = Path(hermes_home).expanduser()
        self._routes = routes
        self._call_func = call_func
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.judgement_threshold = judgement_threshold

    def resolve_routes(self) -> list[JudgeRoute]:
        if self._routes is not None:
            return self._routes
        os.environ["HERMES_HOME"] = str(self.hermes_home)
        _ensure_hermes_import_path()
        approval_config: dict[str, Any] = {}
        main_provider = None
        main_model = None
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            aux = cfg.get("auxiliary", {}) if isinstance(cfg, dict) else {}
            approval_config = aux.get("approval", {}) if isinstance(aux, dict) else {}
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
        return build_judge_routes(approval_config, main_provider=main_provider, main_model=main_model)

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
        payload = build_eval_payload(unit, signals, judgement_threshold=self.judgement_threshold)
        prompt = load_prompt_template()
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2, default=str)},
        ]

    def _messages_for_tool_outcome(self, example: dict[str, Any], prediction: dict[str, Any] | None = None) -> list[dict[str, str]]:
        payload = build_tool_outcome_judge_payload(example, prediction)
        return [
            {"role": "system", "content": load_tool_outcome_prompt_template()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2, default=str)},
        ]

    def _messages_for_tool_outcome_batch(self, items: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> list[dict[str, str]]:
        payload = build_tool_outcome_batch_judge_payload(items)
        return [
            {"role": "system", "content": load_tool_outcome_batch_prompt_template()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2, default=str)},
        ]

    def _repair_messages(self, invalid_output: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "Repair the following evaluator output into exactly one valid JSON object matching schema_version turn_case_review_v1. Use `findings` for judge findings. Return JSON only."},
            {"role": "user", "content": invalid_output[:12000]},
        ]

    def _tool_outcome_repair_messages(self, invalid_output: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "Repair the following tool_outcome evaluator output into exactly one valid JSON object matching schema_version tool_outcome_review_v1. Return JSON only."},
            {"role": "user", "content": invalid_output[:12000]},
        ]

    def _tool_outcome_batch_repair_messages(self, invalid_output: str, expected_ids: list[str]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "Repair the following tool_outcome batch evaluator output into exactly one valid JSON object with schema_version tool_outcome_batch_eval_v1 and a results array. Return JSON only."},
            {"role": "user", "content": json.dumps({"expected_tool_outcome_case_ids": expected_ids, "invalid_output": invalid_output[:12000]}, ensure_ascii=False)},
        ]

    def evaluate_unit(self, unit: dict[str, Any], signals: list[dict[str, Any]]) -> JudgeResult:
        messages = self._messages_for_unit(unit, signals)
        errors: list[str] = []
        for route in self.resolve_routes():
            raw = ""
            token_usage = TokenUsage()
            try:
                response = self._call_hermes_llm(route, messages, self.temperature, self.max_tokens)
                token_usage += _extract_token_usage(response)
                raw = _extract_response_text(response)
                try:
                    data = validate_eval_json(extract_json_object(raw))
                except Exception as parse_err:
                    repair_response = self._call_hermes_llm(route, self._repair_messages(raw), self.temperature, self.max_tokens)
                    token_usage += _extract_token_usage(repair_response)
                    raw = _extract_response_text(repair_response)
                    data = validate_eval_json(extract_json_object(raw))
                return JudgeResult(
                    eval_data=data,
                    judge_provider=route.name if route.name in {"auxiliary.approval", "main", "auto"} else route.provider,
                    judge_model=route.model or data.get("judge_model"),
                    raw_output=raw,
                    token_usage=token_usage,
                )
            except Exception as exc:
                errors.append(f"{route.name}: {exc}")
                continue
        error = "; ".join(errors) or "no judge routes available"
        data = {
            "schema_version": EVAL_SCHEMA_VERSION,
            "outcome_status": "failed",
            "confidence": "low",
            "goal_summary": str(unit.get("request_text") or "")[:160],
            "observed_outcome": "The evaluator could not obtain a valid LLM judge response.",
            "summary_reason": f"Evaluator error: {error}"[:500],
            "user_reaction": {"type": "unknown", "used_as_evidence": False, "evidence": ""},
            "findings": [],
        "prolongation_evidence": {"tool_calls": unit.get("tool_interaction_count") or 0, "api_calls": unit.get("source_session_api_interaction_count") or 0, "duration_seconds": None, "repeated_actions": []},
        "missed_or_mishandled_requirements": [],
        "friction_score": 1.0,
    }
        return JudgeResult(data, judge_provider=None, judge_model=None, raw_output="", evaluator_error=error)

    def evaluate_tool_outcome(self, example: dict[str, Any], prediction: dict[str, Any] | None = None) -> JudgeResult:
        messages = self._messages_for_tool_outcome(example, prediction)
        errors: list[str] = []
        for route in self.resolve_routes():
            raw = ""
            token_usage = TokenUsage()
            try:
                response = self._call_hermes_llm(route, messages, self.temperature, self.max_tokens)
                token_usage += _extract_token_usage(response)
                raw = _extract_response_text(response)
                try:
                    data = validate_tool_outcome_eval_json(extract_json_object(raw))
                except Exception:
                    repair_response = self._call_hermes_llm(route, self._tool_outcome_repair_messages(raw), self.temperature, self.max_tokens)
                    token_usage += _extract_token_usage(repair_response)
                    raw = _extract_response_text(repair_response)
                    data = validate_tool_outcome_eval_json(extract_json_object(raw))
                return JudgeResult(
                    eval_data=data,
                    judge_provider=route.name if route.name in {"auxiliary.approval", "main", "auto"} else route.provider,
                    judge_model=route.model or data.get("judge_model"),
                    raw_output=raw,
                    token_usage=token_usage,
                )
            except Exception as exc:
                errors.append(f"{route.name}: {exc}")
                continue
        error = "; ".join(errors) or "no judge routes available"
        data = {
            "schema_version": TOOL_OUTCOME_REVIEW_SCHEMA_VERSION,
            "outcome_label": "unsure",
            "reason_code": None,
            "confidence": 0.0,
            "evidence_summary": f"Evaluator error: {error}"[:500],
        }
        return JudgeResult(data, judge_provider=None, judge_model=None, raw_output="", evaluator_error=error)

    def evaluate_tool_outcomes_batch(self, items: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> JudgeBatchResult:
        if not items:
            return JudgeBatchResult({}, [], judge_provider=None, judge_model=None, raw_output="", token_usage=TokenUsage(calls=0))
        messages = self._messages_for_tool_outcome_batch(items)
        expected_ids = [str(example.get("id")) for example, _prediction in items]
        errors: list[str] = []
        for route in self.resolve_routes():
            raw = ""
            token_usage = TokenUsage()
            try:
                response = self._call_hermes_llm(route, messages, self.temperature, self.max_tokens)
                token_usage += _extract_token_usage(response)
                raw = _extract_response_text(response)
                try:
                    data_by_id = validate_tool_outcome_batch_eval_json(extract_json_object(raw), expected_ids=expected_ids)
                except Exception:
                    repair_response = self._call_hermes_llm(route, self._tool_outcome_batch_repair_messages(raw, expected_ids), self.temperature, self.max_tokens)
                    token_usage += _extract_token_usage(repair_response)
                    raw = _extract_response_text(repair_response)
                    data_by_id = validate_tool_outcome_batch_eval_json(extract_json_object(raw), expected_ids=expected_ids)
                provider = route.name if route.name in {"auxiliary.approval", "main", "auto"} else route.provider
                results = {
                    example_id: JudgeResult(
                        eval_data=data,
                        judge_provider=provider,
                        judge_model=route.model or data.get("judge_model"),
                        raw_output=raw,
                        token_usage=TokenUsage(calls=0),
                    )
                    for example_id, data in data_by_id.items()
                }
                missing = [example_id for example_id in expected_ids if example_id not in results]
                return JudgeBatchResult(
                    results=results,
                    missing_example_ids=missing,
                    judge_provider=provider,
                    judge_model=route.model,
                    raw_output=raw,
                    token_usage=token_usage,
                )
            except Exception as exc:
                errors.append(f"{route.name}: {exc}")
                continue
        error = "; ".join(errors) or "no judge routes available"
        return JudgeBatchResult(
            results={},
            missing_example_ids=expected_ids,
            judge_provider=None,
            judge_model=None,
            raw_output="",
            evaluator_error=error,
            token_usage=TokenUsage(calls=0),
        )
