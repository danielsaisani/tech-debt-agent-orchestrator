import json

import httpx
import pytest
import respx

from app.github import GitHubClient, GitHubError, verify_signature
from tests.conftest import REPO, WEBHOOK_SECRET, sign

API = "https://api.github.com"


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as http:
        yield GitHubClient("test-github-token", API, http)


def test_valid_signature_is_accepted():
    body = b'{"action": "opened"}'
    assert verify_signature(WEBHOOK_SECRET, body, sign(body))


def test_signature_for_a_different_body_or_secret_is_rejected():
    body = b'{"action": "opened"}'
    assert not verify_signature(WEBHOOK_SECRET, body, sign(b"other body"))
    assert not verify_signature(WEBHOOK_SECRET, body, sign(body, secret="wrong"))
    assert not verify_signature(WEBHOOK_SECRET, body, "sha256=nonsense")


@pytest.mark.parametrize("header", [None, ""])
def test_missing_signature_is_rejected(header):
    assert not verify_signature(WEBHOOK_SECRET, b"{}", header)


@respx.mock
async def test_get_issue(client):
    route = respx.get(f"{API}/repos/{REPO}/issues/7").mock(
        return_value=httpx.Response(200, json={"number": 7, "title": "T"})
    )
    assert await client.get_issue(REPO, 7) == {"number": 7, "title": "T"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-github-token"


@respx.mock
async def test_get_issue_returns_none_when_missing(client):
    respx.get(f"{API}/repos/{REPO}/issues/7").mock(return_value=httpx.Response(404))
    assert await client.get_issue(REPO, 7) is None


@respx.mock
async def test_get_issue_does_not_return_pull_requests(client):
    respx.get(f"{API}/repos/{REPO}/issues/7").mock(
        return_value=httpx.Response(200, json={"number": 7, "pull_request": {"url": "u"}})
    )
    assert await client.get_issue(REPO, 7) is None


@respx.mock
async def test_create_comment(client):
    route = respx.post(f"{API}/repos/{REPO}/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    await client.create_comment(REPO, 7, "hello")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-github-token"
    assert json.loads(request.content) == {"body": "hello"}


@respx.mock
async def test_github_errors_are_raised(client):
    respx.post(f"{API}/repos/{REPO}/issues/7/comments").mock(return_value=httpx.Response(403))
    with pytest.raises(GitHubError):
        await client.create_comment(REPO, 7, "hello")

    respx.get(f"{API}/repos/{REPO}/issues/7").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(GitHubError):
        await client.get_issue(REPO, 7)


@respx.mock
async def test_list_labeled_issues_queries_open_issues_and_drops_pull_requests(client):
    route = respx.get(f"{API}/repos/{REPO}/issues").mock(
        return_value=httpx.Response(
            200, json=[{"number": 1}, {"number": 2, "pull_request": {"url": "u"}}, {"number": 3}]
        )
    )
    issues = await client.list_labeled_issues(REPO, "tech-debt")

    assert [issue["number"] for issue in issues] == [1, 3]
    params = route.calls.last.request.url.params
    assert (params["labels"], params["state"]) == ("tech-debt", "open")


@respx.mock
async def test_list_labeled_issues_follows_pages(client):
    first_page = [{"number": n} for n in range(1, 101)]
    route = respx.get(f"{API}/repos/{REPO}/issues").mock(
        side_effect=[
            httpx.Response(200, json=first_page),
            httpx.Response(200, json=[{"number": 101}]),
        ]
    )
    issues = await client.list_labeled_issues(REPO, "tech-debt")

    assert len(issues) == 101
    assert [call.request.url.params["page"] for call in route.calls] == ["1", "2"]


@respx.mock
async def test_list_labeled_issues_raises_on_error(client):
    respx.get(f"{API}/repos/{REPO}/issues").mock(return_value=httpx.Response(500))
    with pytest.raises(GitHubError):
        await client.list_labeled_issues(REPO, "tech-debt")
