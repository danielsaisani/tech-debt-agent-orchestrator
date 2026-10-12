import asyncio

import pytest

from app import db
from app.devin import DevinError, DevinTransientError
from app.github import GitHubError
from app.lifecycle import Worker
from tests.conftest import REPO, FakeDevin, FakeGitHub, FakeNotifier, insert_run, session_payload

PR = f"https://github.com/{REPO}/pull/12"
LONG_AGO = "2020-01-01T00:00:00+00:00"


def queue_run(conn, issue_number=7):
    return db.create_run(
        conn,
        repo=REPO,
        issue_number=issue_number,
        issue_title="Remove dead code",
        issue_body="The legacy helper is unused.",
        issue_url=f"https://github.com/{REPO}/issues/{issue_number}",
        trigger="webhook",
    )


async def running_run(conn, worker):
    """A run whose session has been launched and polled once while still working."""
    run = queue_run(conn)
    await worker.run_cycle()
    return db.get_run(conn, run["id"])


# --- Launch ---


async def test_queued_run_is_launched(conn, worker, devin, github):
    run = queue_run(conn)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert run["status"] == "running"
    assert run["devin_session_id"] == "sess1"
    assert run["devin_session_url"] == "https://app.devin.ai/sessions/sess1"
    assert run["session_started_at"]

    request = devin.create_calls[0]
    assert request["repos"] == [REPO]
    assert request["title"] == "tech-debt #7: Remove dead code"
    assert request["tags"] == ["remediation-orchestrator", "issue-7"]
    assert request["max_acu_limit"] == 10
    assert "The legacy helper is unused." in request["prompt"]
    assert f"open it against {REPO}" in request["prompt"]

    assert github.comments == [
        (REPO, 7, "Devin has started working on this issue: https://app.devin.ai/sessions/sess1")
    ]


async def test_session_create_failure_fails_the_run(conn, worker, devin, github, notifier):
    devin.create_error = DevinError("Devin returned 403: forbidden")
    run = queue_run(conn)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert (run["status"], run["failure_reason"]) == ("failed", "session_create_failed")
    assert "403" in run["error_detail"]
    assert run["finished_at"]
    assert "could not be started" in github.comments[-1][2]
    assert [finished["id"] for finished in notifier.finished] == [run["id"]]


# --- Poll ---


async def test_session_finishing_with_a_pull_request_succeeds(conn, worker, devin, github):
    run = await running_run(conn, worker)
    devin.session = session_payload("exit", "finished", PR, acus=3.5)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert run["status"] == "succeeded"
    assert run["failure_reason"] is None
    assert (run["pr_url"], run["pr_state"]) == (PR, "open")
    assert run["acus_consumed"] == 3.5
    assert run["pr_opened_at"] and run["finished_at"]
    assert (run["devin_status"], run["devin_status_detail"]) == ("exit", "finished")
    assert github.comments[-1] == (REPO, 7, f"Devin opened a pull request for this issue: {PR}")


async def test_pull_request_while_devin_is_still_working_keeps_running(conn, worker, devin):
    run = await running_run(conn, worker)
    devin.session = session_payload("running", "working", PR)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert run["status"] == "running"
    assert run["pr_url"] == PR
    first_sighting = run["pr_opened_at"]
    assert first_sighting

    conn.execute("UPDATE runs SET pr_opened_at = ? WHERE id = ?", (LONG_AGO, run["id"]))
    devin.session = session_payload("running", "waiting_for_user", PR)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert run["status"] == "succeeded"
    assert run["pr_opened_at"] == LONG_AGO  # the first sighting is kept


async def test_session_stopping_without_a_pull_request_fails_as_no_pr(conn, worker, devin, github):
    run = await running_run(conn, worker)
    devin.session = session_payload("exit", "finished")

    await worker.run_cycle()
    assert db.get_run(conn, run["id"])["status"] == "running"  # one poll is not enough

    await worker.run_cycle()
    run = db.get_run(conn, run["id"])
    assert (run["status"], run["failure_reason"]) == ("failed", "no_pr")
    assert "without opening a pull request" in github.comments[-1][2]
    assert run["devin_session_url"] in github.comments[-1][2]


async def test_session_waiting_for_a_human_fails_as_needs_input(conn, worker, devin):
    run = await running_run(conn, worker)
    devin.session = session_payload("running", "waiting_for_user")
    await worker.run_cycle()
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert (run["status"], run["failure_reason"]) == ("failed", "needs_input")


async def test_usage_limit_fails_the_run(conn, worker, devin):
    run = await running_run(conn, worker)
    devin.session = session_payload("suspended", "usage_limit_exceeded")
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert (run["status"], run["failure_reason"]) == ("failed", "usage_limit")


async def test_session_error_fails_the_run(conn, worker, devin):
    run = await running_run(conn, worker)
    devin.session = session_payload("error", None)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert (run["status"], run["failure_reason"]) == ("failed", "session_error")


async def test_session_still_working_keeps_running(conn, worker, devin):
    run = await running_run(conn, worker)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert run["status"] == "running"
    assert run["last_polled_at"]
    assert run["finished_at"] is None


# --- Timeout ---


async def test_overdue_run_times_out_and_is_not_polled_again(conn, worker, devin, github):
    run = await running_run(conn, worker)
    conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (LONG_AGO, run["id"]))
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert (run["status"], run["failure_reason"]) == ("timed_out", "timed_out")
    assert run["finished_at"]
    assert "time limit" in github.comments[-1][2]

    polls = len(devin.get_calls)
    await worker.run_cycle()
    assert len(devin.get_calls) == polls


