# Stale Newsletter Cleanup Design

Date: 2026-03-25
Project: `email_hoover`
Scope: First sub-project only

## Goal

Build a Gmail-only operator console that discovers stale newsletter senders, lets the user approve sender-level cleanup rules, and then applies those rules autonomously on a schedule or manually on demand.

This first slice is intentionally narrower than a full inbox triage product. It is optimized for eliminating defined stale email, starting with newsletters and similar promotional mail.

## Problem Statement

The inbox backlog is not primarily caused by high-value mail. A large share of volume comes from recurring newsletters and promotional senders whose usefulness decays quickly. The product should reduce inbox volume by:

- identifying likely newsletter senders,
- proposing safe stale-mail policies,
- collecting explicit user approval before autonomous action,
- executing approved policies continuously,
- preserving trust through previews, logs, and clear controls.

## Out of Scope

The first sub-project does not attempt to:

- replace Gmail as a general-purpose mail client,
- support Outlook, IMAP, or multi-provider sync,
- perform fully autonomous decisions without prior rule approval,
- permanently delete mail,
- solve all forms of inbox triage,
- draft replies or manage broad conversation workflows.

## Product Shape

The product is a rule-first cleanup utility with a review console.

Core loop:

1. Scan Gmail metadata and recent message samples.
2. Discover likely newsletter or promotional senders.
3. Recommend a sender-level stale rule.
4. Let the user approve, reject, or edit the rule.
5. Persist approved rules.
6. Apply those rules on a schedule and via a manual `Run Cleanup Now` action.
7. Record every automated action in an audit trail.

The AI role is limited to discovery and recommendation. Execution should be deterministic from stored rules.

## Primary User Outcome

The user should be able to approve a set of newsletter cleanup rules once, then rely on the system to keep old low-value mail out of the inbox with minimal ongoing effort.

Success for the MVP means:

- the system can identify likely newsletter senders with enough evidence to support user approval,
- approved rules can safely archive or trash stale mail,
- scheduled runs operate without manual intervention,
- the user can inspect what happened and disable rules when needed.

## Primary Workflow

### 1. Initial discovery

The system fetches a bounded slice of Gmail data and clusters likely newsletter senders using signals such as:

- sender address and sender name,
- recurring subject patterns,
- Gmail category and label metadata,
- presence of `List-Unsubscribe` headers,
- message frequency,
- age distribution of unread or lingering mail.

### 2. Candidate review

For each candidate sender, the UI presents:

- sender identity,
- sample subjects,
- observed frequency,
- example matched messages,
- recommended stale threshold,
- recommended action,
- an estimated risk level.

User choices:

- approve as suggested,
- edit threshold or action and approve,
- ignore,
- postpone.

### 3. Rule activation

Approved rules become active policies. Each rule should be narrow by default, ideally tied to a sender or stable mailing-list signature rather than broad semantic categories.

### 4. Scheduled or manual execution

The executor loads active rules and applies them:

- on a schedule,
- on manual demand from the UI.

### 5. Audit and exception handling

Each run records:

- which rules ran,
- how many messages matched,
- which actions were applied,
- any failures,
- any rules paused for review.

## UI Design

The first UI is an operator console, not a generic inbox view.

### Left rail

The left rail provides navigation and operational controls:

- `Review Candidates`
- `Active Rules`
- `Recent Runs`
- `Exceptions`
- scheduler state
- manual `Run Cleanup Now` action

### Center pane

The center pane focuses on one sender or rule at a time and contains:

- sender identity and current status,
- evidence for classification,
- preview of stale matches,
- editable stale threshold,
- editable chosen action,
- approval or disable controls.

This is the main decision surface.

### Right rail

The right rail maintains trust and observability:

- recent run summaries,
- counts of archived or trashed items,
- failures,
- paused rules,
- exception notices.

The UI should make autonomous behavior visible without requiring a separate reporting workflow.

## Core Components

### 1. Gmail sync layer

Responsibilities:

- handle OAuth and token refresh,
- fetch Gmail metadata and limited message content,
- expose thread/message identifiers and action endpoints,
- read Gmail-native signals such as labels, categories, and unsubscribe headers.

