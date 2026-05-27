from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agent_health.adapters.hermes import HermesAdapter, HermesStateReader, default_hermes_home
from agent_health.incident_model import (
    IncidentDecision,
    IncidentModelUnavailable,
    TfidfIncidentModel,
    smoke_check_incident_model,
    train_tfidf_incident_model,
)
from agent_health.incident_features import build_incident_features
from agent_health.incident_routing import route_incident_decision
from agent_health.config import init_home, instruction_health_dir
from agent_health.dashboard_plugin import install_dashboard_plugin
from agent_health.db import EvalDB, default_eval_db_path
from agent_health.judge import HermesLLMJudgeClient, INCIDENT_PROMPT_VERSION, PROMPT_VERSION, TokenUsage
from agent_health.scheduler import run_due_eval_once, run_eval_task
from agent_health.signals import extract_deterministic_signals


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
    return {str(s.get("signal_name")): s for s in signals}


def deterministic_priority_score(signals: list[dict]) -> int:
    """Score how valuable an eval unit is to spend judge budget on.

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

    for name in ("tool_call_count", "api_call_count", "turn_duration_seconds"):
        severity = by_name.get(name, {}).get("severity")
        if severity == "high":
            score += 35
        elif severity == "medium":
            score += 20

    return max(score, 0)


def select_priority_units(
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
    print("Budget guard defaults: at most 5 judge calls per eval run, with a 120-minute cooldown for no-reaction turns.")
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
    incident_count = 0
    deleted = 0
    for session_id in adapter.discover_due_sources(since=since, limit=args.limit):
        raw = adapter.load_source(session_id)
        units = adapter.normalize_eval_units(raw)
        keep_ids = {unit["id"] for unit in units}
        for unit in units:
            db.upsert_eval_unit(unit)
            signals = extract_deterministic_signals(unit)
            db.replace_signals(unit["id"], signals)
            count += 1
        for example in adapter.normalize_incident_examples(raw):
            db.upsert_incident_example(example)
            incident_count += 1
        deleted += db.delete_stale_session_units(str(session_id), keep_ids)
    suffix = f"; removed {deleted} stale unit(s)" if deleted else ""
    print(f"Imported {count} eval units and {incident_count} incident examples into {db.path}{suffix}")
    return 0


def cmd_units(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    for unit in db.list_units(limit=args.limit, since=_parse_since(args.since)):
        request = (unit.get("user_request") or "").replace("\n", " ")[:80]
        print(f"{unit['started_at'] or '-':>12}  {unit['source_session_id']}  turn={unit['source_turn_index']}  tools={unit['tool_call_count']}  reaction={'yes' if unit.get('next_user_reaction_text') else 'no'}  {request}")
    return 0


def cmd_incidents(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    incidents = db.list_canonical_incident_examples(limit=args.limit, since=_parse_since(args.since))
    if args.summary:
        labels: dict[str, int] = {}
        predictions: dict[str, int] = {}
        for incident in incidents:
            labels[str(incident.get("label") or "unlabeled")] = labels.get(str(incident.get("label") or "unlabeled"), 0) + 1
            predictions[str(incident.get("prediction_label") or "unpredicted")] = predictions.get(str(incident.get("prediction_label") or "unpredicted"), 0) + 1
        _print_json({"total_incident_examples": len(incidents), "labels": labels, "predictions": predictions})
        return 0
    for incident in incidents:
        tool = f" tool={incident.get('tool_name')}" if incident.get("tool_name") else ""
        print(
            f"{incident.get('result_timestamp') or '-':>12}  label={incident.get('label') or '-':<13} "
            f"prediction={incident.get('prediction_label') or '-':<13} {incident.get('source_session_id')} "
            f"turn={incident.get('source_turn_index')}{tool} friction={incident.get('request_friction_score') or 0}"
        )
        if args.details:
            request = _one_line(incident.get("user_request_excerpt"), 110)
            result = _one_line(incident.get("tool_result"), 180)
            print(f"  request: {request}")
            print(f"  result: {result}")
    return 0


def cmd_signals(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    unit = db.get_unit_with_trace(args.eval_unit_id)
    signals = extract_deterministic_signals(unit)
    db.replace_signals(args.eval_unit_id, signals)
    _print_json(signals)
    return 0


def _incident_model_output_dir(hermes_home: str | Path, model_version: str) -> Path:
    return instruction_health_dir(hermes_home) / "incident-models" / model_version


def _incident_prediction_payload(decision: IncidentDecision, *, llm_budget_available: bool) -> dict:
    return {
        "label": decision.label,
        "is_incident": decision.is_incident,
        "reason_code": decision.reason_code,
        "reason_confidence": decision.reason_confidence,
        "confidence": decision.confidence,
        "uncertainty": decision.uncertainty,
        "decision_source": decision.decision_source,
        "model_name": decision.model_name,
        "model_version": decision.model_version,
        "should_defer_to_llm": decision.should_defer_to_llm,
        "llm_budget_available": llm_budget_available,
        "budget_fallback": decision.budget_fallback,
        "evidence_json": {"summary": decision.evidence_summary},
    }


def cmd_incident_examples(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    for example in db.list_incident_examples(source_session_id=args.source_session_id, since=_parse_since(args.since), limit=args.limit, unlabeled=args.unlabeled, unpredicted=args.unpredicted):
        _print_json(example) if args.json else print(f"{example['id']} session={example['source_session_id']} tool={example.get('tool_name') or '-'} result={example['result_message_id']}")
    return 0


def cmd_incident_export_training(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    for row in db.export_accepted_incident_training(limit=args.limit):
        print(json.dumps(row, ensure_ascii=False))
    return 0


def cmd_incident_label(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    if args.example_id:
        example_id = args.example_id
    else:
        example = db.find_incident_example_by_source_key(
            source_session_id=args.source_session_id,
            assistant_tool_call_message_id=args.assistant_tool_call_message_id,
            result_message_id=args.result_message_id,
            tool_call_id=args.tool_call_id,
        )
        if example is None:
            raise KeyError("incident example source key not found")
        example_id = example["id"]
    source = "human_correction" if args.correction else "human"
    label_id = db.insert_incident_label(
        example_id,
        label=args.label,
        reason_code=args.reason_code,
        reason_confidence=args.confidence,
        label_source=source,
        accepted_for_training=True,
        reviewer=args.reviewer,
        comment=args.comment,
    )
    _print_json({"label_id": label_id, "example_id": example_id, "label_source": source})
    return 0


def _latest_prediction_from_incident_example(example: dict) -> dict | None:
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
        "is_incident": example.get("prediction_is_incident"),
        "reason_code": example.get("prediction_reason_code"),
        "reason_confidence": example.get("prediction_reason_confidence"),
        "confidence": example.get("prediction_confidence"),
        "uncertainty": example.get("prediction_uncertainty"),
        "decision_source": example.get("prediction_decision_source"),
        "model_name": example.get("prediction_model_name"),
        "model_version": example.get("prediction_model_version"),
        "should_defer_to_llm": bool(example.get("prediction_should_defer_to_llm")),
        "llm_budget_available": None if example.get("prediction_llm_budget_available") is None else bool(example.get("prediction_llm_budget_available")),
        "budget_fallback": bool(example.get("prediction_budget_fallback")),
        "evidence_json": evidence or {},
    }


def _insert_incident_judge_label(db: EvalDB, example_id: str, result) -> int:
    return db.insert_incident_label(
        example_id,
        label=result.eval_data["label"],
        reason_code=result.eval_data.get("reason_code"),
        reason_confidence=result.eval_data.get("confidence"),
        label_source="incident_llm_judge",
        label_source_version=INCIDENT_PROMPT_VERSION,
        accepted_for_training=True,
        comment=result.eval_data.get("evidence_summary"),
    )


def _chunks(items: list[dict], size: int):
    size = max(1, int(size))
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _label_incident_examples_with_judge(
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
    examples = db.list_incident_examples(
        limit=limit,
        since=since,
        unlabeled=not reevaluate,
        prioritize_prediction_gaps=prioritize_prediction_gaps,
    )
    remaining_budget = max(0, max_judge_calls)
    count = 0
    total_usage = TokenUsage()
    for batch in _chunks(examples, batch_size):
        if remaining_budget <= 0:
            break
        if dry_run:
            ids = ",".join(str(example["id"]) for example in batch)
            print(f"DRY incident-batch size={len(batch)} ids={ids}")
            count += len(batch)
            remaining_budget -= 1
            continue
        if len(batch) == 1:
            example = batch[0]
            result = judge.evaluate_incident(example, _latest_prediction_from_incident_example(example))
            total_usage += result.token_usage
            remaining_budget -= result.token_usage.calls or 1
            if result.evaluator_error:
                continue
            _insert_incident_judge_label(db, example["id"], result)
            count += 1
            continue

        items = [(example, _latest_prediction_from_incident_example(example)) for example in batch]
        batch_result = judge.evaluate_incidents_batch(items)
        total_usage += batch_result.token_usage
        remaining_budget -= batch_result.token_usage.calls or 1
        labeled_ids: set[str] = set()
        for example in batch:
            example_id = str(example["id"])
            result = batch_result.results.get(example_id)
            if result is None or result.evaluator_error:
                continue
            _insert_incident_judge_label(db, example_id, result)
            labeled_ids.add(example_id)
            count += 1

        failed_examples = [example for example in batch if str(example["id"]) not in labeled_ids]
        if retry_failed and failed_examples and remaining_budget > 0:
            for example in failed_examples:
                if remaining_budget <= 0:
                    break
                result = judge.evaluate_incident(example, _latest_prediction_from_incident_example(example))
                total_usage += result.token_usage
                remaining_budget -= result.token_usage.calls or 1
                if result.evaluator_error:
                    continue
                _insert_incident_judge_label(db, example["id"], result)
                count += 1
    return count, total_usage


def cmd_incident_judge_label(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    judge = HermesLLMJudgeClient(home, max_tokens=args.max_tokens)
    count, usage = _label_incident_examples_with_judge(
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
    print(f"{verb} {count} incident example(s). tokens={usage.total_tokens} calls={usage.calls}")
    return 0


def _load_ml_first_incident_model(db: EvalDB, model_path: str | None) -> TfidfIncidentModel:
    if model_path:
        return TfidfIncidentModel.load(Path(model_path).expanduser())
    promoted = db.get_promoted_incident_model()
    if not promoted:
        raise ValueError("no promoted incident model; pass --model")
    return TfidfIncidentModel.load(promoted["artifact_path"])


def cmd_incident_predict(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    model = _load_ml_first_incident_model(db, args.model)
    examples = db.list_incident_examples(limit=args.limit, unpredicted=not args.reevaluate)
    remaining_llm_budget = max(0, args.max_judge_calls)
    judge = HermesLLMJudgeClient(home, max_tokens=args.max_tokens) if args.judge_deferred else None
    count = 0
    for example in examples:
        features = build_incident_features(example)
        llm_budget_available = bool(args.judge_deferred and remaining_llm_budget > 0)
        decision = route_incident_decision(model.predict(features), llm_budget_available=llm_budget_available)
        payload = _incident_prediction_payload(decision, llm_budget_available=llm_budget_available)
        db.insert_incident_prediction(example["id"], **payload)
        if decision.should_defer_to_llm and not decision.budget_fallback and judge is not None and remaining_llm_budget > 0:
            result = judge.evaluate_incident(example, payload)
            remaining_llm_budget -= result.token_usage.calls or 1
            if not result.evaluator_error:
                db.insert_incident_label(
                    example["id"],
                    label=result.eval_data["label"],
                    reason_code=result.eval_data.get("reason_code"),
                    reason_confidence=result.eval_data.get("confidence"),
                    label_source="incident_llm_judge",
                    label_source_version=INCIDENT_PROMPT_VERSION,
                    accepted_for_training=True,
                    comment=result.eval_data.get("evidence_summary"),
                )
        count += 1
    print(f"Predicted {count} incident example(s).")
    return 0


def cmd_incident_train(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    rows = db.export_accepted_incident_training(limit=args.limit)
    model_version = args.model_version or str(int(time.time()))
    try:
        model = train_tfidf_incident_model(rows, model_version=model_version)
    except (IncidentModelUnavailable, ValueError) as exc:
        print(f"agent-health: error: {exc}", file=sys.stderr)
        return 2
    artifact = model.save(Path(args.output).expanduser() if args.output else _incident_model_output_dir(home, model_version))
    try:
        candidate_ok = smoke_check_incident_model(artifact.artifact_path)
    except Exception as exc:
        print(f"agent-health: error: incident model smoke-check failed: {exc}", file=sys.stderr)
        return 2
    if not candidate_ok:
        print("agent-health: error: incident model smoke-check failed", file=sys.stderr)
        return 2
    model_id = db.record_incident_model({
        "model_name": artifact.model_name,
        "model_version": artifact.model_version,
        "artifact_path": artifact.artifact_path,
        "training_record_count": artifact.training_record_count,
        "accepted_label_count": artifact.accepted_label_count,
        "metrics_json": artifact.metrics,
    })
    promoted = False
    current = db.get_promoted_incident_model()
    if args.auto_promote and (current is None or artifact.training_record_count > int(current.get("training_record_count") or 0)):
        db.promote_incident_model(model_id)
        promoted = True
    print(f"Trained incident model on {artifact.training_record_count} accepted label row(s), wrote {artifact.artifact_path}, promoted={promoted}")
    return 0


def _format_anomalies(row: dict) -> str:
    anomalies = row.get("anomalies") or []
    return ",".join(str(a.get("anomaly_type") or a.get("type")) for a in anomalies[:5]) or "-"



def _one_line(value: object, limit: int = 180) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[: limit - 1] + "…" if len(text) > limit else text


def _print_eval_context(unit: dict, eval_data: dict, *, prefix: str = "  ") -> None:
    print(f"{prefix}request: {_one_line(unit.get('user_request'), 220)}")
    if unit.get("next_user_reaction_text"):
        print(f"{prefix}next user: {_one_line(unit.get('next_user_reaction_text'), 180)}")
    observed = eval_data.get("observed_outcome")
    if observed:
        print(f"{prefix}outcome: {_one_line(observed, 180)}")
    anomalies = eval_data.get("anomalies") or []
    for anomaly in anomalies[:3]:
        if not isinstance(anomaly, dict):
            continue
        print(
            f"{prefix}anomaly: {anomaly.get('type') or '-'} "
            f"({anomaly.get('severity') or 'medium'}): {_one_line(anomaly.get('evidence'), 180)}"
        )


def cmd_eval(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    budget = judge_call_budget(limit=args.limit, max_judge_calls=args.max_judge_calls)
    if budget <= 0:
        print("Judge call budget is 0; no LLM calls will be made.")
        return 0
    due = db.list_due_units(
        limit=args.limit,
        since=_parse_since(args.since),
        reevaluate=args.reevaluate,
        cooldown_seconds=args.cooldown_minutes * 60,
    )
    if not due:
        print("No due eval units. Run `agent-health import hermes --since 24h` first, wait for cooldown, or pass --reevaluate.")

    candidates = []
    skipped_load_errors = 0
    for row in due:
        try:
            unit = db.get_unit_with_trace(row["id"])
        except KeyError:
            skipped_load_errors += 1
            continue
        signals = extract_deterministic_signals(unit)
        db.replace_signals(unit["id"], signals)
        candidates.append((unit, signals))

    selected = select_priority_units(candidates, budget=budget, min_priority_score=args.min_priority_score)
    if due and not selected:
        print(
            "No due eval units passed deterministic prefilter. "
            f"candidate_units={len(candidates)} min_priority_score={args.min_priority_score}. "
            "Use --min-priority-score 0 to sample low-priority units."
        )

    judge = HermesLLMJudgeClient(home, max_tokens=args.max_tokens, judgement_threshold=args.judgement_threshold)
    routes = judge.resolve_routes()
    print("Judge route priority: " + " -> ".join(f"{r.name}({r.model or r.provider or 'default'})" for r in routes))
    print(
        f"Budget guard: max_judge_calls={budget}, cooldown_minutes={args.cooldown_minutes}, "
        f"candidate_units={len(candidates)}, selected_units={len(selected)}, min_priority_score={args.min_priority_score}, "
        f"judgement_threshold={args.judgement_threshold}"
    )
    if skipped_load_errors:
        print(f"Skipped {skipped_load_errors} candidate unit(s) that could not be loaded.")
    count = 0
    total_usage = TokenUsage()
    for unit, signals, priority_score in selected:
        if args.dry_run:
            print(f"DRY {unit['id']} priority={priority_score} signals={len(signals)}")
            count += 1
            continue
        result = judge.evaluate_unit(unit, signals)
        eval_id = db.insert_llm_eval(
            unit["id"],
            prompt_version=PROMPT_VERSION,
            judge_provider=result.judge_provider,
            judge_model=result.judge_model,
            eval_data=result.eval_data,
            evaluator_error=result.evaluator_error,
            judge_prompt_tokens=result.token_usage.prompt_tokens,
            judge_completion_tokens=result.token_usage.completion_tokens,
            judge_total_tokens=result.token_usage.total_tokens,
            judge_call_count=result.token_usage.calls,
        )
        status = result.eval_data.get("health_status", "failed")
        confidence = result.eval_data.get("confidence", "low")
        reason = str(result.eval_data.get("primary_reason") or "").replace("\n", " ")[:140]
        err = f" evaluator_error={result.evaluator_error}" if result.evaluator_error else ""
        total_usage += result.token_usage
        print(f"{status:14} {confidence:6} {unit['id']} eval={eval_id} tokens={result.token_usage.total_tokens} calls={result.token_usage.calls}{err}  {reason}")
        _print_eval_context(unit, result.eval_data)
        count += 1
    verb = "Selected" if args.dry_run else "Evaluated"
    print(f"{verb} {count} unit(s).")
    if not args.dry_run:
        print(
            "Judge tokens: "
            f"prompt={total_usage.prompt_tokens} completion={total_usage.completion_tokens} "
            f"total={total_usage.total_tokens} calls={total_usage.calls}"
        )
        remaining_budget = max(0, budget - total_usage.calls)
        incident_count, incident_usage = _label_incident_examples_with_judge(
            db,
            judge,
            limit=max(remaining_budget, remaining_budget * 10),
            max_judge_calls=remaining_budget,
            since=_parse_since(args.since),
            batch_size=10,
            prioritize_prediction_gaps=True,
        )
        if incident_count or remaining_budget:
            print(
                "Incident labels: "
                f"labeled={incident_count} remaining_start_budget={remaining_budget} "
                f"tokens={incident_usage.total_tokens} calls={incident_usage.calls}"
            )
    return 0


def cmd_list(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    statuses = [s.strip() for s in args.status.split(",") if s.strip()] if args.status else None
    for row in db.list_llm_evals(statuses=statuses, limit=args.limit, since=_parse_since(args.since)):
        request = (row.get("user_request") or "").replace("\n", " ")[:80]
        print(f"{row.get('started_at') or '-':>12}  {row['health_status']:<12} {row['confidence']:<6} {row['source_session_id']} turn={row['source_turn_index']} tokens={row.get('judge_total_tokens') or 0} anomalies={_format_anomalies(row)}  {request}")
        if args.details:
            unit = db.get_unit_with_trace(row["eval_unit_id"])
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
    unit = db.get_unit_with_trace(args.eval_unit_id)
    latest = db.get_latest_llm_eval(args.eval_unit_id)
    signals = extract_deterministic_signals(unit)
    _print_json({"unit": unit, "signals": signals, "latest_eval": latest})
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
    if getattr(args, "max_judge_total_tokens", None) is not None:
        updates["max_judge_total_tokens"] = args.max_judge_total_tokens
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
    for task in db.list_eval_tasks():
        print(f"{task['id']} enabled={task['enabled']} kind={task['schedule_kind']} next_due_at={task.get('next_due_at')} version={task['config_version']}")
    return 0


def cmd_schedule_show(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    _print_json(db.get_eval_task(args.task))
    return 0


def cmd_schedule_set(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    task = db.upsert_eval_task(args.task, _schedule_update_from_args(args))
    _print_json(task)
    return 0


def cmd_schedule_run_now(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    task = db.get_eval_task(args.task)
    task = db.upsert_eval_task(task["id"], {"enabled": True, "next_due_at": time.time()})
    _print_json(task)
    return 0


def cmd_schedule_pause(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    _print_json(db.upsert_eval_task(args.task, {"enabled": False}))
    return 0


def cmd_schedule_resume(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    _print_json(db.upsert_eval_task(args.task, {"enabled": True, "next_due_at": time.time()}))
    return 0


def cmd_schedule_runs(args) -> int:
    db = EvalDB(default_eval_db_path(Path(args.hermes_home).expanduser()))
    task_id = db.get_eval_task(args.task)["id"] if args.task else None
    _print_json(db.list_eval_runs(task_id=task_id, limit=args.limit))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-health", description="Local Hermes instruction-health evaluator")
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

    p_units = sub.add_parser("units")
    p_units.add_argument("--since")
    p_units.add_argument("--limit", type=int, default=50)
    p_units.set_defaults(func=cmd_units)

    p_incidents = sub.add_parser("incidents", help="List canonical incident examples with latest labels and predictions")
    p_incidents.add_argument("--since")
    p_incidents.add_argument("--limit", type=int, default=50, help="Maximum incident examples to print")
    p_incidents.add_argument("--details", action="store_true", help="Show request/result context below each incident example")
    p_incidents.add_argument("--summary", action="store_true", help="Print incident example counts by canonical label/prediction as JSON")
    p_incidents.set_defaults(func=cmd_incidents)

    p_incident = sub.add_parser("incident", help="ML-first tool-call incident examples, labels, predictions, and models")
    incident_sub = p_incident.add_subparsers(dest="incident_command", required=True)
    p_incident_examples = incident_sub.add_parser("examples", help="List normalized tool-call incident examples")
    p_incident_examples.add_argument("--source-session-id")
    p_incident_examples.add_argument("--since")
    p_incident_examples.add_argument("--limit", type=int, default=50)
    p_incident_examples.add_argument("--unlabeled", action="store_true")
    p_incident_examples.add_argument("--unpredicted", action="store_true")
    p_incident_examples.add_argument("--json", action="store_true")
    p_incident_examples.set_defaults(func=cmd_incident_examples)
    p_incident_export = incident_sub.add_parser("export-training", help="Export accepted incident-specific LLM/human labels as JSONL")
    p_incident_export.add_argument("--limit", type=int, default=10000)
    p_incident_export.set_defaults(func=cmd_incident_export_training)
    p_incident_label = incident_sub.add_parser("label", help="Insert a human incident label by example id or source key")
    p_incident_label.add_argument("--example-id")
    p_incident_label.add_argument("--source-session-id")
    p_incident_label.add_argument("--assistant-tool-call-message-id")
    p_incident_label.add_argument("--result-message-id")
    p_incident_label.add_argument("--tool-call-id")
    p_incident_label.add_argument("--label", choices=["not_incident", "incident", "unsure"], required=True)
    p_incident_label.add_argument("--reason-code", choices=["execution_error", "no_result", "bad_request", "bad_output", "other"])
    p_incident_label.add_argument("--confidence", type=float)
    p_incident_label.add_argument("--correction", action="store_true")
    p_incident_label.add_argument("--reviewer", default="user")
    p_incident_label.add_argument("--comment")
    p_incident_label.set_defaults(func=cmd_incident_label)
    p_incident_judge = incident_sub.add_parser("judge-label", help="Label incident examples with the incident-specific LLM judge")
    p_incident_judge.add_argument("--since")
    p_incident_judge.add_argument("--limit", type=int, default=10)
    p_incident_judge.add_argument("--max-judge-calls", type=int, default=5)
    p_incident_judge.add_argument("--batch-size", type=_int_at_least(1), default=10, help="Incident examples per LLM call; failed/missing rows retry one-by-one by default")
    p_incident_judge.add_argument("--no-retry-failed", action="store_true", help="Disable one-by-one retry for rows missing from a batch result")
    p_incident_judge.add_argument("--prioritize-prediction-gaps", action="store_true", help="Prioritize budget fallback, deferred, missing, and low-confidence predictions")
    p_incident_judge.add_argument("--reevaluate", action="store_true")
    p_incident_judge.add_argument("--dry-run", action="store_true")
    p_incident_judge.add_argument("--max-tokens", type=int, default=1600)
    p_incident_judge.set_defaults(func=cmd_incident_judge_label)
    p_incident_predict = incident_sub.add_parser("predict", help="Predict stored incident examples with the promoted ML-first incident model")
    p_incident_predict.add_argument("--model")
    p_incident_predict.add_argument("--limit", type=int, default=50)
    p_incident_predict.add_argument("--reevaluate", action="store_true")
    p_incident_predict.add_argument("--judge-deferred", action="store_true")
    p_incident_predict.add_argument("--max-judge-calls", type=int, default=0)
    p_incident_predict.add_argument("--max-tokens", type=int, default=900)
    p_incident_predict.set_defaults(func=cmd_incident_predict)
    p_incident_train = incident_sub.add_parser("train", help="Train and optionally auto-promote an ML-first incident model from accepted labels")
    p_incident_train.add_argument("--limit", type=int, default=10000)
    p_incident_train.add_argument("--model-version")
    p_incident_train.add_argument("--output")
    p_incident_train.add_argument("--auto-promote", dest="auto_promote", action="store_true", default=True)
    p_incident_train.add_argument("--no-auto-promote", dest="auto_promote", action="store_false")
    p_incident_train.set_defaults(func=cmd_incident_train)


    p_signals = sub.add_parser("signals")
    p_signals.add_argument("eval_unit_id")
    p_signals.set_defaults(func=cmd_signals)

    p_eval = sub.add_parser("eval")
    p_eval.add_argument("--due", action="store_true", help="Evaluate due imported units (default behavior for V1)")
    p_eval.add_argument("--since")
    p_eval.add_argument("--limit", type=int, default=10, help="Candidate units to consider this run; default is intentionally small")
    p_eval.add_argument("--max-judge-calls", type=int, default=5, help="Hard cap on LLM judge calls for this command invocation")
    p_eval.add_argument("--cooldown-minutes", type=int, default=120, help="Wait this long before judging last turns without next-user reaction evidence")
    p_eval.add_argument("--min-priority-score", type=int, default=1, help="Skip due units below this deterministic priority score; use 0 to sample low-priority units")
    p_eval.add_argument("--reevaluate", action="store_true", help="Explicitly rerun the judge for units that already have any prior judgement")
    p_eval.add_argument("--judgement-threshold", choices=["strict", "balanced", "relaxed"], default="strict", help="How much evidence the judge needs before marking an anomaly; strict focuses on concrete trace/assistant evidence")
    p_eval.add_argument("--dry-run", action="store_true", help="Show due units without calling the judge model")
    p_eval.add_argument("--max-tokens", type=int, default=1200)
    p_eval.set_defaults(func=cmd_eval)

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default="failed,mishandled,prolonged")
    p_list.add_argument("--since")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--details", action="store_true", help="Show request, next-user reaction, outcome, and anomaly evidence below each row")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show")
    p_show.add_argument("eval_unit_id")
    p_show.set_defaults(func=cmd_show)

    p_summary = sub.add_parser("summary")
    p_summary.add_argument("--since")
    p_summary.set_defaults(func=cmd_summary)

    p_dashboard = sub.add_parser("dashboard", help="Install or manage the Hermes dashboard tab")
    dashboard_sub = p_dashboard.add_subparsers(dest="dashboard_command", required=True)
    p_dashboard_install = dashboard_sub.add_parser("install", help="Install the Ariadne Eval tab into $HERMES_HOME/plugins")
    p_dashboard_install.set_defaults(func=cmd_dashboard_install)

    p_scheduler = sub.add_parser("scheduler", help="Run recurring evaluation task scheduler")
    scheduler_sub = p_scheduler.add_subparsers(dest="scheduler_command", required=True)
    p_scheduler_tick = scheduler_sub.add_parser("tick", help="Run one scheduler tick for due tasks")
    p_scheduler_tick.set_defaults(func=cmd_scheduler_tick)
    p_scheduler_run = scheduler_sub.add_parser("run", help="Poll for due recurring eval tasks")
    p_scheduler_run.add_argument("--poll-seconds", type=float, default=60.0)
    p_scheduler_run.set_defaults(func=cmd_scheduler_run)

    p_schedule = sub.add_parser("schedule", help="Manage recurring evaluation tasks")
    schedule_sub = p_schedule.add_subparsers(dest="schedule_command", required=True)
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
    p_schedule_set.add_argument("--max-judge-total-tokens", type=_int_at_least(0))
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