async def test_run_within_the_timeout_is_left_running(conn, worker):
    run = await running_run(conn, worker)
    await worker.run_cycle()
    assert db.get_run(conn, run["id"])["status"] == "running"


# --- Side effects never change the outcome ---


async def test_github_comment_failure_leaves_the_run_unchanged(conn, worker, devin, github):
    github.comment_error = GitHubError("GitHub returned 403")
    run = queue_run(conn)
    await worker.run_cycle()
    assert db.get_run(conn, run["id"])["status"] == "running"

    devin.session = session_payload("exit", "finished", PR)
    await worker.run_cycle()
    run = db.get_run(conn, run["id"])
    assert (run["status"], run["pr_url"]) == ("succeeded", PR)


async def test_comment_failure_is_recorded_on_the_run(conn, worker, devin, github):
    github.comment_error = GitHubError("GitHub returned 403: Resource not accessible")
    run = queue_run(conn)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert run["status"] == "running"
    assert "403" in run["comment_error"]

    devin.session = session_payload("exit", "finished", PR)
    await worker.run_cycle()
    run = db.get_run(conn, run["id"])
    assert run["status"] == "succeeded"
    assert "403" in run["comment_error"]


async def test_comment_error_is_cleared_when_a_later_comment_succeeds(conn, worker, devin, github):
    github.comment_error = GitHubError("GitHub returned 403")
    run = queue_run(conn)
    await worker.run_cycle()
    assert db.get_run(conn, run["id"])["comment_error"]

    github.comment_error = None  # the token was fixed mid-run
    devin.session = session_payload("exit", "finished", PR)
    await worker.run_cycle()

    run = db.get_run(conn, run["id"])
    assert run["status"] == "succeeded"
    assert run["comment_error"] is None
    assert len(github.comments) == 1


async def test_successful_comments_leave_no_comment_error(conn, worker, devin):
    run = await running_run(conn, worker)
    assert db.get_run(conn, run["id"])["comment_error"] is None


async def test_notifier_is_called_once_with_the_final_run(conn, worker, devin, notifier):
    run = await running_run(conn, worker)
    assert notifier.finished == []

    devin.session = session_payload("exit", "finished", PR)
    await worker.run_cycle()
    await worker.run_cycle()

    assert len(notifier.finished) == 1
    assert notifier.finished[0]["id"] == run["id"]
    assert notifier.finished[0]["status"] == "succeeded"
    assert notifier.finished[0]["pr_url"] == PR


async def test_notifier_failure_leaves_the_run_unchanged(conn, worker, devin, notifier):
    notifier.error = RuntimeError("Slack returned 500")
    run = await running_run(conn, worker)
    devin.session = session_payload("exit", "finished", PR)
    await worker.run_cycle()

    assert db.get_run(conn, run["id"])["status"] == "succeeded"


# --- Resilience ---


@pytest.mark.parametrize(
    "error", [DevinTransientError("Devin returned 503"), DevinError("Devin returned 404")]
)
async def test_failed_poll_keeps_the_run_running(conn, worker, devin, error):
    run = await running_run(conn, worker)
    devin.get_error = error
    await worker.run_cycle()
    assert db.get_run(conn, run["id"])["status"] == "running"

    devin.get_error = None
    devin.session = session_payload("exit", "finished", PR)
    await worker.run_cycle()
    assert db.get_run(conn, run["id"])["status"] == "succeeded"


async def test_finished_runs_trigger_no_devin_request(conn, worker, devin):
    for status in ("succeeded", "failed", "timed_out"):
        insert_run(conn, status=status, devin_session_id="sess-old", finished_at=LONG_AGO)
    before = {run["id"]: run for run in db.list_runs(conn)}

    await worker.run_cycle()

    assert devin.get_calls == [] and devin.create_calls == []
    assert {run["id"]: run for run in db.list_runs(conn)} == before


async def test_fresh_worker_resumes_queued_and_running_runs(settings, tmp_path):
    """A restart: a new connection and worker over a database with active runs."""
    first = db.connect(settings.database_path)
    queued = insert_run(first, status="queued", created_at=db.utcnow())
    running = insert_run(
        first, status="running", created_at=db.utcnow(), devin_session_id="sess-before-restart"
    )
    first.close()

    conn = db.connect(settings.database_path)
    devin, github = FakeDevin(), FakeGitHub()
    devin.session = session_payload("exit", "finished", PR)
    worker = Worker(conn, settings, devin, github, FakeNotifier())
    await worker.run_cycle()

    assert len(devin.create_calls) == 1
    assert "sess-before-restart" in devin.get_calls
    assert db.get_run(conn, running["id"])["status"] == "succeeded"
    assert db.get_run(conn, queued["id"])["devin_session_id"] == "sess1"
    conn.close()


# --- Loop ---


async def test_wake_launches_a_queued_run_without_waiting_for_the_interval(conn, worker, devin):
    assert worker._settings.poll_interval_seconds >= 30
    worker.start()
    try:
        await asyncio.sleep(0.01)  # the first, empty cycle has run and the loop is waiting
        run = queue_run(conn)
        worker.wake()
        await asyncio.sleep(0.05)
        assert db.get_run(conn, run["id"])["status"] == "running"
    finally:
        await worker.stop()


async def test_loop_survives_a_failing_cycle(conn, worker, devin):
    devin.create_error = RuntimeError("unexpected")  # not a DevinError: escapes the cycle
    worker.start()
    try:
        run = queue_run(conn)
        worker.wake()
        await asyncio.sleep(0.05)
        assert db.get_run(conn, run["id"])["status"] == "queued"

        devin.create_error = None
        worker.wake()
        await asyncio.sleep(0.05)
        assert db.get_run(conn, run["id"])["status"] == "running"
    finally:
        await worker.stop()
