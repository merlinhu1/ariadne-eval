from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agent_health.adapters.hermes import HermesAdapter, HermesStateReader, default_hermes_home
from agent_health.config import init_home
from agent_health.db import EvalDB, default_eval_db_path
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


def cmd_init(args) -> int:
    home = Path(args.hermes_home).expanduser()
    base = init_home(home)
    EvalDB(default_eval_db_path(home)).migrate()
    print(f"Initialized {base}")
    print("Judge provider defaults to Hermes main provider/model; remote Hermes models send eval inputs to that same provider path.")
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
    for session_id in adapter.discover_due_sources(since=since, limit=args.limit):
        raw = adapter.load_source(session_id)
        for unit in adapter.normalize_eval_units(raw):
            db.upsert_eval_unit(unit)
            signals = extract_deterministic_signals(unit)
            db.replace_signals(unit["id"], signals)
            count += 1
    print(f"Imported {count} eval units into {db.path}")
    return 0


def cmd_units(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    for unit in db.list_units(limit=args.limit, since=_parse_since(args.since)):
        request = (unit.get("user_request") or "").replace("\n", " ")[:80]
        print(f"{unit['started_at'] or '-':>12}  {unit['source_session_id']}  turn={unit['source_turn_index']}  tools={unit['tool_call_count']}  reaction={'yes' if unit.get('next_user_reaction_text') else 'no'}  {request}")
    return 0


def cmd_signals(args) -> int:
    home = Path(args.hermes_home).expanduser()
    db = EvalDB(default_eval_db_path(home))
    unit = db.get_unit_with_trace(args.eval_unit_id)
    signals = extract_deterministic_signals(unit)
    db.replace_signals(args.eval_unit_id, signals)
    _print_json(signals)
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

    p_signals = sub.add_parser("signals")
    p_signals.add_argument("eval_unit_id")
    p_signals.set_defaults(func=cmd_signals)

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
