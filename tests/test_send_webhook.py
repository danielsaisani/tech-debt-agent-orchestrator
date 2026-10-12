import json

from app import db
from scripts import send_webhook
from tests.conftest import REPO, WEBHOOK_SECRET


def script_request(*argv) -> tuple[bytes, dict]:
    """The body and headers the script would send for these arguments."""
    args = send_webhook.parse_args(["--repo", REPO, "--secret", WEBHOOK_SECRET, *argv])
    payload = send_webhook.build_payload(
        repo=args.repo, issue=args.issue, title=args.title, body=args.body,
        action=args.action, label=args.label,
    )
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": send_webhook.signature(args.secret, body),
    }
    return body, headers


async def test_script_payload_and_signature_are_accepted(client, conn):
    body, headers = script_request("--issue", "12", "--title", "Remove dead code")
    response = await client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 202
    run = db.get_run(conn, response.json()["run_id"])
    assert (run["issue_number"], run["issue_title"], run["trigger"]) == (
        12, "Remove dead code", "webhook",
    )


async def test_script_can_send_an_opened_event(client):
    body, headers = script_request("--action", "opened")
    assert json.loads(body)["action"] == "opened"
    assert "label" not in json.loads(body)
    assert (await client.post("/webhooks/github", content=body, headers=headers)).status_code == 202


async def test_script_with_the_wrong_secret_is_rejected(client):
    body, headers = script_request("--secret", "not-the-secret")
    assert (await client.post("/webhooks/github", content=body, headers=headers)).status_code == 401


def test_scenario_flag_adds_the_simulation_marker():
    args = send_webhook.parse_args(["--title", "Remove dead code", "--scenario", "no_pr"])
    assert args.title == "Remove dead code [sim:no_pr]"


def test_settings_fall_back_from_environment_to_env_file_to_simulation(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TARGET_REPO=me/superset\nGITHUB_WEBHOOK_SECRET='from-file'\n")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)

    assert send_webhook.setting("GITHUB_WEBHOOK_SECRET", env_file) == "from-file"
    assert send_webhook.setting("TARGET_REPO", tmp_path / "missing.env") == "simulated/superset"
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "from-env")
    assert send_webhook.setting("GITHUB_WEBHOOK_SECRET", env_file) == "from-env"
