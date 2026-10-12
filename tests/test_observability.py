from datetime import datetime, timedelta

import pytest

from tests.conftest import insert_run

T0 = "2026-10-12T09:00:00+00:00"


def at(seconds: int) -> str:
    """A timestamp the given number of seconds after T0."""
    return (datetime.fromisoformat(T0) + timedelta(seconds=seconds)).isoformat()


def succeeded(conn, *, time_to_pr=600, duration=None, acus=None):
    return insert_run(
        conn,
        status="succeeded",
        created_at=T0,
        pr_opened_at=at(time_to_pr),
        finished_at=at(duration if duration is not None else time_to_pr),
        pr_url="https://github.com/octo/superset/pull/1",
        acus_consumed=acus,
    )


def unsuccessful(conn, reason, *, status="failed", duration=60, acus=None):
    return insert_run(
        conn, status=status, failure_reason=reason, created_at=T0, finished_at=at(duration),
        acus_consumed=acus,
    )


# --- Health ---


async def test_health_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_unavailable_when_the_run_store_cannot_be_read(client, conn):
    conn.close()
    response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


# --- Metrics ---


async def test_metrics_with_no_runs(client):
    metrics = (await client.get("/api/metrics")).json()

    assert metrics["total_runs"] == 0
    assert metrics["active_runs"] == 0
    assert metrics["by_status"] == {
        "queued": 0, "running": 0, "succeeded": 0, "failed": 0, "timed_out": 0,
    }
    assert metrics["success_rate"] is None
    assert metrics["median_time_to_pr_seconds"] is None
    assert metrics["median_run_duration_seconds"] is None
    assert metrics["total_acus"] == 0
    assert metrics["acus_per_succeeded_run"] is None
    assert metrics["failure_reasons"] == {}
    assert metrics["simulated"] is False


async def test_outcome_metrics_with_mixed_outcomes(client, conn):
    for _ in range(3):
        succeeded(conn)
    unsuccessful(conn, "no_pr")
    unsuccessful(conn, "timed_out", status="timed_out")
    insert_run(conn, status="running", created_at=T0)

    metrics = (await client.get("/api/metrics")).json()
    assert metrics["total_runs"] == 6
    assert metrics["active_runs"] == 1
    assert metrics["by_status"] == {
        "queued": 0, "running": 1, "succeeded": 3, "failed": 1, "timed_out": 1,
    }
    assert metrics["success_rate"] == pytest.approx(0.6)


async def test_success_rate_is_null_until_a_run_finishes(client, conn):
    insert_run(conn, status="queued")
    insert_run(conn, status="running")
    metrics = (await client.get("/api/metrics")).json()
    assert metrics["active_runs"] == 2
    assert metrics["success_rate"] is None


async def test_median_time_to_pull_request(client, conn):
    for seconds in (300, 600, 1500):
        succeeded(conn, time_to_pr=seconds)
    unsuccessful(conn, "no_pr", duration=30)  # not a succeeded run: excluded

    metrics = (await client.get("/api/metrics")).json()
    assert metrics["median_time_to_pr_seconds"] == 600


async def test_median_time_to_pull_request_is_null_without_a_success(client, conn):
    unsuccessful(conn, "no_pr")
    metrics = (await client.get("/api/metrics")).json()
    assert metrics["median_time_to_pr_seconds"] is None


async def test_median_run_duration_covers_all_finished_runs(client, conn):
    succeeded(conn, time_to_pr=100, duration=200)
    unsuccessful(conn, "no_pr", duration=400)
    unsuccessful(conn, "timed_out", status="timed_out", duration=3600)
    insert_run(conn, status="running", created_at=T0)  # not finished: excluded

    metrics = (await client.get("/api/metrics")).json()
    assert metrics["median_run_duration_seconds"] == 400


async def test_cost_metrics(client, conn):
    succeeded(conn, acus=5)
    succeeded(conn, acus=3)
    succeeded(conn, acus=2)
    unsuccessful(conn, "no_pr", acus=2)

    metrics = (await client.get("/api/metrics")).json()
    assert metrics["total_acus"] == 12
    assert metrics["acus_per_succeeded_run"] == 4


async def test_failure_breakdown(client, conn):
    unsuccessful(conn, "no_pr")
    unsuccessful(conn, "no_pr")
    unsuccessful(conn, "needs_input")
    unsuccessful(conn, "timed_out", status="timed_out")
    succeeded(conn)

    metrics = (await client.get("/api/metrics")).json()
    assert metrics["failure_reasons"] == {"no_pr": 2, "needs_input": 1, "timed_out": 1}


# --- Run list and detail ---


async def test_run_list_is_newest_first_and_defaults_to_50(client, conn):
    for minute in range(60):
        insert_run(conn, created_at=f"2026-10-12T09:{minute:02d}:00+00:00")

    runs = (await client.get("/api/runs")).json()
    assert len(runs) == 50
    created = [run["created_at"] for run in runs]
    assert created == sorted(created, reverse=True)
    assert created[0] == "2026-10-12T09:59:00+00:00"


async def test_run_list_filters_by_status(client, conn):
    insert_run(conn, status="running")
    failed = insert_run(conn, status="failed")

    runs = (await client.get("/api/runs", params={"status": "failed"})).json()
    assert [run["id"] for run in runs] == [failed["id"]]


async def test_run_list_limit_is_capped_at_200(client, conn):
    for _ in range(205):
        insert_run(conn, status="failed")

    assert len((await client.get("/api/runs", params={"limit": 3})).json()) == 3
    assert len((await client.get("/api/runs", params={"limit": 1000})).json()) == 200


async def test_run_list_rejects_an_unknown_status(client):
    response = await client.get("/api/runs", params={"status": "exploded"})
    assert response.status_code == 422


async def test_run_detail(client, conn):
    run = insert_run(
        conn,
        status="succeeded",
        devin_session_url="https://app.devin.ai/sessions/s1",
        pr_url="https://github.com/octo/superset/pull/1",
    )
    response = await client.get(f"/api/runs/{run['id']}")

    assert response.status_code == 200
    assert response.json() == run
    assert response.json()["issue_url"] == run["issue_url"]


async def test_run_detail_for_an_unknown_run(client):
    assert (await client.get("/api/runs/999")).status_code == 404


# --- Dashboard ---


async def test_dashboard_is_a_self_contained_page(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    assert "/api/metrics" in page and "/api/runs" in page
    # No build step and no external scripts, styles or fonts.
    assert "<script src" not in page
    assert "<link" not in page
    assert "@import" not in page
    assert 'id="simulated"' in page  # the simulation notice, hidden unless metrics say so
