import asyncio

from app import db
from app.github import GitHubError
from app.main import create_app
from app.sync import sync_labeled_issues
from tests.conftest import REPO, insert_run, issue_payload, make_settings


def labeled(number: int) -> dict:
    return issue_payload(number=number, title=f"Issue {number}", labels=("tech-debt",))


# --- The sweep ---


async def test_labeled_issue_without_a_run_is_queued(conn, settings, github):
    github.issues[7] = labeled(7)
    result = await sync_labeled_issues(conn, settings, github)

    run = db.get_run(conn, result["queued"][0]["run_id"])
    assert result == {
        "queued": [{"run_id": run["id"], "issue_number": 7}], "already_tracked": 0, "deferred": 0,
    }
    assert (run["status"], run["trigger"]) == ("queued", "sync")
    assert (run["repo"], run["issue_title"], run["issue_body"]) == (REPO, "Issue 7", "Details here")


async def test_issue_with_a_run_in_any_status_is_left_alone(conn, settings, github):
    for number, status in enumerate(["queued", "running", "succeeded", "failed", "timed_out"], 1):
        github.issues[number] = labeled(number)
        insert_run(conn, issue_number=number, status=status)

    result = await sync_labeled_issues(conn, settings, github)
    assert result == {"queued": [], "already_tracked": 5, "deferred": 0}
    assert len(db.list_runs(conn)) == 5


async def test_issues_without_the_trigger_label_are_not_queued(conn, settings, github):
    github.issues[1] = issue_payload(number=1, labels=("bug",))
    result = await sync_labeled_issues(conn, settings, github)
    assert result["queued"] == []
    assert db.list_runs(conn) == []


async def test_sweep_is_capped_and_takes_the_oldest_issues_first(conn, settings, github):
    for number in (30, 12, 25, 8, 19, 41, 3, 16):
        github.issues[number] = labeled(number)

    result = await sync_labeled_issues(conn, settings, github)
    assert [item["issue_number"] for item in result["queued"]] == [3, 8, 12, 16, 19]
    assert result["deferred"] == 3

    # The next sweep takes the rest.
    result = await sync_labeled_issues(conn, settings, github)
    assert [item["issue_number"] for item in result["queued"]] == [25, 30, 41]
    assert (result["already_tracked"], result["deferred"]) == (5, 0)


async def test_a_second_sweep_queues_nothing_new(conn, settings, github):
    github.issues[7] = labeled(7)
    await sync_labeled_issues(conn, settings, github)
    result = await sync_labeled_issues(conn, settings, github)
    assert result == {"queued": [], "already_tracked": 1, "deferred": 0}


# --- The endpoint ---


async def test_sync_endpoint_queues_missed_issues(client, conn, github, devin):
    github.issues[7] = labeled(7)
    response = await client.post("/api/runs/sync")

    assert response.status_code == 200
    assert [item["issue_number"] for item in response.json()["queued"]] == [7]
    assert db.list_runs(conn)[0]["trigger"] == "sync"
    assert devin.create_calls == []  # launched by the worker, not the request


async def test_sync_endpoint_reports_a_github_failure(client, conn, github):
    github.list_error = GitHubError("GitHub returned 500")
    response = await client.post("/api/runs/sync")
    assert response.status_code == 502
    assert db.list_runs(conn) == []


# --- At startup ---


async def test_startup_queues_and_launches_an_issue_labeled_while_the_service_was_down(
    app, conn, github, devin
):
    github.issues[7] = labeled(7)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        runs = db.list_runs(conn)
        assert [(run["issue_number"], run["trigger"]) for run in runs] == [(7, "sync")]
        assert runs[0]["status"] == "running"
    assert len(devin.create_calls) == 1


async def test_startup_continues_when_github_cannot_be_read(app, github):
    github.list_error = GitHubError("GitHub returned 500")
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        assert app.state.worker._task is not None and not app.state.worker._task.done()


async def test_startup_sweep_can_be_disabled(tmp_path, devin, github, notifier):
    github.issues[7] = labeled(7)
    settings = make_settings(tmp_path, sync_on_startup=False)
    app = create_app(settings, devin=devin, github=github, notifier=notifier)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        assert db.list_runs(app.state.conn) == []
