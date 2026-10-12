"""Run lifecycle: the Devin status mapping (pure) and the worker loop that applies it."""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import db
from app.config import Settings
from app.devin import DevinClient, DevinError, build_prompt
from app.github import GitHubClient
from app.notify import SlackNotifier

log = logging.getLogger(__name__)

USAGE_LIMIT_DETAILS = {
    "usage_limit_exceeded", "out_of_credits", "out_of_quota", "no_quota_allocation",
    "payment_declined", "org_usage_limit_exceeded", "user_usage_limit_exceeded",
    "total_session_limit_exceeded", "contract_expired",
}
NEEDS_INPUT_DETAILS = {"waiting_for_user", "waiting_for_approval"}
RESTING_STATUSES = {"exit", "suspended"}
RESTING_DETAILS = NEEDS_INPUT_DETAILS | {"finished", "inactivity", "user_request"}

# A session resting without a pull request must be seen this many polls in a
# row before the run fails, so a momentary pause does not end it.
IDLE_POLLS_BEFORE_FAILURE = 2

FAILURE_EXPLANATIONS = {
    "session_create_failed": "the Devin session could not be started",
    "session_error": "the Devin session ended in an error",
    "usage_limit": "the Devin session hit a usage or billing limit",
    "needs_input": "Devin is waiting for a human and has not opened a pull request",
    "no_pr": "the Devin session finished without opening a pull request",
    "timed_out": "the run did not finish within the time limit",
}


@dataclass(frozen=True)
class Outcome:
    status: str  # running | succeeded | failed
    failure_reason: str | None = None
    idle_polls: int = 0


def map_session(session: dict, idle_polls: int) -> Outcome:
    """Decide a running run's outcome from one Devin session payload.

    Unknown status values fall through to "still running" and are left to the
    run timeout, so a surprise from the API never produces a wrong outcome.
    """
    status = session.get("status")
    detail = session.get("status_detail")
    errored = status == "error" or detail == "error"
    hit_limit = detail in USAGE_LIMIT_DETAILS
    resting = status in RESTING_STATUSES or detail in RESTING_DETAILS

    if not (errored or hit_limit or resting):
        return Outcome("running")
    if session.get("pull_requests"):
        return Outcome("succeeded")
    if errored:
        return Outcome("failed", "session_error")
    if hit_limit:
        return Outcome("failed", "usage_limit")

    idle_polls += 1
    if idle_polls < IDLE_POLLS_BEFORE_FAILURE:
        return Outcome("running", idle_polls=idle_polls)
    reason = "needs_input" if detail in NEEDS_INPUT_DETAILS else "no_pr"
    return Outcome("failed", reason, idle_polls)


class Worker:
    """Drives every active run forward. All run state changes happen here."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        devin: DevinClient,
        github: GitHubClient,
        notifier: SlackNotifier,
    ):
        self._conn = conn
        self._settings = settings
        self._devin = devin
        self._github = github
        self._notifier = notifier
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def wake(self) -> None:
        """Run a cycle now instead of waiting out the poll interval."""
        self._wake.set()

    async def _run_forever(self) -> None:
        while True:
            try:
                await self.run_cycle()
            except Exception:
                log.exception("worker cycle failed")
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._settings.poll_interval_seconds
                )
            except TimeoutError:
                pass
            self._wake.clear()

    async def run_cycle(self) -> None:
        for run in db.list_by_status(self._conn, "queued"):
            await self._launch(run)
        for run in db.list_by_status(self._conn, "running"):
            await self._poll(run)
        for status in db.ACTIVE_STATUSES:
            for run in db.list_by_status(self._conn, status):
                if self._is_overdue(run):
                    await self._finish(run, "timed_out", "timed_out")

    async def _launch(self, run: dict) -> None:
        number = run["issue_number"]
        try:
            session = await self._devin.create_session(
                prompt=build_prompt(run, self._settings.target_repo),
                repos=[self._settings.target_repo],
                title=f"{self._settings.trigger_label} #{number}: {run['issue_title']}",
                tags=["remediation-orchestrator", f"issue-{number}"],
                max_acu_limit=self._settings.devin_max_acu_limit,
            )
        except DevinError as exc:
            log.warning("run %s: session create failed: %s", run["id"], exc)
            await self._finish(run, "failed", "session_create_failed", error_detail=str(exc))
            return

        run = db.update_run(
            self._conn,
            run["id"],
            status="running",
            devin_session_id=session["session_id"],
            devin_session_url=session["url"],
            devin_status=session.get("status"),
            session_started_at=db.utcnow(),
        )
        log.info("run %s: started Devin session %s", run["id"], run["devin_session_id"])
        await self._comment(run, f"Devin has started working on this issue: {session['url']}")

    async def _poll(self, run: dict) -> None:
        try:
            session = await self._devin.get_session(run["devin_session_id"])
        except DevinError as exc:
            # Never finalise a run on a failed poll; retry next cycle until the timeout.
            log.warning("run %s: poll failed, will retry: %s", run["id"], exc)
            return

        outcome = map_session(session, run["idle_polls"])
        now = db.utcnow()
        fields = {
            "devin_status": session.get("status"),
            "devin_status_detail": session.get("status_detail"),
            "acus_consumed": session.get("acus_consumed"),
            "idle_polls": outcome.idle_polls,
            "last_polled_at": now,
        }
        pull_requests = session.get("pull_requests") or []
        if pull_requests:
            fields["pr_url"] = pull_requests[0].get("pr_url")
            fields["pr_state"] = pull_requests[0].get("pr_state")
            if not run["pr_opened_at"]:
                fields["pr_opened_at"] = now
        run = db.update_run(self._conn, run["id"], **fields)

        if outcome.status != "running":
            await self._finish(run, outcome.status, outcome.failure_reason)

    def _is_overdue(self, run: dict) -> bool:
        deadline = datetime.fromisoformat(run["created_at"]) + timedelta(
            minutes=self._settings.run_timeout_minutes
        )
        return datetime.now(timezone.utc) >= deadline

    async def _finish(
        self, run: dict, status: str, failure_reason: str | None, error_detail: str | None = None
    ) -> None:
        """Commit the final status first, then report it; reporting can never undo it."""
        run = db.update_run(
            self._conn,
            run["id"],
            status=status,
            failure_reason=failure_reason,
            error_detail=error_detail,
            finished_at=db.utcnow(),
        )
        log.info("run %s: finished as %s (%s)", run["id"], status, failure_reason or "ok")

        if status == "succeeded":
            comment = f"Devin opened a pull request for this issue: {run['pr_url']}"
        else:
            explanation = FAILURE_EXPLANATIONS.get(failure_reason, failure_reason)
            comment = f"The remediation run for this issue ended without a result: {explanation}."
            if run["devin_session_url"]:
                comment += f"\n\nDevin session: {run['devin_session_url']}"
        await self._comment(run, comment)

        try:
            await self._notifier.run_finished(run)
        except Exception as exc:
            log.warning("run %s: Slack notification failed: %s", run["id"], exc)

    async def _comment(self, run: dict, body: str) -> None:
        """Post a progress comment. A failure is recorded on the run, never raised:
        `comment_error` holds the error from the latest attempt, or None if it worked."""
        try:
            await self._github.create_comment(run["repo"], run["issue_number"], body)
        except Exception as exc:
            log.warning("run %s: could not comment on the issue: %s", run["id"], exc)
            db.update_run(self._conn, run["id"], comment_error=str(exc)[:500])
        else:
            db.update_run(self._conn, run["id"], comment_error=None)
