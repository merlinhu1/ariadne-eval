"""Ariadne Eval Hermes dashboard plugin API.

Mounted by Hermes at /api/plugins/ariadne-eval/.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import APIRouter, Body, HTTPException, Query
except ModuleNotFoundError:
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str | None = None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class APIRouter:
        def get(self, *args, **kwargs):
            return lambda fn: fn

        def post(self, *args, **kwargs):
            return lambda fn: fn

        def patch(self, *args, **kwargs):
            return lambda fn: fn

    def Body(default=None, **kwargs):
        return default

    def Query(default=None, **kwargs):
        return default

from agent_health.adapters.hermes import default_hermes_home
from agent_health.config import instruction_health_dir
from agent_health.dashboard_queries import dashboard_summary, turn_case_detail, session_detail
from agent_health.db import EvalDB, default_eval_db_path
from agent_health.tool_outcome_reviewer_model import ToolOutcomeModelUnavailable, smoke_check_tool_outcome_reviewer_model, train_tfidf_tool_outcome_reviewer_model

router = APIRouter()


def _parse_since(value: Optional[str]) -> float | None:
    if value is None or value == "":
        return None
    text = value.strip().lower()
    now = time.time()
    if text.endswith("h"):
        return now - float(text[:-1]) * 3600
    if text.endswith("d"):
        return now - float(text[:-1]) * 86400
    return float(text)


def _db(hermes_home: Optional[str] = None) -> EvalDB:
    home = Path(hermes_home).expanduser() if hermes_home else default_hermes_home()
    return EvalDB(default_eval_db_path(home))


def _home(hermes_home: Optional[str] = None) -> Path:
    return Path(hermes_home).expanduser() if hermes_home else default_hermes_home()


def _tool_outcome_reviewer_model_output_dir(hermes_home: str | Path, model_version: str) -> Path:
    return instruction_health_dir(hermes_home) / "tool-outcome-reviewer-models" / model_version


def _dashboard_config_payload(db: EvalDB) -> dict[str, Any]:
    models = db.list_tool_outcome_reviewer_models()
    promoted = next((model for model in models if model.get("promoted")), None)
    return {
        "tasks": db.list_review_jobs(),
        "tool_outcome_reviewer_models": models,
        "promoted_tool_outcome_reviewer_model": promoted,
        "review_job_options": {
            "schedule_kinds": ["interval", "continuous"],
            "interval_unit": "hours",
            "default_interval_hours": 5,
            "judgement_thresholds": ["strict", "balanced", "relaxed"],
        },
        "llm_judging": {
            "route_priority": "Hermes auxiliary.approval first, then Hermes main model, then auto approval fallback.",
            "editable": False,
        },
    }


@router.get("/summary")
def get_summary(
    since: Optional[str] = Query("24h", description="Relative window such as 5h/24h/7d, epoch seconds, or empty for all time"),
    bucket_seconds: int = Query(3600, ge=60, le=86400),
    unit_limit: int = Query(1000, ge=1, le=10000),
    session_limit: int = Query(24, ge=1, le=100),
    session_offset: int = Query(0, ge=0),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        return dashboard_summary(
            _db(hermes_home),
            since=_parse_since(since),
            bucket_seconds=bucket_seconds,
            unit_limit=unit_limit,
            session_limit=session_limit,
            session_offset=session_offset,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sessions/{source_session_id}")
def get_session(
    source_session_id: str,
    since: Optional[str] = Query("24h", description="Relative window such as 5h/24h/7d, epoch seconds, or empty for all time"),
    unit_limit: int = Query(500, ge=1, le=5000),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        detail = session_detail(_db(hermes_home), source_session_id, since=_parse_since(since), unit_limit=unit_limit)
        if not detail["cases"]:
            raise KeyError(source_session_id)
        return detail
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"session {source_session_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/cases/{turn_case_id}")
def get_unit(turn_case_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return turn_case_detail(_db(hermes_home), turn_case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"turn case {turn_case_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tool-outcome-reviews")
def post_tool_outcome_label(
    payload: dict[str, Any] = Body(...),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        db = _db(hermes_home)
        source = "human_correction" if payload.get("correction") else "human"
        tool_outcome_case_id = str(payload.get("tool_outcome_case_id") or payload.get("target_id") or "").strip()
        review_id = db.insert_tool_outcome_review(
            tool_outcome_case_id,
            outcome_label=str(payload.get("outcome_label") or payload.get("label") or ""),
            reason_code=payload.get("reason_code"),
            confidence=payload.get("confidence"),
            label_source=source,
            training_eligible=True,
            reviewer=payload.get("reviewer") or "dashboard",
            comment=payload.get("comment"),
        )
        feedback_id = db.insert_feedback(
            target_type="tool_outcome_case",
            target_id=tool_outcome_case_id,
            label=str(payload.get("outcome_label") or payload.get("label") or ""),
            correction=bool(payload.get("correction")),
            source=source,
            reviewer=payload.get("reviewer") or "dashboard",
            comment=payload.get("comment"),
        )
        return {"review_id": review_id, "feedback_id": feedback_id, "status": "recorded"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"tool outcome case {str(exc)!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/tool-outcome-reviews")
def get_tool_outcome_reviews(
    limit: int = Query(50, ge=1, le=500),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        return _db(hermes_home).list_tool_outcome_reviews(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/feedback")
def post_feedback(
    payload: dict[str, Any] = Body(...),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        db = _db(hermes_home)
        target_type = str(payload.get("target_type") or "").strip()
        target_id = str(payload.get("target_id") or payload.get("tool_outcome_case_id") or "").strip()
        label = str(payload.get("outcome_label") or payload.get("label") or "").strip()
        source = "human_correction" if payload.get("correction") else "human"
        review_id = None
        if target_type == "tool_outcome_case":
            review_id = db.insert_tool_outcome_review(
                target_id,
                outcome_label=label,
                reason_code=payload.get("reason_code"),
                confidence=payload.get("confidence"),
                label_source=source,
                training_eligible=True,
                reviewer=payload.get("reviewer") or "dashboard",
                comment=payload.get("comment"),
            )
        feedback_id = db.insert_feedback(
            target_type=target_type,
            target_id=target_id,
            turn_case_id=payload.get("turn_case_id"),
            label=label,
            correction=bool(payload.get("correction")),
            source=source,
            reviewer=payload.get("reviewer") or "dashboard",
            comment=payload.get("comment"),
        )
        return {"feedback_id": feedback_id, "review_id": review_id, "status": "recorded"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"feedback target {str(exc)!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/config/options")
def get_config_options(hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return _dashboard_config_payload(_db(hermes_home))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/review-jobs")
def get_review_jobs(hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return _db(hermes_home).list_review_jobs()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/review-jobs")
def post_review_job(
    payload: dict[str, Any] = Body(...),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        name = str(payload.get("name") or payload.get("id") or "").strip()
        if not name:
            raise ValueError("name is required")
        return _db(hermes_home).upsert_review_job(name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/review-jobs/{job_id}")
def get_review_job(job_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return _db(hermes_home).get_review_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"review job {job_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/review-jobs/{job_id}")
def patch_review_job(
    job_id: str,
    payload: dict[str, Any] = Body(...),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        db = _db(hermes_home)
        task = db.get_review_job(job_id)
        return db.upsert_review_job(task["id"], payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"review job {job_id!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/review-jobs/{job_id}/run-now")
def post_review_job_run_now(job_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        db = _db(hermes_home)
        task = db.get_review_job(job_id)
        return db.upsert_review_job(task["id"], {"enabled": True, "next_due_at": time.time()})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"review job {job_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/review-jobs/{job_id}/pause")
def post_review_job_pause(job_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        db = _db(hermes_home)
        task = db.get_review_job(job_id)
        return db.upsert_review_job(task["id"], {"enabled": False})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"review job {job_id!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/review-jobs/{job_id}/resume")
def post_review_job_resume(job_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        db = _db(hermes_home)
        task = db.get_review_job(job_id)
        return db.upsert_review_job(task["id"], {"enabled": True, "next_due_at": time.time()})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"review job {job_id!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/tool-outcome-reviewer-models")
def get_tool_outcome_reviewer_models(hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return _db(hermes_home).list_tool_outcome_reviewer_models()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tool-outcome-reviewer-models/{model_id}/promote")
def post_tool_outcome_reviewer_model_promote(
    model_id: str,
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        db = _db(hermes_home)
        db.promote_tool_outcome_reviewer_model(model_id)
        return _dashboard_config_payload(db)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"tool_outcome model {model_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tool-outcome-reviewer-models/retrain")
def post_tool_outcome_reviewer_model_retrain(
    payload: dict[str, Any] = Body(default={}),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        home = _home(hermes_home)
        db = _db(hermes_home)
        rows = db.export_tool_outcome_review_training(limit=int(payload.get("limit") or 10000))
        model_version = str(payload.get("model_version") or int(time.time()))
        model = train_tfidf_tool_outcome_reviewer_model(rows, model_version=model_version)
        artifact = model.save(_tool_outcome_reviewer_model_output_dir(home, model_version))
        if not smoke_check_tool_outcome_reviewer_model(artifact.artifact_path):
            raise ValueError("tool_outcome model smoke-check failed")
        model_id = db.record_tool_outcome_reviewer_model({
            "model_name": artifact.model_name,
            "model_version": artifact.model_version,
            "artifact_path": artifact.artifact_path,
            "training_record_count": artifact.training_record_count,
            "accepted_label_count": artifact.accepted_label_count,
            "metrics_json": artifact.metrics,
        })
        promoted = False
        if bool(payload.get("auto_promote")):
            db.promote_tool_outcome_reviewer_model(model_id)
            promoted = True
        model_record = next((model for model in db.list_tool_outcome_reviewer_models() if model.get("id") == model_id), None)
        return {
            "model": model_record,
            "artifact": {
                "model_name": artifact.model_name,
                "model_version": artifact.model_version,
                "artifact_path": artifact.artifact_path,
                "training_record_count": artifact.training_record_count,
                "accepted_label_count": artifact.accepted_label_count,
                "metrics": artifact.metrics,
            },
            "promoted": promoted,
            "config": _dashboard_config_payload(db),
        }
    except (ToolOutcomeModelUnavailable, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/review-runs")
def get_review_runs(
    job_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        return _db(hermes_home).list_review_runs(task_id=job_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
