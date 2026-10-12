"""One test per row of the status mapping in design decision 4."""

import pytest

from app.lifecycle import USAGE_LIMIT_DETAILS, Outcome, map_session
from tests.conftest import session_payload

PR = "https://github.com/octo/superset/pull/1"


@pytest.mark.parametrize(
    "status, detail",
    [("new", None), ("claimed", None), ("resuming", None), ("running", "working"),
     ("running", None)],
)
def test_session_still_working_keeps_the_run_running(status, detail):
    assert map_session(session_payload(status, detail), 0) == Outcome("running")


def test_pull_request_while_still_working_keeps_the_run_running():
    assert map_session(session_payload("running", "working", PR), 0) == Outcome("running")


@pytest.mark.parametrize(
    "status, detail",
    [("exit", "finished"), ("exit", None), ("suspended", "inactivity"),
     ("running", "waiting_for_user"), ("running", "waiting_for_approval"),
     ("suspended", "user_request"), ("running", "finished")],
)
def test_resting_with_a_pull_request_succeeds(status, detail):
    assert map_session(session_payload(status, detail, PR), 0) == Outcome("succeeded")


@pytest.mark.parametrize(
    "status, detail", [("error", None), ("exit", "error"), ("suspended", "out_of_credits")]
)
def test_a_pull_request_counts_even_if_the_session_then_stopped_badly(status, detail):
    assert map_session(session_payload(status, detail, PR), 0) == Outcome("succeeded")


@pytest.mark.parametrize("status, detail", [("error", None), ("running", "error")])
def test_error_without_a_pull_request_fails_as_session_error(status, detail):
    outcome = map_session(session_payload(status, detail), 0)
    assert outcome == Outcome("failed", "session_error")


@pytest.mark.parametrize("detail", sorted(USAGE_LIMIT_DETAILS))
def test_usage_or_billing_limit_fails_as_usage_limit(detail):
    outcome = map_session(session_payload("suspended", detail), 0)
    assert outcome == Outcome("failed", "usage_limit")


@pytest.mark.parametrize("detail", ["waiting_for_user", "waiting_for_approval"])
def test_waiting_for_a_human_fails_as_needs_input_on_the_second_poll(detail):
    session = session_payload("running", detail)
    first = map_session(session, 0)
    assert first == Outcome("running", idle_polls=1)
    assert map_session(session, first.idle_polls) == Outcome("failed", "needs_input", 2)


@pytest.mark.parametrize(
    "status, detail",
    [("exit", "finished"), ("exit", None), ("suspended", "inactivity"),
     ("suspended", "user_request")],
)
def test_stopping_without_a_pull_request_fails_as_no_pr_on_the_second_poll(status, detail):
    session = session_payload(status, detail)
    first = map_session(session, 0)
    assert first == Outcome("running", idle_polls=1)
    assert map_session(session, first.idle_polls) == Outcome("failed", "no_pr", 2)


def test_idle_count_resets_when_the_session_resumes_working():
    paused = map_session(session_payload("running", "waiting_for_user"), 0)
    resumed = map_session(session_payload("running", "working"), paused.idle_polls)
    assert resumed == Outcome("running", idle_polls=0)


def test_unknown_status_values_keep_the_run_running():
    assert map_session(session_payload("hibernating", "dreaming"), 1) == Outcome("running")
    assert map_session({}, 0) == Outcome("running")
