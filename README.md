# remediation-orchestrator

A small service that turns labeled GitHub issues into Devin sessions, pull requests and
numbers an engineering leader can read. The service is responsible for asynchronous development work with Devin and so should answer the question  **if I were running an
engineering org, how would I know Devin is working?**

Label an issue `tech-debt` on your fork of `apache/superset`. The service starts a Devin
session for it, comments the session link on the issue, watches the session, and comments
the pull request (or the reason there isn't one). Every attempt is recorded as a *run*, and
the dashboard shows the success rate, time to pull request and cost per pull request.

```
GitHub issue ──webhook──▶ remediation-orchestrator ──create session──▶ Devin
     ▲                       │        ▲                                  │
     └──── comments ─────────┘        └────────── poll status ───────────┘
                             │
                             ▼
                    SQLite `runs` table ──▶ /api/metrics, /api/runs, dashboard
```

The service does not fix anything itself. It owns the lifecycle of each run: trigger,
dispatch to Devin, track, report.

## Quick start

You need Docker, a Devin organization, and a fork of `apache/superset`.

1. Copy the example configuration and fill in the five required values
   (see [Setup](#setup) for where each comes from):

   ```bash
   cp .env.example .env
   ```

2. Start the service:

   ```bash
   docker compose up --build
   ```

3. Open the dashboard at <http://localhost:8000>.

4. Start a run without waiting for a webhook (replace `1` with a real issue number on your fork):

   ```bash
   curl -X POST http://localhost:8000/api/runs/trigger -H 'Content-Type: application/json' -d '{"issue_number": 1}'
   ```

The service refuses to start if a required value is missing, and says which one.

## Try it without credentials (simulation mode)

Simulation mode replaces Devin, GitHub and Slack with in-process fakes. The rest of the
service runs unchanged, so you can exercise the whole loop and the dashboard with no
credentials, no ACUs and no real pull requests. No `.env` is needed.

1. Start the service in simulation mode, polling every 2 seconds so runs finish quickly:

   ```bash
   DEVIN_SIMULATE=true POLL_INTERVAL_SECONDS=2 docker compose up --build
   ```

2. Send it a signed sample webhook (standard library only, no virtualenv needed):

   ```bash
   python3 scripts/send_webhook.py --issue 1
   ```

3. Send one that fails, to see a failure on the dashboard:

   ```bash
   python3 scripts/send_webhook.py --issue 2 --scenario no_pr
   ```

4. Open <http://localhost:8000>. A notice marks the data as simulated.

A simulated session works for `DEVIN_SIMULATE_POLLS` polls (default 2) and then opens a
pull request. A marker in the issue title, `[sim:<scenario>]`, selects another outcome;
`--scenario` adds it for you.

| Scenario | Outcome |
|---|---|
| *(none)* | `succeeded` with a pull request |
| `no_pr` | `failed` / `no_pr` |
| `needs_input` | `failed` / `needs_input` |
| `session_error` | `failed` / `session_error` |
| `usage_limit` | `failed` / `usage_limit` |
| `create_failed` | `failed` / `session_create_failed` |
| `timeout` | stays `running` until `RUN_TIMEOUT_MINUTES` passes |

Simulated links point at `simulated.invalid`, which never resolves, so they cannot be
mistaken for real sessions or pull requests. The manual trigger also works in this mode and
always succeeds. Simulated runs are stored like any others, so remove the volume
(`docker compose down -v`) before switching to real credentials.

### The webhook script against a real setup

The script is not tied to simulation mode. With real settings in `.env` it signs with your
`GITHUB_WEBHOOK_SECRET` and uses your `TARGET_REPO`, so it tests the webhook path without
ngrok. The run that follows is real: it starts a Devin session for that issue number.

```bash
python3 scripts/send_webhook.py --issue 7 --title "Remove dead code" --action opened
```

To replay a delivery GitHub actually sent, use *Redeliver* under the webhook's
*Recent Deliveries* tab.

## Setup

### Devin

- Create a **service user** in your Devin organization with permission to use sessions, and
  copy its key (it starts with `cog_`) into `DEVIN_API_KEY`.
- Put your organization id (it starts with `org-`) into `DEVIN_ORG_ID`.
- Make sure Devin has access to your Superset fork.

### GitHub

1. Fork `apache/superset`. Put the fork's `owner/name` into `TARGET_REPO`.
2. **Enable Issues on the fork.** Forks have Issues turned off by default:
   *Settings → General → Features → Issues*.
3. Create a label named `tech-debt` on the fork.
4. Create a token that can read and write Issues on the fork (a fine-grained personal access
   token with *Issues: Read and write* on that repository) and put it into `GITHUB_TOKEN`.

### Webhook (optional: the manual trigger works without it)

1. Expose the service:

   ```bash
   ngrok http 8000
   ```

2. Choose a random string and put it into `GITHUB_WEBHOOK_SECRET`. Restart the service.
3. On the fork, add a webhook under *Settings → Webhooks*:
   - Payload URL: `https://<your-ngrok-host>/webhooks/github`
   - Content type: `application/json`
   - Secret: the same string as `GITHUB_WEBHOOK_SECRET`
   - Events: *Let me select individual events* → **Issues** only

GitHub sends a `ping` on creation; the service acknowledges it and ignores it.

### Configuration reference

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DEVIN_API_KEY` | yes | | Devin service user key |
| `DEVIN_ORG_ID` | yes | | Devin organization id |
| `GITHUB_TOKEN` | yes | | Reads issues and writes comments on the fork |
| `GITHUB_WEBHOOK_SECRET` | yes | | Verifies webhook signatures |
| `TARGET_REPO` | yes | | The fork, as `owner/name` |
| `TRIGGER_LABEL` | no | `tech-debt` | Label that starts a run |
| `POLL_INTERVAL_SECONDS` | no | `30` | How often Devin sessions are polled |
| `RUN_TIMEOUT_MINUTES` | no | `60` | A run still active after this long is timed out |
| `DEVIN_MAX_ACU_LIMIT` | no | `10` | Spend cap passed to each Devin session |
| `DATABASE_PATH` | no | `data/runs.db` | SQLite file (Compose sets `/data/runs.db` on a volume) |
| `SYNC_ON_STARTUP` | no | `true` | Sweep for missed labeled issues when the service starts |
| `SYNC_MAX_RUNS` | no | `5` | Most runs one sweep may queue |
| `DEVIN_SIMULATE` | no | `false` | Simulation mode; the five required values become optional |
| `DEVIN_SIMULATE_POLLS` | no | `2` | Polls before a simulated session finishes |
| `SLACK_WEBHOOK_URL` | no | | Slack incoming webhook; one message per finished run |

## How a run works

A run is one attempt to have Devin resolve one issue.

| Status | Meaning |
|---|---|
| `queued` | Recorded; the Devin session has not been started yet |
| `running` | Devin is working |
| `succeeded` | Devin opened a pull request and stopped working |
| `failed` | Devin stopped without a pull request (see reasons below) |
| `timed_out` | Still active after `RUN_TIMEOUT_MINUTES` |

Failure reasons:

| Reason | Meaning |
|---|---|
| `no_pr` | The session finished without opening a pull request |
| `needs_input` | Devin is waiting for a human; this flow is unattended, so it counts as a failure and the issue comment links the session |
| `usage_limit` | The session hit a usage, quota or billing limit |
| `session_error` | The session ended in an error |
| `session_create_failed` | Devin rejected the session request |

What triggers a run:

- An issue is **opened** already carrying the trigger label, or
- the trigger label is **added** to an issue, or
- someone calls the manual trigger. Calling it is the approval, so the label is not required, or
- a sweep finds a labeled issue that has never had a run (see below).

Each run records which of these started it: `webhook`, `manual` or `sync`.

An issue can have only one active run. GitHub sends both `opened` and `labeled` when an
issue is created with the label; the second delivery is reported as a duplicate. Once a run
has finished, removing and re-adding the label starts a new one.

### Missed webhooks

A webhook only reports what happens while the service is listening. Two cases never
produce a delivery the service sees: an issue that already carried the label before the
webhook was set up, and an issue labeled while the service (or ngrok) was down. GitHub does
not retry failed deliveries.

The sweep covers both. It lists the open issues on the fork that carry the trigger label and
queues a run for each one that has **never had a run**. It runs once when the service starts,
and on request:

```bash
curl -X POST http://localhost:8000/api/runs/sync
```

- An issue whose run failed is not picked up again; retry it by re-adding the label or with
  the manual trigger.
- One sweep queues at most `SYNC_MAX_RUNS` runs (default 5), oldest issue first, so pointing
  the service at a fork with a backlog of labeled issues does not start a session for all of
  them. The response reports how many were `deferred`; run the sweep again for the next batch.
- Set `SYNC_ON_STARTUP=false` to sweep only on request.

Success means "a pull request exists and Devin has stopped", not "the session exited":
Devin normally opens the pull request and then waits for feedback.

## API

| Method and path | Purpose |
|---|---|
| `POST /webhooks/github` | GitHub webhook receiver (signature required) |
| `POST /api/runs/trigger` | Start a run for an issue by hand |
| `POST /api/runs/sync` | Queue runs for labeled issues that no webhook reported |
| `GET /api/runs` | Runs, newest first. `status` filters; `limit` defaults to 50, capped at 200 |
| `GET /api/runs/{id}` | One run with every recorded field |
| `GET /api/metrics` | Aggregates for the dashboard |
| `GET /health` | 200 when the run store is readable, 503 otherwise |
| `GET /` | Dashboard |

```bash
curl http://localhost:8000/health
```

```bash
curl http://localhost:8000/api/metrics
```

```bash
curl 'http://localhost:8000/api/runs?status=failed&limit=10'
```

```bash
curl http://localhost:8000/api/runs/1
```

The manual trigger returns 202 with the new run, 409 if the issue already has an active
run, and 404 if the issue does not exist on the target repository.

### Metrics

| Field | Definition |
|---|---|
| `total_runs`, `by_status` | Counts of runs, in total and per status |
| `active_runs` | Runs that are `queued` or `running` |
| `success_rate` | Succeeded runs ÷ finished runs. `null` until a run finishes |
| `median_time_to_pr_seconds` | Median, over succeeded runs, of run creation to the first poll that saw a pull request |
| `median_run_duration_seconds` | Median, over finished runs, of run creation to completion |
| `total_acus` | ACUs consumed across all runs, as reported by Devin |
| `acus_per_succeeded_run` | `total_acus` ÷ succeeded runs: what a pull request costs, failures included |
| `failure_reasons` | Unsuccessful runs grouped by reason; timeouts appear as `timed_out` |
| `simulated` | `true` when the service is in simulation mode |

Time to pull request is measured to the nearest poll, so it is accurate to within
`POLL_INTERVAL_SECONDS`.

## Demo script

1. `docker compose up --build`, and open <http://localhost:8000>: the dashboard shows the empty state.
2. On the fork, open an issue describing a small piece of tech debt and add the `tech-debt` label.
3. Within a few seconds a comment appears on the issue with the Devin session link, and the
   run appears on the dashboard as *Running*.
4. When Devin opens its pull request and stops, a second comment links the pull request and
   the run becomes *Succeeded*. Success rate, time to pull request and ACUs per pull request update.
5. Restart the container mid-run (`docker compose restart`) to show that the run is picked
   up again and still finishes.

## Development

The service is Python 3.12 with FastAPI, httpx and the standard-library `sqlite3`.

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
```

```bash
.venv/bin/pytest
```

```bash
docker compose run --rm remediation-orchestrator pytest
```

```bash
.venv/bin/uvicorn --factory app.main:create_app --reload
```

The tests need no network access or credentials: the Devin and GitHub clients are replaced
with fakes, and the clients themselves are tested against mocked HTTP.

```
app/
  main.py        app factory; starts and stops the worker
  config.py      settings from the environment
  db.py          schema and run repository functions
  devin.py       Devin API client and the session prompt
  github.py      GitHub client and webhook signature check
  lifecycle.py   Devin status mapping (pure function) and the worker loop
  metrics.py     metric aggregation
  notify.py      Slack notification
  sync.py        sweep for labeled issues that no webhook reported
  simulate.py    fake Devin and GitHub clients for simulation mode
  routes/        webhooks, runs, observability
  static/        the dashboard (one HTML file, no build step)
scripts/
  send_webhook.py  sends a signed sample webhook to a local instance
```

One background loop drives everything: each cycle it starts sessions for queued runs, polls
running ones, and times out overdue ones. Request handlers only insert a row and wake the
loop, so a webhook is acknowledged before any call to Devin. Because all state lives in the
database, a restart resumes active runs with no recovery step. The reasoning behind each
decision is in [design.md](openspec/changes/devin-remediation-orchestrator/design.md), and
the required behaviour is in the specs beside it.

## Security and limits

- **The API and dashboard have no authentication.** Keep port 8000 on localhost. Only
  `/webhooks/github` is meant to be reachable from the internet, and it rejects any request
  without a valid signature.
- **The first start against a real fork runs the sweep**, which can start up to
  `SYNC_MAX_RUNS` Devin sessions for issues that already carry the label.
- **Issue text is passed to an agent.** Only people with triage rights on the fork can add
  the trigger label, so labelling is the approval step. Treat the manual trigger the same way.
- Devin is told to open its pull request against the fork only, never against `apache/superset`.
  Check this on your first real run.
- On timeout the Devin session is left running; `DEVIN_MAX_ACU_LIMIT` bounds what it can spend.
- A single process and a single SQLite file: this is a demo, not a multi-instance service.
- Pull requests are not tracked after the run finishes, so there is no merge rate yet.
