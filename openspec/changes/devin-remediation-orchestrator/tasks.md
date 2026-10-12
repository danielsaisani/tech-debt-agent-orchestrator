# Tasks

## 1. Project scaffold

- [x] 1.1 Run `git init`, add a `.gitignore` covering `.env`, `__pycache__`, `.venv`, `.pytest_cache` and `*.db`, and verify `git status` does not list `.env`
- [x] 1.2 Add `pyproject.toml` with FastAPI, uvicorn, httpx, pydantic-settings and dev dependencies pytest, pytest-asyncio and respx, and verify `pip install -e ".[dev]"` succeeds on Python 3.12
- [x] 1.3 Create the `app/` and `tests/` layout from design decision 8 with a minimal FastAPI app, and verify `uvicorn --factory app.main:create_app` starts
- [x] 1.4 Implement `app/config.py` settings per design decision 10, and verify a test shows startup fails when a required variable is missing and defaults apply when optional ones are absent
- [x] 1.5 Add `.env.example` with placeholder values for every variable, and verify it contains no real secret and every name matches `config.py`
- [x] 1.6 Add `Dockerfile` and `docker-compose.yml` with a named volume for the SQLite file and `restart: unless-stopped`, and verify `docker compose up --build` serves the app on localhost

## 2. Run store

- [x] 2.1 Implement `app/db.py` schema creation with the `runs` table, WAL mode and the partial unique index, and verify a test creates the schema in a temporary database
- [x] 2.2 Implement repository functions (create, get, list with status filter and limit, list active, update), and verify tests cover each function
- [x] 2.3 Verify by test that a second active run for the same issue is rejected as a duplicate and that a new run is allowed after the first is `failed`
- [x] 2.4 Verify by test that runs written by one connection are read back after the database is closed and reopened

## 3. Devin client and status mapping

- [x] 3.1 Implement `DevinClient.create_session` and `get_session` against the v3 endpoints with bearer auth and `devin-` prefix handling, and verify with respx tests asserting the URL, headers and request body
- [x] 3.2 Implement the prompt template per design decision 6, and verify a test asserts it contains the issue title, body, URL and the target repository instruction
- [x] 3.3 Implement the pure status mapping function per design decision 4, and verify one test per table row plus an unknown-status case and the two-consecutive-polls rule
- [x] 3.4 Verify by test that 429, 5xx and network errors from `get_session` surface as a transient error type distinct from a session outcome

## 4. GitHub client and triggers

- [x] 4.1 Implement webhook signature verification over the raw body with a constant-time compare, and verify tests for valid, invalid and missing signatures
- [x] 4.2 Implement `GitHubClient.get_issue` and `create_comment`, and verify with respx tests asserting the URL, auth header and body
- [x] 4.3 Implement `POST /webhooks/github` with trigger filtering, and verify tests cover every scenario in the `github-webhooks` trigger filtering requirement
- [x] 4.4 Verify by test that an accepted delivery returns 202 with the run id while the Devin client has not been called, and that a duplicate delivery returns 200 naming the existing run
- [x] 4.5 Implement `POST /api/runs/trigger`, and verify tests for the 202, 409, 404 and 422 scenarios and that the run records a manual trigger source
- [ ] 4.6 Document in `README.md` how to enable Issues on the fork, create the `tech-debt` label, create the GitHub token and configure the webhook through ngrok, and verify the steps against a real fork

## 5. Run lifecycle worker

- [x] 5.1 Implement the launch step (queued run to Devin session to `running`, with the session-started comment), and verify tests for success and for `session_create_failed`
- [x] 5.2 Implement the poll step applying the status mapping, stamping `pr_opened_at` on first sighting and storing raw Devin status, and verify tests for succeeded, each failure reason and still-running
- [x] 5.3 Implement the timeout step measured from run creation, and verify a test with an overdue run ends `timed_out` and is not polled again
- [x] 5.4 Post the success, failure and timeout comments after the state change, and verify by test that a GitHub error leaves the run status unchanged
- [x] 5.5 Implement the worker loop with interval and wake event, started and stopped by the app lifespan, and verify a test that a queued run is launched without waiting a full interval
- [x] 5.6 Verify by test that a transient poll error leaves the run `running`, and that finished runs trigger no Devin request
- [x] 5.7 Verify by test that a fresh worker over a database holding one `queued` and one `running` run launches the first and polls the second

## 6. Leader observability

