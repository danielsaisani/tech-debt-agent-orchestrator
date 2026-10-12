import json

import httpx
import pytest
import respx

from app import db
from app.config import Settings
from app.main import create_app
from app.simulate import SimulatedDevin, SimulatedGitHub, scenario_from
from tests.conftest import sign


@pytest.fixture
def sim_app(tmp_path):
    """The app in simulation mode with no credentials at all."""
    settings = Settings(
        _env_file=None, devin_simulate=True, database_path=str(tmp_path / "runs.db")
    )
    app = create_app(settings)
    yield app
    app.state.conn.close()


@pytest.fixture
async def sim_client(sim_app):
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def send_issue(client, *, number=7, title="Remove dead code"):
    payload = {
        "action": "labeled",
        "label": {"name": "tech-debt"},
        "issue": {
            "number": number,
            "title": title,
            "body": "",
            "html_url": f"https://github.com/simulated/superset/issues/{number}",
            "labels": [{"name": "tech-debt"}],
        },
        "repository": {"full_name": "simulated/superset"},
    }
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": "issues", "X-Hub-Signature-256": sign(body, secret="simulate")}
    response = await client.post("/webhooks/github", content=body, headers=headers)
    assert response.status_code == 202
    return response.json()["run_id"]


async def cycles(app, count):
    for _ in range(count):
        await app.state.worker.run_cycle()


def test_scenario_markers():
    assert scenario_from("Remove dead code") == "success"
    assert scenario_from("tech-debt #7: Remove dead code [sim:no_pr]") == "no_pr"
    assert scenario_from("Remove dead code [sim:made_up]") == "success"


def test_simulation_mode_uses_the_simulated_clients(sim_app):
    assert isinstance(sim_app.state.github, SimulatedGitHub)
    assert isinstance(sim_app.state.worker._devin, SimulatedDevin)
    assert sim_app.state.worker._notifier._webhook_url is None


@respx.mock
async def test_simulated_run_succeeds_with_no_credentials_and_no_network(sim_app, sim_client):
    run_id = await send_issue(sim_client)
    await cycles(sim_app, 1)
    assert db.get_run(sim_app.state.conn, run_id)["status"] == "running"

    await cycles(sim_app, 1)
    run = db.get_run(sim_app.state.conn, run_id)
    assert run["status"] == "succeeded"
    assert run["pr_url"].startswith("https://simulated.invalid/simulated/superset/pull/")
    assert run["devin_session_url"].startswith("https://simulated.invalid/sessions/sim-success-")
    assert run["acus_consumed"] > 0
    assert len(respx.calls) == 0  # nothing left the process


@pytest.mark.parametrize(
    "scenario, status, reason",
    [
        ("no_pr", "failed", "no_pr"),
        ("needs_input", "failed", "needs_input"),
        ("session_error", "failed", "session_error"),
        ("usage_limit", "failed", "usage_limit"),
        ("create_failed", "failed", "session_create_failed"),
    ],
)
async def test_simulated_failure_on_demand(sim_app, sim_client, scenario, status, reason):
    run_id = await send_issue(sim_client, title=f"Remove dead code [sim:{scenario}]")
    await cycles(sim_app, 4)

    run = db.get_run(sim_app.state.conn, run_id)
    assert (run["status"], run["failure_reason"]) == (status, reason)


async def test_simulated_timeout_never_finishes_on_its_own(sim_app, sim_client):
    run_id = await send_issue(sim_client, title="Rewrite everything [sim:timeout]")
    await cycles(sim_app, 6)
    assert db.get_run(sim_app.state.conn, run_id)["status"] == "running"

    sim_app.state.conn.execute(
        "UPDATE runs SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (run_id,)
    )
    await cycles(sim_app, 1)
    assert db.get_run(sim_app.state.conn, run_id)["status"] == "timed_out"


async def test_simulated_session_survives_a_restart(sim_app, sim_client, tmp_path):
    """The scenario is in the session id, so a new process still knows the outcome."""
    run_id = await send_issue(sim_client, title="Remove dead code [sim:no_pr]")
    await cycles(sim_app, 1)

    restarted = create_app(sim_app.state.settings)
    await cycles(restarted, 4)
    run = db.get_run(restarted.state.conn, run_id)
    restarted.state.conn.close()
    assert (run["status"], run["failure_reason"]) == ("failed", "no_pr")


async def test_manual_trigger_works_in_simulation_mode(sim_app, sim_client):
    response = await sim_client.post("/api/runs/trigger", json={"issue_number": 3})
    assert response.status_code == 202
    assert response.json()["issue_title"] == "Simulated issue 3"

    await cycles(sim_app, 2)
    assert db.get_run(sim_app.state.conn, response.json()["id"])["status"] == "succeeded"


async def test_metrics_report_simulation_mode(sim_client, client):
    assert (await sim_client.get("/api/metrics")).json()["simulated"] is True
    assert (await client.get("/api/metrics")).json()["simulated"] is False
