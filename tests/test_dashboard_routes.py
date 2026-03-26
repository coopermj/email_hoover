from pathlib import Path
from dataclasses import replace

from fastapi.testclient import TestClient
import httpx
import pytest
from bs4 import BeautifulSoup
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel import Session, SQLModel, create_engine

from app import create_app
from app.gmail.auth import AuthState
from app.gmail.oauth import read_gmail_credentials, write_gmail_credentials
from app.models.candidate import Candidate
from app.models.rule import CleanupRule
from app.models.run_log import RunLog


class GmailClientStub:
    async def aclose(self) -> None:
        return None


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(session: Session, tmp_path: Path) -> TestClient:
    from app.db import get_session

    try:
        from app.web.routes import get_gmail_client
    except ModuleNotFoundError:
        get_gmail_client = None

    app = create_app()
    app.state.settings = replace(app.state.settings, gmail_token_path=tmp_path / "missing-token.json")
    app.dependency_overrides[get_session] = lambda: session
    if get_gmail_client is not None:
        app.dependency_overrides[get_gmail_client] = lambda: GmailClientStub()

    with TestClient(app) as test_client:
        yield test_client


def test_dashboard_renders_candidates_rules_and_run_log(client: TestClient, session: Session) -> None:
    from app.gmail.auth import AuthState

    session.add(
        Candidate(
            sender_address="pending@example.com",
            sender_name="Pending Sender",
            recommended_stale_days=2,
            recommended_action="trash",
        )
    )
    session.add(
        CleanupRule(
            sender_address="rule@example.com",
            sender_name="Rule Sender",
            stale_days=5,
            action="archive",
        )
    )
    session.add(
        CleanupRule(
            sender_address="paused@example.com",
            sender_name="Paused Sender",
            stale_days=7,
            action="trash",
            pause_reason="volume_spike",
        )
    )
    session.add(
        RunLog(
            trigger="manual",
            triggered_by="manual",
            status="completed",
            matched_count=3,
            actioned_count=2,
            action="trash",
            error_message="Daily Example | Top stories today",
        )
    )
    session.commit()
    client.app.state.cleanup_job_auth_failed = False
    client.app.state.scheduler.resume_job("cleanup")
    original_from_disk = AuthState.from_disk
    AuthState.from_disk = classmethod(lambda cls, settings: AuthState(True))  # type: ignore[method-assign]

    try:
        response = client.get("/")
    finally:
        AuthState.from_disk = original_from_disk  # type: ignore[method-assign]

    assert response.status_code == 200
    assert "Scan Gmail For Newsletters" in response.text
    assert "Potential Newsletters" in response.text
    assert "Create Rule" in response.text
    assert "Run Cleanup Now" in response.text
    assert "Pending Sender" in response.text
    assert 'aria-current="page">Candidates<' in response.text


def test_dashboard_defaults_to_candidates_tab_and_hides_other_sections(
    client: TestClient,
    session: Session,
) -> None:
    session.add(
        Candidate(
            sender_address="pending@example.com",
            sender_name="Pending Sender",
            recommended_stale_days=2,
            recommended_action="trash",
        )
    )
    session.add(
        CleanupRule(
            sender_address="rule@example.com",
            sender_name="Rule Sender",
            stale_days=5,
            action="archive",
        )
    )
    session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "Potential Newsletters" in response.text
    assert "Pending Sender" in response.text
    assert "Recent Cleanup Activity" not in response.text
    assert "Rule Sender" not in response.text
    assert "Paused Sender" not in response.text


def test_dashboard_candidates_tab_hides_pending_sender_when_rule_already_exists(
    client: TestClient,
    session: Session,
) -> None:
    session.add(
        Candidate(
            sender_address="duplicate@example.com",
            sender_name="Duplicate Sender",
            recommended_stale_days=2,
            recommended_action="trash",
        )
    )
    session.add(
        CleanupRule(
            sender_address="duplicate@example.com",
            sender_name="Duplicate Sender",
            stale_days=2,
            action="trash",
        )
    )
    session.commit()

    response = client.get("/?tab=candidates")

    assert response.status_code == 200
    assert "Duplicate Sender" not in response.text


