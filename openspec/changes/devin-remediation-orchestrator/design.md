# Design

## Context

Greenfield repository: no code, no git history, only this change. See `proposal.md` for motivation and the three specs under `specs/` for required behaviour.

Constraints that shape the approach:

- It is a take-home demo run locally with `docker compose up`, reviewed by reading the code. Small and legible beats scalable.
- The Devin Organization API v3 was checked against the published reference on 2026-10-12:
  - `POST https://api.devin.ai/v3/organizations/{org_id}/sessions`, bearer auth with a service user key (`cog_` prefix). Only `prompt` is required; `repos`, `tags`, `title` and `max_acu_limit` are optional.
  - `GET https://api.devin.ai/v3/organizations/{org_id}/sessions/{devin_id}` returns `status`, `status_detail`, `pull_requests[]` (`pr_url`, `pr_state`), `acus_consumed` and `url`.
  - `status` is one of `new`, `claimed`, `running`, `exit`, `error`, `suspended`, `resuming`. `status_detail` is only populated on get/list and includes `working`, `waiting_for_user`, `waiting_for_approval`, `finished`, `inactivity`, `user_request`, `error` and a family of usage, quota and billing values.
- There is no Devin webhook for session completion in this flow, so outcomes must be polled.
- GitHub gives a webhook delivery 10 seconds to be acknowledged.

## Goals / Non-Goals

**Goals:**

- One process, one container, one SQLite file. A reviewer can read the whole service in a sitting.
- No run is lost or duplicated by a restart, a duplicate delivery or a slow upstream.
- External calls sit behind two thin clients so the whole lifecycle is testable without network access.

**Non-Goals:**

- Horizontal scaling or multiple worker processes. The polling loop assumes a single instance.
- Terminating Devin sessions on timeout. The session is left alone; spend is bounded by `max_acu_limit` instead.
- Tracking a pull request after the run finishes (review, merge, revert). Merge rate is the natural next leader metric but needs `pull_request` webhooks or continued polling.
- Periodic scanning of GitHub. The sweep runs at startup and on request only; the webhook stays the primary trigger.
- Replying to a session that is waiting for input. That outcome is recorded as `needs_input`.
- Schema migrations. The schema is created on startup; a schema change during development means deleting the volume.

## Decisions

### 1. One database-driven worker loop, not per-request background tasks

A single asyncio task started in the FastAPI lifespan runs a cycle every `POLL_INTERVAL_SECONDS` (default 30): launch a session for each `queued` run, then poll each `running` run, then time out overdue runs. The webhook and manual trigger handlers only insert a `queued` row and set an `asyncio.Event` that wakes the loop immediately.

- *Why:* handlers return in milliseconds, which satisfies prompt acknowledgement; all state lives in the database, so restart recovery is the normal code path and needs no extra logic; and there is exactly one place where a run changes state.
- *Alternative: FastAPI `BackgroundTasks` per webhook, each polling its own run.* Rejected: tasks die with the process, so a restart strands runs.
- *Alternative: Celery or RQ with Redis.* Rejected: two more containers for a workload of a handful of runs.

Runs in a cycle are handled sequentially. With a 30 second interval and tens of runs this is ample, and it avoids concurrency inside the loop.

### 2. SQLite through the standard library, one `runs` table

`sqlite3` with WAL mode, a small repository module of plain functions, no ORM. Calls are synchronous and short, made directly from async handlers.

```
runs(
  id INTEGER PRIMARY KEY,
  repo TEXT, issue_number INTEGER, issue_title TEXT, issue_body TEXT, issue_url TEXT,
  trigger TEXT,                 -- webhook | manual
  status TEXT,                  -- queued | running | succeeded | failed | timed_out
  failure_reason TEXT, error_detail TEXT,
  devin_session_id TEXT, devin_session_url TEXT,
  devin_status TEXT, devin_status_detail TEXT,
  pr_url TEXT, pr_state TEXT, acus_consumed REAL,
  idle_polls INTEGER DEFAULT 0,
  created_at TEXT, session_started_at TEXT, pr_opened_at TEXT,
  finished_at TEXT, last_polled_at TEXT
)
```

`issue_body` is stored because the session is launched by the worker after the handler has returned, and the prompt needs it. Timestamps are the service's own UTC ISO-8601 strings. Devin's `created_at` and `updated_at` are not used because the reference does not state their unit.

- *Why no ORM:* one table and about eight queries. SQLAlchemy would be more code than the SQL it replaces.
- *Alternative: Postgres.* Rejected: a second container and no benefit at this size.

### 3. Deduplication enforced by the database

A partial unique index, `UNIQUE (repo, issue_number) WHERE status IN ('queued', 'running')`, makes a second active run for the same issue impossible. Creating a run is a plain `INSERT`; an integrity error means "duplicate", and the handler looks up and returns the existing run.

- *Why:* GitHub sends both `opened` and `labeled` when an issue is created with the label, usually within milliseconds. A check-then-insert in application code would race; the index cannot.
- *Alternative: dedupe on the `X-GitHub-Delivery` id.* Rejected: those two deliveries have different ids, so it would not catch the common case.

