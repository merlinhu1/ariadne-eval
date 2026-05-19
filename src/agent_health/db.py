from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
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
    judge_prompt_tokens INTEGER DEFAULT 0,
    judge_completion_tokens INTEGER DEFAULT 0,
    judge_total_tokens INTEGER DEFAULT 0,
    judge_call_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_id TEXT NOT NULL REFERENCES llm_evals(id) ON DELETE CASCADE,
    eval_unit_id TEXT NOT NULL REFERENCES eval_units(id) ON DELETE CASCADE,
    anomaly_type TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_anomalies_type ON anomalies(anomaly_type);
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
        with closing(self.connect()) as con:
            con.executescript(SCHEMA_SQL)
            con.execute("DROP TABLE IF EXISTS barriers")
            existing = {r[1] for r in con.execute("PRAGMA table_info(llm_evals)").fetchall()}
            token_columns = {
                "judge_prompt_tokens": "INTEGER DEFAULT 0",
                "judge_completion_tokens": "INTEGER DEFAULT 0",
                "judge_total_tokens": "INTEGER DEFAULT 0",
                "judge_call_count": "INTEGER DEFAULT 0",
            }
            for column, ddl in token_columns.items():
                if column not in existing:
                    con.execute(f"ALTER TABLE llm_evals ADD COLUMN {column} {ddl}")
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
        with closing(self.connect()) as con:
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
                source_event_id = event_row.get("id") or event_row.get("source_event_id")
                event_row["id"] = f"{row['id']}:event:{idx}"
                event_row.setdefault("source_event_id", source_event_id)
                event_row["eval_unit_id"] = row["id"]
                if isinstance(event_row.get("raw_payload_json"), (dict, list)):
                    event_row["raw_payload_json"] = json.dumps(event_row["raw_payload_json"], ensure_ascii=False)
                event_row["result_error"] = 1 if event_row.get("result_error") else 0
                con.execute(
                    f"INSERT INTO trace_events ({', '.join(TRACE_FIELDS)}) VALUES ({', '.join('?' for _ in TRACE_FIELDS)})",
                    [event_row.get(field) for field in TRACE_FIELDS],
                )
            con.commit()

    def delete_stale_session_units(self, source_session_id: str, keep_ids: set[str]) -> int:
        """Remove imported units for a session that no longer normalize.

        This lets normalization fixes remove synthetic/context-compaction turns
        from the sidecar instead of leaving stale due units behind.
        """
        self.migrate()
        with closing(self.connect()) as con:
            params: list[Any] = [str(source_session_id)]
            sql = "DELETE FROM eval_units WHERE source_session_id = ?"
            if keep_ids:
                sql += f" AND id NOT IN ({', '.join('?' for _ in keep_ids)})"
                params.extend(sorted(keep_ids))
            cur = con.execute(sql, params)
            con.commit()
            return int(cur.rowcount or 0)

    def replace_signals(self, eval_unit_id: str, signals: list[dict[str, Any]]) -> None:
        self.migrate()
        now = time.time()
        with closing(self.connect()) as con:
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
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def get_unit_with_trace(self, eval_unit_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM eval_units WHERE id = ?", (eval_unit_id,)).fetchone()
            if row is None:
                raise KeyError(eval_unit_id)
            unit = dict(row)
            unit["trace_events"] = [dict(r) for r in con.execute("SELECT * FROM trace_events WHERE eval_unit_id = ? ORDER BY timestamp, id", (eval_unit_id,)).fetchall()]
            return unit

    def list_due_units(
        self,
        limit: int = 50,
        since: float | None = None,
        reevaluate: bool = False,
        cooldown_seconds: float = 7200,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return imported units eligible for LLM judging.

        A unit is due immediately when it has a next-user reaction, because that
        reaction is useful retrospective evidence. Units without a reaction wait
        for a cooldown so repeated eval batches do not spam the judge for fresh
        last turns that may soon gain reaction evidence.
        """
        self.migrate()
        if now is None:
            now = time.time()
        cutoff = now - max(0, cooldown_seconds)
        sql = """
            SELECT u.*
            FROM eval_units u
            WHERE u.assistant_response IS NOT NULL
              AND (
                u.next_user_message_id IS NOT NULL
                OR u.next_user_reaction_text IS NOT NULL
                OR COALESCE(u.ended_at, u.started_at, u.updated_at) <= ?
              )
        """
        params: list[Any] = [cutoff]
        if since is not None:
            sql += " AND u.started_at >= ?"
            params.append(since)
        if not reevaluate:
            sql += " AND NOT EXISTS (SELECT 1 FROM llm_evals e WHERE e.eval_unit_id = u.id)"
        sql += " ORDER BY u.started_at ASC, u.id ASC LIMIT ?"
        params.append(limit)
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def insert_llm_eval(
        self,
        eval_unit_id: str,
        *,
        prompt_version: str,
        judge_provider: str | None,
        judge_model: str | None,
        eval_data: dict[str, Any],
        evaluator_error: str | None = None,
        judge_prompt_tokens: int = 0,
        judge_completion_tokens: int = 0,
        judge_total_tokens: int = 0,
        judge_call_count: int = 0,
    ) -> str:
        self.migrate()
        now = time.time()
        eval_id = f"{eval_unit_id}:eval:{int(now * 1000)}"
        health_status = str(eval_data.get("health_status") or "not_evaluable")
        confidence = str(eval_data.get("confidence") or "low")
        primary_reason = str(eval_data.get("primary_reason") or evaluator_error or "No primary reason supplied")
        with closing(self.connect()) as con:
            con.execute(
                """
                INSERT INTO llm_evals
                    (id, eval_unit_id, prompt_version, judge_provider, judge_model, health_status, confidence, primary_reason, eval_json, evaluator_error, judge_prompt_tokens, judge_completion_tokens, judge_total_tokens, judge_call_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_id,
                    eval_unit_id,
                    prompt_version,
                    judge_provider,
                    judge_model,
                    health_status,
                    confidence,
                    primary_reason,
                    json.dumps(eval_data, ensure_ascii=False),
                    evaluator_error,
                    int(judge_prompt_tokens or 0),
                    int(judge_completion_tokens or 0),
                    int(judge_total_tokens or 0),
                    int(judge_call_count or 0),
                    now,
                ),
            )
            anomalies = eval_data.get("anomalies")
            if not isinstance(anomalies, list):
                anomalies = []
            for anomaly in anomalies:
                if not isinstance(anomaly, dict):
                    continue
                anomaly_type = str(anomaly.get("type") or "").strip()
                if not anomaly_type:
                    continue
                con.execute(
                    """
                    INSERT INTO anomalies
                        (eval_id, eval_unit_id, anomaly_type, severity, evidence, source, related_event_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eval_id,
                        eval_unit_id,
                        anomaly_type,
                        str(anomaly.get("severity") or "medium"),
                        anomaly.get("evidence"),
                        anomaly.get("source"),
                        anomaly.get("related_event_id"),
                    ),
                )
            con.commit()
        return eval_id

    def get_latest_llm_eval(self, eval_unit_id: str) -> dict[str, Any] | None:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                "SELECT * FROM llm_evals WHERE eval_unit_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (eval_unit_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            try:
                result["eval_json"] = json.loads(result["eval_json"])
            except Exception:
                pass
            result["anomalies"] = [dict(r) for r in con.execute(
                "SELECT * FROM anomalies WHERE eval_id = ? ORDER BY id ASC",
                (result["id"],),
            ).fetchall()]
            return result

    def list_llm_evals(self, statuses: list[str] | None = None, limit: int = 50, since: float | None = None) -> list[dict[str, Any]]:
        self.migrate()
        sql = """
            WITH latest AS (
                SELECT eval_unit_id, MAX(created_at) AS max_created
                FROM llm_evals
                GROUP BY eval_unit_id
            )
            SELECT e.*, u.source_session_id, u.source_turn_index, u.started_at, u.user_request, u.tool_call_count
            FROM llm_evals e
            JOIN latest l ON l.eval_unit_id = e.eval_unit_id AND l.max_created = e.created_at
            JOIN eval_units u ON u.id = e.eval_unit_id
            WHERE 1 = 1
        """
        params: list[Any] = []
        if statuses:
            sql += f" AND e.health_status IN ({', '.join('?' for _ in statuses)})"
            params.extend(statuses)
        if since is not None:
            sql += " AND u.started_at >= ?"
            params.append(since)
        sql += " ORDER BY e.created_at DESC, e.id DESC LIMIT ?"
        params.append(limit)
        with closing(self.connect()) as con:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
            for row in rows:
                row["anomalies"] = [dict(r) for r in con.execute(
                    "SELECT * FROM anomalies WHERE eval_id = ? ORDER BY id ASC",
                    (row["id"],),
                ).fetchall()]
            return rows

    def summary(self, since: float | None = None) -> dict[str, Any]:
        self.migrate()
        params: list[Any] = []
        where = ""
        if since is not None:
            where = "WHERE u.started_at >= ?"
            params.append(since)
        with closing(self.connect()) as con:
            status_rows = con.execute(
                f"""
                WITH latest AS (
                    SELECT eval_unit_id, MAX(created_at) AS max_created
                    FROM llm_evals
                    GROUP BY eval_unit_id
                )
                SELECT e.health_status, COUNT(*) AS count
                FROM llm_evals e
                JOIN latest l ON l.eval_unit_id = e.eval_unit_id AND l.max_created = e.created_at
                JOIN eval_units u ON u.id = e.eval_unit_id
                {where}
                GROUP BY e.health_status
                ORDER BY count DESC
                """,
                params,
            ).fetchall()
            anomaly_rows = con.execute(
                f"""
                WITH latest AS (
                    SELECT eval_unit_id, MAX(created_at) AS max_created
                    FROM llm_evals
                    GROUP BY eval_unit_id
                )
                SELECT a.anomaly_type, COUNT(*) AS count
                FROM anomalies a
                JOIN llm_evals e ON e.id = a.eval_id
                JOIN latest l ON l.eval_unit_id = e.eval_unit_id AND l.max_created = e.created_at
                JOIN eval_units u ON u.id = a.eval_unit_id
                {where}
                GROUP BY a.anomaly_type
                ORDER BY count DESC, a.anomaly_type ASC
                LIMIT 20
                """,
                params,
            ).fetchall()
            total = sum(int(r["count"]) for r in status_rows)
            token_row = con.execute(
                f"""
                WITH latest AS (
                    SELECT eval_unit_id, MAX(created_at) AS max_created
                    FROM llm_evals
                    GROUP BY eval_unit_id
                )
                SELECT
                    COALESCE(SUM(e.judge_prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(e.judge_completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(e.judge_total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(e.judge_call_count), 0) AS calls
                FROM llm_evals e
                JOIN latest l ON l.eval_unit_id = e.eval_unit_id AND l.max_created = e.created_at
                JOIN eval_units u ON u.id = e.eval_unit_id
                {where}
                """,
                params,
            ).fetchone()
            return {
                "evaluated_turns": total,
                "statuses": {r["health_status"]: r["count"] for r in status_rows},
                "top_anomalies": [{"anomaly_type": r["anomaly_type"], "count": r["count"]} for r in anomaly_rows],
                "judge_tokens": {
                    "prompt_tokens": int(token_row["prompt_tokens"] or 0),
                    "completion_tokens": int(token_row["completion_tokens"] or 0),
                    "total_tokens": int(token_row["total_tokens"] or 0),
                    "calls": int(token_row["calls"] or 0),
                },
            }


def default_eval_db_path(hermes_home: str | Path) -> Path:
    return Path(hermes_home).expanduser() / "instruction-health" / "evals.db"
