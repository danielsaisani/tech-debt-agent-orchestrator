# Spec Delta

## Purpose

Gives an engineering leader a direct answer to "is Devin working?" by exposing the outcomes, speed and cost of remediation runs as JSON and as a single dashboard page.

## ADDED Requirements

### Requirement: Health check
The service SHALL expose `GET /health`, returning 200 with a status body when the service can read its run store, and 503 when it cannot.

#### Scenario: Service healthy
- **WHEN** a client requests `GET /health` and the run store is readable
- **THEN** the service responds 200 with a body reporting an ok status

#### Scenario: Run store unavailable
- **WHEN** a client requests `GET /health` and the run store cannot be read
- **THEN** the service responds 503

### Requirement: Outcome metrics
The service SHALL expose `GET /api/metrics`, returning counts of runs in total and by status, the number of active runs, and a success rate calculated as succeeded runs divided by runs in a final status. The success rate MUST be null when no run has reached a final status.

#### Scenario: Mixed outcomes
- **WHEN** the store holds 3 succeeded, 1 failed, 1 timed-out and 1 running run
- **THEN** the metrics report 6 total runs, 1 active run and a success rate of 0.6

#### Scenario: No runs yet
- **WHEN** the store holds no runs
- **THEN** every count is 0 and the success rate is null

### Requirement: Speed metrics
`GET /api/metrics` SHALL report the median time to pull request (run creation to first pull request sighting) across succeeded runs, and the median run duration (creation to completion) across runs in a final status, both in seconds and null when there is no qualifying run.

#### Scenario: Median time to pull request
- **WHEN** three succeeded runs took 300, 600 and 1500 seconds from creation to first pull request sighting
- **THEN** the metrics report a median time to pull request of 600 seconds

#### Scenario: No succeeded runs
- **WHEN** no run has succeeded
- **THEN** the median time to pull request is null

### Requirement: Cost metrics
`GET /api/metrics` SHALL report the total ACUs consumed across all runs and the ACUs consumed per succeeded run (total ACUs divided by succeeded runs), with the per-run figure null when no run has succeeded.

#### Scenario: Cost per pull request
- **WHEN** runs have consumed 12 ACUs in total and 3 runs have succeeded
- **THEN** the metrics report 12 total ACUs and 4 ACUs per succeeded run

### Requirement: Failure breakdown
`GET /api/metrics` SHALL report the number of unsuccessful runs grouped by reason, counting timed-out runs under `timed_out`.

#### Scenario: Failures by reason
- **WHEN** the store holds 2 runs failed with `no_pr`, 1 failed with `needs_input` and 1 timed out
- **THEN** the breakdown reports `no_pr` 2, `needs_input` 1 and `timed_out` 1

### Requirement: Run list
The service SHALL expose `GET /api/runs`, returning runs newest first, with an optional `status` filter and a `limit` that defaults to 50 and is capped at 200.

#### Scenario: Default listing
- **WHEN** a client requests `GET /api/runs`
- **THEN** the service returns up to 50 runs ordered by creation time, newest first

#### Scenario: Filter by status
- **WHEN** a client requests `GET /api/runs?status=failed`
- **THEN** only runs with status `failed` are returned

#### Scenario: Unknown status filter
- **WHEN** a client requests `GET /api/runs` with a status that is not a valid run status
- **THEN** the service responds 422

### Requirement: Run detail
The service SHALL expose `GET /api/runs/{id}`, returning every recorded field of one run, including the issue, Devin session and pull request URLs.

#### Scenario: Existing run
- **WHEN** a client requests a run by an identifier that exists
- **THEN** the service returns that run with all recorded fields

#### Scenario: Unknown run
- **WHEN** a client requests a run by an identifier that does not exist
- **THEN** the service responds 404

### Requirement: Dashboard
The service SHALL serve a single HTML page at `GET /` that shows the headline metrics and a table of recent runs with links to each run's issue, Devin session and pull request, and refreshes its data automatically without a page reload. The page MUST work without any build step or external script.

#### Scenario: Dashboard shows current state
- **WHEN** a leader opens the dashboard
- **THEN** the page shows success rate, active runs, median time to pull request, ACUs per succeeded run and the recent runs with their statuses and links

#### Scenario: Dashboard updates on its own
- **WHEN** a run changes status while the dashboard is open
- **THEN** the page reflects the new status within one refresh interval without user action

#### Scenario: No runs yet
- **WHEN** a leader opens the dashboard before any run exists
- **THEN** the page shows an empty state explaining how to trigger the first run

### Requirement: Simulated data is marked
`GET /api/metrics` SHALL report whether the service is in simulation mode, and the dashboard MUST show a visible notice while it is, so simulated outcomes are never mistaken for real ones.

#### Scenario: Dashboard in simulation mode
- **WHEN** a leader opens the dashboard while the service is in simulation mode
- **THEN** the page shows a notice that the runs and metrics are simulated

#### Scenario: Dashboard in normal mode
- **WHEN** a leader opens the dashboard while simulation mode is off
- **THEN** no simulation notice is shown and the metrics report `simulated` as false
