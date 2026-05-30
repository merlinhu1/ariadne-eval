from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_health.adapters.hermes import HermesAdapter
from agent_health.db import EvalDB
from agent_health.judge import HermesLLMJudgeClient, TOOL_OUTCOME_PROMPT_VERSION, PROMPT_VERSION, TokenUsage
from agent_health.signals import extract_case_signals


@dataclass
class EvalRunBudget:
    max_judge_calls: int
    max_review_total_tokens: int | None = None
    calls_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    stop_reason: str | None = None

    def _usage_calls(self, usage: TokenUsage | None = None) -> int:
        return max(1, int((usage.calls if usage else 0) or 0))

    def can_spend(self, usage: TokenUsage | None = None) -> bool:
        calls = self._usage_calls(usage)
        if self.calls_used + calls > max(0, int(self.max_judge_calls)):
            self.stop_reason = "budget_exhausted"
            return False
        projected_tokens = self.total_tokens + int((usage.total_tokens if usage else 0) or 0)
        if self.max_review_total_tokens is not None and projected_tokens > max(0, int(self.max_review_total_tokens)):
            self.stop_reason = "budget_exhausted"
            return False
        return True

    def debit(self, usage: TokenUsage | None) -> None:
        usage = usage or TokenUsage()
        calls = self._usage_calls(usage)
        self.calls_used += calls
        self.prompt_tokens += int(usage.prompt_tokens or 0)
        self.completion_tokens += int(usage.completion_tokens or 0)
        self.total_tokens += int(usage.total_tokens or 0)
        if self.calls_used >= max(0, int(self.max_judge_calls)):
            self.stop_reason = "budget_exhausted"
        if self.max_review_total_tokens is not None and self.total_tokens >= max(0, int(self.max_review_total_tokens)):
            self.stop_reason = "budget_exhausted"

    def metrics(self) -> dict[str, int]:
        return {
            "llm_review_calls_used": self.calls_used,
            "review_prompt_tokens": self.prompt_tokens,
            "review_completion_tokens": self.completion_tokens,
            "review_total_tokens": self.total_tokens,
        }


def deterministic_priority_score(signals: list[dict[str, Any]]) -> int:
    by_name = {str(s.get("signal_type")): s for s in signals}
    score = 0
    reaction = str(by_name.get("reaction", {}).get("signal_value") or "unknown")
    if reaction in {"correction", "complaint", "repeated_request"}:
        score += 100
    elif reaction == "clarification":
        score += 35
    elif reaction == "acceptance":
        score -= 20
    for name, base in (("tool_error_count", 70), ("same_tool_repeat_count", 30)):
        try:
            count = int(float(by_name.get(name, {}).get("signal_value") or 0))
        except ValueError:
            count = 0
        if count:
            score += base + min(count, 5) * 10
    for name in ("tool_interaction_count", "source_session_api_interaction_count", "turn_duration_seconds"):
        severity = by_name.get(name, {}).get("severity")
        if severity == "high":
            score += 35
        elif severity == "medium":
            score += 20
    return max(score, 0)


def select_priority_cases(candidates: list[tuple[dict[str, Any], list[dict[str, Any]]]], *, budget: int, min_priority_score: int) -> list[tuple[dict[str, Any], list[dict[str, Any]], int]]:
    scored = [(row, signals, deterministic_priority_score(signals)) for row, signals in candidates]
    scored = [item for item in scored if item[2] >= min_priority_score]
    scored.sort(key=lambda item: (-item[2], item[0].get("started_at") or 0, item[0].get("id") or ""))
    return scored[:max(0, int(budget))]


def schedule_next_task_run(task: dict[str, Any], *, finished_at: float, backlog_remaining: bool, stop_reason: str) -> float | None:
    kind = str(task.get("schedule_kind") or "interval")
    no_gap = bool(task.get("no_gap")) or kind == "continuous"
    if no_gap:
        if backlog_remaining or stop_reason == "budget_exhausted":
            return finished_at
        return finished_at + max(1, int(task.get("idle_backoff_seconds") or 300))
    if kind == "interval":
        return finished_at + max(1, int(task.get("interval_seconds") or 3600))
    if kind == "cron":
        return None
    return finished_at + max(1, int(task.get("idle_backoff_seconds") or 300))


