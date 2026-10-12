# Spec Delta

## Purpose

Owns the lifecycle of a remediation run: one attempt to have Devin resolve one GitHub issue, from request through Devin session to a recorded, final outcome.

## ADDED Requirements

### Requirement: Run persistence
The service SHALL persist every remediation attempt as a run that records the issue (repository, number, title, URL), trigger source, status, failure reason, Devin session identifier and URL, pull request URL, ACUs consumed, and timestamps for creation, session start, first pull request sighting and completion. Run status MUST be one of `queued`, `running`, `succeeded`, `failed` or `timed_out`.

#### Scenario: Run recorded on request
- **WHEN** a remediation run is requested for an issue
- **THEN** a run exists with status `queued`, the issue details, the trigger source and a creation timestamp

#### Scenario: Runs survive a restart
- **WHEN** the service is stopped and started again
- **THEN** every previously recorded run is still present with the status it had before the stop

### Requirement: One active run per issue
The service MUST NOT hold more than one active run (`queued` or `running`) for the same issue at any time, including when two requests for the same issue arrive concurrently. A new run SHALL be allowed once the issue's previous run has reached a final status.

#### Scenario: Opened and labeled deliveries for the same issue
- **WHEN** two run requests for the same issue arrive at nearly the same moment
- **THEN** exactly one run is created and the second request is reported as a duplicate of it

#### Scenario: Retry after a failed run
- **WHEN** a run is requested for an issue whose only previous run is `failed` or `timed_out`
- **THEN** a new run is created

### Requirement: Devin session launch
For each queued run the service SHALL create one Devin session whose prompt contains the issue title, body and URL and instructs Devin to open a pull request against the configured target repository. On success the run MUST become `running` and record the session identifier, session URL and session start time.

#### Scenario: Session created
- **WHEN** Devin accepts the session creation request for a queued run
- **THEN** the run becomes `running` with the Devin session identifier and URL recorded

#### Scenario: Pull request targets the fork
- **WHEN** a session is created
- **THEN** its prompt names the configured target repository as the only repository to open a pull request against

### Requirement: Session launch failure
If Devin rejects the session creation request, or the request cannot be completed, the run MUST become `failed` with reason `session_create_failed` and a recorded error detail.

#### Scenario: Devin returns an error on create
- **WHEN** the session creation request returns an error response
- **THEN** the run becomes `failed` with reason `session_create_failed` and a completion timestamp

### Requirement: Outcome from session state
The service SHALL poll the Devin session of every `running` run and mark the run `succeeded` when the session has produced a pull request and is no longer working, recording the pull request URL. A run whose session stops working without a pull request MUST become `failed` with a reason of `no_pr`, `needs_input`, `usage_limit` or `session_error`.

#### Scenario: Session finishes with a pull request
- **WHEN** a poll shows the session has a pull request and is finished or waiting
- **THEN** the run becomes `succeeded` with the pull request URL, ACUs consumed and a completion timestamp

#### Scenario: Pull request appears while Devin is still working
- **WHEN** a poll shows a pull request but the session is still working
- **THEN** the run stays `running` and records the first pull request sighting time

#### Scenario: Session finishes without a pull request
- **WHEN** consecutive polls show the session has stopped with no pull request
- **THEN** the run becomes `failed` with reason `no_pr`

#### Scenario: Session is waiting for a human
- **WHEN** consecutive polls show the session waiting for user input or approval with no pull request
- **THEN** the run becomes `failed` with reason `needs_input`

#### Scenario: Session hits a usage or billing limit
- **WHEN** a poll shows the session stopped because of a usage, quota or billing limit
- **THEN** the run becomes `failed` with reason `usage_limit`

#### Scenario: Session errors
- **WHEN** a poll shows the session in an error state
- **THEN** the run becomes `failed` with reason `session_error`

### Requirement: Run timeout
A run that has not reached a final status within the configured timeout of its creation MUST become `timed_out`. The timeout defaults to 60 minutes.

#### Scenario: Session still working at the deadline
- **WHEN** a run is still `running` after the configured timeout has elapsed
- **THEN** the run becomes `timed_out` with a completion timestamp and is no longer polled

### Requirement: Resilience to transient errors
A failed poll of the Devin API MUST NOT finalise a run; the service SHALL retry on the next polling cycle until the session reports an outcome or the run times out.

#### Scenario: Devin API unavailable during a poll
- **WHEN** a poll request fails with a network error or a 5xx or 429 response
- **THEN** the run stays `running` and is polled again on the next cycle

### Requirement: Lifecycle resumes after restart
After a restart the service SHALL continue the lifecycle of every active run without manual intervention.

#### Scenario: Restart with a running run
- **WHEN** the service restarts while a run is `running`
- **THEN** polling of that run's Devin session resumes and the run still reaches a final status

#### Scenario: Restart with a queued run
- **WHEN** the service restarts while a run is `queued`
- **THEN** a Devin session is launched for that run after startup

### Requirement: Final statuses are immutable
Once a run reaches `succeeded`, `failed` or `timed_out` the service MUST NOT change its status or poll its session again.

#### Scenario: Finished run is left alone
- **WHEN** a polling cycle runs while a run is in a final status
- **THEN** no Devin request is made for that run and its recorded outcome is unchanged

### Requirement: Completion notification
When a Slack incoming webhook URL is configured, the service SHALL post one message when a run reaches a final status, naming the issue, the outcome and the pull request or failure reason. A failed notification MUST NOT change the run. When no URL is configured, no notification is attempted.

#### Scenario: Slack configured and run succeeds
- **WHEN** a run becomes `succeeded` and a Slack webhook URL is configured
- **THEN** one Slack message is posted containing the issue reference and the pull request URL

#### Scenario: Slack not configured
- **WHEN** a run reaches a final status and no Slack webhook URL is configured
- **THEN** no Slack request is made

#### Scenario: Slack request fails
- **WHEN** the Slack webhook returns an error
- **THEN** the run keeps its final status and the error is recorded in the service logs

### Requirement: Simulation mode
When simulation mode is enabled the service SHALL run the complete run lifecycle without contacting Devin, GitHub or Slack and without requiring their credentials. A simulated session SHALL produce a pull request after a configurable number of polls by default, and SHALL produce a named other outcome when the issue title contains a marker of the form `[sim:<scenario>]`. Simulation mode is off by default.

#### Scenario: Simulated run succeeds with no credentials
- **WHEN** the service starts in simulation mode with no Devin or GitHub credentials and a run is requested
- **THEN** the run reaches `succeeded` with a pull request URL and no request leaves the machine

#### Scenario: Simulated failure on demand
- **WHEN** a run is requested in simulation mode for an issue whose title contains `[sim:no_pr]`
- **THEN** the run reaches `failed` with reason `no_pr`

#### Scenario: Every outcome can be simulated
- **WHEN** runs are requested with the markers `needs_input`, `session_error`, `usage_limit`, `create_failed` and `timeout`
- **THEN** they end as `failed` with the matching reason, or for `timeout` stay `running` until the run timeout

#### Scenario: Credentials still required outside simulation
- **WHEN** the service starts with simulation mode off and a required credential missing
- **THEN** the service refuses to start and names the missing setting
