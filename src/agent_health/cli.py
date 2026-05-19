from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agent_health.adapters.hermes import HermesAdapter, HermesStateReader, default_hermes_home
from agent_health.incidents import extract_incident_events, summarize_incident_events
from agent_health.config import init_home
from agent_health.db import EvalDB, default_eval_db_path
from agent_health.judge import HermesLLMJudgeClient, PROMPT_VERSION, TokenUsage
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

    reaction = str(by_name.get("next_user_reaction_type", {}).get("signal_value") or "unknown")
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
    print("V1 reads Hermes state.db only; no plugin, scheduler, or dashboard is required.")
    print("Judge provider inherits Hermes routing: auxiliary.compression when configured, then the main provider/model.")
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
        deleted += db.delete_stale_session_units(str(session_id), keep_ids)
    suffix = f"; removed {deleted} stale unit(s)" if deleted else ""
    print(f"Imported {count} eval units into {db.path}{suffix}")
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
    incidents: list[dict] = []
    for row in db.list_units(limit=args.unit_limit, since=_parse_since(args.since)):
        unit = db.get_unit_with_trace(row["id"])
        incidents.extend(extract_incident_events(unit))
    incidents.sort(key=lambda i: (i.get("started_at") or 0, i.get("eval_unit_id") or "", i.get("related_event_id") or ""), reverse=True)
    selected = incidents[: max(0, args.limit)]
    if args.summary:
        _print_json(summarize_incident_events(incidents))
        return 0
    for incident in selected:
        request = _one_line(incident.get("user_request"), 110)
        evidence = _one_line(incident.get("evidence"), 180)
        event = f" event={incident.get('related_event_id')}" if incident.get("related_event_id") else ""
        tool = f" tool={incident.get('tool_name')}" if incident.get("tool_name") else ""
        print(
            f"{incident.get('started_at') or '-':>12}  {incident.get('incident_type'):<30} {incident.get('severity'):<6} "
            f"{incident.get('source_session_id')} turn={incident.get('source_turn_index')}{event}{tool}  {evidence}"
        )
        if args.details:
            print(f"  request: {request}")
    return 0



def cmd_signals(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    unit = db.get_unit_with_trace(args.eval_unit_id)
    signals = extract_deterministic_signals(unit)
    db.replace_signals(args.eval_unit_id, signals)
    _print_json(signals)
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
        return 0

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
    if not selected:
        print(
            "No due eval units passed deterministic prefilter. "
            f"candidate_units={len(candidates)} min_priority_score={args.min_priority_score}. "
            "Use --min-priority-score 0 to sample low-priority units."
        )
        return 0

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
        status = result.eval_data.get("health_status", "not_evaluable")
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

    p_incidents = sub.add_parser("incidents", help="List deterministic event-level incidents/anomalies without calling the LLM judge")
    p_incidents.add_argument("--since")
    p_incidents.add_argument("--limit", type=int, default=50, help="Maximum incident events to print")
    p_incidents.add_argument("--unit-limit", type=int, default=200, help="Maximum imported eval units to scan")
    p_incidents.add_argument("--details", action="store_true", help="Show request context below each incident")
    p_incidents.add_argument("--summary", action="store_true", help="Print incident counts by type/severity as JSON")
    p_incidents.set_defaults(func=cmd_incidents)


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