- [x] 6.1 Implement `GET /health` with a run store read, and verify tests for 200 and for 503 when the store is unreadable
- [x] 6.2 Implement metric aggregation and `GET /api/metrics`, and verify tests reproduce each numeric scenario in the `leader-observability` spec, including the empty store
- [x] 6.3 Implement `GET /api/runs` and `GET /api/runs/{id}`, and verify tests for ordering, status filter, limit cap, 422 on unknown status and 404 on unknown id
- [x] 6.4 Build `app/static/index.html` with headline tiles, runs table with issue, session and pull request links, 10 second refresh and an empty state, served at `GET /`, and verify in a browser with seeded runs and with an empty store
- [x] 6.5 Document the API endpoints and each metric's definition in `README.md`, and verify every documented `curl` command runs as written

## 7. Slack notification (optional, do last)

- [x] 7.1 Implement the Slack post on final status when `SLACK_WEBHOOK_URL` is set, and verify tests for configured, not configured and Slack error leaving the run unchanged
- [ ] 7.2 Document the Slack variable in `README.md` and `.env.example`, and verify a real message arrives in a test channel

## 8. End-to-end verification

- [x] 8.1 Run the full test suite in the container, and verify `docker compose run --rm remediation-orchestrator pytest` passes
- [ ] 8.2 Trigger a real run with `POST /api/runs/trigger` against the fork, and verify the session comment, the pull request comment, the dashboard row and that the pull request targets the fork and not `apache/superset`
- [ ] 8.3 Label a real issue `tech-debt` with the webhook delivered through ngrok, and verify exactly one run is created for the `opened` and `labeled` pair
- [ ] 8.4 Restart the container while a run is `running`, and verify the run still reaches a final status
- [ ] 8.5 Finish `README.md` with a quick start, architecture summary and demo script, and verify a clean clone reaches a working dashboard by following it

## 9. Reconciliation sweep

- [x] 9.1 Implement `GitHubClient.list_labeled_issues` with pagination and pull requests dropped, and verify with respx tests asserting the query (`labels`, `state=open`) and a two-page result
- [x] 9.2 Implement the sweep in `app/sync.py` with the `SYNC_MAX_RUNS` cap, and verify tests for a never-run issue queued with trigger `sync`, an issue with a failed run skipped, oldest-first ordering and the deferred count
- [x] 9.3 Implement `POST /api/runs/sync`, and verify tests for the queued response and for 502 with no runs when GitHub fails
- [x] 9.4 Run the sweep as a background task at startup unless `SYNC_ON_STARTUP` is false, and verify by test that a labeled issue is queued by app startup alone and that a GitHub failure does not stop startup
- [x] 9.5 Document the sweep, its cap and its settings in `README.md` and `.env.example`, and verify the documented `curl` command runs as written

## 10. Simulation mode

- [x] 10.1 Make the five credentials optional when `DEVIN_SIMULATE` is true and keep them required otherwise, and verify tests for both, including the error naming the missing settings
- [x] 10.2 Implement `SimulatedDevin` and `SimulatedGitHub` in `app/simulate.py`, and verify tests for the default pull request outcome and each `[sim:<scenario>]` marker
- [x] 10.3 Wire simulation mode into `create_app` with Slack disabled and a startup warning, and verify by test that a webhook-triggered run reaches `succeeded` with no credentials and no HTTP request made
- [x] 10.4 Report `simulated` in `GET /api/metrics` and show a notice on the dashboard, and verify by test and in a browser
- [x] 10.5 Let Compose start in simulation mode without a `.env`, document the mode and scenario markers in `README.md` and `.env.example`, and verify the documented simulation quick start in a container

## 11. Webhook replay script

- [x] 11.1 Implement `scripts/send_webhook.py` using only the standard library, and verify by test that its payload and signature are accepted by the webhook route and that `--scenario` adds the marker
- [x] 11.2 Document the script in `README.md`, and verify the documented commands create runs against a container in simulation mode

## 12. Visible comment failures

- [x] 12.1 Add the `comment_error` column, added to existing databases at startup, and verify by test that a database created without it keeps its runs and gains the column
- [x] 12.2 Record the result of each comment attempt on the run, and verify tests for a failure recorded, cleared by a later success, and absent when comments work
- [x] 12.3 Report `comment_failures` in `GET /api/metrics` and show a notice and per-run marker on the dashboard, and verify by test and in a browser
- [x] 12.4 Document the GitHub token permissions and how comment failures surface in `README.md` and `.env.example`, and verify the documented commands run as written

## Workflow follow-up

- Archive the change once the end-to-end checks pass, so the three specs become the main specs.