This layer should minimize unnecessary content retrieval and prefer metadata-first classification where possible.

### 2. Candidate discovery and recommendation engine

Responsibilities:

- identify likely newsletter or promotional senders,
- cluster related messages,
- compute recommended stale thresholds,
- propose an action per sender,
- produce structured evidence for UI review.

This layer may use AI assistance, but its output must be converted into structured recommendations rather than freeform execution logic.

### 3. Rule store

Responsibilities:

- persist approved sender-level rules,
- track threshold, action, enabled state, schedule state, and creation metadata,
- support edits, disablement, and pause states.

Suggested rule shape:

- match criteria
- stale threshold
- chosen action
- enabled flag
- schedule eligibility
- audit metadata

### 4. Review console

Responsibilities:

- display candidates and evidence,
- allow rule approval and editing,
- show current rules,
- trigger manual execution,
- expose audit history and exceptions.

### 5. Scheduled executor

Responsibilities:

- load active rules,
- query Gmail for stale matches,
- apply archive or trash actions,
- record execution results,
- isolate failures per rule,
- support internal dry-run behavior for safety and testing.

## Rule Model

Each rule should include:

- a sender-level match target,
- stale threshold, such as `older than 2 days`,
- action, such as `archive` or `trash`,
- enabled or disabled status,
- scheduling eligibility,
- timestamps for creation and last execution,
- optional pause reason.

The action is part of the learned rule, not globally fixed.

## Autonomy Model

The first version should support both:

- continuous scheduled execution after approval,
- manual execution on demand.

Autonomous behavior is allowed only after explicit rule approval.

This is not a fully autonomous inbox agent. It is a policy executor operating on user-approved rules.

## Safety Constraints

- No autonomous action before explicit approval of a rule.
- No permanent deletion.
- Rules must be previewable before activation.
- Rules should be narrow and sender-oriented by default.
- If a rule begins matching unusually large volumes, it should pause for review.
- Execution must be idempotent so reruns do not repeatedly act on the same mail.
- One rule failure must not abort the full run.

Trash is acceptable because Gmail retention provides a practical recovery window. Hard delete is out of scope.

## Error Handling

### Authentication failures

If OAuth or token refresh fails:

- disable scheduled execution,
- surface a reconnect state in the UI,
- preserve rules without running them.

### Gmail API failures

If the Gmail API rate-limits or returns transient errors:

- retry with backoff per batch,
- log partial failure if execution cannot complete,
- keep successful rule executions recorded even if the overall run is partial.

### Rule-specific failures

If one rule fails due to query, matching, or action issues:

- pause or mark that rule as failed,
- continue other rules,
- record the failure in the audit trail.

## Testing Strategy

### Unit tests

Cover:

- newsletter detection logic,
- sender clustering,
- stale-threshold evaluation,
- rule matching,
- action planning,
- unusual-volume pause detection.

### Integration tests

Cover mocked Gmail interactions for:

- pagination,
- label and category retrieval,
- message or thread action application,
- retry and partial-failure handling,
- token failure transitions.

### End-to-end tests

Cover the operator flow:

1. discover a candidate sender,
2. review the proposed rule,
3. edit or approve it,
4. run cleanup manually,
5. inspect resulting audit output.

### Safety verification

Tests must prove:

- no autonomous execution for unapproved rules,
- no permanent deletion path,
- idempotent repeated runs,
- per-rule failure isolation.

## Recommended MVP Sequence

Build the first implementation in this order:

1. Gmail connection and bounded metadata sync
2. Candidate discovery with structured evidence
3. Rule persistence model
4. Review console for approval and editing
5. Manual executor
6. Scheduler
7. Audit and exceptions UI

This ordering delivers the highest-risk pieces first while keeping execution behavior observable before automation expands.

## Open Questions For Implementation Planning

- What local stack should host the Gmail integration, scheduler, and UI?
- What data store is sufficient for rules and run logs in the first version?
- What exact signals define a “likely newsletter” in the first cut before model assistance is added?
- What threshold should count as an unusually large match spike that pauses a rule?
- Should execution operate at the Gmail message level or thread level for this use case?
