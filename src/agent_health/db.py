from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eval_units (
    id TEXT PRIMARY KEY,
    framework TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    source_turn_index INTEGER NOT NULL,
    user_message_id TEXT NOT NULL,
    assistant_message_id TEXT,
    next_user_message_id TEXT,
    started_at REAL,
    ended_at REAL,
    source TEXT,
    model TEXT,
    title TEXT,
    parent_session_id TEXT,
    user_request TEXT NOT NULL,
    assistant_response TEXT,
    previous_context_summary TEXT,
    next_user_reaction_text TEXT,
    tool_call_count INTEGER DEFAULT 0,
    api_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    normalization_version TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(framework, source_session_id, source_turn_index)
);

CREATE TABLE IF NOT EXISTS trace_events (
    id TEXT PRIMARY KEY,
    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id) ON DELETE CASCADE,
    source_event_id TEXT,
    event_type TEXT NOT NULL,
    timestamp REAL,
    tool_name TEXT,
    args_hash TEXT,
    args_preview TEXT,
    result_hash TEXT,
    result_preview TEXT,
    result_error INTEGER DEFAULT 0,
    duration_ms INTEGER,
    raw_payload_json TEXT
);

CREATE TABLE IF NOT EXISTS deterministic_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id) ON DELETE CASCADE,
    signal_name TEXT NOT NULL,
    signal_value TEXT NOT NULL,
    severity TEXT,
    evidence TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_evals (
    id TEXT PRIMARY KEY,
    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id) ON DELETE CASCADE,
    prompt_version TEXT NOT NULL,
    judge_provider TEXT,
    judge_model TEXT,
    health_status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    primary_reason TEXT NOT NULL,
    eval_json TEXT NOT NULL,
    evaluator_error TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS barriers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_id TEXT NOT NULL REFERENCES llm_evals(id) ON DELETE CASCADE,
    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id) ON DELETE CASCADE,
    barrier_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    evidence TEXT,
    source TEXT,
    related_event_id TEXT
);

CREATE TABLE IF NOT EXISTS eval_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eval_units_source_session ON eval_units(source_session_id);
CREATE INDEX IF NOT EXISTS idx_eval_units_started_at ON eval_units(started_at);
CREATE INDEX IF NOT EXISTS idx_trace_events_eval_unit ON trace_events(eval_unit_id);
CREATE INDEX IF NOT EXISTS idx_signals_eval_unit ON deterministic_signals(eval_unit_id);
CREATE INDEX IF NOT EXISTS idx_llm_evals_status ON llm_evals(health_status);
CREATE INDEX IF NOT EXISTS idx_barriers_type ON barriers(barrier_type);
"""

EVAL_UNIT_FIELDS = [
    "id", "framework", "source_session_id", "source_turn_index", "user_message_id",
    "assistant_message_id", "next_user_message_id", "started_at", "ended_at",
    "source", "model", "title", "parent_session_id", "user_request",
    "assistant_response", "previous_context_summary", "next_user_reaction_text",
    "tool_call_count", "api_call_count", "input_tokens", "output_tokens",
    "normalization_version", "created_at", "updated_at",
]

TRACE_FIELDS = [
    "id", "eval_unit_id", "source_event_id", "event_type", "timestamp",
    "tool_name", "args_hash", "args_preview", "result_hash", "result_preview",
    "result_error", "duration_ms", "raw_payload_json",
]


class EvalDB:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def migrate(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA_SQL)
            con.execute(
                "INSERT OR REPLACE INTO eval_state (key, value, updated_at) VALUES (?, ?, ?)",
                ("schema_version", "eval_schema_v1", time.time()),
            )
            con.commit()

    def upsert_eval_unit(self, unit: dict[str, Any]) -> None:
        self.migrate()
        now = time.time()
        row = dict(unit)
        row.setdefault("created_at", now)
        row["updated_at"] = now
        with self.connect() as con:
            placeholders = ", ".join("?" for _ in EVAL_UNIT_FIELDS)
            assignments = ", ".join(f"{field}=excluded.{field}" for field in EVAL_UNIT_FIELDS if field != "id")
            con.execute(
                f"INSERT INTO eval_units ({', '.join(EVAL_UNIT_FIELDS)}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {assignments}",
                [row.get(field) for field in EVAL_UNIT_FIELDS],
            )
            con.execute("DELETE FROM trace_events WHERE eval_unit_id = ?", (row["id"],))
            for idx, event in enumerate(unit.get("trace_events") or [], start=1):
                event_row = dict(event)
                event_row.setdefault("id", f"{row['id']}:event:{idx}")
                event_row["eval_unit_id"] = row["id"]
                if isinstance(event_row.get("raw_payload_json"), (dict, list)):
                    event_row["raw_payload_json"] = json.dumps(event_row["raw_payload_json"], ensure_ascii=False)
                event_row["result_error"] = 1 if event_row.get("result_error") else 0
                con.execute(
                    f"INSERT INTO trace_events ({', '.join(TRACE_FIELDS)}) VALUES ({', '.join('?' for _ in TRACE_FIELDS)})",
                    [event_row.get(field) for field in TRACE_FIELDS],
                )
            con.commit()

    def replace_signals(self, eval_unit_id: str, signals: list[dict[str, Any]]) -> None:
        self.migrate()
        now = time.time()
        with self.connect() as con:
            con.execute("DELETE FROM deterministic_signals WHERE eval_unit_id = ?", (eval_unit_id,))
            for signal in signals:
                con.execute(
                    "INSERT INTO deterministic_signals (eval_unit_id, signal_name, signal_value, severity, evidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (eval_unit_id, signal["signal_name"], str(signal["signal_value"]), signal.get("severity"), signal.get("evidence"), now),
                )
            con.commit()

    def list_units(self, limit: int = 50, since: float | None = None) -> list[dict[str, Any]]:
        self.migrate()
        sql = "SELECT * FROM eval_units"
        params: list[Any] = []
        if since is not None:
            sql += " WHERE started_at >= ?"
            params.append(since)
        sql += " ORDER BY started_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def get_unit_with_trace(self, eval_unit_id: str) -> dict[str, Any]:
        self.migrate()
        with self.connect() as con:
            row = con.execute("SELECT * FROM eval_units WHERE id = ?", (eval_unit_id,)).fetchone()
            if row is None:
                raise KeyError(eval_unit_id)
            unit = dict(row)
            unit["trace_events"] = [dict(r) for r in con.execute("SELECT * FROM trace_events WHERE eval_unit_id = ? ORDER BY timestamp, id", (eval_unit_id,)).fetchall()]
            return unit


def default_eval_db_path(hermes_home: str | Path) -> Path:
    return Path(hermes_home).expanduser() / "instruction-health" / "evals.db"