def import_hermes_for_task(db: EvalDB, hermes_home: str | Path, task: dict[str, Any]) -> int:
    adapter = HermesAdapter(hermes_home)
    since = task.get("import_since")
    cursor = db.get_review_job_cursor(task["id"], "hermes", "import")
    cursor_latest: float | None = None
    cursor_latest_ids: set[str] = set()
    overlap = max(0, int(task.get("import_overlap_seconds") or 0))
    if isinstance(cursor, dict) and cursor.get("latest_started_at") is not None:
        cursor_latest = float(cursor["latest_started_at"])
        if isinstance(cursor.get("latest_session_ids"), list):
            cursor_latest_ids = {str(value) for value in cursor["latest_session_ids"]}
        cursor_since = cursor_latest - overlap
        since = max(cursor_since, float(since or 0)) if since is not None else cursor_since
    count = 0
    latest = since
    latest_session_ids = set(cursor_latest_ids if cursor_latest is not None and latest == cursor_latest else ())
    candidate_limit = max(1, int(task.get("candidate_limit") or 10))
    discovery_limit = candidate_limit + (len(cursor_latest_ids) if overlap == 0 else 0)
    for session_id in adapter.discover_due_sources(since=since, limit=discovery_limit, oldest_first=True):
        raw = adapter.load_source(session_id)
        session_started = raw.get("session", {}).get("started_at")
        if overlap == 0 and cursor_latest is not None and session_started is not None and float(session_started) == cursor_latest and str(session_id) in cursor_latest_ids:
            continue
        if count >= candidate_limit:
            break
        cases = adapter.normalize_turn_cases(raw)
        keep_ids = {unit["id"] for unit in cases}
        for unit in cases:
            db.upsert_turn_case(unit)
            db.replace_signals(unit["id"], extract_case_signals(unit))
            count += 1
            started = unit.get("started_at")
            if started is not None:
                started_float = float(started)
                if latest is None or started_float > float(latest):
                    latest = started_float
                    latest_session_ids = {str(session_id)}
                elif started_float == float(latest):
                    latest_session_ids.add(str(session_id))
        for example in adapter.build_tool_outcome_cases(raw):
            db.upsert_tool_outcome_case(example)
        db.delete_stale_session_cases(str(session_id), keep_ids)
    if latest is not None:
        db.set_review_job_cursor(task["id"], "hermes", "import", {"latest_started_at": latest, "latest_session_ids": sorted(latest_session_ids)})
    return count


