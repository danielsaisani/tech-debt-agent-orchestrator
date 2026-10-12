"""Run APIs: manual trigger, list and detail."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, PositiveInt

from app import db
from app.github import GitHubError
from app.sync import sync_labeled_issues

router = APIRouter(prefix="/api/runs")

MAX_LIMIT = 200
RunStatus = Literal["queued", "running", "succeeded", "failed", "timed_out"]


class TriggerRequest(BaseModel):
    issue_number: PositiveInt


@router.post("/trigger")
async def trigger_run(body: TriggerRequest, request: Request):
    """Start a run for an issue by hand. Calling this is the approval, so the
    trigger label is not required."""
    state = request.app.state
    repo = state.settings.target_repo
    try:
        issue = await state.github.get_issue(repo, body.issue_number)
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"could not read the issue: {exc}") from exc
    if issue is None:
        raise HTTPException(
            status_code=404, detail=f"issue #{body.issue_number} not found on {repo}"
        )

    try:
        run = db.create_run(
            state.conn,
            repo=repo,
            issue_number=issue["number"],
            issue_title=issue["title"],
            issue_body=issue.get("body") or "",
            issue_url=issue["html_url"],
            trigger="manual",
        )
    except db.DuplicateActiveRun as dup:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "this issue already has an active run",
                "run_id": dup.existing["id"],
            },
        )

    state.worker.wake()
    return JSONResponse(status_code=202, content=run)


@router.post("/sync")
async def sync_runs(request: Request):
    """Queue runs for labeled issues that no webhook reported."""
    state = request.app.state
    try:
        result = await sync_labeled_issues(state.conn, state.settings, state.github)
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"could not list issues: {exc}") from exc
    if result["queued"]:
        state.worker.wake()
    return result


@router.get("")
async def list_runs(
    request: Request,
    status: RunStatus | None = None,
    limit: int = Query(default=50, ge=1),
):
    return db.list_runs(request.app.state.conn, status=status, limit=min(limit, MAX_LIMIT))


@router.get("/{run_id}")
async def get_run(run_id: int, request: Request):
    run = db.get_run(request.app.state.conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run
