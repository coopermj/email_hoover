# Stale Newsletter Cleanup Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Gmail-only operator console that discovers stale newsletter senders, lets the user approve sender-level cleanup rules, and then executes those rules manually or on a schedule with an audit log.

**Architecture:** Use a single FastAPI application with server-rendered Jinja templates and HTMX-style partial updates so the first version stays small and debuggable. Keep Gmail access, discovery logic, rule persistence, execution, and scheduling in separate modules; AI-style discovery remains advisory, while all destructive actions flow through persisted user-approved rules.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX, SQLModel/SQLite, httpx, google-auth/google-auth-oauthlib, APScheduler, pytest, pytest-asyncio, respx

---

## File Structure

### Application files

- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/main.py`
- Create: `app/config.py`
- Create: `app/db.py`
- Create: `app/models/__init__.py`
- Create: `app/models/candidate.py`
- Create: `app/models/rule.py`
- Create: `app/models/run_log.py`
- Create: `app/gmail/__init__.py`
- Create: `app/gmail/auth.py`
- Create: `app/gmail/client.py`
- Create: `app/discovery/__init__.py`
- Create: `app/discovery/newsletters.py`
- Create: `app/services/__init__.py`
- Create: `app/services/discovery.py`
- Create: `app/services/rules.py`
- Create: `app/services/executor.py`
- Create: `app/services/scheduler.py`
- Create: `app/web/__init__.py`
- Create: `app/web/routes.py`
- Create: `app/templates/base.html`
- Create: `app/templates/dashboard.html`
- Create: `app/templates/partials/candidate_list.html`
- Create: `app/templates/partials/exceptions.html`
- Create: `app/templates/partials/rule_editor.html`
- Create: `app/templates/partials/run_log.html`

### Test files

- Create: `tests/conftest.py`
- Create: `tests/test_app_health.py`
- Create: `tests/test_rule_repository.py`
- Create: `tests/test_gmail_client.py`
- Create: `tests/test_newsletter_discovery.py`
- Create: `tests/test_executor.py`
- Create: `tests/test_dashboard_routes.py`

### Responsibility map

- `app/config.py`: environment-driven settings for Gmail OAuth, database path, scheduler cadence, and safety thresholds
- `app/db.py`: SQLModel engine, session factory, and startup schema creation for MVP
- `app/models/*.py`: persistence models for discovery candidates, approved rules, paused exceptions, and run/audit records
- `app/gmail/auth.py`: OAuth token loading, refresh, and reconnect-state helpers
- `app/gmail/client.py`: minimal Gmail REST wrapper for listing messages, fetching headers, and applying archive/trash actions
- `app/discovery/newsletters.py`: newsletter heuristics and sender clustering
- `app/services/discovery.py`: orchestrates Gmail fetch + heuristic classification into persisted candidate records with review evidence
- `app/services/rules.py`: candidate approval, reject/postpone actions, rule editing, enable/disable, and rule preview queries
- `app/services/executor.py`: manual/scheduled cleanup execution with idempotence, retry/backoff, partial-failure logging, and per-rule failure isolation
- `app/services/scheduler.py`: APScheduler bootstrap, job registration, and paused scheduler state on auth failure
- `app/web/routes.py`: dashboard endpoints, partial updates, candidate disposition actions, rule edits, exception views, and manual-run trigger
- `app/templates/*`: operator console shell and partials

## Task 1: Bootstrap the FastAPI app and local test harness

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/main.py`
- Create: `app/config.py`
- Test: `tests/conftest.py`
- Test: `tests/test_app_health.py`

- [x] **Step 1: Write the failing health test**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_healthcheck_returns_ok():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [x] **Step 2: Run the test to verify the app does not exist yet**

Run: `pytest tests/test_app_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [x] **Step 3: Add the minimal project scaffold and dependencies**

```toml
[project]
name = "email-hoover"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi",
  "uvicorn[standard]",
  "jinja2",
  "sqlmodel",
  "httpx",
  "google-auth",
  "google-auth-oauthlib",
  "apscheduler",
  "python-multipart",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "respx", "beautifulsoup4"]
```

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [x] **Step 4: Run the health test to verify the scaffold**

Run: `pytest tests/test_app_health.py -v`
Expected: PASS

- [x] **Step 5: Commit the bootstrap**

```bash
git add pyproject.toml app/__init__.py app/main.py app/config.py tests/conftest.py tests/test_app_health.py
git commit -m "feat: bootstrap FastAPI app"
```

## Task 2: Add persistence for candidates, rules, and run logs

**Files:**
- Create: `app/db.py`
- Create: `app/models/__init__.py`
- Create: `app/models/candidate.py`
- Create: `app/models/rule.py`
- Create: `app/models/run_log.py`
- Create: `app/services/__init__.py`
- Create: `app/services/rules.py`
- Test: `tests/test_rule_repository.py`
- Modify: `app/main.py`

- [x] **Step 1: Write the failing persistence tests**

```python
from sqlmodel import Session

from app.models.candidate import Candidate
from app.models.rule import CleanupRule
from app.services.rules import approve_candidate


def test_approving_candidate_creates_rule(session: Session):
    candidate = Candidate(
        sender_address="newsletter@example.com",
        sender_name="Example Daily",
        recommended_stale_days=2,
        recommended_action="trash",
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)

    rule = approve_candidate(session, candidate.id, stale_days=3, action="archive")

    assert rule.sender_address == "newsletter@example.com"
    assert rule.stale_days == 3
    assert rule.action == "archive"
```

- [x] **Step 2: Run the repository test to verify the models/services are missing**

Run: `pytest tests/test_rule_repository.py -v`
Expected: FAIL with `ModuleNotFoundError` for models or services

- [x] **Step 3: Implement the database and SQLModel entities**

```python
class Candidate(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    sender_address: str = Field(index=True)
    sender_name: str
    sample_subjects_json: str = "[]"
    example_message_ids_json: str = "[]"
    observed_frequency: str = "unknown"
    recommended_stale_days: int
    recommended_action: str
    risk_level: str = "low"
    status: str = "pending"
```

```python
class CleanupRule(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    sender_address: str = Field(index=True, unique=True)
    sender_name: str
    stale_days: int
    action: str
    enabled: bool = True
    schedule_enabled: bool = True
    pause_reason: str | None = None
```

- [x] **Step 4: Wire startup schema creation and repository helper functions**

Run: `pytest tests/test_rule_repository.py -v`
Expected: PASS

- [x] **Step 5: Commit the persistence layer**

```bash
git add app/db.py app/models/__init__.py app/models/candidate.py app/models/rule.py app/models/run_log.py app/services/__init__.py app/services/rules.py app/main.py tests/test_rule_repository.py
git commit -m "feat: add persistence models for cleanup rules"
```

## Task 3: Build Gmail auth state and the minimal Gmail client

**Files:**
- Create: `app/gmail/__init__.py`
- Create: `app/gmail/auth.py`
- Create: `app/gmail/client.py`
- Test: `tests/test_gmail_client.py`
- Modify: `app/config.py`

- [x] **Step 1: Write failing tests for auth-state detection and Gmail list calls**

```python
from app.gmail.auth import AuthState
from app.gmail.client import GmailClient


def test_auth_state_reports_reconnect_when_token_missing(settings):
    state = AuthState.from_disk(settings)
    assert state.connected is False
    assert state.reason == "missing_token"
```

```python
async def test_list_candidate_messages_uses_expected_query(respx_mock, settings):
    respx_mock.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").respond(
        json={"messages": [{"id": "m1"}]}
    )
    client = GmailClient(settings, token_getter=lambda: "token")
    ids = await client.list_message_ids("category:promotions older_than:2d")
    assert ids == ["m1"]
```

- [x] **Step 2: Run the Gmail client tests to capture the missing modules**

Run: `pytest tests/test_gmail_client.py -v`
Expected: FAIL with import errors

- [x] **Step 3: Implement the smallest testable Gmail boundary**

```python
@dataclass
class AuthState:
    connected: bool
    reason: str | None = None

    @classmethod
    def from_disk(cls, settings: Settings) -> "AuthState":
        if not settings.gmail_token_path.exists():
            return cls(connected=False, reason="missing_token")
        return cls(connected=True)
```

```python
class GmailClient:
    async def list_message_ids(self, query: str) -> list[str]:
        response = await self._client.get(
            "/gmail/v1/users/me/messages",
            params={"q": query, "maxResults": 100},
            headers={"Authorization": f"Bearer {self._token_getter()}"},
        )
        response.raise_for_status()
        return [item["id"] for item in response.json().get("messages", [])]
```

- [x] **Step 4: Extend the client for metadata fetch and archive/trash actions**

Run: `pytest tests/test_gmail_client.py -v`
Expected: PASS

- [x] **Step 5: Commit the Gmail integration boundary**

```bash
git add app/config.py app/gmail/__init__.py app/gmail/auth.py app/gmail/client.py tests/test_gmail_client.py
git commit -m "feat: add Gmail auth state and client"
```

## Task 4: Implement newsletter discovery and candidate persistence

**Files:**
- Create: `app/discovery/__init__.py`
- Create: `app/discovery/newsletters.py`
- Create: `app/services/discovery.py`
- Test: `tests/test_newsletter_discovery.py`
- Modify: `app/models/candidate.py`
- Modify: `app/gmail/client.py`

- [x] **Step 1: Write failing tests for newsletter heuristics**

```python
from app.discovery.newsletters import classify_sender


def test_sender_with_list_unsubscribe_is_classified_as_newsletter():
    sender = classify_sender(
        sender_address="daily@example.com",
        sender_name="Daily Example",
        headers={"List-Unsubscribe": "<mailto:leave@example.com>"},
        subjects=["Top stories today", "Your Wednesday briefing"],
        category="promotions",
    )
    assert sender.is_newsletter is True
    assert sender.recommended_stale_days == 2
    assert sender.recommended_action == "trash"
    assert sender.observed_frequency == "daily"
    assert sender.example_message_ids == ["m1", "m2", "m3"]
```

- [x] **Step 2: Run the discovery tests to verify classification is not implemented**

Run: `pytest tests/test_newsletter_discovery.py -v`
Expected: FAIL with `ImportError` or missing function errors

- [x] **Step 3: Implement deterministic newsletter scoring and recommendation logic**

```python
def classify_sender(... ) -> CandidateRecommendation:
    signals = 0
    if headers.get("List-Unsubscribe"):
        signals += 2
    if category == "promotions":
        signals += 1
    if len(subjects) >= 2 and len({subject.split()[0] for subject in subjects}) <= len(subjects):
        signals += 1
    is_newsletter = signals >= 2
    action = "trash" if category == "promotions" else "archive"
    stale_days = 2 if is_newsletter else 7
    observed_frequency = "daily" if message_count_last_7_days >= 5 else "weekly"
    return CandidateRecommendation(
        ...,
        observed_frequency=observed_frequency,
        example_message_ids=message_ids[:3],
    )
```

- [x] **Step 4: Add a discovery service that fetches Gmail metadata, computes review evidence, and upserts pending candidates**

```python
candidate = Candidate(
    sender_address=recommendation.sender_address,
    sender_name=recommendation.sender_name,
    sample_subjects_json=json.dumps(recommendation.sample_subjects),
    example_message_ids_json=json.dumps(recommendation.example_message_ids),
    observed_frequency=recommendation.observed_frequency,
    recommended_stale_days=recommendation.recommended_stale_days,
    recommended_action=recommendation.recommended_action,
    risk_level=recommendation.risk_level,
)
```

Run: `pytest tests/test_newsletter_discovery.py -v`
Expected: PASS

- [x] **Step 5: Commit the discovery pipeline**

```bash
git add app/discovery/__init__.py app/discovery/newsletters.py app/services/discovery.py app/models/candidate.py app/gmail/client.py tests/test_newsletter_discovery.py
git commit -m "feat: add newsletter discovery pipeline"
```

## Task 5: Implement rule approval, editing, and preview logic

**Files:**
- Modify: `app/services/rules.py`
- Test: `tests/test_rule_repository.py`
- Modify: `app/models/candidate.py`
- Modify: `app/models/rule.py`
- Modify: `app/gmail/client.py`

- [x] **Step 1: Extend repository tests to cover approval, disablement, and stale preview**

```python
from app.services.rules import (
    approve_candidate,
    disable_rule,
    mark_candidate_rejected,
    mark_candidate_postponed,
    preview_rule_matches,
    update_rule,
)


async def test_preview_rule_matches_returns_stale_messages(session, gmail_client_stub):
    rule = approve_candidate(session, candidate_id=1, stale_days=2, action="trash")
    matches = await preview_rule_matches(session, gmail_client_stub, rule.id)
    assert matches[0].message_id == "m-older"
    assert matches[0].planned_action == "trash"


def test_candidate_can_be_rejected_or_postponed(session):
    rejected = mark_candidate_rejected(session, candidate_id=1)
    postponed = mark_candidate_postponed(session, candidate_id=2)
    assert rejected.status == "rejected"
    assert postponed.status == "postponed"


def test_rule_can_be_updated_and_disabled(session):
    rule = approve_candidate(session, candidate_id=3, stale_days=2, action="trash")
    edited = update_rule(session, rule.id, stale_days=5, action="archive")
    disabled = disable_rule(session, rule.id)
    assert edited.stale_days == 5
    assert edited.action == "archive"
    assert disabled.enabled is False
```

- [x] **Step 2: Run the repository suite and verify disposition/edit behavior is missing**

Run: `pytest tests/test_rule_repository.py -v`
Expected: FAIL on missing reject, postpone, edit, disable, or preview functions

- [x] **Step 3: Implement candidate disposition, rule edit/disable, and preview services with narrow sender matching**

```python
def approve_candidate(session: Session, candidate_id: int, stale_days: int, action: str) -> CleanupRule:
    candidate = session.get(Candidate, candidate_id)
    rule = CleanupRule(
        sender_address=candidate.sender_address,
        sender_name=candidate.sender_name,
        stale_days=stale_days,
        action=action,
    )
    candidate.status = "approved"
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule
```

```python
async def preview_rule_matches(...):
    query = f'from:{rule.sender_address} older_than:{rule.stale_days}d'
    return await gmail_client.preview_matches(query, action=rule.action)
```

```python
def mark_candidate_rejected(session: Session, candidate_id: int) -> Candidate:
    candidate = session.get(Candidate, candidate_id)
    candidate.status = "rejected"
    session.add(candidate)
    session.commit()
    return candidate
```

```python
def update_rule(session: Session, rule_id: int, stale_days: int, action: str) -> CleanupRule:
    rule = session.get(CleanupRule, rule_id)
    rule.stale_days = stale_days
    rule.action = action
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule
```

- [x] **Step 4: Re-run repository tests to verify rules can be approved and previewed**

Run: `pytest tests/test_rule_repository.py -v`
Expected: PASS

- [x] **Step 5: Commit the rule services**

```bash
git add app/services/rules.py app/models/candidate.py app/models/rule.py app/gmail/client.py tests/test_rule_repository.py
git commit -m "feat: add sender rule approval flow"
```

## Task 6: Implement manual execution, idempotence, and audit logging

**Files:**
- Create: `app/services/executor.py`
- Test: `tests/test_executor.py`
- Modify: `app/models/run_log.py`
- Modify: `app/models/rule.py`
- Modify: `app/gmail/client.py`

- [x] **Step 1: Write failing tests for per-rule execution and idempotence**

```python
from app.services.executor import run_cleanup_once


async def test_run_cleanup_once_records_audit_and_skips_already_processed_messages(session, gmail_client_stub):
    result = await run_cleanup_once(session, gmail_client_stub, triggered_by="manual")
    assert result.rules_ran == 1
    assert result.messages_acted_on == 2

    second = await run_cleanup_once(session, gmail_client_stub, triggered_by="manual")
    assert second.messages_acted_on == 0


async def test_executor_pauses_rule_after_repeated_gmail_failures(session, flaky_gmail_client_stub):
    result = await run_cleanup_once(session, flaky_gmail_client_stub, triggered_by="scheduled")
    assert result.failed_rules == 1
    assert result.paused_rules == 1
    assert "retry_exhausted" in result.errors[0]


async def test_executor_pauses_rule_when_match_volume_spikes(session, gmail_client_stub):
    result = await run_cleanup_once(session, gmail_client_stub, triggered_by="scheduled", dry_run=False, max_matches_per_rule=100)
    assert result.paused_rules == 1
    assert result.messages_acted_on == 0
    assert result.errors[0] == "volume_spike"


async def test_dry_run_records_planned_actions_without_mutating_gmail(session, gmail_client_stub):
    result = await run_cleanup_once(session, gmail_client_stub, triggered_by="manual", dry_run=True)
    assert result.messages_acted_on == 0
    assert result.planned_actions == 2
```

- [x] **Step 2: Run the executor tests to verify cleanup execution is missing**

Run: `pytest tests/test_executor.py -v`
Expected: FAIL with missing service errors

- [x] **Step 3: Implement execution with retry/backoff, partial-failure logging, dry-run support, spike pausing, and action logging**

```python
async def run_cleanup_once(
    session: Session,
    gmail_client: GmailClient,
    triggered_by: str,
    dry_run: bool = False,
    max_matches_per_rule: int = 100,
) -> RunSummary:
    for rule in enabled_rules:
        try:
            matches = await preview_rule_matches(session, gmail_client, rule.id)
            if len(matches) > max_matches_per_rule:
                rule.pause_reason = "volume_spike"
                session.add(rule)
                session.add(RunLog(rule_id=rule.id, action="paused", error_message="volume_spike", triggered_by=triggered_by))
                continue
            for match in matches:
                if already_logged(session, rule.id, match.message_id):
                    continue
                if dry_run:
                    session.add(RunLog(rule_id=rule.id, message_id=match.message_id, action=f"dry_run:{rule.action}", triggered_by=triggered_by))
                    continue
                await apply_with_retry(gmail_client, match.message_id, rule.action, attempts=3)
                session.add(RunLog(rule_id=rule.id, message_id=match.message_id, action=rule.action, triggered_by=triggered_by))
        except Exception as exc:
            rule.pause_reason = "retry_exhausted"
            session.add(rule)
            session.add(RunLog(rule_id=rule.id, action="error", error_message=str(exc), triggered_by=triggered_by))
```

- [x] **Step 4: Re-run executor tests and verify idempotence + failure isolation**

Run: `pytest tests/test_executor.py -v`
Expected: PASS

- [x] **Step 5: Commit the executor**

```bash
git add app/services/executor.py app/models/run_log.py app/models/rule.py app/gmail/client.py tests/test_executor.py
git commit -m "feat: add manual cleanup executor"
```

## Task 7: Build the operator console routes and templates

**Files:**
- Create: `app/web/__init__.py`
- Create: `app/web/routes.py`
- Create: `app/templates/base.html`
- Create: `app/templates/dashboard.html`
- Create: `app/templates/partials/candidate_list.html`
- Create: `app/templates/partials/exceptions.html`
- Create: `app/templates/partials/rule_editor.html`
- Create: `app/templates/partials/run_log.html`
- Test: `tests/test_dashboard_routes.py`
- Modify: `app/main.py`

- [x] **Step 1: Write failing route tests for the dashboard and approval actions**

```python
def test_dashboard_renders_candidates_rules_and_run_log(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Review Candidates" in response.text
    assert "Run Cleanup Now" in response.text
    assert "Exceptions" in response.text
```

```python
def test_approve_rule_action_redirects_back_to_dashboard(client):
    response = client.post("/rules/approve", data={"candidate_id": 1, "stale_days": 2, "action": "trash"})
    assert response.status_code == 303
```

```python
def test_reject_and_postpone_actions_redirect_back_to_dashboard(client):
    assert client.post("/candidates/1/reject").status_code == 303
    assert client.post("/candidates/2/postpone").status_code == 303
```

```python
def test_run_cleanup_now_triggers_manual_execution(client):
    response = client.post("/runs/execute")
    assert response.status_code == 303
```

- [x] **Step 2: Run the dashboard tests to verify web routes are not mounted**

Run: `pytest tests/test_dashboard_routes.py -v`
Expected: FAIL with 404s or import errors

- [x] **Step 3: Implement the server-rendered operator console**

```python
router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "candidates": list_pending_candidates(session),
            "rules": list_rules(session),
            "recent_runs": list_recent_runs(session),
            "exceptions": list_paused_rules(session),
        },
    )


@router.post("/runs/execute")
async def run_cleanup(session: Session = Depends(get_session), gmail_client: GmailClient = Depends(get_gmail_client)):
    await run_cleanup_once(session, gmail_client, triggered_by="manual", dry_run=False)
    return RedirectResponse("/", status_code=303)
```

```html
<aside>Review Candidates / Active Rules / Recent Runs / Exceptions</aside>
<form method="post" action="/runs/execute"><button>Run Cleanup Now</button></form>
<section>{% include "partials/candidate_list.html" %}</section>
<main>{% include "partials/rule_editor.html" %}</main>
<section>{% include "partials/exceptions.html" %}</section>
<section>{% include "partials/run_log.html" %}</section>
```

- [x] **Step 4: Re-run the dashboard tests and verify the UI shell works**

Run: `pytest tests/test_dashboard_routes.py -v`
Expected: PASS

- [x] **Step 5: Commit the operator console**

```bash
git add app/web/__init__.py app/web/routes.py app/templates/base.html app/templates/dashboard.html app/templates/partials/candidate_list.html app/templates/partials/exceptions.html app/templates/partials/rule_editor.html app/templates/partials/run_log.html app/main.py tests/test_dashboard_routes.py
git commit -m "feat: add operator console dashboard"
```

## Task 8: Add scheduler wiring, auth-failure pausing, and full verification

**Files:**
- Create: `app/services/scheduler.py`
- Modify: `app/main.py`
- Modify: `app/services/executor.py`
- Modify: `app/web/routes.py`
- Test: `tests/test_executor.py`
- Test: `tests/test_dashboard_routes.py`
- Test: `tests/test_app_health.py`

- [x] **Step 1: Extend tests to cover scheduled execution pause behavior**

```python
def test_scheduler_is_paused_when_auth_state_is_disconnected(client, monkeypatch):
    monkeypatch.setattr("app.gmail.auth.AuthState.from_disk", lambda settings: AuthState(False, "missing_token"))
    response = client.get("/")
    assert "Reconnect Gmail" in response.text
```

- [x] **Step 2: Run targeted tests to verify scheduler state is not exposed yet**

Run: `pytest tests/test_executor.py tests/test_dashboard_routes.py -v`
Expected: FAIL on missing scheduler/auth pause behavior

- [x] **Step 3: Implement APScheduler bootstrap and paused-state UI**

```python
async def run_scheduled_cleanup(app: FastAPI) -> RunSummary:
    with Session(app.state.engine) as session:
        gmail_client = build_gmail_client(app.state.settings)
        return await run_cleanup_once(session, gmail_client, triggered_by="scheduled", dry_run=False)


def start_scheduler(app: FastAPI) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: run_scheduled_cleanup(app), "cron", hour=6, minute=0, id="cleanup")
    if not AuthState.from_disk(app.state.settings).connected:
        scheduler.pause_job("cleanup")
    scheduler.start()
    return scheduler
```

- [x] **Step 4: Run the full suite and verify the MVP is stable**

Run: `pytest -v`
Expected: PASS across health, repository, Gmail client, discovery, executor, and dashboard tests

- [x] **Step 5: Smoke-test the app locally**

Run: `python -m uvicorn app.main:create_app --factory --reload`
Expected: server starts and `/` renders the operator console with reconnect state or seeded data

- [x] **Step 6: Commit scheduler + verification changes**

```bash
git add app/services/scheduler.py app/main.py app/services/executor.py app/web/routes.py tests/test_executor.py tests/test_dashboard_routes.py tests/test_app_health.py
git commit -m "feat: add scheduled cleanup orchestration"
```

## Final Verification Checklist

- [x] Run `pytest -v`
- [x] Run `python -m uvicorn app.main:create_app --factory`
- [x] Verify `/health` returns `{"status":"ok"}`
- [x] Verify `/` shows candidate review, active rules, recent runs, and scheduler state
- [x] Verify approving a candidate creates a rule and updates the candidate status
- [x] Verify `Run Cleanup Now` writes run-log entries and does not reprocess the same message on a second run
- [x] Verify missing Gmail tokens show `Reconnect Gmail` and pause scheduled execution