def test_dashboard_activity_tab_hides_editor_and_other_workspaces(
    client: TestClient,
    session: Session,
) -> None:
    session.add(
        RunLog(
            trigger="manual",
            triggered_by="manual",
            status="completed",
            matched_count=1,
            actioned_count=1,
            action="trash",
            error_message="Daily Example | Top stories today",
        )
    )
    session.commit()

    response = client.get("/?tab=activity")

    assert response.status_code == 200
    assert "Recent Cleanup Activity" in response.text
    assert "Top stories today" in response.text
    assert "Potential Newsletters" not in response.text
    assert "Rule Workspace" not in response.text
    assert "Create Rule" not in response.text
    assert 'aria-current="page">Activity<' in response.text


def test_dashboard_rules_tab_shows_rule_workspace_and_editor(
    client: TestClient,
    session: Session,
) -> None:
    session.add(
        CleanupRule(
            sender_address="rule@example.com",
            sender_name="Rule Sender",
            stale_days=5,
            action="archive",
        )
    )
    session.commit()

    response = client.get("/?tab=rules")

    assert response.status_code == 200
    assert "Rule Workspace" in response.text
    assert "Rule Sender" in response.text
    assert "Create Rule" in response.text
    assert "Potential Newsletters" not in response.text
    assert "Recent Cleanup Activity" not in response.text
    assert 'aria-current="page">Rules<' in response.text


def test_dashboard_exceptions_tab_shows_paused_rules_without_editor(
    client: TestClient,
    session: Session,
) -> None:
    session.add(
        CleanupRule(
            sender_address="paused@example.com",
            sender_name="Paused Sender",
            stale_days=7,
            action="trash",
            pause_reason="volume_spike",
        )
    )
    session.commit()

    response = client.get("/?tab=exceptions")

    assert response.status_code == 200
    assert "Exceptions" in response.text
    assert "Paused Sender" in response.text
    assert "Create Rule" not in response.text
    assert "Potential Newsletters" not in response.text
    assert "Rule Workspace" not in response.text
    assert 'aria-current="page">Exceptions<' in response.text


