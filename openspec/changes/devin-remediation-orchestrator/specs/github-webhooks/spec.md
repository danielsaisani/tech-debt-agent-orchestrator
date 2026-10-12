# Spec Delta

## Purpose

Turns GitHub issue activity on the target repository into remediation run requests, and reports each run's progress back on the originating issue so engineers can follow it without leaving GitHub.

## ADDED Requirements

### Requirement: Webhook signature verification
The service SHALL accept GitHub webhooks at `POST /webhooks/github` and MUST reject any delivery whose `X-Hub-Signature-256` header does not match the HMAC-SHA256 of the raw request body under the configured webhook secret.

#### Scenario: Valid signature
- **WHEN** a delivery arrives with a signature that matches the raw body and the configured secret
- **THEN** the service processes the delivery and responds with a 2xx status

#### Scenario: Invalid signature
- **WHEN** a delivery arrives with a signature that does not match
- **THEN** the service responds 401 and creates no run

#### Scenario: Missing signature
- **WHEN** a delivery arrives without an `X-Hub-Signature-256` header
- **THEN** the service responds 401 and creates no run

### Requirement: Trigger filtering
The service SHALL request a remediation run only for `issues` events on the configured target repository where either the action is `opened` and the issue carries the trigger label, or the action is `labeled` and the label just added is the trigger label. The trigger label defaults to `tech-debt`. Every other verified delivery MUST be acknowledged with 200 and a reason, without creating a run.

#### Scenario: Issue opened with the trigger label
- **WHEN** an `issues` event with action `opened` arrives for an issue labeled `tech-debt`
- **THEN** the service requests a remediation run for that issue

#### Scenario: Trigger label added to an existing issue
- **WHEN** an `issues` event with action `labeled` arrives and the added label is `tech-debt`
- **THEN** the service requests a remediation run for that issue

#### Scenario: A different label is added to a tech-debt issue
- **WHEN** an `issues` event with action `labeled` arrives for an issue that already has `tech-debt` and the added label is `bug`
- **THEN** the service responds 200 with an ignored reason and creates no run

#### Scenario: Issue opened without the trigger label
- **WHEN** an `issues` event with action `opened` arrives for an issue with no `tech-debt` label
- **THEN** the service responds 200 with an ignored reason and creates no run

#### Scenario: Unrelated event or action
- **WHEN** a verified delivery arrives for another event type (such as `ping` or `push`) or another `issues` action (such as `closed`)
- **THEN** the service responds 200 with an ignored reason and creates no run

#### Scenario: Event from another repository
- **WHEN** a verified `issues` event arrives for a repository other than the configured target repository
- **THEN** the service responds 200 with an ignored reason and creates no run

### Requirement: Prompt acknowledgement
The service SHALL acknowledge a webhook delivery without waiting for any Devin or GitHub API call to complete, so that deliveries are never failed by GitHub's delivery timeout.

#### Scenario: Run accepted
- **WHEN** a delivery passes verification and filtering and a run is created
- **THEN** the service responds 202 with the run's identifier before the Devin session has been started

#### Scenario: Duplicate delivery
- **WHEN** a delivery passes verification and filtering but the issue already has an active run
- **THEN** the service responds 200 naming the existing run and creates no new run

### Requirement: Issue progress comments
The service SHALL comment on the originating issue when the Devin session starts (with the session URL), when the run succeeds (with the pull request URL), and when the run fails or times out (with the reason and the session URL when one exists).

#### Scenario: Session started
- **WHEN** a Devin session is started for a run
- **THEN** a comment containing the Devin session URL appears on the issue

#### Scenario: Run succeeded
- **WHEN** a run finishes with a pull request
- **THEN** a comment containing the pull request URL appears on the issue

#### Scenario: Run failed or timed out
- **WHEN** a run finishes as failed or timed out
- **THEN** a comment stating the failure reason appears on the issue

### Requirement: Comment failures do not affect runs
A failure to post an issue comment MUST NOT change the status or outcome of the run it describes.

#### Scenario: GitHub rejects a comment
- **WHEN** GitHub returns an error while the service posts a progress comment
- **THEN** the run keeps its status and continues its lifecycle, and the error is recorded in the service logs

### Requirement: Comment failures are recorded
Each run SHALL record the error from its most recent attempt to post an issue comment, and MUST clear that record when a later comment for the same run is posted successfully.

#### Scenario: Token cannot write comments
- **WHEN** GitHub rejects a progress comment for a run
- **THEN** the run's `comment_error` holds the error returned by GitHub

#### Scenario: Problem fixed during a run
- **WHEN** a run's first comment failed and a later comment for the same run is posted successfully
- **THEN** the run's `comment_error` is empty

#### Scenario: Comments working
- **WHEN** every comment for a run is posted successfully
- **THEN** the run's `comment_error` is empty

### Requirement: Manual trigger
The service SHALL expose `POST /api/runs/trigger`, accepting an issue number on the target repository, which requests a remediation run through the same lifecycle as a webhook without requiring a webhook delivery or the trigger label. Runs MUST record whether they were triggered by webhook or manually.

#### Scenario: Manual trigger for an existing issue
- **WHEN** a client posts an issue number that exists on the target repository and has no active run
- **THEN** the service responds 202 with the new run, recorded with a manual trigger source

#### Scenario: Manual trigger while a run is active
- **WHEN** a client posts an issue number that already has an active run
- **THEN** the service responds 409 naming the existing run and creates no new run

#### Scenario: Manual trigger for an unknown issue
- **WHEN** a client posts an issue number that does not exist on the target repository
- **THEN** the service responds 404 and creates no run

#### Scenario: Invalid request body
- **WHEN** a client posts a body without a valid positive issue number
- **THEN** the service responds 422 and creates no run

### Requirement: Reconciliation of missed issues
The service SHALL expose `POST /api/runs/sync`, which requests a run for each open issue on the target repository that carries the trigger label and has never had a run, oldest first, recorded with a `sync` trigger source. The service SHALL perform the same sweep once at startup unless that is disabled. One sweep MUST NOT request more runs than the configured limit, which defaults to 5.

#### Scenario: Issue labeled before the webhook existed
- **WHEN** a sweep runs and an open issue carries the trigger label but has no run
- **THEN** a run is created for it with a `sync` trigger source and the response lists it as queued

#### Scenario: Issue that already has a run
- **WHEN** a sweep runs and a labeled issue already has a run in any status, including `failed`
- **THEN** no run is created for it and the response counts it as already tracked

#### Scenario: Delivery missed while the service was down
- **WHEN** the service starts and an issue was labeled while it was stopped
- **THEN** a run is created for that issue without any request being made to the service

#### Scenario: More labeled issues than the limit
- **WHEN** a sweep finds 8 labeled issues without a run and the limit is 5
- **THEN** runs are created for the 5 oldest and the response reports 3 as deferred

#### Scenario: Pull requests and closed issues
- **WHEN** a sweep runs and the trigger label is on a pull request or a closed issue
- **THEN** no run is created for it

#### Scenario: GitHub unavailable on request
- **WHEN** a client posts to `POST /api/runs/sync` and GitHub cannot be read
- **THEN** the service responds 502 and creates no run

#### Scenario: GitHub unavailable at startup
- **WHEN** the startup sweep cannot read GitHub
- **THEN** the service still starts and serves requests, and the error is recorded in the service logs
