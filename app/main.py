"""remediation-orchestrator: FastAPI app wiring.

Run with: uvicorn --factory app.main:create_app
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app import db
from app.config import Settings
from app.devin import DevinClient
from app.github import GitHubClient
from app.lifecycle import Worker
from app.notify import SlackNotifier
from app.routes import observability, runs, webhooks
from app.simulate import SimulatedDevin, SimulatedGitHub
from app.sync import sync_labeled_issues

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, devin=None, github=None, notifier=None):
    """Build the app. Clients can be injected so tests run without network access."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = settings or Settings()
    conn = db.connect(settings.database_path)
    http = httpx.AsyncClient(timeout=30)
    if settings.devin_simulate:
        log.warning("SIMULATION MODE: Devin, GitHub and Slack are simulated; no real work is done")
        devin = devin or SimulatedDevin(settings.target_repo, settings.devin_simulate_polls)
        github = github or SimulatedGitHub()
        notifier = notifier or SlackNotifier(None, http)
    devin = devin or DevinClient(
        settings.devin_api_key, settings.devin_org_id, settings.devin_api_base, http
    )
    github = github or GitHubClient(settings.github_token, settings.github_api_base, http)
    notifier = notifier or SlackNotifier(settings.slack_webhook_url, http)
    worker = Worker(conn, settings, devin, github, notifier)

    async def startup_sync() -> None:
        """Catch issues labeled while the service was down. Never blocks startup."""
        try:
            result = await sync_labeled_issues(conn, settings, github)
        except Exception as exc:
            log.warning("startup sync failed: %s", exc)
            return
        log.info("startup sync: %s", result)
        if result["queued"]:
            worker.wake()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The worker resumes every active run from the database, so a restart
        # needs no recovery step of its own.
        worker.start()
        sync_task = asyncio.create_task(startup_sync()) if settings.sync_on_startup else None
        yield
        if sync_task:
            sync_task.cancel()
            await asyncio.gather(sync_task, return_exceptions=True)
        await worker.stop()
        await http.aclose()
        conn.close()

    app = FastAPI(title="remediation-orchestrator", lifespan=lifespan)
    app.state.settings = settings
    app.state.conn = conn
    app.state.github = github
    app.state.worker = worker
    app.include_router(webhooks.router)
    app.include_router(runs.router)
    app.include_router(observability.router)
    return app
