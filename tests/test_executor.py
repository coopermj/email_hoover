import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.candidate import Candidate
from app.models.rule import CleanupRule
from app.models.run_log import RunLog
from app.services.rules import approve_candidate


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class GmailClientStub:
    def __init__(self) -> None:
        self.preview_calls: list[tuple[str, str]] = []
        self.archived: list[str] = []
        self.trashed: list[str] = []

    async def preview_matches(self, query: str, *, action: str):
        self.preview_calls.append((query, action))
        sender = query.split(" ", 1)[0].removeprefix("from:")
        if sender == "spike@example.com":
            return [
                {
                    "message_id": f"spike-{index}",
                    "thread_id": f"thread-{index}",
                    "subject": f"Spike {index}",
                    "planned_action": action,
                }
                for index in range(101)
            ]
        return [
            {
                "message_id": "m-1",
                "thread_id": "t-1",
                "subject": "Newsletter 1",
                "planned_action": action,
            },
            {
                "message_id": "m-2",
                "thread_id": "t-2",
                "subject": "Newsletter 2",
                "planned_action": action,
            },
        ]

    async def archive_message(self, message_id: str) -> None:
        self.archived.append(message_id)

    async def trash_message(self, message_id: str) -> None:
        self.trashed.append(message_id)


class FlakyGmailClientStub(GmailClientStub):
    def __init__(self) -> None:
        super().__init__()
        self.failures: dict[str, int] = {}

    async def archive_message(self, message_id: str) -> None:
        self.failures[message_id] = self.failures.get(message_id, 0) + 1
        raise RuntimeError(f"transient failure for {message_id}")


class MissingTokenGmailClientStub(GmailClientStub):
    async def preview_matches(self, query: str, *, action: str):
        raise ValueError("Gmail token missing")


class MissingTokenFileGmailClientStub(GmailClientStub):
    async def preview_matches(self, query: str, *, action: str):
        raise FileNotFoundError("/tmp/gmail-token.json")


class GmailAuthHTTPFailureStub(GmailClientStub):
    def __init__(self) -> None:
        super().__init__()
        self.archive_attempts: list[str] = []

    async def archive_message(self, message_id: str) -> None:
        self.archive_attempts.append(message_id)
        raise _gmail_auth_http_error()


class MidRunAuthFailureGmailClientStub(GmailClientStub):
    def __init__(self) -> None:
        super().__init__()
        self.archive_attempts: list[str] = []

    async def archive_message(self, message_id: str) -> None:
        self.archive_attempts.append(message_id)
        if message_id == "m-2":
            raise FileNotFoundError("/tmp/gmail-token.json")
        await super().archive_message(message_id)


@pytest.fixture
def gmail_client_stub() -> GmailClientStub:
    return GmailClientStub()


@pytest.fixture
def flaky_gmail_client_stub() -> FlakyGmailClientStub:
    return FlakyGmailClientStub()


@pytest.fixture
def missing_token_gmail_client_stub() -> MissingTokenGmailClientStub:
    return MissingTokenGmailClientStub()


@pytest.fixture
def missing_token_file_gmail_client_stub() -> MissingTokenFileGmailClientStub:
    return MissingTokenFileGmailClientStub()


@pytest.fixture
def gmail_auth_http_failure_stub() -> GmailAuthHTTPFailureStub:
    return GmailAuthHTTPFailureStub()


@pytest.fixture
def mid_run_auth_failure_gmail_client_stub() -> MidRunAuthFailureGmailClientStub:
    return MidRunAuthFailureGmailClientStub()


def seed_rule(
    session: Session,
    *,
    sender_address: str,
    sender_name: str,
    stale_days: int = 2,
    action: str = "archive",
) -> CleanupRule:
    candidate = Candidate(
        sender_address=sender_address,
        sender_name=sender_name,
        recommended_stale_days=stale_days,
        recommended_action=action,
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return approve_candidate(session, candidate.id, stale_days=stale_days, action=action)


def _gmail_auth_http_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/m-1/modify")
    response = httpx.Response(401, request=request)
    return httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)


