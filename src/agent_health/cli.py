from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agent_health.adapters.hermes import HermesAdapter, HermesStateReader, default_hermes_home
from agent_health.tool_outcome_reviewer_model import (
    ToolOutcomeDecision,
    ToolOutcomeModelUnavailable,
    TfidfToolOutcomeReviewerModel,
    smoke_check_tool_outcome_reviewer_model,
    train_tfidf_tool_outcome_reviewer_model,
)
from agent_health.tool_outcome_features import build_tool_outcome_features
from agent_health.tool_outcome_routing import route_tool_outcome_decision
from agent_health.config import init_home, instruction_health_dir
from agent_health.dashboard_plugin import install_dashboard_plugin
from agent_health.db import EvalDB, default_eval_db_path
from agent_health.judge import HermesLLMJudgeClient, TOOL_OUTCOME_PROMPT_VERSION, PROMPT_VERSION, TokenUsage
from agent_health.scheduler import run_due_eval_once, run_review_job
from agent_health.scheduler_bootstrap import DEFAULT_SCHEDULER_POLL_SECONDS, DEFAULT_WATCHDOG_SCHEDULE, install_scheduler_watchdog
from agent_health.signals import extract_case_signals


def _parse_since(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().lower()
    now = time.time()
    if text.endswith("h"):
        return now - float(text[:-1]) * 3600
    if text.endswith("d"):
        return now - float(text[:-1]) * 86400
    return float(text)


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _int_at_least(minimum: int):
    def parse(value: str) -> int:
        parsed = int(value)
        if parsed < minimum:
            raise argparse.ArgumentTypeError(f"must be >= {minimum}")
        return parsed
    return parse


def judge_call_budget(*, limit: int, max_judge_calls: int) -> int:
    return max(0, min(max(0, limit), max(0, max_judge_calls)))


def _signal_map(signals: list[dict]) -> dict[str, dict]:
    return {str(s.get("signal_type")): s for s in signals}


def deterministic_priority_score(signals: list[dict]) -> int:
    """Score how valuable an turn case is to spend judge budget on.

    The score is intentionally heuristic. It prioritizes user-visible evidence
    of trouble first, then concrete trace failures, then prolonged/loopy runs.
    Low/no-signal continuation turns score 0 and are skipped by default.
    """
    by_name = _signal_map(signals)
    score = 0

    reaction = str(by_name.get("reaction", {}).get("signal_value") or "unknown")
    if reaction in {"correction", "complaint", "repeated_request"}:
        score += 100
    elif reaction == "clarification":
        score += 35
    elif reaction == "acceptance":
        score -= 20

    try:
        tool_errors = int(float(by_name.get("tool_error_count", {}).get("signal_value") or 0))
    except ValueError:
        tool_errors = 0
    if tool_errors:
        score += 70 + min(tool_errors, 5) * 10

    try:
        repeats = int(float(by_name.get("same_tool_repeat_count", {}).get("signal_value") or 0))
    except ValueError:
        repeats = 0
    if repeats >= 3:
        score += 55
    elif repeats == 2:
        score += 20

    for name in ("tool_interaction_count", "source_session_api_interaction_count", "turn_duration_seconds"):
        severity = by_name.get(name, {}).get("severity")
        if severity == "high":
            score += 35
        elif severity == "medium":
            score += 20

    return max(score, 0)


def select_priority_cases(
    candidates: list[tuple[dict, list[dict]]],
    *,
    budget: int,
    min_priority_score: int = 1,
) -> list[tuple[dict, list[dict], int]]:
    scored = [(row, signals, deterministic_priority_score(signals)) for row, signals in candidates]
    scored = [item for item in scored if item[2] >= min_priority_score]
    scored.sort(key=lambda item: (-item[2], item[0].get("started_at") or 0, item[0].get("id") or ""))
    return scored[:max(0, budget)]


def cmd_init(args) -> int:
    home = Path(args.hermes_home).expanduser()
    base = init_home(home)
    EvalDB(default_eval_db_path(home)).migrate()
    print(f"Initialized {base}")
    print("Hermes dashboard support is available as an explicit opt-in plugin install.")
    print("Judge provider inherits Hermes routing: auxiliary.approval when configured, then the main provider/model.")
    print("Budget guard defaults: at most 5 judge calls per review run, with a 120-minute cooldown for no-reaction turns.")
    return 0


def cmd_inspect_hermes(args) -> int:
    reader = HermesStateReader(args.hermes_home)
    _print_json(reader.inspect(limit=args.limit))
    return 0


def cmd_import_hermes(args) -> int:
    home = Path(args.hermes_home).expanduser()
    adapter = HermesAdapter(home)
    db = EvalDB(default_eval_db_path(home))
    since = _parse_since(args.since)
    count = 0
    tool_outcome_count = 0
    deleted = 0
    for session_id in adapter.discover_due_sources(since=since, limit=args.limit):
        raw = adapter.load_source(session_id)
        cases = adapter.normalize_turn_cases(raw)
        keep_ids = {unit["id"] for unit in cases}
        for unit in cases:
            db.upsert_turn_case(unit)
            signals = extract_case_signals(unit)
            db.replace_signals(unit["id"], signals)
            count += 1
        for example in adapter.build_tool_outcome_cases(raw):
            db.upsert_tool_outcome_case(example)
            tool_outcome_count += 1
        deleted += db.delete_stale_session_cases(str(session_id), keep_ids)
    suffix = f"; removed {deleted} stale case(s)" if deleted else ""
    print(f"Imported {count} turn cases and {tool_outcome_count} tool outcome cases into {db.path}{suffix}")
    return 0


def cmd_cases(args) -> int:
    if getattr(args, "case_action", None) == "show":
        if not getattr(args, "turn_case_id", None):
            raise SystemExit("cases show requires turn_case_id")
        return cmd_show(args)
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    for unit in db.list_turn_cases(limit=args.limit, since=_parse_since(args.since)):
        request = (unit.get("request_text") or "").replace("\n", " ")[:80]
        print(f"{unit['started_at'] or '-':>12}  {unit['source_session_id']}  turn={unit['turn_index']}  tools={unit['tool_interaction_count']}  reaction={'yes' if unit.get('next_request_text') else 'no'}  {request}")
    return 0


def cmd_tool_outcomes(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    tool_outcomes = db.list_canonical_tool_outcome_cases(limit=args.limit, since=_parse_since(args.since))
    if args.summary:
        latest_reviews: dict[str, int] = {}
        latest_predictions: dict[str, int] = {}
        for tool_outcome in tool_outcomes:
            latest_reviews[str(tool_outcome.get("label") or "unreviewed")] = latest_reviews.get(str(tool_outcome.get("label") or "unreviewed"), 0) + 1
            latest_predictions[str(tool_outcome.get("prediction_label") or "unpredicted")] = latest_predictions.get(str(tool_outcome.get("prediction_label") or "unpredicted"), 0) + 1
        _print_json({"total_tool_outcome_cases": len(tool_outcomes), "latest_reviews": latest_reviews, "latest_predictions": latest_predictions})
        return 0
    for tool_outcome in tool_outcomes:
        tool = f" tool={tool_outcome.get('tool_name')}" if tool_outcome.get("tool_name") else ""
        print(
            f"{tool_outcome.get('result_timestamp') or '-':>12}  label={tool_outcome.get('label') or '-':<13} "
            f"prediction={tool_outcome.get('prediction_label') or '-':<13} {tool_outcome.get('source_session_id')} "
            f"turn={tool_outcome.get('turn_index')}{tool} friction={tool_outcome.get('friction_score') or 0}"
        )
        if args.details:
            request = _one_line(tool_outcome.get("request_text_excerpt"), 110)
            result = _one_line(tool_outcome.get("tool_result"), 180)
            print(f"  request: {request}")
            print(f"  result: {result}")
    return 0


def cmd_signals(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    unit = db.get_turn_case_with_trace(args.turn_case_id)
    signals = extract_case_signals(unit)
    db.replace_signals(args.turn_case_id, signals)
    _print_json(signals)
    return 0


def _tool_outcome_reviewer_model_output_dir(hermes_home: str | Path, model_version: str) -> Path:
    return instruction_health_dir(hermes_home) / "tool-outcome-reviewer-models" / model_version


def _tool_outcome_prediction_payload(decision: ToolOutcomeDecision, *, llm_review_budget_available: bool) -> dict:
    return {
        "label": decision.label,
        "is_tool_outcome": decision.is_tool_outcome,
        "reason_code": decision.reason_code,
        "reason_confidence": decision.reason_confidence,
        "confidence": decision.confidence,
        "uncertainty": decision.uncertainty,
        "decision_source": decision.decision_source,
        "model_name": decision.model_name,
        "model_version": decision.model_version,
        "should_defer_to_llm": decision.should_defer_to_llm,
        "llm_review_budget_available": llm_review_budget_available,
        "budget_fallback": decision.budget_fallback,
        "evidence_json": {"summary": decision.evidence_summary},
    }


def cmd_tool_outcome_cases(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    for case in db.list_tool_outcome_cases(source_session_id=args.source_session_id, since=_parse_since(args.since), limit=args.limit, unlabeled=args.unlabeled, unpredicted=args.unpredicted):
        if args.json:
            _print_json(case)
        else:
            print(
                f"{case['id']} turn_case={case.get('turn_case_id') or '-'} "
                f"tool_interaction={case.get('tool_interaction_id') or '-'} "
                f"tool={case.get('tool_name') or '-'} "
                f"called_at={case.get('called_at') or '-'} completed_at={case.get('result_timestamp') or '-'} "
                f"review={case.get('label') or '-'} prediction={case.get('prediction_label') or '-'}"
            )
    return 0


def cmd_tool_outcome_export_training(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    for row in db.export_tool_outcome_review_training(limit=args.limit):
        print(json.dumps(row, ensure_ascii=False))
    return 0


def cmd_tool_outcome_label(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    tool_outcome_case_id = args.tool_outcome_case_id
    source = "human_correction" if args.correction else "human"
    label_id = db.insert_tool_outcome_review(
        tool_outcome_case_id,
        label=args.label,
        reason_code=args.reason_code,
        reason_confidence=args.confidence,
        label_source=source,
        accepted_for_training=True,
        reviewer=args.reviewer,
        comment=args.comment,
    )
    _print_json({"review_id": label_id, "tool_outcome_case_id": tool_outcome_case_id, "reviewer_type": source})
    return 0


def _latest_prediction_from_tool_outcome_case(example: dict) -> dict | None:
    if not example.get("prediction_label"):
        return None
    evidence = example.get("prediction_evidence_json")
    if isinstance(evidence, str) and evidence:
        try:
            evidence = json.loads(evidence)
        except Exception:
            evidence = {"summary": evidence}
    return {
        "label": example.get("prediction_label"),
        "is_tool_outcome": example.get("prediction_is_tool_outcome"),
        "reason_code": example.get("prediction_reason_code"),
        "reason_confidence": example.get("prediction_reason_confidence"),
        "confidence": example.get("prediction_confidence"),
        "uncertainty": example.get("prediction_uncertainty"),
        "decision_source": example.get("prediction_decision_source"),
        "model_name": example.get("prediction_model_name"),
        "model_version": example.get("prediction_model_version"),
        "should_defer_to_llm": bool(example.get("prediction_should_defer_to_llm")),
        "llm_review_budget_available": None if example.get("prediction_llm_review_budget_available") is None else bool(example.get("prediction_llm_review_budget_available")),
        "budget_fallback": bool(example.get("prediction_budget_fallback")),
        "evidence_json": evidence or {},
    }


def _insert_tool_outcome_judge_label(db: EvalDB, tool_outcome_case_id: str, result) -> int:
    return db.insert_tool_outcome_review(
        tool_outcome_case_id,
        outcome_label=result.eval_data["outcome_label"],
        reason_code=result.eval_data.get("reason_code"),
        confidence=result.eval_data.get("confidence"),
        reviewer_type="automatic_llm",
        reviewer_version=TOOL_OUTCOME_PROMPT_VERSION,
        training_eligible=True,
        evidence_summary=result.eval_data.get("evidence_summary"),
    )


def _chunks(items: list[dict], size: int):
    size = max(1, int(size))
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _label_tool_outcome_cases_with_judge(
    db: EvalDB,
    judge: HermesLLMJudgeClient,
    *,
    limit: int,
    max_judge_calls: int,
    since: float | None = None,
    reevaluate: bool = False,
    dry_run: bool = False,
    batch_size: int = 10,
    retry_failed: bool = True,
    prioritize_prediction_gaps: bool = False,
) -> tuple[int, TokenUsage]:
    examples = db.list_tool_outcome_cases(
        limit=limit,
        since=since,
        unlabeled=not reevaluate,
        prioritize_prediction_gaps=prioritize_prediction_gaps,
        exclude_automatic_case_reviewed=True,
        llm_eligible_only=True,
    )
    remaining_budget = max(0, max_judge_calls)
    count = 0
    total_usage = TokenUsage()
    for batch in _chunks(examples, batch_size):
        if remaining_budget <= 0:
            break
        if dry_run:
            ids = ",".join(str(example["id"]) for example in batch)
            print(f"DRY tool_outcome-batch size={len(batch)} ids={ids}")
            count += len(batch)
            remaining_budget -= 1
            continue
        if len(batch) == 1:
            example = batch[0]
            claim = db.claim_automatic_llm_review(
                "tool_outcome_case",
                str(example.get("id") or ""),
                src="cli.tool_outcomes.llm_review.single",
            )
            if claim is None:
                continue
            claim_id = str(claim["id"])
            if not db.mark_automatic_llm_claim_started(claim_id):
                continue
            try:
                result = judge.evaluate_tool_outcome(example, _latest_prediction_from_tool_outcome_case(example))
            except Exception as exc:
                db.mark_automatic_llm_claim_failed(claim_id, before_call=False, error_message=str(exc))
                raise
            total_usage += result.token_usage
            remaining_budget -= result.token_usage.calls or 1
            if result.evaluator_error:
                db.mark_automatic_llm_claim_failed(claim_id, before_call=False, error_message=result.evaluator_error)
                continue
            _insert_tool_outcome_judge_label(db, example["id"], result)
            db.mark_automatic_llm_claim_review_inserted(claim_id)
            count += 1
            continue

        claimed_batch: list[tuple[dict, dict]] = []
        for example in batch:
            claim = db.claim_automatic_llm_review(
                "tool_outcome_case",
                str(example.get("id") or ""),
                src="cli.tool_outcomes.llm_review.batch",
            )
            if claim is not None:
                claimed_batch.append((example, claim))
        if not claimed_batch:
            continue
        started_batch = [
            (example, claim)
            for example, claim in claimed_batch
            if db.mark_automatic_llm_claim_started(str(claim["id"]))
        ]
        if not started_batch:
            continue
        items = [(example, _latest_prediction_from_tool_outcome_case(example)) for example, _claim in started_batch]
        try:
            batch_result = judge.evaluate_tool_outcomes_batch(items)
        except Exception as exc:
            for _example, claim in started_batch:
                db.mark_automatic_llm_claim_failed(str(claim["id"]), before_call=False, error_message=str(exc))
            raise
        total_usage += batch_result.token_usage
        remaining_budget -= batch_result.token_usage.calls or 1
        labeled_ids: set[str] = set()
        for example, claim in started_batch:
            tool_outcome_case_id = str(example["id"])
            result = batch_result.results.get(tool_outcome_case_id)
            if result is None or result.evaluator_error:
                db.mark_automatic_llm_claim_failed(
                    str(claim["id"]),
                    before_call=False,
                    error_message=result.evaluator_error if result is not None else "missing batch result",
                )
                continue
            _insert_tool_outcome_judge_label(db, tool_outcome_case_id, result)
            db.mark_automatic_llm_claim_review_inserted(str(claim["id"]))
            labeled_ids.add(tool_outcome_case_id)
            count += 1

        failed_examples = [example for example, _claim in started_batch if str(example["id"]) not in labeled_ids]
        if retry_failed and failed_examples and remaining_budget > 0:
            continue
    return count, total_usage


def cmd_tool_outcome_judge_label(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    judge = HermesLLMJudgeClient(home, max_tokens=args.max_tokens)
    count, usage = _label_tool_outcome_cases_with_judge(
        db,
        judge,
        limit=args.limit,
        max_judge_calls=judge_call_budget(limit=args.limit, max_judge_calls=args.max_judge_calls),
        since=_parse_since(args.since),
        reevaluate=args.reevaluate,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        retry_failed=not args.no_retry_failed,
        prioritize_prediction_gaps=args.prioritize_prediction_gaps,
    )
    verb = "Selected" if args.dry_run else "Labeled"
    print(f"{verb} {count} tool outcome case(s). tokens={usage.total_tokens} calls={usage.calls}")
    return 0


def _load_ml_first_tool_outcome_reviewer_model(db: EvalDB, model_path: str | None) -> TfidfToolOutcomeReviewerModel:
    if model_path:
        return TfidfToolOutcomeReviewerModel.load(Path(model_path).expanduser())
    promoted = db.get_promoted_tool_outcome_reviewer_model()
    if not promoted:
        raise ValueError("no promoted tool_outcome model; pass --model")
    return TfidfToolOutcomeReviewerModel.load(promoted["artifact_path"])


def cmd_tool_outcome_predict(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    model = _load_ml_first_tool_outcome_reviewer_model(db, args.model)
    examples = db.list_tool_outcome_cases(limit=args.limit, unpredicted=not args.reevaluate)
    remaining_llm_budget = max(0, args.max_judge_calls)
    judge = HermesLLMJudgeClient(home, max_tokens=args.max_tokens) if args.judge_deferred else None
    count = 0
    for example in examples:
        features = build_tool_outcome_features(example)
        llm_review_budget_available = bool(args.judge_deferred and remaining_llm_budget > 0)
        decision = route_tool_outcome_decision(model.predict(features), llm_review_budget_available=llm_review_budget_available)
        payload = _tool_outcome_prediction_payload(decision, llm_review_budget_available=llm_review_budget_available)
        db.insert_tool_outcome_review(example["id"], **payload)
        if decision.should_defer_to_llm and not decision.budget_fallback and judge is not None and remaining_llm_budget > 0:
            claim = db.claim_automatic_llm_review(
                "tool_outcome_case",
                str(example.get("id") or ""),
                src="cli.tool_outcomes.predict.judge_deferred",
            )
            if claim is None:
                continue
            claim_id = str(claim["id"])
            if not db.mark_automatic_llm_claim_started(claim_id):
                continue
            try:
                result = judge.evaluate_tool_outcome(example, payload)
            except Exception as exc:
                db.mark_automatic_llm_claim_failed(claim_id, before_call=False, error_message=str(exc))
                raise
            remaining_llm_budget -= result.token_usage.calls or 1
            if not result.evaluator_error:
                db.insert_tool_outcome_review(
                    example["id"],
                    outcome_label=result.eval_data["outcome_label"],
                    reason_code=result.eval_data.get("reason_code"),
                    reason_confidence=result.eval_data.get("confidence"),
                    label_source="tool_outcome_llm_reviewer",
                    label_source_version=TOOL_OUTCOME_PROMPT_VERSION,
                    accepted_for_training=True,
                    comment=result.eval_data.get("evidence_summary"),
                )
                db.mark_automatic_llm_claim_review_inserted(claim_id)
            else:
                db.mark_automatic_llm_claim_failed(claim_id, before_call=False, error_message=result.evaluator_error)
        count += 1
    print(f"Predicted {count} tool outcome case(s).")
    return 0


def cmd_tool_outcome_train(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    rows = db.export_tool_outcome_review_training(limit=args.limit)
    model_version = args.model_version or str(int(time.time()))
    try:
        model = train_tfidf_tool_outcome_reviewer_model(rows, model_version=model_version)
    except (ToolOutcomeModelUnavailable, ValueError) as exc:
        print(f"agent-health: error: {exc}", file=sys.stderr)
        return 2
    artifact = model.save(Path(args.output).expanduser() if args.output else _tool_outcome_reviewer_model_output_dir(home, model_version))
    try:
        candidate_ok = smoke_check_tool_outcome_reviewer_model(artifact.artifact_path)
    except Exception as exc:
        print(f"agent-health: error: tool_outcome model smoke-check failed: {exc}", file=sys.stderr)
        return 2
    if not candidate_ok:
        print("agent-health: error: tool_outcome model smoke-check failed", file=sys.stderr)
        return 2
    model_id = db.record_tool_outcome_reviewer_model({
        "model_name": artifact.model_name,
        "model_version": artifact.model_version,
        "artifact_path": artifact.artifact_path,
        "training_record_count": artifact.training_record_count,
        "accepted_label_count": artifact.accepted_label_count,
        "metrics_json": artifact.metrics,
    })
    promoted = False
    current = db.get_promoted_tool_outcome_reviewer_model()
    if args.auto_promote and (current is None or artifact.training_record_count > int(current.get("training_record_count") or 0)):
        db.promote_tool_outcome_reviewer_model(model_id)
        promoted = True
    print(f"Trained tool_outcome model on {artifact.training_record_count} accepted label row(s), wrote {artifact.artifact_path}, promoted={promoted}")
    return 0


def _format_findings(row: dict) -> str:
    findings = row.get("findings") or []
    return ",".join(str(a.get("finding_type") or a.get("type")) for a in findings[:5]) or "-"



def _one_line(value: object, limit: int = 180) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[: limit - 1] + "…" if len(text) > limit else text


def _print_eval_context(unit: dict, eval_data: dict, *, prefix: str = "  ") -> None:
    print(f"{prefix}request: {_one_line(unit.get('request_text'), 220)}")
    if unit.get("next_request_text"):
        print(f"{prefix}next user: {_one_line(unit.get('next_request_text'), 180)}")
    observed = eval_data.get("observed_outcome")
    if observed:
        print(f"{prefix}outcome: {_one_line(observed, 180)}")
    findings = eval_data.get("findings") or []
    for finding in findings[:3]:
        if not isinstance(finding, dict):
            continue
        print(
            f"{prefix}finding: {finding.get('type') or '-'} "
            f"({finding.get('severity') or 'medium'}): {_one_line(finding.get('evidence'), 180)}"
        )


def cmd_eval(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    budget = judge_call_budget(limit=args.limit, max_judge_calls=args.max_judge_calls)
    if budget <= 0:
        print("Judge call budget is 0; no LLM calls will be made.")
        return 0
    due = db.list_due_turn_cases(
        limit=args.limit,
        since=_parse_since(args.since),
        reevaluate=args.reevaluate,
        cooldown_seconds=args.cooldown_minutes * 60,
    )
    if not due:
        if args.reevaluate:
            print("No LLM-eligible turn cases; automatic LLM reviews are never re-run.")
        else:
            print("No due turn cases. Run `agent-health import hermes --since 24h` first, wait for cooldown, or pass --reevaluate.")

    candidates = []
    skipped_load_errors = 0
    for row in due:
        try:
            unit = db.get_turn_case_with_trace(row["id"])
        except KeyError:
            skipped_load_errors += 1
            continue
        signals = extract_case_signals(unit)
        db.replace_signals(unit["id"], signals)
        candidates.append((unit, signals))

    selected = select_priority_cases(candidates, budget=budget, min_priority_score=args.min_priority_score)
    if due and not selected:
        print(
            "No due turn cases passed deterministic prefilter. "
            f"candidate_cases={len(candidates)} min_priority_score={args.min_priority_score}. "
            "Use --min-priority-score 0 to sample low-priority cases."
        )

    judge = HermesLLMJudgeClient(home, max_tokens=args.max_tokens, judgement_threshold=args.judgement_threshold)
    routes = judge.resolve_routes()
    print("Judge route priority: " + " -> ".join(f"{r.name}({r.model or r.provider or 'default'})" for r in routes))
    print(
        f"Budget guard: max_judge_calls={budget}, cooldown_minutes={args.cooldown_minutes}, "
        f"candidate_cases={len(candidates)}, selected_cases={len(selected)}, min_priority_score={args.min_priority_score}, "
        f"judgement_threshold={args.judgement_threshold}"
    )
    if skipped_load_errors:
        print(f"Skipped {skipped_load_errors} candidate case(s) that could not be loaded.")
    count = 0
    total_usage = TokenUsage()
    for unit, signals, priority_score in selected:
        if args.dry_run:
            print(f"DRY {unit['id']} priority={priority_score} signals={len(signals)}")
            count += 1
            continue
        claim = db.claim_automatic_llm_review("turn_case", str(unit.get("id") or ""), src="cli.review")
        if claim is None:
            continue
        claim_id = str(claim["id"])
        if not db.mark_automatic_llm_claim_started(claim_id):
            continue
        try:
            result = judge.evaluate_unit(unit, signals)
            eval_id = db.insert_case_review(
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
        status = result.eval_data.get("outcome_status", "failed")
        confidence = result.eval_data.get("confidence", "low")
        reason = str(result.eval_data.get("summary_reason") or "").replace("\n", " ")[:140]
        err = f" evaluator_error={result.evaluator_error}" if result.evaluator_error else ""
        total_usage += result.token_usage
        print(f"{status:14} {confidence:6} {unit['id']} review={eval_id} tokens={result.token_usage.total_tokens} calls={result.token_usage.calls}{err}  {reason}")
        _print_eval_context(unit, result.eval_data)
        count += 1
    verb = "Selected" if args.dry_run else "Reviewed"
    print(f"{verb} {count} case(s).")
    if not args.dry_run:
        print(
            "Judge tokens: "
            f"prompt={total_usage.prompt_tokens} completion={total_usage.completion_tokens} "
            f"total={total_usage.total_tokens} calls={total_usage.calls}"
        )
        remaining_budget = max(0, budget - total_usage.calls)
        tool_outcome_count, tool_outcome_usage = _label_tool_outcome_cases_with_judge(
            db,
            judge,
            limit=max(remaining_budget, remaining_budget * 10),
            max_judge_calls=remaining_budget,
            since=_parse_since(args.since),
            batch_size=10,
            prioritize_prediction_gaps=True,
        )
        if tool_outcome_count or remaining_budget:
            print(
            "Tool outcome reviews: "
                f"labeled={tool_outcome_count} remaining_start_budget={remaining_budget} "
                f"tokens={tool_outcome_usage.total_tokens} calls={tool_outcome_usage.calls}"
            )
    return 0


def cmd_list(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    statuses = [s.strip() for s in args.status.split(",") if s.strip()] if args.status else None
    for row in db.list_case_reviews(statuses=statuses, limit=args.limit, since=_parse_since(args.since)):
        request = (row.get("request_text") or "").replace("\n", " ")[:80]
        print(f"{row.get('started_at') or '-':>12}  {row['outcome_status']:<12} {row['confidence']:<6} {row['source_session_id']} turn={row['turn_index']} tokens={row.get('review_total_tokens') or 0} findings={_format_findings(row)}  {request}")
        if args.details:
            unit = db.get_turn_case_with_trace(row["turn_case_id"])
            eval_json = row.get("eval_json") if isinstance(row.get("eval_json"), dict) else {}
            if not eval_json:
                try:
                    eval_json = json.loads(row.get("eval_json") or "{}")
                except Exception:
                    eval_json = {}
            _print_eval_context(unit, eval_json)
    return 0


def cmd_show(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    unit = db.get_turn_case_with_trace(args.turn_case_id)
    latest = db.get_latest_case_review(args.turn_case_id)
    signals = extract_case_signals(unit)
    _print_json({"turn_case": unit, "signals": signals, "latest_review": latest})
    return 0


def cmd_summary(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    _print_json(db.summary(since=_parse_since(args.since)))
    return 0


def cmd_dashboard_install(args) -> int:
    home = Path(args.hermes_home).expanduser()
    destination = install_dashboard_plugin(home)
    print(f"Installed Ariadne Eval dashboard plugin to {destination}")
    if not args.no_scheduler_watchdog:
        watchdog = install_scheduler_watchdog(
            home,
            schedule=args.watchdog_schedule,
            poll_seconds=args.scheduler_poll_seconds,
        )
        print(f"Installed Ariadne Eval scheduler watchdog script to {watchdog.script_path}")
        if watchdog.job_registered:
            action = "Created" if watchdog.job_created else "Updated"
            print(f"{action} Hermes cron watchdog job {watchdog.job_id} ({args.watchdog_schedule}).")
        else:
            print(
                "Warning: could not register Hermes cron watchdog job automatically: "
                f"{watchdog.error}. Run `agent-health --hermes-home "
                f"{home} scheduler run --poll-seconds {args.scheduler_poll_seconds}` "
                "from your process supervisor if scheduled evals should execute."
            )
    else:
        print(
            "Skipped scheduler watchdog install. Scheduled evals require a running "
            "`agent-health scheduler run` process."
        )
    print("Open Hermes dashboard and use the Ariadne Eval tab after restarting/reloading the dashboard server.")
    return 0


def _schedule_update_from_args(args) -> dict:
    updates = {}
    if getattr(args, "enabled", False):
        updates["enabled"] = True
    if getattr(args, "disabled", False):
        updates["enabled"] = False
    if getattr(args, "every", None) is not None:
        updates["schedule_kind"] = "interval"
        updates["interval_seconds"] = args.every
    if getattr(args, "continuous", False):
        updates["schedule_kind"] = "continuous"
    if getattr(args, "no_gap", False):
        updates["no_gap"] = True
    if getattr(args, "idle_backoff", None) is not None:
        updates["idle_backoff_seconds"] = args.idle_backoff
    if getattr(args, "import_since", None) is not None:
        updates["import_since"] = _parse_since(args.import_since)
    if getattr(args, "import_overlap", None) is not None:
        updates["import_overlap_seconds"] = args.import_overlap
    if getattr(args, "max_judge_calls", None) is not None:
        updates["max_judge_calls"] = args.max_judge_calls
    if getattr(args, "max_review_total_tokens", None) is not None:
        updates["max_review_total_tokens"] = args.max_review_total_tokens
    if getattr(args, "max_tokens", None) is not None:
        updates["max_tokens_per_call"] = args.max_tokens
    if getattr(args, "candidate_limit", None) is not None:
        updates["candidate_limit"] = args.candidate_limit
    if getattr(args, "cooldown_minutes", None) is not None:
        updates["cooldown_minutes"] = args.cooldown_minutes
    if getattr(args, "min_priority_score", None) is not None:
        updates["min_priority_score"] = args.min_priority_score
    if getattr(args, "judgement_threshold", None) is not None:
        updates["judgement_threshold"] = args.judgement_threshold
    return updates


def cmd_scheduler_tick(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    results = run_due_eval_once(db, home)
    _print_json({"runs": results, "count": len(results)})
    return 0


def cmd_scheduler_run(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    while True:
        results = run_due_eval_once(db, home)
        if results:
            _print_json({"runs": results, "count": len(results)})
        time.sleep(max(0.1, float(args.poll_seconds)))


def cmd_schedule_list(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    for task in db.list_review_jobs():
        print(f"{task['id']} enabled={task['enabled']} kind={task['schedule_kind']} next_due_at={task.get('next_due_at')} version={task['config_version']}")
    return 0


def cmd_schedule_show(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    _print_json(db.get_review_job(args.task))
    return 0


def cmd_schedule_set(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    task = db.upsert_review_job(args.task, _schedule_update_from_args(args))
    _print_json(task)
    return 0


def cmd_schedule_run_now(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    task = db.get_review_job(args.task)
    task = db.upsert_review_job(task["id"], {"enabled": True, "next_due_at": time.time()})
    _print_json(task)
    return 0


def cmd_schedule_pause(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    _print_json(db.upsert_review_job(args.task, {"enabled": False}))
    return 0


def cmd_schedule_resume(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    _print_json(db.upsert_review_job(args.task, {"enabled": True, "next_due_at": time.time()}))
    return 0


def cmd_schedule_runs(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    task_id = db.get_review_job(args.task)["id"] if args.task else None
    _print_json(db.list_review_runs(task_id=task_id, limit=args.limit))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-health", description="Local Hermes turn-case reviewer")
    parser.add_argument("--hermes-home", default=str(default_hermes_home()))
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.set_defaults(func=cmd_init)

    p_inspect = sub.add_parser("inspect")
    inspect_sub = p_inspect.add_subparsers(dest="framework", required=True)
    p_inspect_hermes = inspect_sub.add_parser("hermes")
    p_inspect_hermes.add_argument("--limit", type=int, default=5)
    p_inspect_hermes.set_defaults(func=cmd_inspect_hermes)

    p_import = sub.add_parser("import")
    import_sub = p_import.add_subparsers(dest="framework", required=True)
    p_import_hermes = import_sub.add_parser("hermes")
    p_import_hermes.add_argument("--since")
    p_import_hermes.add_argument("--limit", type=int, default=100)
    p_import_hermes.set_defaults(func=cmd_import_hermes)

    p_cases = sub.add_parser("cases")
    p_cases.add_argument("--since")
    p_cases.add_argument("--limit", type=int, default=50)
    p_cases.add_argument("case_action", nargs="?", choices=["show"])
    p_cases.add_argument("turn_case_id", nargs="?")
    p_cases.set_defaults(func=cmd_cases)

    p_tool_outcome = sub.add_parser("tool-outcomes", help="ML-first tool-call tool outcome cases, reviews, predictions, and models")
    tool_outcome_sub = p_tool_outcome.add_subparsers(dest="tool_outcome_command", required=True)
    p_tool_outcome_cases = tool_outcome_sub.add_parser("cases", help="List normalized tool-call tool outcome cases")
    p_tool_outcome_cases.add_argument("--source-session-id")
    p_tool_outcome_cases.add_argument("--since")
    p_tool_outcome_cases.add_argument("--limit", type=int, default=50)
    p_tool_outcome_cases.add_argument("--unlabeled", action="store_true")
    p_tool_outcome_cases.add_argument("--unpredicted", action="store_true")
    p_tool_outcome_cases.add_argument("--json", action="store_true")
    p_tool_outcome_cases.set_defaults(func=cmd_tool_outcome_cases)
    p_tool_outcome_export = tool_outcome_sub.add_parser("export-training", help="Export accepted tool outcome review rows as JSONL")
    p_tool_outcome_export.add_argument("--limit", type=int, default=10000)
    p_tool_outcome_export.set_defaults(func=cmd_tool_outcome_export_training)
    p_tool_outcome_label = tool_outcome_sub.add_parser("review", help="Insert a human tool outcome review")
    p_tool_outcome_label.add_argument("--tool-outcome-case-id", required=True)
    p_tool_outcome_label.add_argument("--label", choices=["ok", "problem", "unsure"], required=True)
    p_tool_outcome_label.add_argument("--reason-code", choices=["execution_error", "empty_output", "invalid_tool_input", "wrong_or_bad_output", "other"])
    p_tool_outcome_label.add_argument("--confidence", type=float)
    p_tool_outcome_label.add_argument("--correction", action="store_true")
    p_tool_outcome_label.add_argument("--reviewer", default="user")
    p_tool_outcome_label.add_argument("--comment")
    p_tool_outcome_label.set_defaults(func=cmd_tool_outcome_label)
    p_tool_outcome_judge = tool_outcome_sub.add_parser("llm-review", help="Label tool outcome cases with the tool_outcome-specific LLM judge")
    p_tool_outcome_judge.add_argument("--since")
    p_tool_outcome_judge.add_argument("--limit", type=int, default=10)
    p_tool_outcome_judge.add_argument("--max-judge-calls", type=int, default=5)
    p_tool_outcome_judge.add_argument("--batch-size", type=_int_at_least(1), default=10, help="Tool outcome cases per LLM call; failed/missing automatic LLM batch rows are not retried one-by-one")
    p_tool_outcome_judge.add_argument("--no-retry-failed", action="store_true", help="Deprecated safety no-op; automatic LLM batch failures are never retried one-by-one")
    p_tool_outcome_judge.add_argument("--prioritize-prediction-gaps", action="store_true", help="Prioritize budget fallback, deferred, missing, and low-confidence predictions")
    p_tool_outcome_judge.add_argument("--reevaluate", action="store_true", help="Revisit non-automatic review state only; automatic LLM reviews and claims are never re-run by this command")
    p_tool_outcome_judge.add_argument("--dry-run", action="store_true")
    p_tool_outcome_judge.add_argument("--max-tokens", type=int, default=1600)
    p_tool_outcome_judge.set_defaults(func=cmd_tool_outcome_judge_label)
    p_tool_outcome_predict = tool_outcome_sub.add_parser("predict", help="Predict stored tool outcome cases with the promoted ML-first tool_outcome model")
    p_tool_outcome_predict.add_argument("--model")
    p_tool_outcome_predict.add_argument("--limit", type=int, default=50)
    p_tool_outcome_predict.add_argument("--reevaluate", action="store_true", help="Revisit non-automatic prediction state only; automatic LLM reviews and claims are never re-run by this command")
    p_tool_outcome_predict.add_argument("--judge-deferred", action="store_true")
    p_tool_outcome_predict.add_argument("--max-judge-calls", type=int, default=0)
    p_tool_outcome_predict.add_argument("--max-tokens", type=int, default=900)
    p_tool_outcome_predict.set_defaults(func=cmd_tool_outcome_predict)
    p_tool_outcome_train = tool_outcome_sub.add_parser("train-reviewer", help="Train and optionally auto-promote an ML-first tool outcome model from accepted reviews")
    p_tool_outcome_train.add_argument("--limit", type=int, default=10000)
    p_tool_outcome_train.add_argument("--model-version")
    p_tool_outcome_train.add_argument("--output")
    p_tool_outcome_train.add_argument("--auto-promote", dest="auto_promote", action="store_true", default=True)
    p_tool_outcome_train.add_argument("--no-auto-promote", dest="auto_promote", action="store_false")
    p_tool_outcome_train.set_defaults(func=cmd_tool_outcome_train)


    p_signals = sub.add_parser("case-signals")
    p_signals.add_argument("turn_case_id")
    p_signals.set_defaults(func=cmd_signals)

    p_eval = sub.add_parser("review")
    p_eval.add_argument("--due", action="store_true", help="Review due imported cases (default behavior for V1)")
    p_eval.add_argument("--since")
    p_eval.add_argument("--limit", type=int, default=10, help="Candidate cases to consider this run; default is intentionally small")
    p_eval.add_argument("--max-judge-calls", type=int, default=5, help="Hard cap on LLM judge calls for this command invocation")
    p_eval.add_argument("--cooldown-minutes", type=int, default=120, help="Wait this long before judging last turns without next-user reaction evidence")
    p_eval.add_argument("--min-priority-score", type=int, default=1, help="Skip due cases below this deterministic priority score; use 0 to sample low-priority cases")
    p_eval.add_argument("--reevaluate", action="store_true", help="Revisit non-automatic review state only; automatic LLM reviews and claims are never re-run by this command")
    p_eval.add_argument("--judgement-threshold", choices=["strict", "balanced", "relaxed"], default="strict", help="How much evidence the judge needs before marking a finding; strict focuses on concrete trace/assistant evidence")
    p_eval.add_argument("--dry-run", action="store_true", help="Show due cases without calling the judge model")
    p_eval.add_argument("--max-tokens", type=int, default=1200)
    p_eval.set_defaults(func=cmd_eval)

    p_reviews = sub.add_parser("reviews")
    reviews_sub = p_reviews.add_subparsers(dest="reviews_command", required=True)
    p_list = reviews_sub.add_parser("list")
    p_list.add_argument("--status", default="failed,mishandled,prolonged")
    p_list.add_argument("--since")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--details", action="store_true", help="Show request, next-user reaction, outcome, and finding evidence below each row")
    p_list.set_defaults(func=cmd_list)

    p_summary = reviews_sub.add_parser("summary")
    p_summary.add_argument("--since")
    p_summary.set_defaults(func=cmd_summary)

    p_dashboard = sub.add_parser("dashboard", help="Install or manage the Hermes dashboard tab")
    dashboard_sub = p_dashboard.add_subparsers(dest="dashboard_command", required=True)
    p_dashboard_install = dashboard_sub.add_parser("install", help="Install the Ariadne Eval tab into $HERMES_HOME/plugins")
    p_dashboard_install.add_argument(
        "--no-scheduler-watchdog",
        action="store_true",
        help="Only install the dashboard tab; do not install the Hermes cron watchdog that keeps scheduled reviews running",
    )
    p_dashboard_install.add_argument("--watchdog-schedule", default=DEFAULT_WATCHDOG_SCHEDULE)
    p_dashboard_install.add_argument("--scheduler-poll-seconds", type=float, default=DEFAULT_SCHEDULER_POLL_SECONDS)
    p_dashboard_install.set_defaults(func=cmd_dashboard_install)

    p_scheduler = sub.add_parser("scheduler", help="Run recurring review job scheduler")
    scheduler_sub = p_scheduler.add_subparsers(dest="scheduler_command", required=True)
    p_scheduler_tick = scheduler_sub.add_parser("tick", help="Run one scheduler tick for due tasks")
    p_scheduler_tick.set_defaults(func=cmd_scheduler_tick)
    p_scheduler_run = scheduler_sub.add_parser("run", help="Poll for due recurring review jobs")
    p_scheduler_run.add_argument("--poll-seconds", type=float, default=DEFAULT_SCHEDULER_POLL_SECONDS)
    p_scheduler_run.set_defaults(func=cmd_scheduler_run)

    p_schedule = sub.add_parser("review-jobs", help="Manage recurring review jobs")
    schedule_sub = p_schedule.add_subparsers(dest="review_jobs_command", required=True)
    p_schedule_list = schedule_sub.add_parser("list")
    p_schedule_list.set_defaults(func=cmd_schedule_list)
    p_schedule_show = schedule_sub.add_parser("show")
    p_schedule_show.add_argument("task")
    p_schedule_show.set_defaults(func=cmd_schedule_show)
    p_schedule_set = schedule_sub.add_parser("set")
    p_schedule_set.add_argument("task")
    p_schedule_set.add_argument("--enabled", action="store_true")
    p_schedule_set.add_argument("--disabled", action="store_true")
    p_schedule_set.add_argument("--every", type=_int_at_least(1))
    p_schedule_set.add_argument("--continuous", action="store_true")
    p_schedule_set.add_argument("--no-gap", action="store_true")
    p_schedule_set.add_argument("--idle-backoff", type=_int_at_least(1))
    p_schedule_set.add_argument("--import-since")
    p_schedule_set.add_argument("--import-overlap", type=_int_at_least(0))
    p_schedule_set.add_argument("--max-judge-calls", type=_int_at_least(0))
    p_schedule_set.add_argument("--max-judge-total-tokens", dest="max_review_total_tokens", type=_int_at_least(0))
    p_schedule_set.add_argument("--max-tokens", type=_int_at_least(1))
    p_schedule_set.add_argument("--candidate-limit", type=_int_at_least(1))
    p_schedule_set.add_argument("--cooldown-minutes", type=_int_at_least(0))
    p_schedule_set.add_argument("--min-priority-score", type=_int_at_least(0))
    p_schedule_set.add_argument("--judgement-threshold", choices=["strict", "balanced", "relaxed"])
    p_schedule_set.set_defaults(func=cmd_schedule_set)
    p_schedule_run_now = schedule_sub.add_parser("run-now")
    p_schedule_run_now.add_argument("task")
    p_schedule_run_now.set_defaults(func=cmd_schedule_run_now)
    p_schedule_pause = schedule_sub.add_parser("pause")
    p_schedule_pause.add_argument("task")
    p_schedule_pause.set_defaults(func=cmd_schedule_pause)
    p_schedule_resume = schedule_sub.add_parser("resume")
    p_schedule_resume.add_argument("task")
    p_schedule_resume.set_defaults(func=cmd_schedule_resume)
    p_schedule_runs = schedule_sub.add_parser("runs")
    p_schedule_runs.add_argument("task", nargs="?")
    p_schedule_runs.add_argument("--limit", type=int, default=20)
    p_schedule_runs.set_defaults(func=cmd_schedule_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"agent-health: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
