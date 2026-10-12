"""Health check, metrics and the dashboard page."""

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from app.metrics import compute_metrics

router = APIRouter()

DASHBOARD = Path(__file__).resolve().parent.parent / "static" / "index.html"


@router.get("/health")
async def health(request: Request):
    try:
        request.app.state.conn.execute("SELECT 1 FROM runs LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "detail": str(exc)}
        )
    return {"status": "ok"}


@router.get("/api/metrics")
async def metrics(request: Request):
    state = request.app.state
    return {**compute_metrics(state.conn), "simulated": state.settings.devin_simulate}


@router.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(DASHBOARD, media_type="text/html")