@pytest.mark.asyncio
async def test_run_cleanup_once_records_audit_and_skips_already_processed_messages(
    session: Session,
    gmail_client_stub: GmailClientStub,
) -> None:
    from app.services.executor import run_cleanup_once

    seed_rule(
        session,
        sender_address="newsletter@example.com",
        sender_name="Example Newsletter",
        action="archive",
    )

    result = await run_cleanup_once(session, gmail_client_stub, triggered_by="manual")
    assert result.rules_ran == 1
    assert result.messages_acted_on == 2
    assert gmail_client_stub.archived == ["m-1", "m-2"]

    logs = session.exec(select(RunLog).where(RunLog.action == "archive")).all()
    assert [log.message_id for log in logs] == ["m-1", "m-2"]
    assert all(log.triggered_by == "manual" for log in logs)

    second = await run_cleanup_once(session, gmail_client_stub, triggered_by="manual")
    assert second.messages_acted_on == 0
    assert gmail_client_stub.archived == ["m-1", "m-2"]


@pytest.mark.asyncio
async def test_executor_pauses_rule_after_repeated_gmail_failures(
    session: Session,
    flaky_gmail_client_stub: FlakyGmailClientStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.executor import run_cleanup_once
    import app.services.executor as executor_module

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(executor_module.asyncio, "sleep", fake_sleep)

    rule = seed_rule(
        session,
        sender_address="failure@example.com",
        sender_name="Failure Sender",
        action="archive",
    )

    result = await run_cleanup_once(session, flaky_gmail_client_stub, triggered_by="scheduled")

    assert result.failed_rules == 1
    assert result.paused_rules == 1
    assert "retry_exhausted" in result.errors[0]
    session.refresh(rule)
    assert rule.pause_reason == "retry_exhausted"
    assert flaky_gmail_client_stub.failures["m-1"] == 3
    assert sleeps == [0.1, 0.2]

    pause_logs = session.exec(select(RunLog).where(RunLog.action == "paused")).all()
    error_logs = session.exec(select(RunLog).where(RunLog.action == "error")).all()
    assert len(pause_logs) == 1
    assert pause_logs[0].error_message == "retry_exhausted"
    assert len(error_logs) == 1
    assert "transient failure for m-1" in error_logs[0].error_message

    second = await run_cleanup_once(session, flaky_gmail_client_stub, triggered_by="scheduled")
    assert second.rules_ran == 0
    assert flaky_gmail_client_stub.failures["m-1"] == 3


@pytest.mark.asyncio
async def test_executor_pauses_rule_when_match_volume_spikes(
    session: Session,
    gmail_client_stub: GmailClientStub,
) -> None:
    from app.services.executor import run_cleanup_once

    rule = seed_rule(
        session,
        sender_address="spike@example.com",
        sender_name="Spike Sender",
        action="trash",
    )

    result = await run_cleanup_once(
        session,
        gmail_client_stub,
        triggered_by="scheduled",
        dry_run=False,
        max_matches_per_rule=100,
    )

    assert result.paused_rules == 1
    assert result.messages_acted_on == 0
    assert result.errors[0] == "volume_spike"
    session.refresh(rule)
    assert rule.pause_reason == "volume_spike"
    assert gmail_client_stub.trashed == []

    second = await run_cleanup_once(
        session,
        gmail_client_stub,
        triggered_by="scheduled",
        dry_run=False,
        max_matches_per_rule=100,
    )
    assert second.rules_ran == 0
    assert gmail_client_stub.preview_calls == [("from:spike@example.com older_than:2d", "trash")]


@pytest.mark.asyncio
async def test_dry_run_records_planned_actions_without_mutating_gmail(
    session: Session,
    gmail_client_stub: GmailClientStub,
) -> None:
    from app.services.executor import run_cleanup_once

    seed_rule(
        session,
        sender_address="dryrun@example.com",
        sender_name="Dry Run Sender",
        action="archive",
    )

    result = await run_cleanup_once(session, gmail_client_stub, triggered_by="manual", dry_run=True)

    assert result.messages_acted_on == 0
    assert result.planned_actions == 2
    assert gmail_client_stub.archived == []
    dry_run_logs = session.exec(select(RunLog).where(RunLog.action == "dry_run:archive")).all()
    assert [log.message_id for log in dry_run_logs] == ["m-1", "m-2"]


@pytest.mark.asyncio
async def test_executor_bubbles_auth_failures_without_pausing_rule(
    session: Session,
    missing_token_gmail_client_stub: MissingTokenGmailClientStub,
) -> None:
    from app.services.executor import run_cleanup_once

    rule = seed_rule(
        session,
        sender_address="auth@example.com",
        sender_name="Auth Sender",
        action="archive",
    )

    with pytest.raises(ValueError, match="Gmail token missing"):
        await run_cleanup_once(session, missing_token_gmail_client_stub, triggered_by="scheduled")

    session.refresh(rule)
    assert rule.pause_reason is None
    assert session.exec(select(RunLog)).all() == []


@pytest.mark.asyncio
async def test_executor_bubbles_missing_token_file_failures_without_pausing_rule(
    session: Session,
    missing_token_file_gmail_client_stub: MissingTokenFileGmailClientStub,
) -> None:
    from app.services.executor import run_cleanup_once

    rule = seed_rule(
        session,
        sender_address="missing-file@example.com",
        sender_name="Missing File Sender",
        action="archive",
    )

    with pytest.raises(FileNotFoundError, match="gmail-token.json"):
        await run_cleanup_once(session, missing_token_file_gmail_client_stub, triggered_by="scheduled")

    session.refresh(rule)
    assert rule.pause_reason is None
    assert session.exec(select(RunLog)).all() == []


@pytest.mark.asyncio
async def test_executor_bubbles_gmail_auth_http_failures_without_pausing_rule(
    session: Session,
    gmail_auth_http_failure_stub: GmailAuthHTTPFailureStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.executor import run_cleanup_once
    import app.services.executor as executor_module

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(executor_module.asyncio, "sleep", fake_sleep)

    rule = seed_rule(
        session,
        sender_address="http-auth@example.com",
        sender_name="HTTP Auth Sender",
        action="archive",
    )

    with pytest.raises(httpx.HTTPStatusError, match="401 Unauthorized"):
        await run_cleanup_once(session, gmail_auth_http_failure_stub, triggered_by="scheduled")

    session.refresh(rule)
    assert rule.pause_reason is None
    assert gmail_auth_http_failure_stub.archive_attempts == ["m-1"]
    assert sleeps == []
    assert session.exec(select(RunLog)).all() == []


@pytest.mark.asyncio
async def test_executor_preserves_audit_logs_when_auth_fails_mid_run(
    session: Session,
    mid_run_auth_failure_gmail_client_stub: MidRunAuthFailureGmailClientStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.executor import run_cleanup_once
    import app.services.executor as executor_module

    async def fake_sleep(delay: float) -> None:
        raise AssertionError(f"unexpected retry backoff: {delay}")

    monkeypatch.setattr(executor_module.asyncio, "sleep", fake_sleep)

    seed_rule(
        session,
        sender_address="mid-run-auth@example.com",
        sender_name="Mid Run Auth Sender",
        action="archive",
    )

    with pytest.raises(FileNotFoundError, match="gmail-token.json"):
        await run_cleanup_once(session, mid_run_auth_failure_gmail_client_stub, triggered_by="scheduled")

    logs = session.exec(select(RunLog).where(RunLog.action == "archive")).all()
    assert [log.message_id for log in logs] == ["m-1"]
    assert all(log.status == "completed" for log in logs)
    assert mid_run_auth_failure_gmail_client_stub.archived == ["m-1"]
    assert mid_run_auth_failure_gmail_client_stub.archive_attempts == ["m-1", "m-2"]
