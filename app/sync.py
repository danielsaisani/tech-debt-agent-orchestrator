"""Reconciliation sweep: queue runs for labeled issues that no webhook reported."""

import sqlite3

from app import db
from app.config import Settings
from app.github import GitHubClient


async def sync_labeled_issues(
    conn: sqlite3.Connection, settings: Settings, github: GitHubClient
) -> dict:
    """Queue a run for each open, labeled issue that has never had one, oldest first.

    Issues with a run in any status are left alone, so a failed issue is not
    retried by every sweep. At most `sync_max_runs` runs are queued per sweep,
    so a backlog cannot start a session for every issue at once.
    """
    repo = settings.target_repo
    issues = await github.list_labeled_issues(repo, settings.trigger_label)
    untracked = sorted(
        (issue for issue in issues if not db.has_run(conn, repo, issue["number"])),
        key=lambda issue: issue["number"],
    )

    queued = []
    for issue in untracked[: settings.sync_max_runs]:
        run = db.create_run(
            conn,
            repo=repo,
            issue_number=issue["number"],
            issue_title=issue["title"],
            issue_body=issue.get("body") or "",
            issue_url=issue["html_url"],
            trigger="sync",
        )
        queued.append({"run_id": run["id"], "issue_number": issue["number"]})

    return {
        "queued": queued,
        "already_tracked": len(issues) - len(untracked),
        "deferred": len(untracked) - len(queued),
    }
