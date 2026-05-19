"""Ariadne Eval Hermes dashboard plugin API.

Mounted by Hermes at /api/plugins/ariadne-eval/.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from agent_health.adapters.hermes import default_hermes_home
from agent_health.dashboard_queries import dashboard_summary, eval_unit_detail
from agent_health.db import EvalDB, default_eval_db_path

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


@router.get("/summary")
def get_summary(
    since: Optional[str] = Query("24h", description="Relative window such as 5h/24h/7d, epoch seconds, or empty for all time"),
    bucket_seconds: int = Query(3600, ge=60, le=86400),
    unit_limit: int = Query(1000, ge=1, le=10000),
    hermes_home: Optional[str] = Query(None, description="Optional Hermes home override for local development"),
):
    try:
        return dashboard_summary(_db(hermes_home), since=_parse_since(since), bucket_seconds=bucket_seconds, unit_limit=unit_limit)
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
