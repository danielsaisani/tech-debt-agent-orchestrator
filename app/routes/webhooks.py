"""GitHub webhook receiver: verify, filter, enqueue. No upstream calls happen here."""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app import db
from app.github import verify_signature

router = APIRouter()


def _ignored(reason: str) -> dict:
    return {"status": "ignored", "reason": reason}


def _has_label(labels: list[dict], name: str) -> bool:
    return any(label.get("name") == name for label in labels)


@router.post("/webhooks/github")
async def github_webhook(request: Request):
    state = request.app.state
    settings = state.settings

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(settings.github_webhook_secret, body, signature):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    event = request.headers.get("X-GitHub-Event")
    if event != "issues":
        return _ignored(f"event '{event}' is not handled")
    try:
        payload = json.loads(body)
        action = payload["action"]
        issue = payload["issue"]
        repo = payload["repository"]["full_name"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="malformed issues payload") from None

    if repo.lower() != settings.target_repo.lower():
        return _ignored(f"repository '{repo}' is not the target repository")

    label = settings.trigger_label
    if action == "opened":
        if not _has_label(issue.get("labels") or [], label):
            return _ignored(f"issue does not carry the '{label}' label")
    elif action == "labeled":
        # Only the label that was just added counts, so labelling an issue that
        # already has the trigger label does not start another run.
        if (payload.get("label") or {}).get("name") != label:
            return _ignored(f"the added label is not '{label}'")
    else:
        return _ignored(f"action '{action}' is not handled")

    try:
        run = db.create_run(
            state.conn,
            repo=settings.target_repo,
            issue_number=issue["number"],
            issue_title=issue["title"],
            issue_body=issue.get("body") or "",
            issue_url=issue["html_url"],
            trigger="webhook",
        )
    except db.DuplicateActiveRun as dup:
        return {"status": "duplicate", "run_id": dup.existing["id"]}

    state.worker.wake()
    return JSONResponse(status_code=202, content={"status": "accepted", "run_id": run["id"]})
