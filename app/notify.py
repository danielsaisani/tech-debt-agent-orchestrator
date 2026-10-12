"""Optional Slack notification when a run reaches a final status."""

import httpx


class SlackNotifier:
    def __init__(self, webhook_url: str | None, http: httpx.AsyncClient):
        self._webhook_url = webhook_url or None
        self._http = http

    async def run_finished(self, run: dict) -> None:
        """Post one message for a finished run. Does nothing when Slack is not configured."""
        if not self._webhook_url:
            return
        response = await self._http.post(self._webhook_url, json={"text": _message(run)})
        response.raise_for_status()


def _message(run: dict) -> str:
    issue = f"<{run['issue_url']}|{run['repo']}#{run['issue_number']}> {run['issue_title']}"
    if run["status"] == "succeeded":
        return f":white_check_mark: Devin opened a pull request for {issue}: {run['pr_url']}"
    reason = run["failure_reason"] or run["status"]
    text = f":x: Remediation run for {issue} ended as {run['status']} ({reason})"
    if run["devin_session_url"]:
        text += f". Session: {run['devin_session_url']}"
    return text
