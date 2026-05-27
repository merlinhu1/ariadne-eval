"""Ariadne Eval Hermes dashboard plugin API.

Mounted by Hermes at /api/plugins/ariadne-eval/.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from agent_health.adapters.hermes import default_hermes_home
from agent_health.config import instruction_health_dir
from agent_health.dashboard_queries import dashboard_summary, eval_unit_detail, session_detail
from agent_health.db import EvalDB, default_eval_db_path
from agent_health.incident_model import IncidentModelUnavailable, smoke_check_incident_model, train_tfidf_incident_model

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


def _incident_model_output_dir(hermes_home: str | Path, model_version: str) -> Path:
    return instruction_health_dir(hermes_home) / "incident-models" / model_version


def _dashboard_config_payload(db: EvalDB) -> dict[str, Any]:
    models = db.list_incident_models()
    promoted = next((model for model in models if model.get("promoted")), None)
    return {
        "tasks": db.list_eval_tasks(),
        "incident_models": models,
        "promoted_incident_model": promoted,
        "eval_task_options": {
            "schedule_kinds": ["interval", "continuous"],
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
        if not detail["units"]:
            raise KeyError(source_session_id)
        return detail
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"session {source_session_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/units/{eval_unit_id}")
def get_unit(eval_unit_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return eval_unit_detail(_db(hermes_home), eval_unit_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"eval unit {eval_unit_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/labels/incidents")
def post_incident_label(
    payload: dict[str, Any] = Body(...),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        db = _db(hermes_home)
        source = "human_correction" if payload.get("correction") else "human"
        label_id = db.insert_incident_label(
            str(payload.get("example_id") or ""),
            label=str(payload.get("label") or ""),
            reason_code=payload.get("reason_code"),
            reason_confidence=payload.get("confidence"),
            label_source=source,
            accepted_for_training=True,
            reviewer=payload.get("reviewer") or "dashboard",
            comment=payload.get("comment"),
        )
        feedback_id = db.insert_feedback(
            target_type="incident_example",
            target_id=str(payload.get("example_id") or ""),
            label=str(payload.get("label") or ""),
            correction=bool(payload.get("correction")),
            source=source,
            reviewer=payload.get("reviewer") or "dashboard",
            comment=payload.get("comment"),
        )
        return {"label_id": label_id, "feedback_id": feedback_id, "status": "recorded"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"incident example {str(exc)!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        target_id = str(payload.get("target_id") or payload.get("example_id") or "").strip()
        label = str(payload.get("label") or "").strip()
        source = "human_correction" if payload.get("correction") else "human"
        label_id = None
        if target_type == "incident_example":
            label_id = db.insert_incident_label(
                target_id,
                label=label,
                reason_code=payload.get("reason_code"),
                reason_confidence=payload.get("confidence"),
                label_source=source,
                accepted_for_training=True,
                reviewer=payload.get("reviewer") or "dashboard",
                comment=payload.get("comment"),
            )
        feedback_id = db.insert_feedback(
            target_type=target_type,
            target_id=target_id,
            eval_unit_id=payload.get("eval_unit_id"),
            label=label,
            correction=bool(payload.get("correction")),
            source=source,
            reviewer=payload.get("reviewer") or "dashboard",
            comment=payload.get("comment"),
        )
        return {"feedback_id": feedback_id, "label_id": label_id, "status": "recorded"}
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


@router.get("/eval-tasks")
def get_eval_tasks(hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return _db(hermes_home).list_eval_tasks()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/eval-tasks")
def post_eval_task(
    payload: dict[str, Any] = Body(...),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        name = str(payload.get("name") or payload.get("id") or "").strip()
        if not name:
            raise ValueError("name is required")
        return _db(hermes_home).upsert_eval_task(name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/eval-tasks/{task_id}")
def get_eval_task(task_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return _db(hermes_home).get_eval_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"eval task {task_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/eval-tasks/{task_id}")
def patch_eval_task(
    task_id: str,
    payload: dict[str, Any] = Body(...),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        db = _db(hermes_home)
        task = db.get_eval_task(task_id)
        return db.upsert_eval_task(task["id"], payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"eval task {task_id!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/eval-tasks/{task_id}/run-now")
def post_eval_task_run_now(task_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        db = _db(hermes_home)
        task = db.get_eval_task(task_id)
        return db.upsert_eval_task(task["id"], {"enabled": True, "next_due_at": time.time()})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"eval task {task_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/eval-tasks/{task_id}/pause")
def post_eval_task_pause(task_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        db = _db(hermes_home)
        task = db.get_eval_task(task_id)
        return db.upsert_eval_task(task["id"], {"enabled": False})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"eval task {task_id!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/eval-tasks/{task_id}/resume")
def post_eval_task_resume(task_id: str, hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        db = _db(hermes_home)
        task = db.get_eval_task(task_id)
        return db.upsert_eval_task(task["id"], {"enabled": True, "next_due_at": time.time()})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"eval task {task_id!r} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/incident-models")
def get_incident_models(hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development")):
    try:
        return _db(hermes_home).list_incident_models()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/incident-models/{model_id}/promote")
def post_incident_model_promote(
    model_id: str,
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        db = _db(hermes_home)
        db.promote_incident_model(model_id)
        return _dashboard_config_payload(db)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"incident model {model_id!r} not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/incident-models/retrain")
def post_incident_model_retrain(
    payload: dict[str, Any] = Body(default={}),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        home = _home(hermes_home)
        db = _db(hermes_home)
        rows = db.export_accepted_incident_training(limit=int(payload.get("limit") or 10000))
        model_version = str(payload.get("model_version") or int(time.time()))
        model = train_tfidf_incident_model(rows, model_version=model_version)
        artifact = model.save(_incident_model_output_dir(home, model_version))
        if not smoke_check_incident_model(artifact.artifact_path):
            raise ValueError("incident model smoke-check failed")
        model_id = db.record_incident_model({
            "model_name": artifact.model_name,
            "model_version": artifact.model_version,
            "artifact_path": artifact.artifact_path,
            "training_record_count": artifact.training_record_count,
            "accepted_label_count": artifact.accepted_label_count,
            "metrics_json": artifact.metrics,
        })
        promoted = False
        if bool(payload.get("auto_promote")):
            db.promote_incident_model(model_id)
            promoted = True
        model_record = next((model for model in db.list_incident_models() if model.get("id") == model_id), None)
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
    except (IncidentModelUnavailable, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/eval-runs")
def get_eval_runs(
    task_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        return _db(hermes_home).list_eval_runs(task_id=task_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
