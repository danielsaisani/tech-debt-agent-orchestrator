import hashlib
import hmac
import json

import httpx
import pytest

from app import db
from app.config import Settings
from app.main import create_app

REPO = "octo/superset"
WEBHOOK_SECRET = "test-webhook-secret"
REQUIRED_ENV = (
    "DEVIN_API_KEY", "DEVIN_ORG_ID", "GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET", "TARGET_REPO",
)


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "devin_api_key": "test-devin-key",
        "devin_org_id": "org-test",
        "github_token": "test-github-token",
        "github_webhook_secret": WEBHOOK_SECRET,
        "target_repo": REPO,
        "database_path": str(tmp_path / "runs.db"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeDevin:
    """Stands in for DevinClient. `session` is what every poll returns."""

    def __init__(self):
        self.create_calls: list[dict] = []
        self.get_calls: list[str] = []
        self.create_error: Exception | None = None
        self.get_error: Exception | None = None
        self.session = session_payload()

    async def create_session(self, **kwargs) -> dict:
        self.create_calls.append(kwargs)
        if self.create_error:
            raise self.create_error
        session_id = f"sess{len(self.create_calls)}"
        return {
            "session_id": session_id,
            "url": f"https://app.devin.ai/sessions/{session_id}",
            "status": "new",
        }

    async def get_session(self, session_id: str) -> dict:
        self.get_calls.append(session_id)
        if self.get_error:
            raise self.get_error
        return dict(self.session)


class FakeGitHub:
    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.comments: list[tuple[str, int, str]] = []
        self.comment_error: Exception | None = None
        self.list_error: Exception | None = None

    async def get_issue(self, repo: str, number: int) -> dict | None:
        return self.issues.get(number)

    async def list_labeled_issues(self, repo: str, label: str) -> list[dict]:
        if self.list_error:
            raise self.list_error
        return [
            issue for issue in self.issues.values()
            if any(item["name"] == label for item in issue["labels"])
        ]

    async def create_comment(self, repo: str, number: int, body: str) -> None:
        if self.comment_error:
            raise self.comment_error
        self.comments.append((repo, number, body))


class FakeNotifier:
    def __init__(self):
        self.finished: list[dict] = []
        self.error: Exception | None = None

    async def run_finished(self, run: dict) -> None:
        if self.error:
            raise self.error
        self.finished.append(run)


def session_payload(status="running", detail="working", pr_url=None, acus=0.0) -> dict:
    pull_requests = [{"pr_url": pr_url, "pr_state": "open"}] if pr_url else []
    return {
        "status": status,
        "status_detail": detail,
        "pull_requests": pull_requests,
        "acus_consumed": acus,
    }


def issue_payload(number=7, title="Remove dead code", body="Details here", labels=()) -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "labels": [{"name": name} for name in labels],
    }


def issues_event(action="opened", *, labels=("tech-debt",), added_label=None, number=7,
                 repo=REPO) -> dict:
    event = {
        "action": action,
        "issue": issue_payload(number=number, labels=labels),
        "repository": {"full_name": repo},
    }
    if added_label is not None:
        event["label"] = {"name": added_label}
    return event


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def post_webhook(client, payload: dict, *, event="issues", signature="valid"):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if signature == "valid":
        headers["X-Hub-Signature-256"] = sign(body)
    elif signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return await client.post("/webhooks/github", content=body, headers=headers)


def insert_run(conn, **overrides) -> dict:
    """Insert a run row directly, for tests that need a specific history."""
    number = overrides.pop("issue_number", None)
    if number is None:
        number = conn.execute("SELECT COALESCE(MAX(issue_number), 0) + 1 FROM runs").fetchone()[0]
    row = {
        "repo": REPO,
        "issue_number": number,
        "issue_title": f"Issue {number}",
        "issue_body": "",
        "issue_url": f"https://github.com/{REPO}/issues/{number}",
        "trigger": "webhook",
        "status": "queued",
        "created_at": "2026-10-12T09:00:00+00:00",
    }
    row.update(overrides)
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    cur = conn.execute(f"INSERT INTO runs ({columns}) VALUES ({placeholders})", list(row.values()))
    return db.get_run(conn, cur.lastrowid)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (*REQUIRED_ENV, "DEVIN_SIMULATE", "SYNC_ON_STARTUP", "SYNC_MAX_RUNS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def devin():
    return FakeDevin()


@pytest.fixture
def github():
    return FakeGitHub()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def app(settings, devin, github, notifier):
    app = create_app(settings, devin=devin, github=github, notifier=notifier)
    yield app
    app.state.conn.close()


@pytest.fixture
def conn(app):
    return app.state.conn


@pytest.fixture
def worker(app):
    """The app's worker, not started: tests drive it with run_cycle()."""
    return app.state.worker


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
