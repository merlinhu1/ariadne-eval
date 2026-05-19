from __future__ import annotations

import re

_CORRECTION = ["no", "not what", "you didn't", "you did not", "that's wrong", "that is wrong", "actually", "i meant"]
_COMPLAINT = ["terrible", "why", "too hard", "too complicated", "not useful", "useless"]
_ACCEPTANCE = ["thanks", "thank you", "great", "that works", "yes", "perfect", "awesome", "nice"]
_SCOPE_CHANGE = ["now ", "next ", "also ", "can we add", "add tests", "another"]
_CLARIFICATION = ["what i mean", "to clarify", "i mean", "could you clarify"]


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in {"can", "you", "the", "and", "for", "that", "this"}}


def classify_reaction(text: str | None, previous_request: str | None = None) -> str:
    if not text or not text.strip():
        return "none"
    lowered = text.lower().strip()

    if re.search(r"\bno\b", lowered) or any(pattern in lowered for pattern in _CORRECTION if pattern != "no"):
        return "correction"
    if any(pattern in lowered for pattern in _COMPLAINT):
        return "complaint"
    if any(pattern in lowered for pattern in _ACCEPTANCE):
        return "acceptance"
    if any(pattern in lowered for pattern in _CLARIFICATION):
        return "clarification"
    if any(pattern in lowered for pattern in _SCOPE_CHANGE):
        return "scope_change"
    if previous_request:
        prev = _tokens(previous_request)
        cur = _tokens(text)
        if prev and len(prev & cur) / max(len(prev), 1) >= 0.6:
            return "repeated_request"
    return "continuation"
