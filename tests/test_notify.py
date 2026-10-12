import json

import httpx
import pytest
import respx

from app.notify import SlackNotifier

SLACK_URL = "https://hooks.slack.com/services/T000/B000/XXXX"
PR = "https://github.com/octo/superset/pull/12"


def finished_run(**overrides) -> dict:
    run = {
        "repo": "octo/superset",
        "issue_number": 7,
        "issue_title": "Remove dead code",
        "issue_url": "https://github.com/octo/superset/issues/7",
        "status": "succeeded",
        "failure_reason": None,
        "pr_url": PR,
        "devin_session_url": "https://app.devin.ai/sessions/s1",
    }
    run.update(overrides)
    return run


@respx.mock
async def test_posts_one_message_when_a_run_succeeds():
    route = respx.post(SLACK_URL).mock(return_value=httpx.Response(200, text="ok"))
    async with httpx.AsyncClient() as http:
        await SlackNotifier(SLACK_URL, http).run_finished(finished_run())

    assert route.call_count == 1
    text = json.loads(route.calls.last.request.content)["text"]
    assert "octo/superset#7" in text
    assert PR in text


@respx.mock
async def test_failure_message_names_the_reason():
    route = respx.post(SLACK_URL).mock(return_value=httpx.Response(200, text="ok"))
    run = finished_run(status="failed", failure_reason="no_pr", pr_url=None)
    async with httpx.AsyncClient() as http:
        await SlackNotifier(SLACK_URL, http).run_finished(run)

    text = json.loads(route.calls.last.request.content)["text"]
    assert "octo/superset#7" in text
    assert "failed" in text and "no_pr" in text
    assert "https://app.devin.ai/sessions/s1" in text


@respx.mock
@pytest.mark.parametrize("url", [None, ""])
async def test_makes_no_request_when_slack_is_not_configured(url):
    async with httpx.AsyncClient() as http:
        await SlackNotifier(url, http).run_finished(finished_run())
    assert len(respx.calls) == 0


@respx.mock
async def test_raises_when_slack_returns_an_error():
    """The worker catches this, so the run is unaffected (see test_lifecycle)."""
    respx.post(SLACK_URL).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as http:
        with pytest.raises(httpx.HTTPStatusError):
            await SlackNotifier(SLACK_URL, http).run_finished(finished_run())
