from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS source_sessions (
    id TEXT PRIMARY KEY,
    framework TEXT NOT NULL,
    source TEXT,
    model TEXT,
    title TEXT,
    parent_session_id TEXT,
    started_at REAL,
    ended_at REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    source_payload_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_cases (
    id TEXT PRIMARY KEY,
    source_session_id TEXT NOT NULL REFERENCES source_sessions(id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL,
    request_message_id TEXT,
    response_message_id TEXT,
    next_request_message_id TEXT,
    started_at REAL,
    ended_at REAL,
    source TEXT,
    model TEXT,
    title TEXT,
    parent_session_id TEXT,
    request_text TEXT NOT NULL,
    response_text TEXT,
    prior_context_summary TEXT,
    next_request_text TEXT,
    tool_interaction_count INTEGER DEFAULT 0,
    source_session_api_interaction_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    case_builder_version TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(source_session_id, turn_index)
);

CREATE TABLE IF NOT EXISTS case_events (
    id TEXT PRIMARY KEY,
    turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE,
    source_event_id TEXT,
    event_type TEXT NOT NULL,
    event_at REAL,
    tool_interaction_id TEXT,
    tool_name TEXT,
    input_hash TEXT,
    input_preview TEXT,
    output_hash TEXT,
    output_preview TEXT,
    output_error INTEGER DEFAULT 0,
    duration_ms INTEGER,
    source_payload_json TEXT
);

CREATE TABLE IF NOT EXISTS tool_interactions (
    id TEXT PRIMARY KEY,
    turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE,
    call_case_event_id TEXT REFERENCES case_events(id) ON DELETE SET NULL,
    result_case_event_id TEXT REFERENCES case_events(id) ON DELETE SET NULL,
    source_tool_call_id TEXT,
    tool_name TEXT NOT NULL,
    tool_input_text TEXT,
    tool_input_hash TEXT,
    tool_input_preview TEXT,
    tool_output_text TEXT,
    tool_output_hash TEXT,
    tool_output_preview TEXT,
    tool_output_error TEXT,
    called_at REAL,
    completed_at REAL,
    duration_ms INTEGER,
    source_payload_json TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS case_signals (
    id TEXT PRIMARY KEY,
    turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE,
    signal_type TEXT NOT NULL,
    signal_value TEXT NOT NULL,
    score REAL,
    severity TEXT,
    evidence_text TEXT,
    case_event_id TEXT REFERENCES case_events(id) ON DELETE SET NULL,
    source_payload_json TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS case_reviews (
    id TEXT PRIMARY KEY,
    turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE,
    reviewer_type TEXT NOT NULL DEFAULT 'automatic_llm',
    review_scope TEXT NOT NULL DEFAULT 'turn_case',
    review_prompt_version TEXT,
    reviewer_provider TEXT,
    reviewer_model TEXT,
    outcome_status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    summary_reason TEXT NOT NULL,
    review_json TEXT NOT NULL,
    review_error TEXT,
    friction_score REAL DEFAULT 0.0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    review_call_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS case_findings (
    id TEXT PRIMARY KEY,
    case_review_id TEXT NOT NULL REFERENCES case_reviews(id) ON DELETE CASCADE,
    turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE,
    finding_type TEXT NOT NULL,
    severity TEXT,
    evidence_text TEXT,
    evidence_source TEXT,
    case_event_id TEXT REFERENCES case_events(id) ON DELETE SET NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS review_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_outcome_cases (
    id TEXT PRIMARY KEY,
    turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE,
    tool_interaction_id TEXT NOT NULL REFERENCES tool_interactions(id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL,
    request_excerpt TEXT,
    prior_response_excerpt TEXT,
    following_response_excerpt TEXT,
    caller_expectation_text TEXT,
    caller_interpretation_text TEXT,
    intent_source TEXT,
    case_builder_version TEXT NOT NULL,
    source_payload_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(tool_interaction_id)
);

CREATE TABLE IF NOT EXISTS tool_outcome_reviews (
    id TEXT PRIMARY KEY,
    tool_outcome_case_id TEXT NOT NULL REFERENCES tool_outcome_cases(id) ON DELETE CASCADE,
    turn_case_id TEXT NOT NULL REFERENCES turn_cases(id) ON DELETE CASCADE,
    reviewer_type TEXT NOT NULL,
    reviewer_name TEXT,
    reviewer_version TEXT,
    review_source_detail TEXT,
    outcome_label TEXT NOT NULL,
    reason_code TEXT,
    confidence REAL,
    uncertainty REAL,
    evidence_summary TEXT,
    evidence_json TEXT,
    training_eligible INTEGER NOT NULL DEFAULT 0,
    training_weight REAL,
    needs_llm_review INTEGER NOT NULL DEFAULT 0,
    llm_review_budget_available INTEGER,
    budget_fallback INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS automatic_llm_review_claims (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK(target_type IN ('turn_case', 'tool_outcome_case')),
    target_id TEXT NOT NULL,
    parent_turn_case_id TEXT,
    run_id TEXT,
    src TEXT,
    status TEXT NOT NULL DEFAULT 'claimed',
    claimed_at REAL NOT NULL,
    llm_started_at REAL,
    completed_at REAL,
    error_message TEXT,
    metadata_json TEXT,
    UNIQUE(target_type, target_id)
);

CREATE TABLE IF NOT EXISTS review_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    turn_case_id TEXT,
    label TEXT NOT NULL,
    correction INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'human',
    reviewer TEXT,
    comment TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_outcome_reviewer_models (
    id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    feature_schema_version TEXT NOT NULL,
    review_schema_version TEXT NOT NULL,
    training_summary_json TEXT,
    promoted_at REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS review_jobs (
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
    max_review_total_tokens INTEGER,
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

CREATE TABLE IF NOT EXISTS review_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES review_jobs(id) ON DELETE CASCADE,
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
    imported_cases INTEGER NOT NULL DEFAULT 0,
    selected_cases INTEGER NOT NULL DEFAULT 0,
    reviewed_cases INTEGER NOT NULL DEFAULT 0,
    tool_outcome_reviews INTEGER NOT NULL DEFAULT 0,
    llm_review_calls_used INTEGER NOT NULL DEFAULT 0,
    review_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    review_completion_tokens INTEGER NOT NULL DEFAULT 0,
    review_total_tokens INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS review_job_cursors (
    task_id TEXT NOT NULL REFERENCES review_jobs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    cursor_key TEXT NOT NULL,
    cursor_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (task_id, source, cursor_key)
);

CREATE INDEX IF NOT EXISTS idx_source_sessions_framework_started_at ON source_sessions(framework, started_at);
CREATE INDEX IF NOT EXISTS idx_source_sessions_parent ON source_sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_turn_cases_source_session ON turn_cases(source_session_id);
CREATE INDEX IF NOT EXISTS idx_turn_cases_started_at ON turn_cases(started_at);
CREATE INDEX IF NOT EXISTS idx_case_events_turn_case ON case_events(turn_case_id);
CREATE INDEX IF NOT EXISTS idx_tool_interactions_turn_case ON tool_interactions(turn_case_id);
CREATE INDEX IF NOT EXISTS idx_tool_interactions_tool_name ON tool_interactions(tool_name);
CREATE INDEX IF NOT EXISTS idx_signals_turn_case ON case_signals(turn_case_id);
CREATE INDEX IF NOT EXISTS idx_case_reviews_status ON case_reviews(outcome_status);
CREATE INDEX IF NOT EXISTS idx_case_findings_type ON case_findings(finding_type);
CREATE INDEX IF NOT EXISTS idx_tool_outcome_cases_turn_case ON tool_outcome_cases(turn_case_id);
CREATE INDEX IF NOT EXISTS idx_tool_outcome_cases_tool_interaction ON tool_outcome_cases(tool_interaction_id);
CREATE INDEX IF NOT EXISTS idx_tool_outcome_reviews_case ON tool_outcome_reviews(tool_outcome_case_id);
CREATE INDEX IF NOT EXISTS idx_tool_outcome_reviews_turn_case ON tool_outcome_reviews(turn_case_id);
CREATE INDEX IF NOT EXISTS idx_tool_outcome_reviews_reviewer_type ON tool_outcome_reviews(reviewer_type);
CREATE INDEX IF NOT EXISTS idx_tool_outcome_reviews_training ON tool_outcome_reviews(training_eligible);
CREATE INDEX IF NOT EXISTS idx_automatic_llm_review_claims_status ON automatic_llm_review_claims(status, claimed_at);
CREATE INDEX IF NOT EXISTS idx_automatic_llm_review_claims_parent ON automatic_llm_review_claims(parent_turn_case_id);
CREATE INDEX IF NOT EXISTS idx_review_feedback_target ON review_feedback(target_type, target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_review_feedback_unit ON review_feedback(turn_case_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_outcome_reviewer_models_promoted ON tool_outcome_reviewer_models(promoted_at);
CREATE INDEX IF NOT EXISTS idx_review_jobs_due ON review_jobs(enabled, next_due_at);
CREATE INDEX IF NOT EXISTS idx_review_runs_task_status ON review_runs(task_id, status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_review_runs_started ON review_runs(started_at);
"""

TURN_CASE_FIELDS = [
    "id", "source_session_id", "turn_index", "request_message_id",
    "response_message_id", "next_request_message_id", "started_at", "ended_at",
    "source", "model", "title", "parent_session_id", "request_text",
    "response_text", "prior_context_summary", "next_request_text",
    "tool_interaction_count", "source_session_api_interaction_count", "input_tokens", "output_tokens",
    "case_builder_version", "created_at", "updated_at",
]

CASE_EVENT_FIELDS = [
    "id", "turn_case_id", "source_event_id", "event_type", "event_at",
    "tool_interaction_id", "tool_name", "input_hash", "input_preview", "output_hash", "output_preview",
    "output_error", "duration_ms", "source_payload_json",
]

SOURCE_SESSION_FIELDS = [
    "id", "framework", "source", "model", "title", "parent_session_id",
    "started_at", "ended_at", "input_tokens", "output_tokens",
    "source_payload_json", "created_at", "updated_at",
]

TOOL_INTERACTION_FIELDS = [
    "id", "turn_case_id", "call_case_event_id", "result_case_event_id",
    "source_tool_call_id", "tool_name", "tool_input_text", "tool_input_hash",
    "tool_input_preview", "tool_output_text", "tool_output_hash",
    "tool_output_preview", "tool_output_error", "called_at", "completed_at",
    "duration_ms", "source_payload_json", "created_at",
]

TOOL_OUTCOME_CASE_FIELDS = [
    "id", "turn_case_id", "tool_interaction_id", "turn_index", "request_excerpt",
    "prior_response_excerpt", "following_response_excerpt", "caller_expectation_text",
    "caller_interpretation_text", "intent_source", "case_builder_version",
    "source_payload_json", "created_at", "updated_at",
]

TOOL_OUTCOME_LABELS = {"problem", "ok", "unsure"}
REQUEST_HEALTH_LABELS = {"succeed", "failed", "mishandled", "prolonged"}
FEEDBACK_LABELS = TOOL_OUTCOME_LABELS | REQUEST_HEALTH_LABELS
FEEDBACK_TARGET_TYPES = {"turn_case", "case_review", "case_event", "tool_outcome_case", "tool_outcome_review", "case_finding"}
TOOL_OUTCOME_REASON_CODES = {"execution_error", "empty_output", "invalid_tool_input", "wrong_or_bad_output", "other"}
ACCEPTED_TOOL_OUTCOME_REVIEW_SOURCES = {"automatic_llm", "human", "human_correction"}
DISALLOWED_ACCEPTED_TOOL_OUTCOME_REVIEW_SOURCES = {
    "request_finding_label", "ml_self_prediction", "old_case_label", "rule"
}

REVIEW_JOB_FIELDS = [
    "id", "name", "enabled", "schedule_kind", "interval_seconds", "cron_expr",
    "no_gap", "idle_backoff_seconds", "import_since", "import_overlap_seconds",
    "candidate_limit", "max_judge_calls", "max_review_total_tokens",
    "max_tokens_per_call", "cooldown_minutes", "min_priority_score",
    "judgement_threshold", "params_json", "next_due_at", "last_started_at",
    "last_finished_at", "last_success_at", "last_run_id", "config_version",
    "created_at", "updated_at",
]

REVIEW_RUN_FIELDS = [
    "id", "task_id", "status", "reason", "planned_for", "started_at", "finished_at",
    "lease_owner", "lease_expires_at", "heartbeat_at", "effective_config_version",
    "effective_params_json", "imported_cases", "selected_cases", "reviewed_cases",
    "tool_outcome_reviews", "llm_review_calls_used", "review_prompt_tokens",
    "review_completion_tokens", "review_total_tokens", "stop_reason", "error",
    "created_at", "updated_at",
]


def _validate_tool_outcome_label(label: object) -> str:
    value = str(label or "").strip()
    if value not in TOOL_OUTCOME_LABELS:
        raise ValueError(f"tool_outcome label must be one of {sorted(TOOL_OUTCOME_LABELS)}")
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
    if value not in TOOL_OUTCOME_REASON_CODES:
        raise ValueError(f"tool_outcome reason_code must be one of {sorted(TOOL_OUTCOME_REASON_CODES)}")
    return value


def _tool_outcome_label_weight(source: str, confidence: float | None) -> float:
    if source == "human_correction":
        return 3.5
    if source == "human":
        return 3.0
    if source == "tool_outcome_llm_reviewer" and confidence is not None and confidence >= 0.85:
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
            con.executescript(SCHEMA_SQL)
            self._install_automatic_llm_write_barriers(con)
            con.execute(
                "INSERT OR REPLACE INTO review_state (key, value, updated_at) VALUES (?, ?, ?)",
                ("schema_version", "turn_case_review_schema_v1", time.time()),
            )
            con.commit()

    def _install_automatic_llm_write_barriers(self, con: sqlite3.Connection) -> None:
        try:
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_case_reviews_automatic_llm_turn_case
                ON case_reviews(turn_case_id)
                WHERE reviewer_type = 'automatic_llm' AND review_scope = 'turn_case'
                """
            )
        except sqlite3.IntegrityError:
            pass
        try:
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_outcome_reviews_automatic_llm_case
                ON tool_outcome_reviews(tool_outcome_case_id)
                WHERE reviewer_type = 'automatic_llm'
                """
            )
        except sqlite3.IntegrityError:
            pass
        try:
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_automatic_llm_review_claims_target
                ON automatic_llm_review_claims(target_type, target_id)
                """
            )
        except sqlite3.IntegrityError:
            pass
        con.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS trg_case_reviews_no_duplicate_auto_llm_insert
            BEFORE INSERT ON case_reviews
            WHEN NEW.reviewer_type = 'automatic_llm'
              AND NEW.review_scope = 'turn_case'
              AND EXISTS (
                SELECT 1 FROM case_reviews existing
                WHERE existing.turn_case_id = NEW.turn_case_id
                  AND existing.reviewer_type = 'automatic_llm'
                  AND existing.review_scope = 'turn_case'
              )
            BEGIN
                SELECT RAISE(ABORT, 'duplicate automatic LLM case review');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_case_reviews_no_duplicate_auto_llm_update
            BEFORE UPDATE ON case_reviews
            WHEN NEW.reviewer_type = 'automatic_llm'
              AND NEW.review_scope = 'turn_case'
              AND EXISTS (
                SELECT 1 FROM case_reviews existing
                WHERE existing.id != OLD.id
                  AND existing.turn_case_id = NEW.turn_case_id
                  AND existing.reviewer_type = 'automatic_llm'
                  AND existing.review_scope = 'turn_case'
              )
            BEGIN
                SELECT RAISE(ABORT, 'duplicate automatic LLM case review');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_tool_outcome_reviews_no_duplicate_auto_llm_insert
            BEFORE INSERT ON tool_outcome_reviews
            WHEN NEW.reviewer_type = 'automatic_llm'
              AND EXISTS (
                SELECT 1 FROM tool_outcome_reviews existing
                WHERE existing.tool_outcome_case_id = NEW.tool_outcome_case_id
                  AND existing.reviewer_type = 'automatic_llm'
              )
            BEGIN
                SELECT RAISE(ABORT, 'duplicate automatic LLM tool outcome review');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_tool_outcome_reviews_no_duplicate_auto_llm_update
            BEFORE UPDATE ON tool_outcome_reviews
            WHEN NEW.reviewer_type = 'automatic_llm'
              AND EXISTS (
                SELECT 1 FROM tool_outcome_reviews existing
                WHERE existing.id != OLD.id
                  AND existing.tool_outcome_case_id = NEW.tool_outcome_case_id
                  AND existing.reviewer_type = 'automatic_llm'
              )
            BEGIN
                SELECT RAISE(ABORT, 'duplicate automatic LLM tool outcome review');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_tool_outcome_reviews_no_auto_llm_after_parent_review_insert
            BEFORE INSERT ON tool_outcome_reviews
            WHEN NEW.reviewer_type = 'automatic_llm'
              AND EXISTS (
                SELECT 1
                FROM tool_outcome_cases toc
                JOIN case_reviews cr ON cr.turn_case_id = toc.turn_case_id
                WHERE toc.id = NEW.tool_outcome_case_id
                  AND cr.reviewer_type = 'automatic_llm'
                  AND cr.review_scope = 'turn_case'
              )
            BEGIN
                SELECT RAISE(ABORT, 'parent turn_case already has automatic LLM review');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_tool_outcome_reviews_no_auto_llm_after_parent_claim_insert
            BEFORE INSERT ON tool_outcome_reviews
            WHEN NEW.reviewer_type = 'automatic_llm'
              AND EXISTS (
                SELECT 1
                FROM tool_outcome_cases toc
                JOIN automatic_llm_review_claims c ON c.target_type = 'turn_case' AND c.target_id = toc.turn_case_id
                WHERE toc.id = NEW.tool_outcome_case_id
              )
            BEGIN
                SELECT RAISE(ABORT, 'parent turn_case already has automatic LLM claim');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_tool_outcome_reviews_no_auto_llm_after_parent_review_update
            BEFORE UPDATE ON tool_outcome_reviews
            WHEN NEW.reviewer_type = 'automatic_llm'
              AND EXISTS (
                SELECT 1
                FROM tool_outcome_cases toc
                JOIN case_reviews cr ON cr.turn_case_id = toc.turn_case_id
                WHERE toc.id = NEW.tool_outcome_case_id
                  AND cr.reviewer_type = 'automatic_llm'
                  AND cr.review_scope = 'turn_case'
              )
            BEGIN
                SELECT RAISE(ABORT, 'parent turn_case already has automatic LLM review');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_tool_outcome_reviews_no_auto_llm_after_parent_claim_update
            BEFORE UPDATE ON tool_outcome_reviews
            WHEN NEW.reviewer_type = 'automatic_llm'
              AND EXISTS (
                SELECT 1
                FROM tool_outcome_cases toc
                JOIN automatic_llm_review_claims c ON c.target_type = 'turn_case' AND c.target_id = toc.turn_case_id
                WHERE toc.id = NEW.tool_outcome_case_id
              )
            BEGIN
                SELECT RAISE(ABORT, 'parent turn_case already has automatic LLM claim');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_automatic_llm_claims_no_duplicate_insert
            BEFORE INSERT ON automatic_llm_review_claims
            WHEN EXISTS (
                SELECT 1 FROM automatic_llm_review_claims existing
                WHERE existing.target_type = NEW.target_type
                  AND existing.target_id = NEW.target_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'duplicate automatic LLM claim');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_automatic_llm_claims_no_duplicate_update
            BEFORE UPDATE ON automatic_llm_review_claims
            WHEN (NEW.target_type != OLD.target_type OR NEW.target_id != OLD.target_id)
              AND EXISTS (
                SELECT 1 FROM automatic_llm_review_claims existing
                WHERE existing.id != OLD.id
                  AND existing.target_type = NEW.target_type
                  AND existing.target_id = NEW.target_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'duplicate automatic LLM claim');
            END;
            """
        )

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

    def _decode_tool_outcome_reviewer_model_row(self, row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        try:
            data["training_summary_json"] = json.loads(data.get("training_summary_json") or "{}")
        except Exception:
            data["training_summary_json"] = {}
        data["metrics_json"] = data["training_summary_json"]
        data["promoted"] = data.get("promoted_at") is not None
        return data

    def _validate_review_job_updates(self, updates: dict[str, Any]) -> None:
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
            "max_review_total_tokens": 0,
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

    def upsert_review_job(self, name_or_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        self.migrate()
        now = time.time()
        self._validate_review_job_updates(updates)
        allowed = set(REVIEW_JOB_FIELDS) - {"id", "created_at", "updated_at", "config_version", "last_started_at", "last_finished_at", "last_success_at", "last_run_id"}
        with closing(self.connect()) as con:
            explicit_id = str(updates.get("id") or "").strip()
            requested = str(name_or_id).strip()
            requested_name = str(updates.get("name") or "").strip()
            existing = con.execute(
                "SELECT * FROM review_jobs WHERE id IN (?, ?) OR name IN (?, ?) ORDER BY CASE WHEN id = ? THEN 0 WHEN id = ? THEN 1 ELSE 2 END LIMIT 1",
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
                assignments = ", ".join(f"{field}=?" for field in REVIEW_JOB_FIELDS if field != "id")
                con.execute(
                    f"UPDATE review_jobs SET {assignments} WHERE id = ?",
                    [merged.get(field) for field in REVIEW_JOB_FIELDS if field != "id"] + [current["id"]],
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
                    "max_review_total_tokens": None,
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
                    f"INSERT INTO review_jobs ({', '.join(REVIEW_JOB_FIELDS)}) VALUES ({', '.join('?' for _ in REVIEW_JOB_FIELDS)})",
                    [row.get(field) for field in REVIEW_JOB_FIELDS],
                )
            con.commit()
            return self._decode_task_row(con.execute("SELECT * FROM review_jobs WHERE id = ?", (task_id,)).fetchone()) or {}

    def get_review_job(self, task: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM review_jobs WHERE id = ? OR name = ?", (task, task)).fetchone()
            if row is None:
                raise KeyError(task)
            return self._decode_task_row(row) or {}

    def list_review_jobs(self) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as con:
            return [self._decode_task_row(r) or {} for r in con.execute("SELECT * FROM review_jobs ORDER BY name").fetchall()]

    def list_due_review_jobs(self, *, now: float | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self.migrate()
        now = time.time() if now is None else now
        with closing(self.connect()) as con:
            rows = con.execute(
                """
                SELECT t.* FROM review_jobs t
                WHERE t.enabled = 1 AND COALESCE(t.next_due_at, 0) <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM review_runs r
                    WHERE r.task_id = t.id AND r.status = 'running' AND COALESCE(r.lease_expires_at, 0) > ?
                  )
                ORDER BY COALESCE(t.next_due_at, 0), t.name LIMIT ?
                """,
                (now, now, max(1, int(limit))),
            ).fetchall()
            return [self._decode_task_row(r) or {} for r in rows]

    def claim_review_job(self, task: str, *, lease_owner: str, lease_seconds: int = 300, now: float | None = None, reason: str = "due") -> dict[str, Any] | None:
        self.migrate()
        now = time.time() if now is None else now
        with closing(self.connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            task_row = con.execute("SELECT * FROM review_jobs WHERE id = ? OR name = ?", (task, task)).fetchone()
            if task_row is None or not int(task_row["enabled"]):
                con.rollback()
                return None
            active = con.execute(
                "SELECT id FROM review_runs WHERE task_id = ? AND status = 'running' AND COALESCE(lease_expires_at, 0) > ? LIMIT 1",
                (task_row["id"], now),
            ).fetchone()
            if active:
                con.rollback()
                return None
            con.execute(
                "UPDATE review_runs SET status = 'failed', finished_at = ?, stop_reason = 'error', error = 'lease expired', updated_at = ? WHERE task_id = ? AND status = 'running'",
                (now, now, task_row["id"]),
            )
            params = {k: task_row[k] for k in REVIEW_JOB_FIELDS if k not in {"params_json", "created_at", "updated_at"}}
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
                f"INSERT INTO review_runs ({', '.join(REVIEW_RUN_FIELDS)}) VALUES ({', '.join('?' for _ in REVIEW_RUN_FIELDS)})",
                [run.get(field, 0) for field in REVIEW_RUN_FIELDS],
            )
            con.execute("UPDATE review_jobs SET last_started_at = ?, last_run_id = ?, updated_at = ? WHERE id = ?", (now, run_id, now, task_row["id"]))
            con.commit()
            return self._decode_run_row(con.execute("SELECT * FROM review_runs WHERE id = ?", (run_id,)).fetchone())

    def heartbeat_review_run(self, run_id: str, *, lease_seconds: int = 300, now: float | None = None) -> None:
        self.migrate()
        now = time.time() if now is None else now
        with closing(self.connect()) as con:
            con.execute("UPDATE review_runs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ? WHERE id = ? AND status = 'running'", (now, now + lease_seconds, now, run_id))
            con.commit()

    def finish_review_run(self, run_id: str, *, status: str = "succeeded", stop_reason: str = "completed", next_due_at: float | None = None, metrics: dict[str, Any] | None = None, error: str | None = None, now: float | None = None) -> dict[str, Any]:
        self.migrate()
        now = time.time() if now is None else now
        metrics = metrics or {}
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM review_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["status"] != "running":
                return self._decode_run_row(row) or {}
            con.execute(
                """
                UPDATE review_runs SET status = ?, finished_at = ?, lease_expires_at = NULL,
                    imported_cases = ?, selected_cases = ?, reviewed_cases = ?, tool_outcome_reviews = ?,
                    llm_review_calls_used = ?, review_prompt_tokens = ?, review_completion_tokens = ?,
                    review_total_tokens = ?, stop_reason = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status, now, int(metrics.get("imported_cases") or 0), int(metrics.get("selected_cases") or 0),
                    int(metrics.get("reviewed_cases") or 0), int(metrics.get("tool_outcome_reviews") or 0),
                    int(metrics.get("llm_review_calls_used") or 0), int(metrics.get("review_prompt_tokens") or 0),
                    int(metrics.get("review_completion_tokens") or 0), int(metrics.get("review_total_tokens") or 0),
                    stop_reason, error, now, run_id,
                ),
            )
            con.execute(
                "UPDATE review_jobs SET last_finished_at = ?, last_success_at = CASE WHEN ? = 'succeeded' THEN ? ELSE last_success_at END, next_due_at = COALESCE(?, next_due_at), updated_at = ? WHERE id = ?",
                (now, status, now, next_due_at, now, row["task_id"]),
            )
            con.commit()
            return self._decode_run_row(con.execute("SELECT * FROM review_runs WHERE id = ?", (run_id,)).fetchone()) or {}

    def fail_review_run(self, run_id: str, *, error: str, next_due_at: float | None = None, now: float | None = None) -> dict[str, Any]:
        return self.finish_review_run(run_id, status="failed", stop_reason="error", next_due_at=next_due_at, error=error, now=now)

    def get_review_job_cursor(self, task_id: str, source: str, cursor_key: str) -> Any:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT cursor_json FROM review_job_cursors WHERE task_id = ? AND source = ? AND cursor_key = ?", (task_id, source, cursor_key)).fetchone()
            if row is None:
                return None
            return json.loads(row["cursor_json"])

    def set_review_job_cursor(self, task_id: str, source: str, cursor_key: str, value: Any) -> None:
        self.migrate()
        now = time.time()
        with closing(self.connect()) as con:
            con.execute(
                """
                INSERT INTO review_job_cursors (task_id, source, cursor_key, cursor_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id, source, cursor_key) DO UPDATE SET cursor_json = excluded.cursor_json, updated_at = excluded.updated_at
                """,
                (task_id, source, cursor_key, json.dumps(value, ensure_ascii=False), now),
            )
            con.commit()

    def list_review_runs(self, *, task_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self.migrate()
        sql = "SELECT * FROM review_runs"
        params: list[Any] = []
        if task_id is not None:
            sql += " WHERE task_id = ?"
            params.append(task_id)
        sql += " ORDER BY COALESCE(started_at, created_at) DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with closing(self.connect()) as con:
            return [self._decode_run_row(r) or {} for r in con.execute(sql, params).fetchall()]

    def upsert_turn_case(self, unit: dict[str, Any]) -> None:
        self.migrate()
        now = time.time()
        row = dict(unit)
        row.setdefault("created_at", now)
        row["updated_at"] = now
        source_session = {
            "id": row["source_session_id"],
            "framework": row.get("framework") or "hermes",
            "source": row.get("source"),
            "model": row.get("model"),
            "title": row.get("title"),
            "parent_session_id": row.get("parent_session_id"),
            "started_at": row.get("started_at"),
            "ended_at": row.get("ended_at"),
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "source_payload_json": row.get("source_payload_json"),
            "created_at": row.get("created_at", now),
            "updated_at": now,
        }
        with closing(self.connect()) as con:
            con.execute(
                f"INSERT INTO source_sessions ({', '.join(SOURCE_SESSION_FIELDS)}) VALUES ({', '.join('?' for _ in SOURCE_SESSION_FIELDS)}) "
                "ON CONFLICT(id) DO UPDATE SET "
                + ", ".join(f"{field}=excluded.{field}" for field in SOURCE_SESSION_FIELDS if field != "id"),
                [source_session.get(field) for field in SOURCE_SESSION_FIELDS],
            )
            placeholders = ", ".join("?" for _ in TURN_CASE_FIELDS)
            assignments = ", ".join(f"{field}=excluded.{field}" for field in TURN_CASE_FIELDS if field != "id")
            con.execute(
                f"INSERT INTO turn_cases ({', '.join(TURN_CASE_FIELDS)}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {assignments}",
                [row.get(field) for field in TURN_CASE_FIELDS],
            )
            con.execute("DELETE FROM case_events WHERE turn_case_id = ?", (row["id"],))
            for idx, event in enumerate(unit.get("case_events") or [], start=1):
                event_row = dict(event)
                source_event_id = event_row.get("id") or event_row.get("source_event_id")
                event_row["id"] = f"{row['id']}:event:{idx}"
                event_row.setdefault("source_event_id", source_event_id)
                event_row["turn_case_id"] = row["id"]
                if isinstance(event_row.get("source_payload_json"), (dict, list)):
                    event_row["source_payload_json"] = json.dumps(event_row["source_payload_json"], ensure_ascii=False)
                event_row["output_error"] = 1 if event_row.get("output_error") else 0
                con.execute(
                    f"INSERT INTO case_events ({', '.join(CASE_EVENT_FIELDS)}) VALUES ({', '.join('?' for _ in CASE_EVENT_FIELDS)})",
                    [event_row.get(field) for field in CASE_EVENT_FIELDS],
                )
            con.commit()

    def delete_stale_session_cases(self, source_session_id: str, keep_ids: set[str]) -> int:
        """Remove imported cases for a session that no longer normalize.

        This lets normalization fixes remove synthetic/context-compaction turns
        from the sidecar instead of leaving stale due cases behind.
        """
        self.migrate()
        with closing(self.connect()) as con:
            params: list[Any] = [str(source_session_id)]
            sql = "DELETE FROM turn_cases WHERE source_session_id = ?"
            if keep_ids:
                sql += f" AND id NOT IN ({', '.join('?' for _ in keep_ids)})"
                params.extend(sorted(keep_ids))
            cur = con.execute(sql, params)
            con.commit()
            return int(cur.rowcount or 0)

    def replace_signals(self, turn_case_id: str, signals: list[dict[str, Any]]) -> None:
        self.migrate()
        now = time.time()
        with closing(self.connect()) as con:
            con.execute("DELETE FROM case_signals WHERE turn_case_id = ?", (turn_case_id,))
            for idx, signal in enumerate(signals, start=1):
                signal_type = signal.get("signal_type")
                signal_id = signal.get("id") or f"{turn_case_id}:signal:{signal_type}:{idx}"
                source_payload = signal.get("source_payload_json")
                if isinstance(source_payload, (dict, list)):
                    source_payload = json.dumps(source_payload, ensure_ascii=False)
                con.execute(
                    """
                    INSERT INTO case_signals
                        (id, turn_case_id, signal_type, signal_value, score, severity, evidence_text, case_event_id, source_payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal_id,
                        turn_case_id,
                        signal_type,
                        str(signal["signal_value"]),
                        signal.get("score"),
                        signal.get("severity"),
                        signal.get("evidence_text"),
                        signal.get("case_event_id"),
                        source_payload,
                        now,
                    ),
                )
            con.commit()

    def list_turn_cases(self, limit: int = 50, since: float | None = None) -> list[dict[str, Any]]:
        self.migrate()
        sql = "SELECT * FROM turn_cases"
        params: list[Any] = []
        if since is not None:
            sql += " WHERE started_at >= ?"
            params.append(since)
        sql += " ORDER BY started_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def list_session_cases(self, source_session_id: str, limit: int = 50, since: float | None = None) -> list[dict[str, Any]]:
        self.migrate()
        sql = "SELECT * FROM turn_cases WHERE source_session_id = ?"
        params: list[Any] = [str(source_session_id)]
        if since is not None:
            sql += " AND started_at >= ?"
            params.append(since)
        sql += " ORDER BY started_at DESC, turn_index ASC, id DESC LIMIT ?"
        params.append(limit)
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def get_turn_case_with_trace(self, turn_case_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM turn_cases WHERE id = ?", (turn_case_id,)).fetchone()
            if row is None:
                raise KeyError(turn_case_id)
            unit = dict(row)
            unit["case_events"] = [dict(r) for r in con.execute("SELECT * FROM case_events WHERE turn_case_id = ? ORDER BY event_at, id", (turn_case_id,)).fetchall()]
            return unit

    def _resolve_feedback_target(self, con: sqlite3.Connection, target_type: str, target_id: str, turn_case_id: str | None = None) -> tuple[str, str | None]:
        params: list[Any]
        if target_type == "turn_case":
            row = con.execute("SELECT id FROM turn_cases WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(target_id)
            return str(row["id"]), str(row["id"])
        if target_type == "case_review":
            row = con.execute("SELECT id, turn_case_id FROM case_reviews WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(target_id)
            return str(row["id"]), str(row["turn_case_id"])
        if target_type == "case_event":
            sql = "SELECT id, turn_case_id FROM case_events WHERE (id = ? OR source_event_id = ?)"
            params = [target_id, target_id]
            if turn_case_id:
                sql += " AND turn_case_id = ?"
                params.append(turn_case_id)
            sql += " ORDER BY id LIMIT 1"
            row = con.execute(sql, params).fetchone()
            if row is None:
                raise KeyError(target_id)
            return str(row["id"]), str(row["turn_case_id"])
        if target_type == "tool_outcome_case":
            row = con.execute("SELECT id, turn_case_id FROM tool_outcome_cases WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(target_id)
            return str(row["id"]), None if row["turn_case_id"] is None else str(row["turn_case_id"])
        raise ValueError(f"unsupported feedback target_type {target_type!r}")

    def insert_feedback(
        self,
        *,
        target_type: str,
        target_id: str,
        label: str,
        turn_case_id: str | None = None,
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
                str(turn_case_id) if turn_case_id else None,
            )
            final_unit_id = str(turn_case_id) if turn_case_id else resolved_unit_id
            if final_unit_id is not None and con.execute("SELECT 1 FROM turn_cases WHERE id = ?", (final_unit_id,)).fetchone() is None:
                raise KeyError(final_unit_id)
            cur = con.execute(
                """
                INSERT INTO review_feedback
                    (target_type, target_id, turn_case_id, label, correction, source, reviewer, comment, created_at)
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
        turn_case_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.migrate()
        sql = "SELECT * FROM review_feedback WHERE 1 = 1"
        params: list[Any] = []
        if target_type:
            sql += " AND target_type = ?"
            params.append(_validate_feedback_target_type(target_type))
        if target_id:
            sql += " AND target_id = ?"
            params.append(str(target_id))
        if turn_case_id:
            sql += " AND turn_case_id = ?"
            params.append(str(turn_case_id))
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with closing(self.connect()) as con:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        for row in rows:
            row["correction"] = bool(row.get("correction"))
        return rows

    def list_due_turn_cases(
        self,
        limit: int = 50,
        since: float | None = None,
        reevaluate: bool = False,
        cooldown_seconds: float = 7200,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return imported cases eligible for LLM judging.

        A unit is due immediately when it has a next-user reaction, because that
        reaction is useful retrospective evidence. Cases without a reaction wait
        for a cooldown so repeated eval batches do not spam the judge for fresh
        last turns that may soon gain reaction evidence.
        """
        self.migrate()
        if now is None:
            now = time.time()
        cutoff = now - max(0, cooldown_seconds)
        sql = """
            SELECT u.*
            FROM turn_cases u
            WHERE u.response_text IS NOT NULL
              AND (
                u.next_request_message_id IS NOT NULL
                OR u.next_request_text IS NOT NULL
                OR COALESCE(u.ended_at, u.started_at, u.updated_at) <= ?
              )
        """
        params: list[Any] = [cutoff]
        if since is not None:
            sql += " AND u.started_at >= ?"
            params.append(since)
        sql += """
              AND NOT EXISTS (
                SELECT 1 FROM case_reviews e
                WHERE e.turn_case_id = u.id
                  AND e.reviewer_type = 'automatic_llm'
                  AND e.review_scope = 'turn_case'
              )
              AND NOT EXISTS (
                SELECT 1 FROM automatic_llm_review_claims c
                WHERE c.target_type = 'turn_case'
                  AND c.target_id = u.id
              )
              AND NOT EXISTS (
                SELECT 1 FROM tool_outcome_reviews tor
                JOIN tool_outcome_cases toc ON toc.id = tor.tool_outcome_case_id
                WHERE toc.turn_case_id = u.id
                  AND tor.reviewer_type = 'automatic_llm'
              )
              AND NOT EXISTS (
                SELECT 1 FROM automatic_llm_review_claims child_claim
                WHERE child_claim.target_type = 'tool_outcome_case'
                  AND child_claim.parent_turn_case_id = u.id
              )
        """
        if not reevaluate:
            sql += " AND NOT EXISTS (SELECT 1 FROM case_reviews e WHERE e.turn_case_id = u.id)"
        sql += " ORDER BY u.started_at ASC, u.id ASC LIMIT ?"
        params.append(limit)
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def upsert_tool_outcome_case(self, example: dict[str, Any]) -> str:
        self.migrate()
        now = time.time()
        row = dict(example)
        if not row.get("turn_case_id") and row.get("source_session_id"):
            row["turn_case_id"] = f"hermes:{row['source_session_id']}:turn:{row.get('turn_index') or 1}"
        turn_case_id = str(row.get("turn_case_id") or "")
        if not turn_case_id:
            raise ValueError("turn_case_id is required")
        tool_interaction_id = str(row.get("tool_interaction_id") or f"{turn_case_id}:tool:{row.get('tool_call_id') or row.get('source_tool_call_id') or row.get('id')}")
        if not row.get("id"):
            row["id"] = f"tool_outcome_case:{tool_interaction_id}"
        case_row = {
            "id": row["id"],
            "turn_case_id": turn_case_id,
            "tool_interaction_id": tool_interaction_id,
            "turn_index": int(row.get("turn_index") or 0),
            "request_excerpt": row.get("request_excerpt") or row.get("request_text_excerpt"),
            "prior_response_excerpt": row.get("prior_response_excerpt") or row.get("prior_assistant_visible_text"),
            "following_response_excerpt": row.get("following_response_excerpt") or row.get("following_assistant_visible_text"),
            "caller_expectation_text": row.get("caller_expectation_text") or row.get("explicit_caller_expectation"),
            "caller_interpretation_text": row.get("caller_interpretation_text") or row.get("explicit_caller_interpretation"),
            "intent_source": row.get("intent_source") or row.get("upstream_intent_source"),
            "case_builder_version": row.get("case_builder_version") or "normalization_v1",
            "source_payload_json": row.get("source_payload_json"),
            "created_at": row.get("created_at", now),
            "updated_at": now,
        }
        if isinstance(case_row.get("source_payload_json"), (dict, list)):
            case_row["source_payload_json"] = json.dumps(case_row["source_payload_json"], ensure_ascii=False)
        interaction_row = {
            "id": tool_interaction_id,
            "turn_case_id": turn_case_id,
            "call_case_event_id": row.get("call_case_event_id"),
            "result_case_event_id": row.get("result_case_event_id"),
            "source_tool_call_id": row.get("source_tool_call_id") or row.get("tool_call_id"),
            "tool_name": row.get("tool_name") or "unknown",
            "tool_input_text": row.get("tool_input_text") or row.get("tool_arguments"),
            "tool_input_hash": row.get("tool_input_hash"),
            "tool_input_preview": row.get("tool_input_preview"),
            "tool_output_text": row.get("tool_output_text") or row.get("tool_result"),
            "tool_output_hash": row.get("tool_output_hash"),
            "tool_output_preview": row.get("tool_output_preview") or row.get("tool_result"),
            "tool_output_error": row.get("tool_output_error"),
            "called_at": row.get("called_at"),
            "completed_at": row.get("completed_at") or row.get("result_timestamp"),
            "duration_ms": row.get("duration_ms"),
            "source_payload_json": case_row.get("source_payload_json"),
            "created_at": now,
        }
        with closing(self.connect()) as con:
            if con.execute("SELECT 1 FROM turn_cases WHERE id = ?", (turn_case_id,)).fetchone() is None:
                self.upsert_turn_case({
                    "id": turn_case_id,
                    "framework": row.get("framework") or "hermes",
                    "source_session_id": row.get("source_session_id") or turn_case_id,
                    "turn_index": int(row.get("turn_index") or 0),
                    "request_text": row.get("request_excerpt") or row.get("request_text_excerpt") or "",
                    "response_text": row.get("following_response_excerpt") or row.get("following_assistant_visible_text"),
                    "tool_interaction_count": 1,
                    "source_session_api_interaction_count": None,
                    "case_builder_version": row.get("case_builder_version") or "normalization_v1",
                    "case_events": [],
                })
            con.execute(
                f"INSERT INTO tool_interactions ({', '.join(TOOL_INTERACTION_FIELDS)}) VALUES ({', '.join('?' for _ in TOOL_INTERACTION_FIELDS)}) "
                "ON CONFLICT(id) DO UPDATE SET "
                + ", ".join(f"{field}=excluded.{field}" for field in TOOL_INTERACTION_FIELDS if field != "id"),
                [interaction_row.get(field) for field in TOOL_INTERACTION_FIELDS],
            )
            con.execute(
                f"INSERT INTO tool_outcome_cases ({', '.join(TOOL_OUTCOME_CASE_FIELDS)}) VALUES ({', '.join('?' for _ in TOOL_OUTCOME_CASE_FIELDS)}) "
                "ON CONFLICT(tool_interaction_id) DO UPDATE SET "
                + ", ".join(f"{field}=excluded.{field}" for field in TOOL_OUTCOME_CASE_FIELDS if field not in {"id", "created_at"}),
                [case_row.get(field) for field in TOOL_OUTCOME_CASE_FIELDS],
            )
            con.commit()
            return str(case_row["id"])

    def list_tool_outcome_cases(
        self,
        *,
        source_session_id: str | None = None,
        since: float | None = None,
        limit: int = 50,
        unlabeled: bool = False,
        unpredicted: bool = False,
        prioritize_prediction_gaps: bool = False,
        exclude_automatic_case_reviewed: bool = False,
        llm_eligible_only: bool = False,
    ) -> list[dict[str, Any]]:
        self.migrate()
        sql = """
            WITH latest_ml AS (
                SELECT tool_outcome_case_id, MAX(created_at) AS max_created
                FROM tool_outcome_reviews
                WHERE reviewer_type = 'ml_model'
                GROUP BY tool_outcome_case_id
            )
            SELECT
                e.*,
                ti.tool_name,
                ti.tool_input_text AS tool_arguments,
                ti.tool_output_text AS tool_result,
                ti.called_at AS called_at,
                ti.completed_at AS result_timestamp,
                e.request_excerpt AS request_text_excerpt,
                e.prior_response_excerpt AS prior_assistant_visible_text,
                e.following_response_excerpt AS following_assistant_visible_text,
                e.caller_expectation_text AS explicit_caller_expectation,
                e.caller_interpretation_text AS explicit_caller_interpretation,
                p.outcome_label AS prediction_label,
                p.reason_code AS prediction_reason_code,
                p.confidence AS prediction_confidence,
                p.uncertainty AS prediction_uncertainty,
                p.review_source_detail AS prediction_decision_source,
                p.reviewer_name AS prediction_model_name,
                p.reviewer_version AS prediction_model_version,
                p.needs_llm_review AS prediction_should_defer_to_llm,
                p.llm_review_budget_available AS prediction_llm_review_budget_available,
                p.budget_fallback AS prediction_budget_fallback,
                p.evidence_json AS prediction_evidence_json
            FROM tool_outcome_cases e
            JOIN tool_interactions ti ON ti.id = e.tool_interaction_id
            LEFT JOIN latest_ml lp ON lp.tool_outcome_case_id = e.id
            LEFT JOIN tool_outcome_reviews p ON p.tool_outcome_case_id = lp.tool_outcome_case_id AND p.created_at = lp.max_created
            WHERE 1 = 1
        """
        params: list[Any] = []
        if source_session_id is not None:
            sql += " AND e.turn_case_id IN (SELECT id FROM turn_cases WHERE source_session_id = ?)"
            params.append(str(source_session_id))
        if since is not None:
            sql += " AND COALESCE(ti.completed_at, 0) >= ?"
            params.append(float(since))
        if unlabeled:
            sql += " AND NOT EXISTS (SELECT 1 FROM tool_outcome_reviews l WHERE l.tool_outcome_case_id = e.id AND l.reviewer_type IN ('human', 'human_correction', 'automatic_llm'))"
        if unpredicted:
            sql += " AND NOT EXISTS (SELECT 1 FROM tool_outcome_reviews p2 WHERE p2.tool_outcome_case_id = e.id AND p2.reviewer_type = 'ml_model')"
        if exclude_automatic_case_reviewed or llm_eligible_only:
            sql += """
                AND NOT EXISTS (
                    SELECT 1 FROM case_reviews cr
                    WHERE cr.turn_case_id = e.turn_case_id
                      AND cr.reviewer_type = 'automatic_llm'
                      AND cr.review_scope = 'turn_case'
                )
            """
        if llm_eligible_only:
            sql += """
                AND NOT EXISTS (
                    SELECT 1 FROM tool_outcome_reviews tor
                    WHERE tor.tool_outcome_case_id = e.id
                      AND tor.reviewer_type = 'automatic_llm'
                )
                AND NOT EXISTS (
                    SELECT 1 FROM automatic_llm_review_claims c
                    WHERE c.target_type = 'tool_outcome_case'
                      AND c.target_id = e.id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM automatic_llm_review_claims parent_claim
                    WHERE parent_claim.target_type = 'turn_case'
                      AND parent_claim.target_id = e.turn_case_id
                )
            """
        if prioritize_prediction_gaps:
            sql += """
                ORDER BY
                    CASE
                        WHEN COALESCE(p.budget_fallback, 0) = 1 THEN 0
                        WHEN COALESCE(p.needs_llm_review, 0) = 1 THEN 1
                        WHEN p.confidence IS NULL THEN 2
                        WHEN p.confidence < 0.85 THEN 3
                        ELSE 4
                    END ASC,
                    COALESCE(p.confidence, -1.0) ASC,
                    COALESCE(ti.completed_at, 0) ASC,
                    e.id ASC
                LIMIT ?
            """
        else:
            sql += " ORDER BY COALESCE(ti.completed_at, 0) ASC, e.id ASC LIMIT ?"
        params.append(max(1, int(limit)))
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def turn_case_has_automatic_case_review(self, turn_case_id: str) -> bool:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                """
                SELECT 1 FROM case_reviews
                WHERE turn_case_id = ? AND reviewer_type = 'automatic_llm' AND review_scope = 'turn_case'
                LIMIT 1
                """,
                (turn_case_id,),
            ).fetchone()
            return row is not None

    def turn_case_has_automatic_llm_claim(self, turn_case_id: str) -> bool:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                "SELECT 1 FROM automatic_llm_review_claims WHERE target_type = 'turn_case' AND target_id = ? LIMIT 1",
                (turn_case_id,),
            ).fetchone()
            return row is not None

    def tool_outcome_case_has_automatic_llm_review(self, tool_outcome_case_id: str) -> bool:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                "SELECT 1 FROM tool_outcome_reviews WHERE tool_outcome_case_id = ? AND reviewer_type = 'automatic_llm' LIMIT 1",
                (tool_outcome_case_id,),
            ).fetchone()
            return row is not None

    def tool_outcome_case_has_automatic_llm_claim(self, tool_outcome_case_id: str) -> bool:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                "SELECT 1 FROM automatic_llm_review_claims WHERE target_type = 'tool_outcome_case' AND target_id = ? LIMIT 1",
                (tool_outcome_case_id,),
            ).fetchone()
            return row is not None

    def is_turn_case_llm_eligible(self, turn_case_id: str) -> bool:
        target_id = str(turn_case_id or "")
        if not target_id:
            return False
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                """
                SELECT 1 FROM turn_cases tc
                WHERE tc.id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM case_reviews cr
                    WHERE cr.turn_case_id = tc.id
                      AND cr.reviewer_type = 'automatic_llm'
                      AND cr.review_scope = 'turn_case'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM automatic_llm_review_claims c
                    WHERE c.target_type = 'turn_case'
                      AND c.target_id = tc.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM tool_outcome_reviews tor
                    JOIN tool_outcome_cases toc ON toc.id = tor.tool_outcome_case_id
                    WHERE toc.turn_case_id = tc.id
                      AND tor.reviewer_type = 'automatic_llm'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM automatic_llm_review_claims child_claim
                    WHERE child_claim.target_type = 'tool_outcome_case'
                      AND child_claim.parent_turn_case_id = tc.id
                  )
                LIMIT 1
                """,
                (target_id,),
            ).fetchone()
            return row is not None

    def is_tool_outcome_case_llm_eligible(self, tool_outcome_case_id: str) -> bool:
        target_id = str(tool_outcome_case_id or "")
        if not target_id:
            return False
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                """
                SELECT 1 FROM tool_outcome_cases toc
                WHERE toc.id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM tool_outcome_reviews tor
                    WHERE tor.tool_outcome_case_id = toc.id
                      AND tor.reviewer_type = 'automatic_llm'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM automatic_llm_review_claims c
                    WHERE c.target_type = 'tool_outcome_case'
                      AND c.target_id = toc.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM case_reviews cr
                    WHERE cr.turn_case_id = toc.turn_case_id
                      AND cr.reviewer_type = 'automatic_llm'
                      AND cr.review_scope = 'turn_case'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM automatic_llm_review_claims parent_claim
                    WHERE parent_claim.target_type = 'turn_case'
                      AND parent_claim.target_id = toc.turn_case_id
                  )
                LIMIT 1
                """,
                (target_id,),
            ).fetchone()
            return row is not None

    def claim_automatic_llm_review(
        self,
        target_type: str,
        target_id: str,
        *,
        run_id: str | None = None,
        source: str | None = None,
        src: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, Any] | None:
        self.migrate()
        normalized_type = str(target_type or "")
        if normalized_type not in {"turn_case", "tool_outcome_case"}:
            raise ValueError("target_type must be turn_case or tool_outcome_case")
        target_id = str(target_id or "")
        if not target_id:
            return None
        now = time.time()
        claim_id = f"automatic-llm-claim:{normalized_type}:{target_id}:{int(now * 1000000)}"
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
        claim_source = source or src
        with closing(self.connect()) as con:
            if normalized_type == "turn_case":
                con.execute(
                    """
                    INSERT OR IGNORE INTO automatic_llm_review_claims
                        (id, target_type, target_id, parent_turn_case_id, run_id, src, status, claimed_at, metadata_json)
                    SELECT ?, 'turn_case', tc.id, tc.id, ?, ?, 'claimed', ?, ?
                    FROM turn_cases tc
                    WHERE tc.id = ?
                      AND NOT EXISTS (
                        SELECT 1 FROM case_reviews cr
                        WHERE cr.turn_case_id = tc.id
                          AND cr.reviewer_type = 'automatic_llm'
                          AND cr.review_scope = 'turn_case'
                      )
                       AND NOT EXISTS (
                         SELECT 1 FROM automatic_llm_review_claims c
                         WHERE c.target_type = 'turn_case'
                           AND c.target_id = tc.id
                       )
                       AND NOT EXISTS (
                         SELECT 1 FROM tool_outcome_reviews tor
                         JOIN tool_outcome_cases toc ON toc.id = tor.tool_outcome_case_id
                         WHERE toc.turn_case_id = tc.id
                           AND tor.reviewer_type = 'automatic_llm'
                       )
                       AND NOT EXISTS (
                         SELECT 1 FROM automatic_llm_review_claims child_claim
                         WHERE child_claim.target_type = 'tool_outcome_case'
                           AND child_claim.parent_turn_case_id = tc.id
                       )
                    """,
                    (claim_id, run_id, claim_source, now, metadata_json, target_id),
                )
            else:
                con.execute(
                    """
                    INSERT OR IGNORE INTO automatic_llm_review_claims
                        (id, target_type, target_id, parent_turn_case_id, run_id, src, status, claimed_at, metadata_json)
                    SELECT ?, 'tool_outcome_case', toc.id, toc.turn_case_id, ?, ?, 'claimed', ?, ?
                    FROM tool_outcome_cases toc
                    WHERE toc.id = ?
                      AND NOT EXISTS (
                        SELECT 1 FROM tool_outcome_reviews tor
                        WHERE tor.tool_outcome_case_id = toc.id
                          AND tor.reviewer_type = 'automatic_llm'
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM case_reviews cr
                        WHERE cr.turn_case_id = toc.turn_case_id
                          AND cr.reviewer_type = 'automatic_llm'
                          AND cr.review_scope = 'turn_case'
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM automatic_llm_review_claims c
                        WHERE c.target_type = 'tool_outcome_case'
                          AND c.target_id = toc.id
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM automatic_llm_review_claims parent_claim
                        WHERE parent_claim.target_type = 'turn_case'
                          AND parent_claim.target_id = toc.turn_case_id
                      )
                    """,
                    (claim_id, run_id, claim_source, now, metadata_json, target_id),
                )
            row = con.execute("SELECT * FROM automatic_llm_review_claims WHERE id = ?", (claim_id,)).fetchone()
            con.commit()
            return dict(row) if row is not None else None

    def mark_automatic_llm_claim_started(self, claim_id: str) -> bool:
        """Atomically transition a claim to the final pre-call state.

        Returns False when the claim no longer authorizes an automatic LLM call.
        This is intentionally fail-closed: the claim row remains as a spend
        barrier, but the caller must skip the LLM call.
        """
        self.migrate()
        now = time.time()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM automatic_llm_review_claims WHERE id = ?", (claim_id,)).fetchone()
            if row is None or row["status"] != "claimed":
                return False
            blocked_reason: str | None = None
            if row["target_type"] == "turn_case":
                blocked = con.execute(
                    """
                    SELECT reason FROM (
                        SELECT 'target turn_case already has automatic LLM review' AS reason
                        FROM case_reviews
                        WHERE turn_case_id = ?
                          AND reviewer_type = 'automatic_llm'
                          AND review_scope = 'turn_case'
                        LIMIT 1
                    )
                    UNION ALL
                    SELECT reason FROM (
                        SELECT 'child tool_outcome_case already has automatic LLM review' AS reason
                        FROM tool_outcome_reviews tor
                        JOIN tool_outcome_cases toc ON toc.id = tor.tool_outcome_case_id
                        WHERE toc.turn_case_id = ?
                          AND tor.reviewer_type = 'automatic_llm'
                        LIMIT 1
                    )
                    UNION ALL
                    SELECT reason FROM (
                        SELECT 'child tool_outcome_case already has automatic LLM claim' AS reason
                        FROM automatic_llm_review_claims child_claim
                        WHERE child_claim.target_type = 'tool_outcome_case'
                          AND child_claim.parent_turn_case_id = ?
                        LIMIT 1
                    )
                    LIMIT 1
                    """,
                    (row["target_id"], row["target_id"], row["target_id"]),
                ).fetchone()
                if blocked is not None:
                    blocked_reason = str(blocked["reason"])
            elif row["target_type"] == "tool_outcome_case":
                blocked = con.execute(
                    """
                    SELECT reason FROM (
                        SELECT 'target tool_outcome_case already has automatic LLM review' AS reason
                        FROM tool_outcome_reviews tor
                        WHERE tor.tool_outcome_case_id = ?
                          AND tor.reviewer_type = 'automatic_llm'
                        LIMIT 1
                    )
                    UNION ALL
                    SELECT reason FROM (
                        SELECT 'parent turn_case already has automatic LLM review' AS reason
                        FROM case_reviews cr
                        WHERE cr.turn_case_id = ?
                          AND cr.reviewer_type = 'automatic_llm'
                          AND cr.review_scope = 'turn_case'
                        LIMIT 1
                    )
                    UNION ALL
                    SELECT reason FROM (
                        SELECT 'parent turn_case already has automatic LLM claim' AS reason
                        FROM automatic_llm_review_claims parent_claim
                        WHERE parent_claim.target_type = 'turn_case'
                          AND parent_claim.target_id = ?
                        LIMIT 1
                    )
                    LIMIT 1
                    """,
                    (row["target_id"], row["parent_turn_case_id"], row["parent_turn_case_id"]),
                ).fetchone()
                if blocked is not None:
                    blocked_reason = str(blocked["reason"])
            else:
                blocked_reason = "invalid automatic LLM claim target_type"
            if blocked_reason:
                con.execute(
                    """
                    UPDATE automatic_llm_review_claims
                    SET status = 'failed_before_call',
                        completed_at = COALESCE(completed_at, ?),
                        error_message = ?
                    WHERE id = ?
                    """,
                    (now, blocked_reason, claim_id),
                )
                con.commit()
                return False
            cur = con.execute(
                """
                UPDATE automatic_llm_review_claims
                SET status = 'llm_started', llm_started_at = COALESCE(llm_started_at, ?)
                WHERE id = ? AND status = 'claimed'
                """,
                (now, claim_id),
            )
            con.commit()
            return cur.rowcount == 1

    def mark_automatic_llm_claim_review_inserted(self, claim_id: str) -> None:
        self.migrate()
        with closing(self.connect()) as con:
            con.execute(
                "UPDATE automatic_llm_review_claims SET status = 'review_inserted', completed_at = COALESCE(completed_at, ?) WHERE id = ?",
                (time.time(), claim_id),
            )
            con.commit()

    def mark_automatic_llm_claim_failed(self, claim_id: str, *, before_call: bool, error_message: str) -> None:
        self.migrate()
        status = "failed_before_call" if before_call else "failed_before_review"
        with closing(self.connect()) as con:
            con.execute(
                "UPDATE automatic_llm_review_claims SET status = ?, completed_at = COALESCE(completed_at, ?), error_message = ? WHERE id = ?",
                (status, time.time(), str(error_message), claim_id),
            )
            con.commit()

    def list_duplicate_automatic_llm_reviews(self) -> dict[str, list[dict[str, Any]]]:
        self.migrate()
        with closing(self.connect()) as con:
            case_rows = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT turn_case_id, COUNT(*) AS review_count
                    FROM case_reviews
                    WHERE reviewer_type = 'automatic_llm'
                      AND review_scope = 'turn_case'
                    GROUP BY turn_case_id
                    HAVING COUNT(*) > 1
                    ORDER BY review_count DESC, turn_case_id ASC
                    """
                ).fetchall()
            ]
            tool_rows = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT tool_outcome_case_id, COUNT(*) AS review_count
                    FROM tool_outcome_reviews
                    WHERE reviewer_type = 'automatic_llm'
                    GROUP BY tool_outcome_case_id
                    HAVING COUNT(*) > 1
                    ORDER BY review_count DESC, tool_outcome_case_id ASC
                    """
                ).fetchall()
            ]
        return {"case_reviews": case_rows, "tool_outcome_reviews": tool_rows}

    def list_canonical_tool_outcome_cases(
        self,
        *,
        source_session_id: str | None = None,
        since: float | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.migrate()
        sql = """
            WITH latest_display AS (
                SELECT tool_outcome_case_id, MAX(created_at) AS max_created
                FROM tool_outcome_reviews
                WHERE reviewer_type IN ('human', 'human_correction', 'automatic_llm')
                GROUP BY tool_outcome_case_id
            ),
            latest_prediction AS (
                SELECT tool_outcome_case_id, MAX(created_at) AS max_created
                FROM tool_outcome_reviews
                WHERE reviewer_type = 'ml_model'
                GROUP BY tool_outcome_case_id
            ),
            latest_review AS (
                SELECT turn_case_id, MAX(created_at) AS max_created
                FROM case_reviews
                GROUP BY turn_case_id
            )
            SELECT
                e.id,
                e.turn_case_id,
                e.turn_index,
                ti.source_tool_call_id AS tool_call_id,
                ti.tool_name,
                ti.tool_output_text AS tool_result,
                ti.completed_at AS result_timestamp,
                e.request_excerpt AS request_text_excerpt,
                ss.id AS source_session_id,
                l.outcome_label AS label,
                l.reviewer_type AS label_source,
                p.outcome_label AS prediction_label,
                COALESCE(l.reason_code, p.reason_code) AS reason_code,
                COALESCE(le.friction_score, 0.0) AS friction_score
            FROM tool_outcome_cases e
            JOIN tool_interactions ti ON ti.id = e.tool_interaction_id
            JOIN turn_cases tc ON tc.id = e.turn_case_id
            JOIN source_sessions ss ON ss.id = tc.source_session_id
            LEFT JOIN latest_display ll ON ll.tool_outcome_case_id = e.id
            LEFT JOIN tool_outcome_reviews l ON l.tool_outcome_case_id = ll.tool_outcome_case_id AND l.created_at = ll.max_created
            LEFT JOIN latest_prediction lp ON lp.tool_outcome_case_id = e.id
            LEFT JOIN tool_outcome_reviews p ON p.tool_outcome_case_id = lp.tool_outcome_case_id AND p.created_at = lp.max_created
            LEFT JOIN latest_review lex ON lex.turn_case_id = e.turn_case_id
            LEFT JOIN case_reviews le ON le.turn_case_id = lex.turn_case_id AND le.created_at = lex.max_created
            WHERE 1 = 1
        """
        params: list[Any] = []
        if source_session_id is not None:
            sql += " AND ss.id = ?"
            params.append(str(source_session_id))
        if since is not None:
            sql += " AND COALESCE(ti.completed_at, 0) >= ?"
            params.append(float(since))
        sql += " ORDER BY COALESCE(ti.completed_at, 0) DESC, e.id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with closing(self.connect()) as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def get_tool_outcome_case(self, tool_outcome_case_id: str) -> dict[str, Any]:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM tool_outcome_cases WHERE id = ?", (tool_outcome_case_id,)).fetchone()
            if row is None:
                raise KeyError(tool_outcome_case_id)
            return dict(row)

    def export_tool_outcome_review_training(self, limit: int = 10000) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as con:
            rows = con.execute(
                """
                SELECT l.*, ti.tool_name, ti.tool_input_text AS tool_arguments, ti.tool_output_text AS tool_result,
                       e.request_excerpt AS request_text_excerpt,
                       e.prior_response_excerpt AS prior_assistant_visible_text,
                       e.following_response_excerpt AS following_assistant_visible_text,
                       e.caller_expectation_text AS explicit_caller_expectation,
                       e.caller_interpretation_text AS explicit_caller_interpretation
                FROM tool_outcome_reviews l
                JOIN tool_outcome_cases e ON e.id = l.tool_outcome_case_id
                JOIN tool_interactions ti ON ti.id = e.tool_interaction_id
                WHERE l.training_eligible = 1
                  AND l.reviewer_type IN ('automatic_llm', 'human', 'human_correction')
                ORDER BY l.created_at ASC, l.id ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["label"] = data.get("outcome_label")
            data["label_source"] = data.get("reviewer_type")
            data["weight"] = data.get("training_weight")
            data["text"] = "\n".join([
                f"tool={data.get('tool_name') or ''}",
                f"args={data.get('tool_arguments') or ''}",
                f"result={data.get('tool_result') or ''}",
                f"request={data.get('request_text_excerpt') or ''}",
                f"prior_assistant={data.get('prior_assistant_visible_text') or ''}",
                f"following_assistant={data.get('following_assistant_visible_text') or ''}",
            ])
            result.append(data)
        return result

    def list_tool_outcome_reviews(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM tool_outcome_reviews ORDER BY created_at DESC, id DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            ]

    def insert_tool_outcome_review(
        self,
        tool_outcome_case_id: str,
        *,
        label: str | None = None,
        outcome_label: str | None = None,
        reviewer_type: str | None = None,
        label_source: str | None = None,
        decision_source: str | None = None,
        **kwargs: Any,
    ) -> str:
        self.migrate()
        normalized_label = _validate_tool_outcome_label(outcome_label or label)
        reason_code = _validate_reason_code(kwargs.get("reason_code"))
        evidence = kwargs.get("evidence_json")
        if isinstance(evidence, (dict, list)):
            evidence = json.dumps(evidence, ensure_ascii=False)
        now = time.time()
        source = reviewer_type or label_source or decision_source or "human"
        if source == "tool_outcome_llm_reviewer":
            source = "automatic_llm"
        if source in {"ml_defer", "ml_budget_fallback", "ml_model", "ml_model_defer", "ml_model_budget_fallback"}:
            source = "ml_model"
        review_id = str(kwargs.get("id") or f"{tool_outcome_case_id}:review:{source}:{int(now * 1000)}")
        with closing(self.connect()) as con:
            case = con.execute("SELECT turn_case_id FROM tool_outcome_cases WHERE id = ?", (tool_outcome_case_id,)).fetchone()
            if case is None:
                raise KeyError(tool_outcome_case_id)
            training_eligible = kwargs.get("training_eligible", kwargs.get("accepted_for_training", False))
            training_weight = kwargs.get("training_weight", kwargs.get("weight"))
            if training_weight is None and training_eligible:
                training_weight = _tool_outcome_label_weight(source, kwargs.get("confidence"))
            cur = con.execute(
                """
                INSERT INTO tool_outcome_reviews
                    (id, tool_outcome_case_id, turn_case_id, reviewer_type, reviewer_name, reviewer_version,
                     review_source_detail, outcome_label, reason_code, confidence, uncertainty, evidence_summary,
                     evidence_json, training_eligible, training_weight, needs_llm_review, llm_review_budget_available,
                     budget_fallback, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    tool_outcome_case_id,
                    case["turn_case_id"],
                    source,
                    kwargs.get("reviewer") or kwargs.get("reviewer_name") or kwargs.get("model_name"),
                    kwargs.get("label_source_version") or kwargs.get("reviewer_version") or kwargs.get("model_version"),
                    decision_source or label_source or kwargs.get("review_source_detail"),
                    normalized_label,
                    reason_code,
                    kwargs.get("confidence"),
                    kwargs.get("uncertainty"),
                    kwargs.get("evidence_summary") or kwargs.get("comment"),
                    evidence,
                    1 if training_eligible else 0,
                    training_weight,
                    1 if kwargs.get("needs_llm_review", kwargs.get("should_defer_to_llm")) else 0,
                    None if kwargs.get("llm_review_budget_available") is None else (1 if kwargs.get("llm_review_budget_available") else 0),
                    1 if kwargs.get("budget_fallback") else 0,
                    now,
                ),
            )
            con.commit()
            return review_id

    def record_tool_outcome_reviewer_model(self, record: dict[str, Any]) -> str:
        self.migrate()
        now = time.time()
        model_id = str(record.get("id") or f"{record.get('model_name')}:{record.get('model_version')}")
        metrics = record.get("training_summary_json", record.get("metrics_json"))
        if isinstance(metrics, (dict, list)):
            metrics = json.dumps(metrics, ensure_ascii=False)
        with closing(self.connect()) as con:
            con.execute(
                """
                INSERT INTO tool_outcome_reviewer_models
                    (id, model_name, model_version, artifact_path, feature_schema_version,
                     review_schema_version, training_summary_json, promoted_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    model_name=excluded.model_name,
                    model_version=excluded.model_version,
                    artifact_path=excluded.artifact_path,
                    feature_schema_version=excluded.feature_schema_version,
                    review_schema_version=excluded.review_schema_version,
                    training_summary_json=excluded.training_summary_json,
                    promoted_at=COALESCE(excluded.promoted_at, tool_outcome_reviewer_models.promoted_at)
                """,
                (
                    model_id,
                    record["model_name"],
                    record["model_version"],
                    record["artifact_path"],
                    record.get("feature_schema_version") or "tool_outcome_features_v1",
                    record.get("review_schema_version") or "tool_outcome_review_v1",
                    metrics,
                    float(record.get("promoted_at") or now) if record.get("promoted") else record.get("promoted_at"),
                    float(record.get("created_at") or now),
                ),
            )
            con.commit()
        if record.get("promoted"):
            self.promote_tool_outcome_reviewer_model(model_id)
        return model_id

    def promote_tool_outcome_reviewer_model(self, model_id: str) -> None:
        self.migrate()
        now = time.time()
        with closing(self.connect()) as con:
            if con.execute("SELECT 1 FROM tool_outcome_reviewer_models WHERE id = ?", (model_id,)).fetchone() is None:
                raise KeyError(model_id)
            con.execute("UPDATE tool_outcome_reviewer_models SET promoted_at = NULL")
            con.execute("UPDATE tool_outcome_reviewer_models SET promoted_at = ? WHERE id = ?", (now, model_id))
            con.commit()

    def list_tool_outcome_reviewer_models(self) -> list[dict[str, Any]]:
        self.migrate()
        with closing(self.connect()) as con:
            rows = con.execute(
                """
                SELECT * FROM tool_outcome_reviewer_models
                ORDER BY CASE WHEN promoted_at IS NULL THEN 1 ELSE 0 END, COALESCE(promoted_at, created_at) DESC, created_at DESC, model_version DESC
                """
            ).fetchall()
            return [self._decode_tool_outcome_reviewer_model_row(row) or {} for row in rows]

    def get_promoted_tool_outcome_reviewer_model(self) -> dict[str, Any] | None:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute("SELECT * FROM tool_outcome_reviewer_models WHERE promoted_at IS NOT NULL ORDER BY promoted_at DESC LIMIT 1").fetchone()
            return self._decode_tool_outcome_reviewer_model_row(row)

    def insert_case_review(
        self,
        turn_case_id: str,
        *,
        prompt_version: str,
        judge_provider: str | None,
        judge_model: str | None,
        eval_data: dict[str, Any],
        evaluator_error: str | None = None,
        review_prompt_tokens: int = 0,
        review_completion_tokens: int = 0,
        review_total_tokens: int = 0,
        judge_call_count: int = 0,
        reviewer_type: str = "automatic_llm",
        review_scope: str = "turn_case",
    ) -> str:
        self.migrate()
        now = time.time()
        review_id = f"{turn_case_id}:review:{reviewer_type}:{int(now * 1000)}"
        outcome_status = str(eval_data.get("outcome_status") or "")
        if outcome_status not in {"succeed", "failed", "mishandled", "prolonged"}:
            raise ValueError("outcome_status must be one of failed, mishandled, prolonged, succeed")
        deleted_fields = {"not_evaluable_reason", "request_smoothness", "smoothness_score"}
        present_deleted = sorted(field for field in deleted_fields if field in eval_data)
        if present_deleted:
            raise ValueError(f"deleted request eval fields are not accepted: {', '.join(present_deleted)}")
        confidence = str(eval_data.get("confidence") or "low")
        summary_reason = str(eval_data.get("summary_reason") or evaluator_error or "No primary reason supplied")
        if "friction_score" not in eval_data:
            raise ValueError("friction_score is required")
        friction_score = float(eval_data["friction_score"])
        if friction_score < 0.0 or friction_score > 1.0:
            raise ValueError("friction_score must be between 0 and 1")
        with closing(self.connect()) as con:
            if reviewer_type == "automatic_llm" and review_scope == "turn_case":
                blocked = con.execute(
                    """
                    SELECT 1 FROM tool_outcome_reviews tor
                    JOIN tool_outcome_cases toc ON toc.id = tor.tool_outcome_case_id
                    WHERE toc.turn_case_id = ?
                      AND tor.reviewer_type = 'automatic_llm'
                    LIMIT 1
                    """,
                    (turn_case_id,),
                ).fetchone()
                if blocked is not None:
                    raise RuntimeError("turn_case automatic LLM review blocked by child tool_outcome_case automatic LLM review")
                blocked = con.execute(
                    """
                    SELECT 1 FROM automatic_llm_review_claims child_claim
                    WHERE child_claim.target_type = 'tool_outcome_case'
                      AND child_claim.parent_turn_case_id = ?
                    LIMIT 1
                    """,
                    (turn_case_id,),
                ).fetchone()
                if blocked is not None:
                    raise RuntimeError("turn_case automatic LLM review blocked by child tool_outcome_case automatic LLM claim")
            con.execute(
                """
                INSERT INTO case_reviews
                    (id, turn_case_id, reviewer_type, review_scope, review_prompt_version, reviewer_provider, reviewer_model,
                     outcome_status, confidence, summary_reason, review_json, review_error, friction_score,
                     prompt_tokens, completion_tokens, total_tokens, review_call_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    turn_case_id,
                    reviewer_type,
                    review_scope,
                    prompt_version,
                    judge_provider,
                    judge_model,
                    outcome_status,
                    confidence,
                    summary_reason,
                    json.dumps(eval_data, ensure_ascii=False),
                    evaluator_error,
                    friction_score,
                    int(review_prompt_tokens or 0),
                    int(review_completion_tokens or 0),
                    int(review_total_tokens or 0),
                    int(judge_call_count or 0),
                    now,
                    now,
                ),
            )
            con.execute("DELETE FROM case_findings WHERE case_review_id = ?", (review_id,))
            findings = eval_data.get("findings")
            if not isinstance(findings, list):
                findings = []
            for idx, finding in enumerate(findings, start=1):
                if not isinstance(finding, dict):
                    continue
                finding_type = str(finding.get("finding_type") or finding.get("type") or "").strip()
                if not finding_type:
                    continue
                con.execute(
                    """
                    INSERT INTO case_findings
                        (id, case_review_id, turn_case_id, finding_type, severity, evidence_text, evidence_source, case_event_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{review_id}:finding:{idx}",
                        review_id,
                        turn_case_id,
                        finding_type,
                        str(finding.get("severity") or "medium"),
                        finding.get("evidence_text") or finding.get("evidence"),
                        finding.get("evidence_source") or finding.get("source"),
                        finding.get("case_event_id") or finding.get("related_event_id"),
                        now,
                    ),
                )
            con.commit()
        return review_id

    def get_latest_case_review(self, turn_case_id: str) -> dict[str, Any] | None:
        self.migrate()
        with closing(self.connect()) as con:
            row = con.execute(
                "SELECT * FROM case_reviews WHERE turn_case_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (turn_case_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            try:
                result["review_json"] = json.loads(result["review_json"])
            except Exception:
                pass
            result["findings"] = [dict(r) for r in con.execute(
                "SELECT * FROM case_findings WHERE case_review_id = ? ORDER BY id ASC",
                (result["id"],),
            ).fetchall()]
            return result

    def list_case_reviews(self, statuses: list[str] | None = None, limit: int = 50, since: float | None = None) -> list[dict[str, Any]]:
        self.migrate()
        sql = """
            WITH latest AS (
                SELECT turn_case_id, MAX(created_at) AS max_created
                FROM case_reviews
                GROUP BY turn_case_id
            )
            SELECT e.*, u.source_session_id, u.turn_index, u.started_at, u.request_text, u.tool_interaction_count
            FROM case_reviews e
            JOIN latest l ON l.turn_case_id = e.turn_case_id AND l.max_created = e.created_at
            JOIN turn_cases u ON u.id = e.turn_case_id
            WHERE 1 = 1
        """
        params: list[Any] = []
        if statuses:
            sql += f" AND e.outcome_status IN ({', '.join('?' for _ in statuses)})"
            params.extend(statuses)
        if since is not None:
            sql += " AND u.started_at >= ?"
            params.append(since)
        sql += " ORDER BY e.created_at DESC, e.id DESC LIMIT ?"
        params.append(limit)
        with closing(self.connect()) as con:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
            for row in rows:
                row["findings"] = [dict(r) for r in con.execute(
                    "SELECT * FROM case_findings WHERE case_review_id = ? ORDER BY id ASC",
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
                    SELECT turn_case_id, MAX(created_at) AS max_created
                    FROM case_reviews
                    GROUP BY turn_case_id
                )
                SELECT e.outcome_status, COUNT(*) AS count
                FROM case_reviews e
                JOIN latest l ON l.turn_case_id = e.turn_case_id AND l.max_created = e.created_at
                JOIN turn_cases u ON u.id = e.turn_case_id
                {where}
                GROUP BY e.outcome_status
                ORDER BY count DESC
                """,
                params,
            ).fetchall()
            finding_rows = con.execute(
                f"""
                WITH latest AS (
                    SELECT turn_case_id, MAX(created_at) AS max_created
                    FROM case_reviews
                    GROUP BY turn_case_id
                )
                SELECT a.finding_type, COUNT(*) AS count
                FROM case_findings a
                JOIN case_reviews e ON e.id = a.case_review_id
                JOIN latest l ON l.turn_case_id = e.turn_case_id AND l.max_created = e.created_at
                JOIN turn_cases u ON u.id = a.turn_case_id
                {where}
                GROUP BY a.finding_type
                ORDER BY count DESC, a.finding_type ASC
                LIMIT 20
                """,
                params,
            ).fetchall()
            total = sum(int(r["count"]) for r in status_rows)
            token_row = con.execute(
                f"""
                WITH latest AS (
                    SELECT turn_case_id, MAX(created_at) AS max_created
                    FROM case_reviews
                    GROUP BY turn_case_id
                )
                SELECT
                    COALESCE(SUM(e.prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(e.completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(e.total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(e.review_call_count), 0) AS calls
                FROM case_reviews e
                JOIN latest l ON l.turn_case_id = e.turn_case_id AND l.max_created = e.created_at
                JOIN turn_cases u ON u.id = e.turn_case_id
                {where}
                """,
                params,
            ).fetchone()
            friction_row = con.execute(
                f"""
                WITH latest AS (
                    SELECT turn_case_id, MAX(created_at) AS max_created
                    FROM case_reviews
                    GROUP BY turn_case_id
                )
                SELECT
                    COALESCE(MAX(e.friction_score), 0.0) AS max_friction,
                    COALESCE(AVG(e.friction_score), 0.0) AS avg_friction
                FROM case_reviews e
                JOIN latest l ON l.turn_case_id = e.turn_case_id AND l.max_created = e.created_at
                JOIN turn_cases u ON u.id = e.turn_case_id
                {where}
                """,
                params,
            ).fetchone()
            return {
                "evaluated_turns": total,
                "statuses": {r["outcome_status"]: r["count"] for r in status_rows},
                "top_findings": [{"finding_type": r["finding_type"], "count": r["count"]} for r in finding_rows],
                "friction": {
                    "max_friction_score": float(friction_row["max_friction"] or 0.0),
                    "avg_friction_score": float(friction_row["avg_friction"] or 0.0),
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
