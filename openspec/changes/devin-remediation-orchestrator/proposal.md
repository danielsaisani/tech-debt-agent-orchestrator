# Proposal

## Why

A VP of Engineering evaluating Devin needs a working, local demo of how labeled GitHub issues become Devin sessions, pull requests, and measurable outcomes. This take-home must answer: "If I were an engineering leader, how would I know Devin is working?" — with a real end-to-end loop, not slides.

## What Changes

- Add a small Dockerized FastAPI service, named `remediation-orchestrator`, that sits between GitHub and the Devin v3 API. The name reflects its single responsibility: it owns the lifecycle of each remediation run (trigger, dispatch to Devin, track, report) while Devin does the actual code change.
- On `issues` webhooks (`opened`, `labeled`) with the `tech-debt` label, start a Devin session against a fork of `apache/superset`, comment the session URL, poll to completion, and comment the PR or failure.
- Persist every attempt as a `runs` row and expose JSON metrics plus one vanilla HTML dashboard.
- Provide a manual `POST /api/runs/trigger` fallback so the flow can be demoed without a live webhook.
- Add a reconciliation sweep (`POST /api/runs/sync`, also run at startup) that queues runs for labeled issues the webhook never reported: issues labeled before the webhook existed, or while the service was down.
- Add a simulation mode that replaces Devin and GitHub with in-process fakes, so the whole loop and dashboard can be exercised locally with no credentials, ACUs or real pull requests.
- Add a script that sends a signed sample webhook to a local instance, so the webhook path is testable without ngrok or creating issues.
- Optional last: Slack webhook when a run finishes.

## Capabilities

### New Capabilities

- `github-webhooks`: Verify GitHub issue webhooks, filter on `tech-debt`, comment session/PR/failure on the issue, support a signed-off manual trigger for demos, and sweep for labeled issues that no webhook reported.
- `remediation-runs`: Create, dedupe, persist, poll, time out, and close remediation runs, including Devin session create/get and status mapping, plus a simulation mode for local testing.
- `leader-observability`: Health check, aggregated metrics for an engineering leader, run list/detail APIs, and a simple auto-refreshing dashboard that marks simulated data as such.

### Modified Capabilities

- None. Greenfield repo; no existing specs.

## Impact

- New Python 3.12 service `remediation-orchestrator` (`app/*`), SQLite on a Docker volume, Dockerfile, Compose, `.env.example`, pytest, and README.
- Outbound: GitHub Issues API, Devin Organization API v3 (`https://api.devin.ai/v3`), optional Slack incoming webhook.
- Inbound: GitHub webhooks (typically via ngrok) and local HTTP for dashboard/metrics/trigger.
- No auth, multi-tenancy, frontend framework or Slack slash commands. No scanner that finds tech debt and files issues, and no periodic polling of GitHub: the sweep only reads issues a human already labeled, at startup and on request.