Dedupe covers active runs only. A finished issue can be run again by removing and re-adding the label, which is a deliberate human action.

### 4. Status mapping from Devin to run outcome

Evaluated in order on each poll of a `running` run. "Stopped" means errored, limited or resting, as defined below.

| # | Devin session state | Run outcome |
|---|---|---|
| 1 | Not stopped (`new`, `claimed`, `resuming`, `running` + `working`, or any unrecognised value) | stays `running` |
| 2 | Stopped, and has a pull request | `succeeded` |
| 3 | Errored: `status = error` or `status_detail = error` | `failed` / `session_error` |
| 4 | Limited: `status_detail` is a usage, quota, credit, payment or contract value | `failed` / `usage_limit` |
| 5 | Resting with `status_detail` `waiting_for_user` or `waiting_for_approval`, on 2 consecutive polls | `failed` / `needs_input` |
| 6 | Resting otherwise, on 2 consecutive polls | `failed` / `no_pr` |

"Resting" means `status` is `exit` or `suspended`, or `status_detail` is `finished`, `waiting_for_user`, `waiting_for_approval`, `inactivity` or `user_request`.

- A pull request wins over how the session stopped (row 2 before rows 3 and 4), as the `remediation-runs` spec requires: every failure reason applies only to a session that stopped without one. A session that opens its pull request and then hits its ACU limit has still delivered.
- Success is "pull request exists and Devin has stopped", not `status = exit`. Devin commonly opens the pull request and then waits for the user, so waiting with a pull request is the expected successful end state.
- The first poll that shows any pull request stamps `pr_opened_at`, even if Devin is still working, so time to pull request is not inflated by Devin's own follow-up work.
- Rows 5 and 6 need two consecutive polls (`idle_polls`) so a momentary resting state early in a session does not fail the run.
- The mapping is a pure function of the session payload and the previous `idle_polls`, kept apart from I/O so each row of the table is one unit test.
- An unrecognised `status` or `status_detail` lands in row 1 and is caught by the timeout.

### 5. Timeout measured from run creation

A run still active `RUN_TIMEOUT_MINUTES` (default 60) after `created_at` becomes `timed_out`. Measuring from creation rather than session start means a run that can never launch still ends.

Session creation passes `max_acu_limit` (`DEVIN_MAX_ACU_LIMIT`, default 10) so a session the service has stopped watching cannot spend without bound.

### 6. Session request and prompt

Each session is created with `prompt`, `repos: [TARGET_REPO]`, `title: "tech-debt #<n>: <issue title>"`, `tags: ["remediation-orchestrator", "issue-<n>"]` and `max_acu_limit`. The prompt is a fixed template holding the issue title, body and URL, an instruction to open exactly one pull request against `TARGET_REPO` and never against the upstream repository, and a request to reference the issue in the pull request description.

The `devin_id` path parameter is documented as the session id prefixed with `devin-`. The client adds the prefix when the stored id lacks it.

- *Alternative: `structured_output_schema` to have Devin report its own result.* Rejected for now: `pull_requests` already answers the only question the service asks.

### 7. Thin clients, side effects after the state change

`DevinClient` (create, get) and `GitHubClient` (get issue, create comment) wrap one shared `httpx.AsyncClient` each. The worker commits a state transition first and then performs the comment and optional Slack post inside a guard that logs and swallows errors. A failed comment can therefore never roll back or block an outcome, as the specs require.

- *Alternative: PyGithub or an SDK.* Rejected: two endpoints do not justify a dependency.

### 8. Module layout

```
app/
  main.py        # create_app() factory (run with uvicorn --factory); lifespan starts and stops the worker
  config.py      # settings from environment
  db.py          # schema, connection, run repository functions
  devin.py       # DevinClient
  github.py      # GitHubClient, webhook signature check
  lifecycle.py   # status mapping (pure) and worker loop
  metrics.py     # metric aggregation
  notify.py      # Slack post
  sync.py        # reconciliation sweep for labeled issues with no run
  simulate.py    # in-process Devin and GitHub fakes for simulation mode
  routes/        # webhooks.py, runs.py, observability.py
  static/index.html
scripts/
  send_webhook.py  # signed sample webhook for local testing (standard library only)
tests/
```

### 9. Dashboard is one static file

`index.html` with inline CSS and JavaScript fetches `/api/metrics` and `/api/runs` every 10 seconds and re-renders. No template engine, no build, no CDN.

- *Why:* the page is a view over the JSON API, so the API is what gets tested and the page stays trivial.

### 10. Configuration

