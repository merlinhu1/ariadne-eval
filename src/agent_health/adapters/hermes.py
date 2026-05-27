from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from agent_health.normalize import normalize_incident_examples, normalize_session

HIDDEN_MESSAGE_FIELDS = {
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
}

SESSION_FIELDS = [
    "id", "source", "user_id", "model", "model_config", "system_prompt",
    "parent_session_id", "started_at", "ended_at", "end_reason",
    "message_count", "tool_call_count", "input_tokens", "output_tokens",
    "reasoning_tokens", "estimated_cost_usd", "actual_cost_usd", "title",
    "api_call_count",
]

MESSAGE_FIELDS = [
    "id", "session_id", "role", "content", "tool_call_id", "tool_calls",
    "tool_name", "timestamp", "token_count", "finish_reason",
]


def default_hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


class HermesStateReader:
    """Small, schema-tolerant reader for Hermes state.db.

    The reader intentionally excludes hidden/provider reasoning columns from message
    dictionaries so normalized eval records never depend on chain-of-thought fields.
    """

    def __init__(self, hermes_home: str | Path | None = None, state_db: str | Path | None = None):
        self.hermes_home = Path(hermes_home).expanduser() if hermes_home else default_hermes_home()
        self.state_db = Path(state_db).expanduser() if state_db else self.hermes_home / "state.db"

    def connect(self) -> sqlite3.Connection:
        if not self.state_db.exists():
            raise FileNotFoundError(f"Hermes state.db not found: {self.state_db}")
        con = sqlite3.connect(self.state_db)
        con.row_factory = sqlite3.Row
        return con

    def _columns(self, con: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}

    def _select_fields(self, con: sqlite3.Connection, table: str, wanted: list[str]) -> list[str]:
        columns = self._columns(con, table)
        return [field for field in wanted if field in columns and field not in HIDDEN_MESSAGE_FIELDS]

    def schema_version(self) -> int | None:
        with closing(self.connect()) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "schema_version" not in tables:
                return None
            row = con.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
            return None if row is None else int(row[0])

    def list_sessions(self, limit: int = 20, since: float | None = None, *, oldest_first: bool = False) -> list[dict[str, Any]]:
        with closing(self.connect()) as con:
            fields = self._select_fields(con, "sessions", SESSION_FIELDS)
            sql = f"SELECT {', '.join(fields)} FROM sessions"
            params: list[Any] = []
            if since is not None and "started_at" in fields:
                sql += " WHERE started_at >= ?"
                params.append(since)
            direction = "ASC" if oldest_first else "DESC"
            sql += f" ORDER BY started_at {direction}, id {direction} LIMIT ?"
            params.append(limit)
            return [_row_to_dict(row) for row in con.execute(sql, params).fetchall()]  # type: ignore[list-item]

    def get_session(self, session_id: str) -> dict[str, Any]:
        with closing(self.connect()) as con:
            fields = self._select_fields(con, "sessions", SESSION_FIELDS)
            row = con.execute(f"SELECT {', '.join(fields)} FROM sessions WHERE id = ?", (session_id,)).fetchone()
            result = _row_to_dict(row)
            if result is None:
                raise KeyError(f"No Hermes session found with id {session_id!r}")
            return result

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as con:
            fields = self._select_fields(con, "messages", MESSAGE_FIELDS)
            sql = f"SELECT {', '.join(fields)} FROM messages WHERE session_id = ? ORDER BY timestamp ASC, id ASC"
            return [_row_to_dict(row) for row in con.execute(sql, (session_id,)).fetchall()]  # type: ignore[list-item]

    def load_source(self, session_id: str) -> dict[str, Any]:
        return {"session": self.get_session(session_id), "messages": self.get_messages(session_id)}

    def inspect(self, limit: int = 10) -> dict[str, Any]:
        sessions = self.list_sessions(limit=limit)
        inspected = []
        for session in sessions:
            copy = dict(session)
            copy["messages"] = self.get_messages(session["id"])
            inspected.append(copy)
        return {
            "framework": "hermes",
            "hermes_home": str(self.hermes_home),
            "state_db": str(self.state_db),
            "schema_version": self.schema_version(),
            "sessions": inspected,
        }


class HermesAdapter:
    framework_name = "hermes"

    def __init__(self, hermes_home: str | Path | None = None):
        self.reader = HermesStateReader(hermes_home)

    def discover_due_sources(self, since: float | None = None, limit: int = 1000, *, oldest_first: bool = False):
        for session in self.reader.list_sessions(limit=limit, since=since, oldest_first=oldest_first):
            yield session["id"]

    def load_source(self, source_id: str) -> dict[str, Any]:
        return self.reader.load_source(source_id)

    def normalize_eval_units(self, raw_source: dict[str, Any]) -> list[dict[str, Any]]:
        return normalize_session(raw_source["session"], raw_source["messages"])

    def normalize_incident_examples(self, raw_source: dict[str, Any]) -> list[dict[str, Any]]:
        return normalize_incident_examples(raw_source["session"], raw_source["messages"])
