import pytest

from app import db
from app.github import GitHubError
from tests.conftest import issue_payload


async def test_manual_trigger_creates_a_run_without_the_label(client, conn, github, devin):
    github.issues[7] = issue_payload(number=7, labels=())
    response = await client.post("/api/runs/trigger", json={"issue_number": 7})

    assert response.status_code == 202
    run = response.json()
    assert run["status"] == "queued"
    assert run["trigger"] == "manual"
    assert run["issue_number"] == 7
    assert db.get_run(conn, run["id"])["issue_title"] == "Remove dead code"
    assert devin.create_calls == []


async def test_manual_trigger_while_a_run_is_active_conflicts(client, conn, github):
    github.issues[7] = issue_payload(number=7)
    first = await client.post("/api/runs/trigger", json={"issue_number": 7})
    second = await client.post("/api/runs/trigger", json={"issue_number": 7})

    assert second.status_code == 409
    assert second.json()["run_id"] == first.json()["id"]
    assert len(db.list_runs(conn)) == 1


async def test_manual_trigger_for_an_unknown_issue_is_not_found(client, conn):
    response = await client.post("/api/runs/trigger", json={"issue_number": 404})
    assert response.status_code == 404
    assert db.list_runs(conn) == []


@pytest.mark.parametrize(
    "body", [{}, {"issue_number": 0}, {"issue_number": -3}, {"issue_number": "seven"}]
)
async def test_manual_trigger_with_an_invalid_body_is_rejected(client, conn, body):
    response = await client.post("/api/runs/trigger", json=body)
    assert response.status_code == 422
    assert db.list_runs(conn) == []


async def test_manual_trigger_reports_a_github_failure(client, conn, github):
    async def broken(repo, number):
        raise GitHubError("GitHub returned 500")

    github.get_issue = broken
    response = await client.post("/api/runs/trigger", json={"issue_number": 7})
    assert response.status_code == 502
    assert db.list_runs(conn) == []