Environment variables, loaded with `pydantic-settings`: `DEVIN_API_KEY`, `DEVIN_ORG_ID`, `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `TARGET_REPO` (required unless `DEVIN_SIMULATE` is true); `SYNC_ON_STARTUP`, `SYNC_MAX_RUNS`, `DEVIN_SIMULATE`, `DEVIN_SIMULATE_POLLS`, `TRIGGER_LABEL`, `POLL_INTERVAL_SECONDS`, `RUN_TIMEOUT_MINUTES`, `DEVIN_MAX_ACU_LIMIT`, `DATABASE_PATH`, `SLACK_WEBHOOK_URL` (optional). The service refuses to start when a required value is missing. `.env` is git-ignored and `.env.example` carries placeholders only.

### 11. Reconciliation sweep for missed webhooks

GitHub does not retry a failed delivery, and no event ever fires for an issue that was labeled before the webhook existed. `sync.py` closes both gaps: it lists open issues carrying the trigger label (`GET /repos/{repo}/issues?labels=...&state=open`, paginated, pull requests dropped) and inserts a `queued` run with trigger `sync` for each issue that has no run in any status. It runs from `POST /api/runs/sync` and once at startup as a background task, so a slow or failing GitHub never delays or blocks startup.

- *Why "no run at all" and not "no active run":* otherwise every sweep would retry every failed issue forever. A deliberate retry stays a human action: re-label, or use the manual trigger.
- *Why a cap (`SYNC_MAX_RUNS`, default 5, oldest first):* the first sweep against a repository with a backlog of labeled issues would otherwise start a Devin session for all of them at once. The response reports how many were deferred; the next sweep takes the next batch.
- *Why not a periodic sweep:* it would make GitHub polling a second trigger path with its own latency and rate-limit behaviour. Startup plus on-demand covers the two real gaps; `SYNC_ON_STARTUP=false` turns the startup sweep off.
- The check-then-insert has no `await` between the two steps, and the partial unique index still guards against a concurrent webhook for the same issue.

### 12. Simulation mode swaps the clients, not the lifecycle

With `DEVIN_SIMULATE=true`, `create_app` injects `SimulatedDevin` and `SimulatedGitHub` in place of the real clients and disables Slack. The worker, store, routes and dashboard are untouched, so what is exercised locally is the real lifecycle code, not a parallel path. The five credentials stop being required, and the webhook secret and target repository fall back to fixed local values.

- A simulated session reports `working` for `DEVIN_SIMULATE_POLLS` polls (default 2) and then its outcome. The outcome comes from a `[sim:<scenario>]` marker in the issue title (`no_pr`, `needs_input`, `session_error`, `usage_limit`, `create_failed`, `timeout`); with no marker it opens a pull request. The scenario is encoded in the session id, so it survives a restart with no extra state.
- Simulated URLs use the reserved `simulated.invalid` host so they can never be mistaken for, or resolve to, a real session or pull request.
- `/api/metrics` carries a `simulated` flag and the dashboard shows a notice, so simulated numbers are never read as real ones.
- *Alternative: a mock HTTP server for Devin and GitHub.* Rejected: another process to run, and the HTTP layer is already covered by the client tests.
- *Alternative: a `dry_run` flag checked inside the worker.* Rejected: it puts test-only branches into the code that most needs to stay simple.

### 13. Webhook replay script

`scripts/send_webhook.py` builds an `issues` payload, signs it with the webhook secret and posts it to a local instance. It uses only the standard library so it runs without the virtualenv, takes the secret and repository from the environment or `.env`, and has a `--scenario` flag that adds the simulation marker to the title. Together with simulation mode this tests the webhook path with no ngrok, no GitHub and no new issues. For replaying a real delivery, GitHub's own "Redeliver" button is the tool.

## Risks / Trade-offs

- [Devin opens the pull request against `apache/superset` instead of the fork] → The prompt names the fork as the only allowed target and `repos` is limited to it. Verified once by hand in the end-to-end task before the demo.
- [Issue bodies are untrusted text handed to an agent] → Only users with triage rights on the fork can apply the trigger label, so labelling is the approval step. The README states this, and that the manual trigger is unauthenticated and must stay on localhost.
- [The first sweep finds a backlog of labeled issues and starts many sessions] → `SYNC_MAX_RUNS` caps each sweep at 5, and `DEVIN_MAX_ACU_LIMIT` caps each session.
- [Simulation mode left on by mistake] → It is off by default, logs a warning at startup, and the dashboard shows a notice.
- [Forks have GitHub Issues disabled by default] → Setup instructions include enabling Issues on the fork and creating the `tech-debt` label.
- [The status mapping is inferred from the API reference, not from observed sessions] → The mapping is one pure function, the raw `devin_status` and `devin_status_detail` are stored on every poll, and unknown values degrade to a timeout instead of a wrong outcome.
- [`needs_input` is recorded as a failure although a human could unblock the session in the Devin UI] → Accepted. For an unattended flow, needing a human is the outcome a leader wants counted; the comment links the session so someone can still pick it up.
- [No authentication on the API or dashboard] → Accepted per the proposal. Only the webhook route is meant to be exposed through ngrok, and it is signature-verified.
- [Single process: a crash pauses all polling] → Compose uses `restart: unless-stopped`, and the loop resumes from the database.
- [Synchronous SQLite calls on the event loop] → Queries are single-row or small aggregates on a tiny table. Revisit only if the table grows by orders of magnitude.

## Open Questions

- Whether the v3 API exposes a session terminate call worth using on timeout. Not needed for the demo; `max_acu_limit` bounds the cost either way.
- The exact values `pr_state` takes. It is stored and displayed as given and no logic depends on it.