def run_review_job(db: EvalDB, hermes_home: str | Path, task_id: str, *, lease_owner: str = "agent-health-scheduler", lease_seconds: int = 300, now: float | None = None) -> dict[str, Any] | None:
    now = time.time() if now is None else now
    run = db.claim_review_job(task_id, lease_owner=lease_owner, lease_seconds=lease_seconds, now=now)
    if run is None:
        return None
    task = dict(run.get("effective_params_json") or {})
    task.setdefault("id", run["task_id"])
    budget = EvalRunBudget(
        max_judge_calls=int(task.get("max_judge_calls") or 0),
        max_review_total_tokens=task.get("max_review_total_tokens"),
    )
    metrics: dict[str, Any] = {"imported_cases": 0, "selected_cases": 0, "reviewed_cases": 0, "tool_outcome_reviews": 0}
    stop_reason = "completed"
    backlog_remaining = False
    try:
        metrics["imported_cases"] = import_hermes_for_task(db, hermes_home, task)
        db.heartbeat_review_run(run["id"], lease_seconds=lease_seconds)
        due = db.list_due_turn_cases(
            limit=max(1, int(task.get("candidate_limit") or 10)),
            since=task.get("import_since"),
            cooldown_seconds=float(task.get("cooldown_minutes") or 120) * 60,
        )
        if not due:
            stop_reason = "no_due"
        candidates = []
        for row in due:
            db.heartbeat_review_run(run["id"], lease_seconds=lease_seconds)
            unit = db.get_turn_case_with_trace(row["id"])
            signals = extract_case_signals(unit)
            db.replace_signals(unit["id"], signals)
            candidates.append((unit, signals))
        selected = select_priority_cases(candidates, budget=budget.max_judge_calls, min_priority_score=int(task.get("min_priority_score") or 1))
        metrics["selected_cases"] = len(selected)
        judge = HermesLLMJudgeClient(hermes_home, max_tokens=int(task.get("max_tokens_per_call") or 1200), judgement_threshold=str(task.get("judgement_threshold") or "strict"))
        for unit, signals, _score in selected:
            if not budget.can_spend():
                stop_reason = "budget_exhausted"
                break
            db.heartbeat_review_run(run["id"], lease_seconds=lease_seconds)
            claim = db.claim_automatic_llm_review(
                "turn_case",
                str(unit.get("id") or ""),
                run_id=str(run.get("id") or ""),
                src="scheduler.review.turn_case",
            )
            if claim is None:
                continue
            claim_id = str(claim["id"])
            if not db.mark_automatic_llm_claim_started(claim_id):
                continue
            try:
                result = judge.evaluate_unit(unit, signals)
                budget.debit(result.token_usage)
                db.insert_case_review(
                    unit["id"],
                    prompt_version=PROMPT_VERSION,
                    judge_provider=result.judge_provider,
                    judge_model=result.judge_model,
                    eval_data=result.eval_data,
                    evaluator_error=result.evaluator_error,
                    review_prompt_tokens=result.token_usage.prompt_tokens,
                    review_completion_tokens=result.token_usage.completion_tokens,
                    review_total_tokens=result.token_usage.total_tokens,
                    judge_call_count=result.token_usage.calls,
                )
            except Exception as exc:
                db.mark_automatic_llm_claim_failed(claim_id, before_call=False, error_message=str(exc))
                raise
            db.mark_automatic_llm_claim_review_inserted(claim_id)
            metrics["reviewed_cases"] += 1
        for example in db.list_tool_outcome_cases(
            limit=max(1, int(task.get("candidate_limit") or 10)),
            unlabeled=True,
            exclude_automatic_case_reviewed=True,
            llm_eligible_only=True,
        ):
            if not budget.can_spend():
                stop_reason = "budget_exhausted"
                break
            db.heartbeat_review_run(run["id"], lease_seconds=lease_seconds)
            claim = db.claim_automatic_llm_review(
                "tool_outcome_case",
                str(example.get("id") or ""),
                run_id=str(run.get("id") or ""),
                src="scheduler.review.tool_outcome_case",
            )
            if claim is None:
                continue
            claim_id = str(claim["id"])
            if not db.mark_automatic_llm_claim_started(claim_id):
                continue
            try:
                result = judge.evaluate_tool_outcome(example)
                budget.debit(result.token_usage)
            except Exception as exc:
                db.mark_automatic_llm_claim_failed(claim_id, before_call=False, error_message=str(exc))
                raise
            if not result.evaluator_error:
                db.insert_tool_outcome_review(
                    example["id"],
                    outcome_label=result.eval_data["outcome_label"],
                    reason_code=result.eval_data.get("reason_code"),
                    confidence=result.eval_data.get("confidence"),
                    reviewer_type="automatic_llm",
                    reviewer_version=TOOL_OUTCOME_PROMPT_VERSION,
                    training_eligible=True,
                    evidence_summary=result.eval_data.get("evidence_summary"),
                )
                db.mark_automatic_llm_claim_review_inserted(claim_id)
                metrics["tool_outcome_reviews"] += 1
            else:
                db.mark_automatic_llm_claim_failed(claim_id, before_call=False, error_message=result.evaluator_error)
        if budget.stop_reason == "budget_exhausted":
            stop_reason = "budget_exhausted"
        backlog_remaining = len(due) > metrics["reviewed_cases"]
        metrics.update(budget.metrics())
        finished_at = time.time()
        next_due = schedule_next_task_run(task, finished_at=finished_at, backlog_remaining=backlog_remaining, stop_reason=stop_reason)
        return db.finish_review_run(run["id"], status="succeeded", stop_reason=stop_reason, next_due_at=next_due, metrics=metrics, now=finished_at)
    except Exception as exc:
        finished_at = time.time()
        next_due = schedule_next_task_run(task, finished_at=finished_at, backlog_remaining=backlog_remaining, stop_reason="error")
        metrics.update(budget.metrics())
        return db.finish_review_run(run["id"], status="failed", stop_reason="error", next_due_at=next_due, metrics=metrics, error=str(exc), now=finished_at)


def run_due_eval_once(db: EvalDB, hermes_home: str | Path, *, lease_owner: str = "agent-health-scheduler", now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    results = []
    for task in db.list_due_review_jobs(now=now):
        result = run_review_job(db, hermes_home, task["id"], lease_owner=lease_owner, now=now)
        if result is not None:
            results.append(result)
    return results
