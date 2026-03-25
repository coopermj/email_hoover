from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel import Session, SQLModel, create_engine

from app import create_app
from app.gmail.auth import AuthState
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
def client(session: Session) -> TestClient:
    from app.db import get_session

    try:
        from app.web.routes import get_gmail_client
    except ModuleNotFoundError:
        get_gmail_client = None

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    if get_gmail_client is not None:
        app.dependency_overrides[get_gmail_client] = lambda: GmailClientStub()

    with TestClient(app) as test_client:
        yield test_client


def test_dashboard_renders_candidates_rules_and_run_log(client: TestClient, session: Session) -> None:
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
        )
    )
    session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "Review Candidates" in response.text
    assert "Run Cleanup Now" in response.text
    assert "Exceptions" in response.text
    assert "Pending Sender" in response.text
    assert "Rule Sender" in response.text
    assert "Paused Sender" in response.text
    assert "Recent Cleanup Activity" in response.text
    assert "manual cleanup event" in response.text
    assert "2 actions applied" in response.text


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


@pytest.mark.parametrize(
    ("stale_days", "action", "expected_message"),
    [
        (0, "trash", "Stale days must be at least 1."),
        (-3, "archive", "Stale days must be at least 1."),
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
    assert second.status == "pending"


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
    async def fake_run_cleanup_once(session, gmail_client, *, triggered_by: str, dry_run: bool = False):
        raise auth_error

    monkeypatch.setattr("app.web.routes.run_cleanup_once", fake_run_cleanup_once, raising=False)

    response = client.post("/runs/execute", follow_redirects=False)

    assert response.status_code == 303
    follow_up = client.get(response.headers["location"])
    assert "Reconnect Gmail" in follow_up.text


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
