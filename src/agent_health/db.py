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
    request_friction_score REAL DEFAULT 0.0,
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

CREATE TABLE IF NOT EXISTS incident_eval_examples (
    id TEXT PRIMARY KEY,
    framework TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    eval_unit_id TEXT,
    source_turn_index INTEGER,
    assistant_tool_call_message_id TEXT NOT NULL,
    result_message_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT,
    tool_arguments TEXT,
    tool_result TEXT,
    result_timestamp REAL,
    user_request_excerpt TEXT,
    prior_assistant_visible_text TEXT,
    following_assistant_visible_text TEXT,
    explicit_caller_expectation TEXT,
    explicit_caller_interpretation TEXT,
    upstream_intent_source TEXT,
    normalization_version TEXT NOT NULL,
    raw_payload_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(source_session_id, assistant_tool_call_message_id, result_message_id, tool_call_id)
);

CREATE TABLE IF NOT EXISTS incident_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id TEXT NOT NULL REFERENCES incident_eval_examples(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    reason_code TEXT,
    reason_confidence REAL,
    label_source TEXT NOT NULL,
    label_source_version TEXT,
    accepted_for_training INTEGER NOT NULL DEFAULT 0,
    weight REAL NOT NULL DEFAULT 1.0,
    reviewer TEXT,
    comment TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id TEXT NOT NULL REFERENCES incident_eval_examples(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    is_incident INTEGER,
    reason_code TEXT,
    reason_confidence REAL,
    confidence REAL,
    uncertainty REAL,
    decision_source TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    should_defer_to_llm INTEGER NOT NULL DEFAULT 0,
    llm_budget_available INTEGER,
    budget_fallback INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    eval_unit_id TEXT,
    label TEXT NOT NULL,
    correction INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'human',
    reviewer TEXT,
    comment TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_models (
    id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    training_record_count INTEGER NOT NULL,
    accepted_label_count INTEGER NOT NULL,
    metrics_json TEXT,
    promoted INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    promoted_at REAL
);

CREATE TABLE IF NOT EXISTS eval_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 0,
    schedule_kind TEXT NOT NULL DEFAULT 'interval',
    interval_seconds INTEGER,
    cron_expr TEXT,
    no_gap INTEGER NOT NULL DEFAULT 0,
    idle_backoff_seconds INTEGER NOT NULL DEFAULT 300,
    import_since REAL,
    import_overlap_seconds INTEGER NOT NULL DEFAULT 0,
    candidate_limit INTEGER NOT NULL DEFAULT 10,
    max_judge_calls INTEGER NOT NULL DEFAULT 5,
    max_judge_total_tokens INTEGER,
    max_tokens_per_call INTEGER NOT NULL DEFAULT 1200,
    cooldown_minutes INTEGER NOT NULL DEFAULT 120,
    min_priority_score INTEGER NOT NULL DEFAULT 1,
    judgement_threshold TEXT NOT NULL DEFAULT 'strict',
    params_json TEXT NOT NULL DEFAULT '{}',
    next_due_at REAL,
    last_started_at REAL,
    last_finished_at REAL,
    last_success_at REAL,
    last_run_id TEXT,
    config_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES eval_tasks(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    reason TEXT,
    planned_for REAL,
    started_at REAL,
    finished_at REAL,
    lease_owner TEXT,
    lease_expires_at REAL,
    heartbeat_at REAL,
    effective_config_version INTEGER NOT NULL,
    effective_params_json TEXT NOT NULL,
    imported_units INTEGER NOT NULL DEFAULT 0,
    selected_units INTEGER NOT NULL DEFAULT 0,
    evaluated_units INTEGER NOT NULL DEFAULT 0,
    incident_labels INTEGER NOT NULL DEFAULT 0,
    judge_calls_used INTEGER NOT NULL DEFAULT 0,
    judge_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    judge_completion_tokens INTEGER NOT NULL DEFAULT 0,
    judge_total_tokens INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_task_cursors (
    task_id TEXT NOT NULL REFERENCES eval_tasks(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    cursor_key TEXT NOT NULL,
    cursor_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (task_id, source, cursor_key)
);

CREATE INDEX IF NOT EXISTS idx_eval_units_source_session ON eval_units(source_session_id);
CREATE INDEX IF NOT EXISTS idx_eval_units_started_at ON eval_units(started_at);
CREATE INDEX IF NOT EXISTS idx_trace_events_eval_unit ON trace_events(eval_unit_id);
CREATE INDEX IF NOT EXISTS idx_signals_eval_unit ON deterministic_signals(eval_unit_id);
CREATE INDEX IF NOT EXISTS idx_llm_evals_status ON llm_evals(health_status);
CREATE INDEX IF NOT EXISTS idx_anomalies_type ON anomalies(anomaly_type);
CREATE INDEX IF NOT EXISTS idx_incident_examples_session ON incident_eval_examples(source_session_id);
CREATE INDEX IF NOT EXISTS idx_incident_labels_example ON incident_labels(example_id);
CREATE INDEX IF NOT EXISTS idx_incident_labels_training ON incident_labels(accepted_for_training, label_source);
CREATE INDEX IF NOT EXISTS idx_incident_predictions_example ON incident_predictions(example_id);
CREATE INDEX IF NOT EXISTS idx_eval_feedback_target ON eval_feedback(target_type, target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_eval_feedback_unit ON eval_feedback(eval_unit_id, created_at);
CREATE INDEX IF NOT EXISTS idx_incident_models_promoted ON incident_models(promoted);
CREATE INDEX IF NOT EXISTS idx_eval_tasks_due ON eval_tasks(enabled, next_due_at);
CREATE INDEX IF NOT EXISTS idx_eval_runs_task_status ON eval_runs(task_id, status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_eval_runs_started ON eval_runs(started_at);
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

INCIDENT_EXAMPLE_FIELDS = [
    "id", "framework", "source_session_id", "source_event_id", "eval_unit_id",
    "source_turn_index", "assistant_tool_call_message_id", "result_message_id",
    "tool_call_id", "tool_name", "tool_arguments", "tool_result",
    "result_timestamp", "user_request_excerpt", "prior_assistant_visible_text",
    "following_assistant_visible_text", "explicit_caller_expectation",
    "explicit_caller_interpretation", "upstream_intent_source",
    "normalization_version", "raw_payload_json", "created_at", "updated_at",
]
INCIDENT_LABELS = {"incident", "not_incident", "unsure"}
REQUEST_HEALTH_LABELS = {"succeed", "failed", "mishandled", "prolonged"}
FEEDBACK_LABELS = INCIDENT_LABELS | REQUEST_HEALTH_LABELS
FEEDBACK_TARGET_TYPES = {"eval_unit", "llm_eval", "trace_event", "incident_example"}
INCIDENT_REASON_CODES = {"execution_error", "no_result", "bad_request", "bad_output", "other"}
ACCEPTED_INCIDENT_LABEL_SOURCES = {"incident_llm_judge", "human", "human_correction"}
DISALLOWED_ACCEPTED_INCIDENT_LABEL_SOURCES = {
    "request_anomaly_label", "ml_self_prediction", "old_deterministic_label", "deterministic_rule"
}

EVAL_TASK_FIELDS = [
    "id", "name", "enabled", "schedule_kind", "interval_seconds", "cron_expr",
    "no_gap", "idle_backoff_seconds", "import_since", "import_overlap_seconds",
    "candidate_limit", "max_judge_calls", "max_judge_total_tokens",
    "max_tokens_per_call", "cooldown_minutes", "min_priority_score",
    "judgement_threshold", "params_json", "next_due_at", "last_started_at",
    "last_finished_at", "last_success_at", "last_run_id", "config_version",
    "created_at", "updated_at",
]

EVAL_RUN_FIELDS = [
    "id", "task_id", "status", "reason", "planned_for", "started_at", "finished_at",
    "lease_owner", "lease_expires_at", "heartbeat_at", "effective_config_version",
    "effective_params_json", "imported_units", "selected_units", "evaluated_units",
    "incident_labels", "judge_calls_used", "judge_prompt_tokens",
    "judge_completion_tokens", "judge_total_tokens", "stop_reason", "error",
    "created_at", "updated_at",
]


def _validate_incident_label(label: object) -> str:
    value = str(label or "").strip()
    if value not in INCIDENT_LABELS:
        raise ValueError(f"incident label must be one of {sorted(INCIDENT_LABELS)}")
    return value


def _validate_feedback_label(label: object) -> str:
    value = str(label or "").strip()
    if value not in FEEDBACK_LABELS:
        raise ValueError(f"feedback label must be one of {sorted(FEEDBACK_LABELS)}")
    return value


def _validate_feedback_target_type(target_type: object) -> str:
    value = str(target_type or "").strip()
    if value not in FEEDBACK_TARGET_TYPES:
        raise ValueError(f"feedback target_type must be one of {sorted(FEEDBACK_TARGET_TYPES)}")
    return value


def _validate_reason_code(reason_code: object | None) -> str | None:
    if reason_code is None or reason_code == "":
        return None
    value = str(reason_code).strip()
    if value not in INCIDENT_REASON_CODES:
        raise ValueError(f"incident reason_code must be one of {sorted(INCIDENT_REASON_CODES)}")
    return value


def _incident_label_weight(source: str, confidence: float | None) -> float:
    if source == "human_correction":
        return 3.5
    if source == "human":
        return 3.0
    if source == "incident_llm_judge" and confidence is not None and confidence >= 0.85:
        return 1.5
    return 1.0


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
            con.execute("DROP TABLE IF EXISTS incident_reviews")
            con.executescript(SCHEMA_SQL)
            con.execute("DROP TABLE IF EXISTS barriers")
            con.execute("DROP TABLE IF EXISTS incident_reviews")
            existing = {r[1] for r in con.execute("PRAGMA table_info(llm_evals)").fetchall()}
            token_columns = {
                "request_friction_score": "REAL DEFAULT 0.0",
                "judge_prompt_tokens": "INTEGER DEFAULT 0",
                "judge_completion_tokens": "INTEGER DEFAULT 0",
                "judge_total_tokens": "INTEGER DEFAULT 0",
                "judge_call_count": "INTEGER DEFAULT 0",
            }
            for column, ddl in token_columns.items():
                if column not in existing:
                    con.execute(f"ALTER TABLE llm_evals ADD COLUMN {column} {ddl}")
            con.execute("DELETE FROM incident_labels WHERE label NOT IN ('incident', 'not_incident', 'unsure')")
            con.execute("DELETE FROM incident_predictions WHERE label NOT IN ('incident', 'not_incident', 'unsure')")
            con.execute("DELETE FROM llm_evals WHERE health_status NOT IN ('succeed', 'failed', 'mishandled', 'prolonged')")
            con.execute(
                "INSERT OR REPLACE INTO eval_state (key, value, updated_at) VALUES (?, ?, ?)",
                ("schema_version", "eval_schema_v1", time.time()),
            )
            con.commit()

    def _task_id_for_name(self, name: str) -> str:
        return "task:" + "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(name).strip().lower()).strip("-")

    def _decode_task_row(self, row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        try:
            data["params_json"] = json.loads(data.get("params_json") or "{}")
        except Exception:
            data["params_json"] = {}
        for key in ("enabled", "no_gap"):
            data[key] = bool(data.get(key))
        return data

    def _decode_run_row(self, row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        try:
            data["effective_params_json"] = json.loads(data.get("effective_params_json") or "{}")
        except Exception:
            data["effective_params_json"] = {}
        if isinstance(data["effective_params_json"], dict):
            for key, value in data["effective_params_json"].items():
                data.setdefault(key, value)
        return data

    def _decode_incident_model_row(self, row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        try:
            data["metrics_json"] = json.loads(data.get("metrics_json") or "{}")
        except Exception:
            data["metrics_json"] = {}
        data["promoted"] = bool(data.get("promoted"))
        return data

    def _validate_eval_task_updates(self, updates: dict[str, Any]) -> None:
        if "schedule_kind" in updates and updates["schedule_kind"] not in {"interval", "continuous"}:
            raise ValueError("schedule_kind must be interval or continuous")
        for key in ("enabled", "no_gap"):
            if key in updates and not isinstance(updates[key], bool):
                raise ValueError(f"{key} must be boolean")
        minimums = {
            "interval_seconds": 1,
            "idle_backoff_seconds": 1,
            "import_overlap_seconds": 0,
            "candidate_limit": 1,
            "max_judge_calls": 0,
            "max_judge_total_tokens": 0,
            "max_tokens_per_call": 1,
            "cooldown_minutes": 0,
            "min_priority_score": 0,
        }
        for key, minimum in minimums.items():
            if key not in updates or updates[key] is None:
                continue
            try:
                value = int(updates[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer") from exc
            if value < minimum:
                raise ValueError(f"{key} must be >= {minimum}")
        if "judgement_threshold" in updates and updates["judgement_threshold"] not in {"strict", "balanced", "relaxed"}:
            raise ValueError("judgement_threshold must be strict, balanced, or relaxed")

    def upsert_eval_task(self, name_or_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        self.migrate()
        now = time.time()
        self._validate_eval_task_updates(updates)
        allowed = set(EVAL_TASK_FIELDS) - {"id", "created_at", "updated_at", "config_version", "last_started_at", "last_finished_at", "last_success_at", "last_run_id"}
        with closing(self.connect()) as con:
            explicit_id = str(updates.get("id") or "").strip()
            requested = str(name_or_id).strip()
            requested_name = str(updates.get("name") or "").strip()
            existing = con.execute(
                "SELECT * FROM eval_tasks WHERE id IN (?, ?) OR name IN (?, ?) ORDER BY CASE WHEN id = ? THEN 0 WHEN id = ? THEN 1 ELSE 2 END LIMIT 1",
                (requested, explicit_id, requested, requested_name, requested, explicit_id),
            ).fetchone()
            if existing:
                current = dict(existing)
                merged = {**current}
                for key, value in updates.items():
                    if key in allowed:
                        if key in {"enabled", "no_gap"}:
                            value = 1 if value else 0
                        if key == "params_json" and isinstance(value, (dict, list)):
                            value = json.dumps(value, ensure_ascii=False)
                        merged[key] = value
                merged["updated_at"] = now
                merged["config_version"] = int(current.get("config_version") or 1) + 1
                assignments = ", ".join(f"{field}=?" for field in EVAL_TASK_FIELDS if field != "id")
                con.execute(
                    f"UPDATE eval_tasks SET {assignments} WHERE id = ?",
                    [merged.get(field) for field in EVAL_TASK_FIELDS if field != "id"] + [current["id"]],
                )
                task_id = current["id"]
            else:
                name = requested_name or requested
                task_id = explicit_id or self._task_id_for_name(name)
                row: dict[str, Any] = {
                    "id": task_id,
                    "name": name,
                    "enabled": 0,
                    "schedule_kind": "interval",
                    "interval_seconds": 3600,
                    "cron_expr": None,
                    "no_gap": 0,
                    "idle_backoff_seconds": 300,
                    "import_since": None,
                    "import_overlap_seconds": 0,
                    "candidate_limit": 10,
                    "max_judge_calls": 5,
                    "max_judge_total_tokens": None,
                    "max_tokens_per_call": 1200,
                    "cooldown_minutes": 120,
                    "min_priority_score": 1,
                    "judgement_threshold": "strict",
                    "params_json": "{}",
                    "next_due_at": now,
                    "last_started_at": None,
                    "last_finished_at": None,
                    "last_success_at": None,
                    "last_run_id": None,
                    "config_version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                for key, value in updates.items():
                    if key not in allowed:
                        continue
                    if key in {"enabled", "no_gap"}:
                        value = 1 if value else 0
                    if key == "params_json" and isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False)
                    row[key] = value
                con.execute(
                    f"INSERT INTO eval_tasks ({', '.join(EVAL_TASK_FIELDS)}) VALUES ({', '.join('?' for _ in EVAL_TASK_FIELDS)})",
                    [row.get(field) for field in EVAL_TASK_FIELDS],
                )
            con.commit()
            return self._decode_task_row(con.execute("SELECT * FROM eval_tasks WHERE id = ?", (task_id,)).fetchone()) or {}

    def get_eval_task(self, task: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM eval_tasks WHERE id = ? OR name = ?", (task, task)).fetchone()
            if row is None:
                raise KeyError(task)
            return self._decode_task_row(row) or {}

    def list_eval_tasks(self) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as con:
            return [self._decode_task_row(r) or {} for r in con.execute("SELECT * FROM eval_tasks ORDER BY name").fetchall()]

    def list_due_eval_tasks(self, *, now: float | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self.migrate()
        now = time.time() if now is None else now
        with closing(self.connect()) as con:
            rows = con.execute(
                """
                SELECT t.* FROM eval_tasks t
                WHERE t.enabled = 1 AND COALESCE(t.next_due_at, 0) <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM eval_runs r
                    WHERE r.task_id = t.id AND r.status = 'running' AND COALESCE(r.lease_expires_at, 0) > ?
                  )
                ORDER BY COALESCE(t.next_due_at, 0), t.name LIMIT ?
                """,
                (now, now, max(1, int(limit))),
            ).fetchall()
            return [self._decode_task_row(r) or {} for r in rows]

    def claim_eval_task(self, task: str, *, lease_owner: str, lease_seconds: int = 300, now: float | None = None, reason: str = "due") -> dict[str, Any] | None:
        self.migrate()
        now = time.time() if now is None else now
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            task_row = con.execute("SELECT * FROM eval_tasks WHERE id = ? OR name = ?", (task, task)).fetchone()
            if task_row is None or not int(task_row["enabled"]):
                con.rollback()
                return None
            active = con.execute(
                "SELECT id FROM eval_runs WHERE task_id = ? AND status = 'running' AND COALESCE(lease_expires_at, 0) > ? LIMIT 1",
                (task_row["id"], now),
            ).fetchone()
            if active:
                con.rollback()
                return None
            con.execute(
                "UPDATE eval_runs SET status = 'failed', finished_at = ?, stop_reason = 'error', error = 'lease expired', updated_at = ? WHERE task_id = ? AND status = 'running'",
                (now, now, task_row["id"]),
            )
            params = {k: task_row[k] for k in EVAL_TASK_FIELDS if k not in {"params_json", "created_at", "updated_at"}}
            try:
                params["params_json"] = json.loads(task_row["params_json"] or "{}")
            except Exception:
                params["params_json"] = {}
            if isinstance(params["params_json"], dict):
                params.update(params["params_json"])
            run_id = f"run:{task_row['id']}:{int(now * 1000)}"
            run = {
                "id": run_id,
                "task_id": task_row["id"],
                "status": "running",
                "reason": reason,
                "planned_for": task_row["next_due_at"],
                "started_at": now,
                "finished_at": None,
                "lease_owner": lease_owner,
                "lease_expires_at": now + max(1, int(lease_seconds)),
                "heartbeat_at": now,
                "effective_config_version": task_row["config_version"],
                "effective_params_json": json.dumps(params, ensure_ascii=False),
                "created_at": now,
                "updated_at": now,
            }
            con.execute(
                f"INSERT INTO eval_runs ({', '.join(EVAL_RUN_FIELDS)}) VALUES ({', '.join('?' for _ in EVAL_RUN_FIELDS)})",
                [run.get(field, 0) for field in EVAL_RUN_FIELDS],
            )
            con.execute("UPDATE eval_tasks SET last_started_at = ?, last_run_id = ?, updated_at = ? WHERE id = ?", (now, run_id, now, task_row["id"]))
            con.commit()
            return self._decode_run_row(con.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone())

    def heartbeat_eval_run(self, run_id: str, *, lease_seconds: int = 300, now: float | None = None) -> None:
        self.migrate()
        now = time.time() if now is None else now
        with closing(self.connect()) as con:
            con.execute("UPDATE eval_runs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ? WHERE id = ? AND status = 'running'", (now, now + lease_seconds, now, run_id))
            con.commit()

    def finish_eval_run(self, run_id: str, *, status: str = "succeeded", stop_reason: str = "completed", next_due_at: float | None = None, metrics: dict[str, Any] | None = None, error: str | None = None, now: float | None = None) -> dict[str, Any]:
        self.migrate()
        now = time.time() if now is None else now
        metrics = metrics or {}
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] != "running":
                return self._decode_run_row(row) or {}
            con.execute(
                """
                UPDATE eval_runs SET status = ?, finished_at = ?, lease_expires_at = NULL,
                    imported_units = ?, selected_units = ?, evaluated_units = ?, incident_labels = ?,
                    judge_calls_used = ?, judge_prompt_tokens = ?, judge_completion_tokens = ?,
                    judge_total_tokens = ?, stop_reason = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status, now, int(metrics.get("imported_units") or 0), int(metrics.get("selected_units") or 0),
                    int(metrics.get("evaluated_units") or 0), int(metrics.get("incident_labels") or 0),
                    int(metrics.get("judge_calls_used") or 0), int(metrics.get("judge_prompt_tokens") or 0),
                    int(metrics.get("judge_completion_tokens") or 0), int(metrics.get("judge_total_tokens") or 0),
                    stop_reason, error, now, run_id,
                ),
            )
            con.execute(
                "UPDATE eval_tasks SET last_finished_at = ?, last_success_at = CASE WHEN ? = 'succeeded' THEN ? ELSE last_success_at END, next_due_at = COALESCE(?, next_due_at), updated_at = ? WHERE id = ?",
                (now, status, now, next_due_at, now, row["task_id"]),
            )
            con.commit()
            return self._decode_run_row(con.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()) or {}

    def fail_eval_run(self, run_id: str, *, error: str, next_due_at: float | None = None, now: float | None = None) -> dict[str, Any]:
        return self.finish_eval_run(run_id, status="failed", stop_reason="error", next_due_at=next_due_at, error=error, now=now)

    def get_eval_task_cursor(self, task_id: str, source: str, cursor_key: str) -> Any:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT cursor_json FROM eval_task_cursors WHERE task_id = ? AND source = ? AND cursor_key = ?", (task_id, source, cursor_key)).fetchone()
            if row is None:
                return None
            return json.loads(row["cursor_json"])

    def set_eval_task_cursor(self, task_id: str, source: str, cursor_key: str, value: Any) -> None:
        self.migrate()
        now = time.time()
        with closing(self.connect()) as con:
            con.execute(
                """
                INSERT INTO eval_task_cursors (task_id, source, cursor_key, cursor_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id, source, cursor_key) DO UPDATE SET cursor_json = excluded.cursor_json, updated_at = excluded.updated_at
                """,
                (task_id, source, cursor_key, json.dumps(value, ensure_ascii=False), now),
            )
            con.commit()

    def list_eval_runs(self, *, task_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self.migrate()
        sql = "SELECT * FROM eval_runs"
        params: list[Any] = []
        if task_id is not None:
            sql += " WHERE task_id = ?"
            params.append(task_id)
        sql += " ORDER BY COALESCE(started_at, created_at) DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with closing(self.connect()) as con:
            return [self._decode_run_row(r) or {} for r in con.execute(sql, params).fetchall()]

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

    def list_session_units(self, source_session_id: str, limit: int = 50, since: float | None = None) -> list[dict[str, Any]]:
        self.migrate()
        sql = "SELECT * FROM eval_units WHERE source_session_id = ?"
        params: list[Any] = [str(source_session_id)]
        if since is not None:
            sql += " AND started_at >= ?"
            params.append(since)
        sql += " ORDER BY started_at DESC, source_turn_index ASC, id DESC LIMIT ?"
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

    def _resolve_feedback_target(self, con: sqlite3.Connection, target_type: str, target_id: str, eval_unit_id: str | None = None) -> tuple[str, str | None]:
        params: list[Any]
        if target_type == "eval_unit":
            row = con.execute("SELECT id FROM eval_units WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(target_id)
            return str(row["id"]), str(row["id"])
        if target_type == "llm_eval":
            row = con.execute("SELECT id, eval_unit_id FROM llm_evals WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(target_id)
            return str(row["id"]), str(row["eval_unit_id"])
        if target_type == "trace_event":
            sql = "SELECT id, eval_unit_id FROM trace_events WHERE (id = ? OR source_event_id = ?)"
            params = [target_id, target_id]
            if eval_unit_id:
                sql += " AND eval_unit_id = ?"
                params.append(eval_unit_id)
            sql += " ORDER BY id LIMIT 1"
            row = con.execute(sql, params).fetchone()
            if row is None:
                raise KeyError(target_id)
            return str(row["id"]), str(row["eval_unit_id"])
        if target_type == "incident_example":
            row = con.execute("SELECT id, eval_unit_id FROM incident_eval_examples WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(target_id)
            return str(row["id"]), None if row["eval_unit_id"] is None else str(row["eval_unit_id"])
        raise ValueError(f"unsupported feedback target_type {target_type!r}")

    def insert_feedback(
        self,
        *,
        target_type: str,
        target_id: str,
        label: str,
        eval_unit_id: str | None = None,
        correction: bool = False,
        source: str = "human",
        reviewer: str | None = None,
        comment: str | None = None,
    ) -> int:
        self.migrate()
        normalized_target_type = _validate_feedback_target_type(target_type)
        normalized_label = _validate_feedback_label(label)
        now = time.time()
        with closing(self.connect()) as con:
            canonical_target_id, resolved_unit_id = self._resolve_feedback_target(
                con,
                normalized_target_type,
                str(target_id),
                str(eval_unit_id) if eval_unit_id else None,
            )
            final_unit_id = str(eval_unit_id) if eval_unit_id else resolved_unit_id
            if final_unit_id is not None and con.execute("SELECT 1 FROM eval_units WHERE id = ?", (final_unit_id,)).fetchone() is None:
                raise KeyError(final_unit_id)
            cur = con.execute(
                """
                INSERT INTO eval_feedback
                    (target_type, target_id, eval_unit_id, label, correction, source, reviewer, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_target_type,
                    canonical_target_id,
                    final_unit_id,
                    normalized_label,
                    1 if correction else 0,
                    str(source or "human"),
                    reviewer,
                    comment,
                    now,
                ),
            )
            con.commit()
            return int(cur.lastrowid or 0)

    def list_feedback(
        self,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        eval_unit_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.migrate()
        sql = "SELECT * FROM eval_feedback WHERE 1 = 1"
        params: list[Any] = []
        if target_type:
            sql += " AND target_type = ?"
            params.append(_validate_feedback_target_type(target_type))
        if target_id:
            sql += " AND target_id = ?"
            params.append(str(target_id))
        if eval_unit_id:
            sql += " AND eval_unit_id = ?"
            params.append(str(eval_unit_id))
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with closing(self.connect()) as con:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        for row in rows:
            row["correction"] = bool(row.get("correction"))
        return rows

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

    def upsert_incident_example(self, example: dict[str, Any]) -> str:
        self.migrate()
        now = time.time()
        row = dict(example)
        if not row.get("id"):
            row["id"] = "incident:" + "|".join([
                str(row.get("source_session_id") or ""),
                str(row.get("assistant_tool_call_message_id") or ""),
                str(row.get("result_message_id") or ""),
                str(row.get("tool_call_id") or ""),
            ])
        row.setdefault("created_at", now)
        row["updated_at"] = now
        if isinstance(row.get("raw_payload_json"), (dict, list)):
            row["raw_payload_json"] = json.dumps(row["raw_payload_json"], ensure_ascii=False)
        with closing(self.connect()) as con:
            placeholders = ", ".join("?" for _ in INCIDENT_EXAMPLE_FIELDS)
            assignments = ", ".join(
                f"{field}=excluded.{field}"
                for field in INCIDENT_EXAMPLE_FIELDS
                if field not in {"id", "created_at"}
            )
            con.execute(
                f"INSERT INTO incident_eval_examples ({', '.join(INCIDENT_EXAMPLE_FIELDS)}) VALUES ({placeholders}) "
                f"ON CONFLICT(source_session_id, assistant_tool_call_message_id, result_message_id, tool_call_id) DO UPDATE SET {assignments}",
                [row.get(field) for field in INCIDENT_EXAMPLE_FIELDS],
            )
            stored = con.execute(
                """
                SELECT id FROM incident_eval_examples
                WHERE source_session_id = ? AND assistant_tool_call_message_id = ? AND result_message_id = ? AND tool_call_id = ?
                """,
                (
                    row.get("source_session_id"),
                    row.get("assistant_tool_call_message_id"),
                    row.get("result_message_id"),
                    row.get("tool_call_id"),
                ),
            ).fetchone()
            con.commit()
            return str(stored["id"])

    def list_incident_examples(
        self,
        *,
        source_session_id: str | None = None,
        since: float | None = None,
        limit: int = 50,
        unlabeled: bool = False,
        unpredicted: bool = False,
        prioritize_prediction_gaps: bool = False,
    ) -> list[dict[str, Any]]:
        self.migrate()
        sql = """
            WITH latest_prediction AS (
                SELECT example_id, MAX(id) AS max_id
                FROM incident_predictions
                GROUP BY example_id
            )
            SELECT
                e.*,
                p.label AS prediction_label,
                p.is_incident AS prediction_is_incident,
                p.reason_code AS prediction_reason_code,
                p.reason_confidence AS prediction_reason_confidence,
                p.confidence AS prediction_confidence,
                p.uncertainty AS prediction_uncertainty,
                p.decision_source AS prediction_decision_source,
                p.model_name AS prediction_model_name,
                p.model_version AS prediction_model_version,
                p.should_defer_to_llm AS prediction_should_defer_to_llm,
                p.llm_budget_available AS prediction_llm_budget_available,
                p.budget_fallback AS prediction_budget_fallback,
                p.evidence_json AS prediction_evidence_json
            FROM incident_eval_examples e
            LEFT JOIN latest_prediction lp ON lp.example_id = e.id
            LEFT JOIN incident_predictions p ON p.id = lp.max_id
            WHERE 1 = 1
        """
        params: list[Any] = []
        if source_session_id is not None:
            sql += " AND e.source_session_id = ?"
            params.append(str(source_session_id))
        if since is not None:
            sql += " AND COALESCE(e.result_timestamp, 0) >= ?"
            params.append(float(since))
        if unlabeled:
            sql += " AND NOT EXISTS (SELECT 1 FROM incident_labels l WHERE l.example_id = e.id)"
        if unpredicted:
            sql += " AND NOT EXISTS (SELECT 1 FROM incident_predictions p2 WHERE p2.example_id = e.id)"
        if prioritize_prediction_gaps:
            sql += """
                ORDER BY
                    CASE
                        WHEN COALESCE(p.budget_fallback, 0) = 1 THEN 0
                        WHEN COALESCE(p.should_defer_to_llm, 0) = 1 THEN 1
                        WHEN p.confidence IS NULL THEN 2
                        WHEN p.confidence < 0.85 THEN 3
                        ELSE 4
                    END ASC,
                    COALESCE(p.confidence, -1.0) ASC,
                    COALESCE(e.result_timestamp, 0) ASC,
                    e.id ASC
                LIMIT ?
            """
        else:
            sql += " ORDER BY COALESCE(e.result_timestamp, 0) ASC, e.id ASC LIMIT ?"
        params.append(max(1, int(limit)))
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def list_canonical_incident_examples(
        self,
        *,
        source_session_id: str | None = None,
        since: float | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.migrate()
        sql = """
            WITH latest_label AS (
                SELECT example_id, MAX(id) AS max_id
                FROM incident_labels
                GROUP BY example_id
            ),
            latest_prediction AS (
                SELECT example_id, MAX(id) AS max_id
                FROM incident_predictions
                GROUP BY example_id
            ),
            latest_eval AS (
                SELECT eval_unit_id, MAX(created_at) AS max_created
                FROM llm_evals
                GROUP BY eval_unit_id
            )
            SELECT
                e.id,
                e.source_session_id,
                e.source_event_id,
                e.eval_unit_id,
                e.source_turn_index,
                e.assistant_tool_call_message_id,
                e.result_message_id,
                e.tool_call_id,
                e.tool_name,
                e.tool_result,
                e.result_timestamp,
                e.user_request_excerpt,
                l.label,
                l.label_source,
                p.label AS prediction_label,
                COALESCE(l.reason_code, p.reason_code) AS reason_code,
                COALESCE(le.request_friction_score, 0.0) AS request_friction_score
            FROM incident_eval_examples e
            LEFT JOIN latest_label ll ON ll.example_id = e.id
            LEFT JOIN incident_labels l ON l.id = ll.max_id
            LEFT JOIN latest_prediction lp ON lp.example_id = e.id
            LEFT JOIN incident_predictions p ON p.id = lp.max_id
            LEFT JOIN latest_eval lex ON lex.eval_unit_id = e.eval_unit_id
            LEFT JOIN llm_evals le ON le.eval_unit_id = lex.eval_unit_id AND le.created_at = lex.max_created
            WHERE 1 = 1
        """
        params: list[Any] = []
        if source_session_id is not None:
            sql += " AND e.source_session_id = ?"
            params.append(str(source_session_id))
        if since is not None:
            sql += " AND COALESCE(e.result_timestamp, 0) >= ?"
            params.append(float(since))
        sql += " ORDER BY COALESCE(e.result_timestamp, 0) DESC, e.id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def get_incident_example(self, example_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM incident_eval_examples WHERE id = ?", (example_id,)).fetchone()
            if row is None:
                raise KeyError(example_id)
            return dict(row)

    def find_incident_example_by_source_key(
        self,
        *,
        source_session_id: str,
        assistant_tool_call_message_id: str,
        result_message_id: str,
        tool_call_id: str,
    ) -> dict[str, Any] | None:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                """
                SELECT * FROM incident_eval_examples
                WHERE source_session_id = ? AND assistant_tool_call_message_id = ? AND result_message_id = ? AND tool_call_id = ?
                """,
                (source_session_id, assistant_tool_call_message_id, result_message_id, tool_call_id),
            ).fetchone()
            return dict(row) if row else None

    def insert_incident_label(self, example_id: str, *, label: str, label_source: str, **kwargs: Any) -> int:
        self.migrate()
        source = str(label_source or "").strip()
        accepted = 1 if kwargs.get("accepted_for_training", False) else 0
        if accepted and source not in ACCEPTED_INCIDENT_LABEL_SOURCES:
            raise ValueError("accepted incident training labels must come from incident LLM or human sources")
        if accepted and source in DISALLOWED_ACCEPTED_INCIDENT_LABEL_SOURCES:
            raise ValueError(f"{source} cannot be accepted incident training data")
        normalized_label = _validate_incident_label(label)
        reason_code = _validate_reason_code(kwargs.get("reason_code"))
        confidence = kwargs.get("reason_confidence")
        reason_confidence = None if confidence is None else float(confidence)
        weight = kwargs.get("weight")
        if weight is None:
            weight = _incident_label_weight(source, reason_confidence)
        now = time.time()
        with closing(self.connect()) as con:
            if source in {"human", "human_correction"}:
                con.execute(
                    """
                    DELETE FROM incident_labels
                    WHERE example_id = ? AND label_source IN ('human', 'human_correction')
                      AND COALESCE(reviewer, '') = COALESCE(?, '')
                    """,
                    (example_id, kwargs.get("reviewer")),
                )
            cur = con.execute(
                """
                INSERT INTO incident_labels
                    (example_id, label, reason_code, reason_confidence, label_source, label_source_version,
                     accepted_for_training, weight, reviewer, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    example_id,
                    normalized_label,
                    reason_code,
                    reason_confidence,
                    source,
                    kwargs.get("label_source_version"),
                    accepted,
                    float(weight),
                    kwargs.get("reviewer"),
                    kwargs.get("comment"),
                    now,
                ),
            )
            con.commit()
            return int(cur.lastrowid)

    def export_accepted_incident_training(self, limit: int = 10000) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as con:
            rows = con.execute(
                """
                SELECT l.*, e.tool_name, e.tool_arguments, e.tool_result, e.user_request_excerpt,
                       e.prior_assistant_visible_text, e.following_assistant_visible_text,
                       e.explicit_caller_expectation, e.explicit_caller_interpretation
                FROM incident_labels l
                JOIN incident_eval_examples e ON e.id = l.example_id
                WHERE l.accepted_for_training = 1
                  AND l.label_source IN ('incident_llm_judge', 'human', 'human_correction')
                ORDER BY l.created_at ASC, l.id ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["text"] = "\n".join([
                f"tool={data.get('tool_name') or ''}",
                f"args={data.get('tool_arguments') or ''}",
                f"result={data.get('tool_result') or ''}",
                f"request={data.get('user_request_excerpt') or ''}",
                f"prior_assistant={data.get('prior_assistant_visible_text') or ''}",
                f"following_assistant={data.get('following_assistant_visible_text') or ''}",
            ])
            result.append(data)
        return result

    def insert_incident_prediction(self, example_id: str, *, label: str, decision_source: str, **kwargs: Any) -> int:
        self.migrate()
        normalized_label = _validate_incident_label(label)
        reason_code = _validate_reason_code(kwargs.get("reason_code"))
        evidence = kwargs.get("evidence_json")
        if isinstance(evidence, (dict, list)):
            evidence = json.dumps(evidence, ensure_ascii=False)
        now = time.time()
        with closing(self.connect()) as con:
            cur = con.execute(
                """
                INSERT INTO incident_predictions
                    (example_id, label, is_incident, reason_code, reason_confidence, confidence, uncertainty,
                     decision_source, model_name, model_version, should_defer_to_llm, llm_budget_available,
                     budget_fallback, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    example_id,
                    normalized_label,
                    kwargs.get("is_incident"),
                    reason_code,
                    kwargs.get("reason_confidence"),
                    kwargs.get("confidence"),
                    kwargs.get("uncertainty"),
                    decision_source,
                    kwargs.get("model_name"),
                    kwargs.get("model_version"),
                    1 if kwargs.get("should_defer_to_llm") else 0,
                    None if kwargs.get("llm_budget_available") is None else (1 if kwargs.get("llm_budget_available") else 0),
                    1 if kwargs.get("budget_fallback") else 0,
                    evidence,
                    now,
                ),
            )
            con.commit()
            return int(cur.lastrowid)

    def record_incident_model(self, record: dict[str, Any]) -> str:
        self.migrate()
        now = time.time()
        model_id = str(record.get("id") or f"{record.get('model_name')}:{record.get('model_version')}")
        metrics = record.get("metrics_json")
        if isinstance(metrics, (dict, list)):
            metrics = json.dumps(metrics, ensure_ascii=False)
        with closing(self.connect()) as con:
            con.execute(
                """
                INSERT INTO incident_models
                    (id, model_name, model_version, artifact_path, training_record_count, accepted_label_count,
                     metrics_json, promoted, created_at, promoted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    model_name=excluded.model_name,
                    model_version=excluded.model_version,
                    artifact_path=excluded.artifact_path,
                    training_record_count=excluded.training_record_count,
                    accepted_label_count=excluded.accepted_label_count,
                    metrics_json=excluded.metrics_json
                """,
                (
                    model_id,
                    record["model_name"],
                    record["model_version"],
                    record["artifact_path"],
                    int(record.get("training_record_count") or 0),
                    int(record.get("accepted_label_count") or 0),
                    metrics,
                    1 if record.get("promoted") else 0,
                    float(record.get("created_at") or now),
                    record.get("promoted_at"),
                ),
            )
            con.commit()
        if record.get("promoted"):
            self.promote_incident_model(model_id)
        return model_id

    def promote_incident_model(self, model_id: str) -> None:
        self.migrate()
        now = time.time()
        with closing(self.connect()) as con:
            if con.execute("SELECT 1 FROM incident_models WHERE id = ?", (model_id,)).fetchone() is None:
                raise KeyError(model_id)
            con.execute("UPDATE incident_models SET promoted = 0, promoted_at = NULL")
            con.execute("UPDATE incident_models SET promoted = 1, promoted_at = ? WHERE id = ?", (now, model_id))
            con.commit()

    def list_incident_models(self) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as con:
            rows = con.execute(
                """
                SELECT * FROM incident_models
                ORDER BY promoted DESC, COALESCE(promoted_at, created_at) DESC, created_at DESC, model_version DESC
                """
            ).fetchall()
            return [self._decode_incident_model_row(row) or {} for row in rows]

    def get_promoted_incident_model(self) -> dict[str, Any] | None:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM incident_models WHERE promoted = 1 ORDER BY promoted_at DESC LIMIT 1").fetchone()
            return self._decode_incident_model_row(row)

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
        health_status = str(eval_data.get("health_status") or "")
        if health_status not in {"succeed", "failed", "mishandled", "prolonged"}:
            raise ValueError("health_status must be one of failed, mishandled, prolonged, succeed")
        deleted_fields = {"not_evaluable_reason", "request_smoothness", "smoothness_score"}
        present_deleted = sorted(field for field in deleted_fields if field in eval_data)
        if present_deleted:
            raise ValueError(f"deleted request eval fields are not accepted: {', '.join(present_deleted)}")
        confidence = str(eval_data.get("confidence") or "low")
        primary_reason = str(eval_data.get("primary_reason") or evaluator_error or "No primary reason supplied")
        if "request_friction_score" not in eval_data:
            raise ValueError("request_friction_score is required")
        request_friction_score = float(eval_data["request_friction_score"])
        if request_friction_score < 0.0 or request_friction_score > 1.0:
            raise ValueError("request_friction_score must be between 0 and 1")
        with closing(self.connect()) as con:
            con.execute(
                """
                INSERT INTO llm_evals
                    (id, eval_unit_id, prompt_version, judge_provider, judge_model, health_status, confidence, primary_reason, eval_json, evaluator_error, request_friction_score, judge_prompt_tokens, judge_completion_tokens, judge_total_tokens, judge_call_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    request_friction_score,
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
            friction_row = con.execute(
                f"""
                WITH latest AS (
                    SELECT eval_unit_id, MAX(created_at) AS max_created
                    FROM llm_evals
                    GROUP BY eval_unit_id
                )
                SELECT
                    COALESCE(MAX(e.request_friction_score), 0.0) AS max_friction,
                    COALESCE(AVG(e.request_friction_score), 0.0) AS avg_friction
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
                "friction": {
                    "max_request_friction_score": float(friction_row["max_friction"] or 0.0),
                    "avg_request_friction_score": float(friction_row["avg_friction"] or 0.0),
                },
                "judge_tokens": {
                    "prompt_tokens": int(token_row["prompt_tokens"] or 0),
                    "completion_tokens": int(token_row["completion_tokens"] or 0),
                    "total_tokens": int(token_row["total_tokens"] or 0),
                    "calls": int(token_row["calls"] or 0),
                },
            }


def default_eval_db_path(hermes_home: str | Path) -> Path:
    return Path(hermes_home).expanduser() / "instruction-health" / "evals.db"