def test_dashboard_shows_connect_button_when_gmail_disconnected(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Connect Gmail" in response.text


def test_dashboard_uses_configured_callback_host_for_connect_button(client: TestClient) -> None:
    client.app.state.settings = replace(
        client.app.state.settings,
        google_redirect_uri="http://localhost:8765/auth/google/callback",
    )

    response = client.get("/")

    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    form = soup.find("form", {"method": "get", "action": "http://localhost:8765/auth/google/start"})
    assert form is not None


def test_oauth_start_redirects_to_google_and_sets_state_cookie(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_google_oauth_start(settings, state_token: str):
        assert state_token
        from app.gmail.oauth import GoogleOAuthStart

        return GoogleOAuthStart(
            authorization_url="https://accounts.google.com/o/oauth2/v2/auth?state=fake-state",
            code_verifier="verifier-123",
        )

    monkeypatch.setattr(
        "app.web.routes.build_google_oauth_start",
        fake_build_google_oauth_start,
    )

    response = client.get("/auth/google/start", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.google.com/")
    assert "email_hoover_oauth_state=" in response.headers["set-cookie"]
    assert "email_hoover_oauth_code_verifier=verifier-123" in response.headers["set-cookie"]


def test_oauth_start_missing_config_redirects_with_operator_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_google_oauth_start(settings, state_token: str):
        raise ValueError("Google OAuth configuration is incomplete.")

    monkeypatch.setattr(
        "app.web.routes.build_google_oauth_start",
        fake_build_google_oauth_start,
    )

    response = client.get("/auth/google/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?error=")
    assert "Google OAuth configuration is incomplete." in client.get(response.headers["location"]).text


def test_oauth_start_missing_credentials_file_redirects_with_operator_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_google_oauth_start(settings, state_token: str):
        raise FileNotFoundError("/missing/google-oauth-client.json")

    monkeypatch.setattr(
        "app.web.routes.build_google_oauth_start",
        fake_build_google_oauth_start,
    )

    response = client.get("/auth/google/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?error=")
    assert "/missing/google-oauth-client.json" in client.get(response.headers["location"]).text


def test_oauth_start_malformed_credentials_file_redirects_with_operator_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_google_oauth_start(settings, state_token: str):
        raise ValueError("Google OAuth credentials file is invalid JSON.")

    monkeypatch.setattr(
        "app.web.routes.build_google_oauth_start",
        fake_build_google_oauth_start,
    )

    response = client.get("/auth/google/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?error=")
    assert "Google OAuth credentials file is invalid JSON." in client.get(response.headers["location"]).text


def test_oauth_callback_persists_credentials_and_redirects_home(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "gmail-token.json"
    client.app.state.settings = replace(client.app.state.settings, gmail_token_path=token_path)
    client.app.state.cleanup_job_auth_failed = True
    client.app.state.scheduler.pause_job("cleanup")

    def fake_build_google_oauth_start(settings, state_token: str):
        from app.gmail.oauth import GoogleOAuthStart

        return GoogleOAuthStart(
            authorization_url=f"https://accounts.google.com/o/oauth2/v2/auth?state={state_token}",
            code_verifier="verifier-123",
        )

    def fake_exchange_google_code(settings, code: str, code_verifier: str) -> dict[str, object]:
        assert code == "abc"
        assert code_verifier == "verifier-123"
        return {
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
        }

    monkeypatch.setattr(
        "app.web.routes.build_google_oauth_start",
        fake_build_google_oauth_start,
    )
    monkeypatch.setattr("app.web.routes.exchange_google_code", fake_exchange_google_code)

    start_response = client.get("/auth/google/start", follow_redirects=False)
    state = start_response.headers["location"].split("state=")[1]

    response = client.get(f"/auth/google/callback?state={state}&code=abc", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert read_gmail_credentials(token_path)["refresh_token"] == "refresh-token"
    assert client.app.state.cleanup_job_auth_failed is False
    assert client.app.state.scheduler.get_job("cleanup").next_run_time is not None
    assert "email_hoover_oauth_code_verifier=\"\"" in response.headers["set-cookie"]


def test_oauth_callback_rejects_state_mismatch(client: TestClient) -> None:
    client.cookies.set("email_hoover_oauth_state", "expected")
    response = client.get(
        "/auth/google/callback?state=wrong&code=abc",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Google OAuth state validation failed." in client.get(response.headers["location"]).text


def test_oauth_callback_handles_provider_error(client: TestClient) -> None:
    client.cookies.set("email_hoover_oauth_state", "expected")
    response = client.get(
        "/auth/google/callback?state=expected&error=access_denied",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Google OAuth failed: access_denied" in client.get(response.headers["location"]).text


def test_oauth_callback_requires_authorization_code(client: TestClient) -> None:
    client.cookies.set("email_hoover_oauth_state", "expected")
    response = client.get(
        "/auth/google/callback?state=expected",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "did not include an authorization code" in client.get(response.headers["location"]).text


def test_oauth_callback_write_failure_preserves_existing_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "gmail-token.json"
    write_gmail_credentials(
        token_path,
        {
            "token": "old-access-token",
            "refresh_token": "old-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
        },
    )
    client.app.state.settings = replace(client.app.state.settings, gmail_token_path=token_path)

    def fake_exchange_google_code(settings, code: str, code_verifier: str) -> dict[str, object]:
        assert code_verifier == "verifier-123"
        return {
            "token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
        }

    def fake_write_gmail_credentials(path: Path, payload: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("app.web.routes.exchange_google_code", fake_exchange_google_code)
    monkeypatch.setattr("app.web.routes.write_gmail_credentials", fake_write_gmail_credentials)
    client.cookies.set("email_hoover_oauth_state", "expected")
    client.cookies.set("email_hoover_oauth_code_verifier", "verifier-123")

    response = client.get(
        "/auth/google/callback?state=expected&code=abc",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "disk full" in client.get(response.headers["location"]).text
    assert read_gmail_credentials(token_path)["refresh_token"] == "old-refresh-token"


def test_approve_rule_action_redirects_back_to_dashboard(client: TestClient, session: Session) -> None:
    candidate = Candidate(
        sender_address="approve@example.com",
        sender_name="Approve Sender",
        recommended_stale_days=2,
        recommended_action="trash",
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)

    response = client.post(
        "/rules/approve",
        data={"candidate_id": candidate.id, "stale_days": 2, "action": "trash"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_discover_candidates_triggers_newsletter_scan_and_redirects_with_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_discover_newsletter_candidates(session, gmail_client) -> int:
        calls.append("scan")
        return 3

    monkeypatch.setattr(
        "app.web.routes.discover_newsletter_candidates",
        fake_discover_newsletter_candidates,
        raising=False,
    )

    response = client.post("/candidates/discover", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?message=")
    follow_up = client.get(response.headers["location"])
    assert "Found 3 newsletter candidates to review." in follow_up.text
    assert calls == ["scan"]


def test_discover_candidates_handles_empty_scan_results_with_operator_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover_newsletter_candidates(session, gmail_client) -> int:
        return 0

    monkeypatch.setattr(
        "app.web.routes.discover_newsletter_candidates",
        fake_discover_newsletter_candidates,
        raising=False,
    )

    response = client.post("/candidates/discover", follow_redirects=False)

    assert response.status_code == 303
    follow_up = client.get(response.headers["location"])
    assert "No likely newsletters were found in the current scan window." in follow_up.text


def test_create_rule_action_redirects_back_to_dashboard(client: TestClient, session: Session) -> None:
    response = client.post(
        "/rules/create",
        data={
            "sender_address": "manual@example.com",
            "sender_name": "Manual Sender",
            "stale_days": 2,
            "action": "trash",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    created = session.exec(select(CleanupRule).where(CleanupRule.sender_address == "manual@example.com")).one()
    assert created.sender_name == "Manual Sender"
    assert created.action == "trash"


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {"sender_address": "", "sender_name": "Manual Sender", "stale_days": 2, "action": "trash"},
            "Sender email is required.",
        ),
        (
            {"sender_address": "manual@example.com", "sender_name": "Manual Sender", "stale_days": -1, "action": "trash"},
            "Stale days must be at least 0.",
        ),
        (
            {"sender_address": "manual@example.com", "sender_name": "Manual Sender", "stale_days": 2, "action": "delete"},
            "Action must be archive or trash.",
        ),
    ],
)
def test_create_rule_validation_errors_redirect_with_operator_message(
    client: TestClient,
    session: Session,
    payload: dict[str, object],
    expected_message: str,
) -> None:
    response = client.post("/rules/create", data=payload, follow_redirects=False)

    assert response.status_code == 303
    follow_up = client.get(response.headers["location"])
    assert expected_message in follow_up.text
    assert session.exec(select(CleanupRule)).all() == []


def test_update_rule_action_persists_changes(client: TestClient, session: Session) -> None:
    rule = CleanupRule(
        sender_address="before@example.com",
        sender_name="Before Sender",
        stale_days=2,
        action="trash",
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)

    response = client.post(
        f"/rules/{rule.id}/update",
        data={
            "sender_address": "after@example.com",
            "sender_name": "After Sender",
            "stale_days": 7,
            "action": "archive",
            "enabled": "on",
            "schedule_enabled": "",
        },
        follow_redirects=False,
    )

    session.refresh(rule)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert rule.sender_address == "after@example.com"
    assert rule.sender_name == "After Sender"
    assert rule.stale_days == 7
    assert rule.action == "archive"
    assert rule.enabled is True
    assert rule.schedule_enabled is False


def test_disable_and_enable_rule_actions_redirect_back_to_dashboard(
    client: TestClient,
    session: Session,
) -> None:
    rule = CleanupRule(
        sender_address="toggle@example.com",
        sender_name="Toggle Sender",
        stale_days=2,
        action="trash",
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)

    disable_response = client.post(f"/rules/{rule.id}/disable", follow_redirects=False)
    session.refresh(rule)
    assert rule.enabled is False

    enable_response = client.post(f"/rules/{rule.id}/enable", follow_redirects=False)
    session.refresh(rule)

    assert disable_response.status_code == 303
    assert enable_response.status_code == 303
    assert rule.enabled is True


@pytest.mark.parametrize(
    ("stale_days", "action", "expected_message"),
    [
        (0, "trash", None),
        (-3, "archive", "Stale days must be at least 0."),
        (2, "delete", "Action must be archive or trash."),
    ],
)
def test_approve_rule_validation_errors_redirect_with_operator_message(
    client: TestClient,
    session: Session,
    stale_days: int,
    action: str,
    expected_message: str,
) -> None:
    candidate = Candidate(
        sender_address="invalid@example.com",
        sender_name="Invalid Sender",
        recommended_stale_days=2,
        recommended_action="trash",
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)

    response = client.post(
        "/rules/approve",
        data={"candidate_id": candidate.id, "stale_days": stale_days, "action": action},
        follow_redirects=False,
    )

    session.refresh(candidate)

    assert response.status_code == 303
    if expected_message is None:
        assert response.headers["location"] == "/"
        assert candidate.status == "approved"
        created_rules = session.exec(select(CleanupRule)).all()
        assert len(created_rules) == 1
        assert created_rules[0].stale_days == 0
    else:
        assert response.headers["location"].startswith("/?error=")
        follow_up = client.get(response.headers["location"])
        assert expected_message in follow_up.text
        assert candidate.status == "pending"
        assert session.exec(select(CleanupRule)).all() == []


def test_approve_rule_duplicate_sender_redirects_with_operator_message(
    client: TestClient,
    session: Session,
) -> None:
    first = Candidate(
        sender_address="duplicate@example.com",
        sender_name="First Duplicate",
        recommended_stale_days=2,
        recommended_action="trash",
    )
    second = Candidate(
        sender_address="duplicate@example.com",
        sender_name="Second Duplicate",
        recommended_stale_days=3,
        recommended_action="archive",
    )
    session.add(first)
    session.add(second)
    session.commit()
    session.refresh(first)
    session.refresh(second)

    first_response = client.post(
        "/rules/approve",
        data={"candidate_id": first.id, "stale_days": 2, "action": "trash"},
        follow_redirects=False,
    )
    duplicate_response = client.post(
        "/rules/approve",
        data={"candidate_id": second.id, "stale_days": 3, "action": "archive"},
        follow_redirects=False,
    )

    session.refresh(second)

    assert first_response.status_code == 303
    assert duplicate_response.status_code == 303
    follow_up = client.get(duplicate_response.headers["location"])
    assert "already has a cleanup rule" in follow_up.text
    assert second.status == "approved"


def test_reject_and_postpone_actions_redirect_back_to_dashboard(
    client: TestClient,
    session: Session,
) -> None:
    rejected = Candidate(
        sender_address="reject@example.com",
        sender_name="Reject Sender",
        recommended_stale_days=2,
        recommended_action="trash",
    )
    postponed = Candidate(
        sender_address="postpone@example.com",
        sender_name="Postpone Sender",
        recommended_stale_days=4,
        recommended_action="archive",
    )
    session.add(rejected)
    session.add(postponed)
    session.commit()
    session.refresh(rejected)
    session.refresh(postponed)

    reject_response = client.post(f"/candidates/{rejected.id}/reject", follow_redirects=False)
    postpone_response = client.post(f"/candidates/{postponed.id}/postpone", follow_redirects=False)

    assert reject_response.status_code == 303
    assert postpone_response.status_code == 303


def test_reject_and_postpone_value_errors_redirect_with_operator_message(client: TestClient) -> None:
    reject_response = client.post("/candidates/999/reject", follow_redirects=False)
    postpone_response = client.post("/candidates/1000/postpone", follow_redirects=False)

    assert reject_response.status_code == 303
    assert postpone_response.status_code == 303
    assert "Candidate 999 does not exist" in client.get(reject_response.headers["location"]).text
    assert "Candidate 1000 does not exist" in client.get(postpone_response.headers["location"]).text


def test_run_cleanup_now_triggers_manual_execution(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    async def fake_run_cleanup_once(session, gmail_client, *, triggered_by: str, dry_run: bool = False):
        calls.append((triggered_by, dry_run))

    monkeypatch.setattr("app.web.routes.run_cleanup_once", fake_run_cleanup_once, raising=False)

    response = client.post("/runs/execute", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert calls == [("manual", False)]


def test_run_cleanup_without_rules_redirects_with_operator_message_and_logs_noop(
    client: TestClient,
    session: Session,
) -> None:
    response = client.post("/runs/execute", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?message=")
    follow_up = client.get(response.headers["location"])
    assert "No active cleanup rules to run." in follow_up.text
    logs = session.exec(select(RunLog)).all()
    assert len(logs) == 1
    assert logs[0].status == "noop"


def test_run_cleanup_no_matches_redirects_with_operator_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.executor import RunSummary

    async def fake_run_cleanup_once(session, gmail_client, *, triggered_by: str, dry_run: bool = False):
        return RunSummary(rules_ran=1, messages_acted_on=0)

    monkeypatch.setattr("app.web.routes.run_cleanup_once", fake_run_cleanup_once, raising=False)

    response = client.post("/runs/execute", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/?message=")
    follow_up = client.get(response.headers["location"])
    assert "No stale messages matched the active rules." in follow_up.text


def test_run_cleanup_value_error_redirects_with_operator_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_cleanup_once(session, gmail_client, *, triggered_by: str, dry_run: bool = False):
        raise ValueError("Gmail token missing")

    monkeypatch.setattr("app.web.routes.run_cleanup_once", fake_run_cleanup_once, raising=False)

    response = client.post("/runs/execute", follow_redirects=False)

    assert response.status_code == 303
    follow_up = client.get(response.headers["location"])
    assert "Reconnect Gmail" in follow_up.text


@pytest.mark.parametrize(
    "auth_error",
    [
        FileNotFoundError("/tmp/gmail-token.json"),
        httpx.HTTPStatusError(
            "401 Unauthorized",
            request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages"),
            response=httpx.Response(
                401,
                request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages"),
            ),
        ),
    ],
)
def test_run_cleanup_auth_failures_redirect_with_reconnect_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    auth_error: Exception,
) -> None:
    monkeypatch.setattr(
        "app.web.routes.AuthState.from_disk",
        lambda settings: AuthState(True),
    )

    async def fake_run_cleanup_once(session, gmail_client, *, triggered_by: str, dry_run: bool = False):
        raise auth_error

    monkeypatch.setattr("app.web.routes.run_cleanup_once", fake_run_cleanup_once, raising=False)
    client.app.state.scheduler.resume_job("cleanup")

    cleanup_job = client.app.state.scheduler.get_job("cleanup")
    assert cleanup_job is not None
    assert cleanup_job.next_run_time is not None
    response = client.post("/runs/execute", follow_redirects=False)

    assert response.status_code == 303
    cleanup_job = client.app.state.scheduler.get_job("cleanup")
    assert cleanup_job is not None
    assert cleanup_job.next_run_time is None
    follow_up = client.get(response.headers["location"])
    assert "Reconnect Gmail" in follow_up.text
    dashboard = client.get("/")
    assert "Reconnect Gmail" in dashboard.text


def test_scheduler_is_paused_when_auth_state_is_disconnected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.gmail.auth.AuthState.from_disk",
        lambda settings: AuthState(False, "missing_token"),
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "Reconnect Gmail" in response.text
