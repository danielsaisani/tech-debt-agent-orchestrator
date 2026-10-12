"""Simulation mode: in-process stand-ins for the Devin and GitHub clients.

They replace the clients only. The worker, store, routes and dashboard run
unchanged, so a simulated run exercises the real lifecycle code.
"""

import logging
import re
from uuid import uuid4

from app.devin import DevinError

log = logging.getLogger(__name__)

# A reserved host that can never resolve, so simulated links are never real ones.
BASE_URL = "https://simulated.invalid"

SCENARIOS = {
    "success", "no_pr", "needs_input", "session_error", "usage_limit", "create_failed", "timeout",
}
_MARKER = re.compile(r"\[sim:([a-z_]+)\]")


def scenario_from(title: str) -> str:
    """The outcome named by a "[sim:<scenario>]" marker in the title; success by default."""
    match = _MARKER.search(title)
    return match.group(1) if match and match.group(1) in SCENARIOS else "success"


class SimulatedDevin:
    def __init__(self, target_repo: str, polls_to_finish: int):
        self._target_repo = target_repo
        self._polls_to_finish = polls_to_finish
        self._polls: dict[str, int] = {}

    async def create_session(self, *, title: str, **_ignored) -> dict:
        scenario = scenario_from(title)
        if scenario == "create_failed":
            raise DevinError("simulated: Devin rejected the session request")
        # The scenario lives in the id, so it survives a restart with no extra state.
        session_id = f"sim-{scenario}-{uuid4().hex[:8]}"
        return {"session_id": session_id, "url": f"{BASE_URL}/sessions/{session_id}", "status": "new"}

    async def get_session(self, session_id: str) -> dict:
        _, scenario, suffix = session_id.split("-", 2)
        polls = self._polls[session_id] = self._polls.get(session_id, 0) + 1
        session = {
            "status": "running",
            "status_detail": "working",
            "pull_requests": [],
            "acus_consumed": round(0.8 * min(polls, self._polls_to_finish), 1),
        }
        if polls < self._polls_to_finish or scenario == "timeout":
            return session

        if scenario == "success":
            # Devin's usual end state: pull request open, waiting for feedback.
            pr_url = f"{BASE_URL}/{self._target_repo}/pull/{suffix}"
            session.update(
                status_detail="waiting_for_user",
                pull_requests=[{"pr_url": pr_url, "pr_state": "open"}],
            )
        elif scenario == "no_pr":
            session.update(status="exit", status_detail="finished")
        elif scenario == "needs_input":
            session.update(status_detail="waiting_for_user")
        elif scenario == "session_error":
            session.update(status="error", status_detail="error")
        elif scenario == "usage_limit":
            session.update(status="suspended", status_detail="usage_limit_exceeded")
        return session


class SimulatedGitHub:
    async def get_issue(self, repo: str, number: int) -> dict:
        return {
            "number": number,
            "title": f"Simulated issue {number}",
            "body": "This issue exists only in simulation mode.",
            "html_url": f"{BASE_URL}/{repo}/issues/{number}",
        }

    async def list_labeled_issues(self, repo: str, label: str) -> list[dict]:
        return []

    async def create_comment(self, repo: str, number: int, body: str) -> None:
        log.info("simulated comment on %s#%s: %s", repo, number, body)
