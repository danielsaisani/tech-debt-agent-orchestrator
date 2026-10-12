import asyncio

from app import db
from tests.conftest import REPO, issues_event, post_webhook, sign


async def test_valid_signature_is_processed(client, conn):
    response = await post_webhook(client, issues_event("opened"))
    assert response.status_code == 202
    assert len(db.list_runs(conn)) == 1


async def test_invalid_signature_is_rejected(client, conn):
    response = await post_webhook(client, issues_event("opened"), signature=sign(b"other"))
    assert response.status_code == 401
    assert db.list_runs(conn) == []


async def test_missing_signature_is_rejected(client, conn):
    response = await post_webhook(client, issues_event("opened"), signature=None)
    assert response.status_code == 401
    assert db.list_runs(conn) == []


async def test_issue_opened_with_the_trigger_label_creates_a_run(client, conn):
    response = await post_webhook(client, issues_event("opened", labels=("bug", "tech-debt")))

    assert response.status_code == 202
    run = db.get_run(conn, response.json()["run_id"])
    assert run["status"] == "queued"
    assert run["trigger"] == "webhook"
    assert (run["repo"], run["issue_number"]) == (REPO, 7)
    assert run["issue_title"] == "Remove dead code"
    assert run["issue_body"] == "Details here"


async def test_trigger_label_added_to_an_existing_issue_creates_a_run(client, conn):
    response = await post_webhook(client, issues_event("labeled", added_label="tech-debt"))
    assert response.status_code == 202
    assert len(db.list_runs(conn)) == 1


async def test_a_different_label_added_to_a_tech_debt_issue_is_ignored(client, conn):
    event = issues_event("labeled", labels=("tech-debt", "bug"), added_label="bug")
    response = await post_webhook(client, event)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert "tech-debt" in response.json()["reason"]
    assert db.list_runs(conn) == []


async def test_issue_opened_without_the_trigger_label_is_ignored(client, conn):
    response = await post_webhook(client, issues_event("opened", labels=("bug",)))
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert db.list_runs(conn) == []


async def test_other_events_and_actions_are_ignored(client, conn):
    ping = await post_webhook(client, {"zen": "Keep it simple."}, event="ping")
    push = await post_webhook(client, {"ref": "refs/heads/main"}, event="push")
    closed = await post_webhook(client, issues_event("closed"))

    for response in (ping, push, closed):
        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
    assert db.list_runs(conn) == []


async def test_event_from_another_repository_is_ignored(client, conn):
    response = await post_webhook(client, issues_event("opened", repo="someone/else"))
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert db.list_runs(conn) == []


async def test_malformed_issues_payload_is_a_bad_request(client, conn):
    response = await post_webhook(client, {"action": "opened"})
    assert response.status_code == 400
    assert db.list_runs(conn) == []


async def test_accepted_delivery_is_acknowledged_before_any_devin_call(client, devin, github):
    response = await post_webhook(client, issues_event("opened"))

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert devin.create_calls == []
    assert github.comments == []


async def test_duplicate_delivery_names_the_existing_run(client, conn):
    first = await post_webhook(client, issues_event("opened"))
    second = await post_webhook(client, issues_event("labeled", added_label="tech-debt"))

    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "run_id": first.json()["run_id"]}
    assert len(db.list_runs(conn)) == 1


async def test_concurrent_opened_and_labeled_deliveries_create_one_run(client, conn):
    responses = await asyncio.gather(
        post_webhook(client, issues_event("opened")),
        post_webhook(client, issues_event("labeled", added_label="tech-debt")),
    )
    assert sorted(response.status_code for response in responses) == [200, 202]
    assert len(db.list_runs(conn)) == 1


async def test_relabelling_after_a_failed_run_creates_a_new_run(client, conn):
    first = await post_webhook(client, issues_event("opened"))
    db.update_run(conn, first.json()["run_id"], status="failed")

    second = await post_webhook(client, issues_event("labeled", added_label="tech-debt"))
    assert second.status_code == 202
    assert second.json()["run_id"] != first.json()["run_id"]
