import json

import httpx
import pytest
import respx

from app.devin import DevinClient, DevinError, DevinTransientError, build_prompt
from tests.conftest import REPO

SESSIONS_URL = "https://api.devin.ai/v3/organizations/org-test/sessions"


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as http:
        yield DevinClient("test-devin-key", "org-test", "https://api.devin.ai/v3", http)


@respx.mock
async def test_create_session_request(client):
    route = respx.post(SESSIONS_URL).mock(
        return_value=httpx.Response(200, json={"session_id": "abc", "url": "https://x/abc"})
    )
    session = await client.create_session(
        prompt="fix it", repos=[REPO], title="tech-debt #7: T", tags=["a"], max_acu_limit=10
    )

    assert session["session_id"] == "abc"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-devin-key"
    assert json.loads(request.content) == {
        "prompt": "fix it",
        "repos": [REPO],
        "title": "tech-debt #7: T",
        "tags": ["a"],
        "max_acu_limit": 10,
    }


@respx.mock
@pytest.mark.parametrize("session_id", ["abc", "devin-abc"])
async def test_get_session_uses_the_devin_prefixed_id(client, session_id):
    route = respx.get(f"{SESSIONS_URL}/devin-abc").mock(
        return_value=httpx.Response(200, json={"status": "running"})
    )
    assert await client.get_session(session_id) == {"status": "running"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-devin-key"


@respx.mock
@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_retryable_responses_are_transient_errors(client, status_code):
    respx.get(f"{SESSIONS_URL}/devin-abc").mock(return_value=httpx.Response(status_code))
    with pytest.raises(DevinTransientError):
        await client.get_session("abc")


@respx.mock
async def test_network_errors_are_transient_errors(client):
    respx.get(f"{SESSIONS_URL}/devin-abc").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(DevinTransientError):
        await client.get_session("abc")


@respx.mock
async def test_client_errors_are_not_transient(client):
    respx.post(SESSIONS_URL).mock(return_value=httpx.Response(403, json={"title": "Forbidden"}))
    with pytest.raises(DevinError) as excinfo:
        await client.create_session(prompt="p", repos=[], title="t", tags=[], max_acu_limit=1)
    assert not isinstance(excinfo.value, DevinTransientError)
    assert "403" in str(excinfo.value)


def test_prompt_contains_the_issue_and_the_target_repository():
    run = {
        "issue_number": 7,
        "issue_title": "Remove dead code in charts",
        "issue_body": "The legacy helper is unused.",
        "issue_url": f"https://github.com/{REPO}/issues/7",
    }
    prompt = build_prompt(run, REPO)

    assert "Remove dead code in charts" in prompt
    assert "The legacy helper is unused." in prompt
    assert f"https://github.com/{REPO}/issues/7" in prompt
    assert f"open it against {REPO}" in prompt
    assert "Never open a pull\n  request against any other repository" in prompt
    assert "Closes #7" in prompt


def test_prompt_handles_an_empty_issue_body():
    run = {"issue_number": 7, "issue_title": "T", "issue_body": "", "issue_url": "u"}
    assert "(no description provided)" in build_prompt(run, REPO)
