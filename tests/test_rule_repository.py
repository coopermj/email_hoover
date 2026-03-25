import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app import create_app
from app.db import get_database_url, get_engine, init_db
from app.models.candidate import Candidate
from app.models.rule import CleanupRule
from app.models.run_log import RunLog
from app.services.rules import (
    approve_candidate,
    disable_rule,
    mark_candidate_postponed,
    mark_candidate_rejected,
    preview_rule_matches,
    update_rule,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def gmail_client_stub():
    class GmailClientStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def preview_matches(self, query: str, *, action: str):
            self.calls.append((query, action))
            return [
                {
                    "message_id": "m-older",
                    "thread_id": "t-older",
                    "subject": "Older Newsletter",
                    "planned_action": action,
                }
            ]

    return GmailClientStub()


def seed_candidate(
    session: Session,
    *,
    sender_address: str,
    sender_name: str,
    recommended_stale_days: int = 2,
    recommended_action: str = "trash",
) -> Candidate:
    candidate = Candidate(
        sender_address=sender_address,
        sender_name=sender_name,
        recommended_stale_days=recommended_stale_days,
        recommended_action=recommended_action,
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def test_approving_candidate_creates_rule(session: Session) -> None:
    candidate = seed_candidate(
        session,
        sender_address="newsletter@example.com",
        sender_name="Example Daily",
    )

    rule = approve_candidate(session, candidate.id, stale_days=3, action="archive")

    assert isinstance(rule, CleanupRule)
    assert rule.sender_address == "newsletter@example.com"
    assert rule.stale_days == 3
    assert rule.action == "archive"
    session.refresh(candidate)
    assert candidate.status == "approved"


def test_approving_missing_candidate_raises_error(session: Session) -> None:
    with pytest.raises(ValueError, match="Candidate 999 does not exist"):
        approve_candidate(session, 999, stale_days=3, action="archive")


@pytest.mark.asyncio
async def test_preview_rule_matches_returns_stale_messages(
    session: Session,
    gmail_client_stub,
) -> None:
    seed_candidate(
        session,
        sender_address="first@example.com",
        sender_name="First Sender",
    )
    candidate = seed_candidate(
        session,
        sender_address="preview@example.com",
        sender_name="Preview Sender",
    )
    rule = approve_candidate(session, candidate_id=candidate.id, stale_days=2, action="trash")

    matches = await preview_rule_matches(session, gmail_client_stub, rule.id)

    assert gmail_client_stub.calls == [("from:preview@example.com older_than:2d", "trash")]
    assert matches[0].message_id == "m-older"
    assert matches[0].planned_action == "trash"


def test_candidate_can_be_rejected_or_postponed(session: Session) -> None:
    rejected_candidate = seed_candidate(
        session,
        sender_address="rejected@example.com",
        sender_name="Rejected Sender",
    )
    postponed_candidate = seed_candidate(
        session,
        sender_address="postponed@example.com",
        sender_name="Postponed Sender",
    )

    rejected = mark_candidate_rejected(session, candidate_id=rejected_candidate.id)
    postponed = mark_candidate_postponed(session, candidate_id=postponed_candidate.id)

    assert rejected.status == "rejected"
    assert postponed.status == "postponed"


def test_rule_can_be_updated_and_disabled(session: Session) -> None:
    seed_candidate(
        session,
        sender_address="first@example.com",
        sender_name="First Sender",
    )
    seed_candidate(
        session,
        sender_address="second@example.com",
        sender_name="Second Sender",
    )
    candidate = seed_candidate(
        session,
        sender_address="editable@example.com",
        sender_name="Editable Sender",
    )
    rule = approve_candidate(session, candidate_id=candidate.id, stale_days=2, action="trash")

    edited = update_rule(session, rule.id, stale_days=5, action="archive")
    disabled = disable_rule(session, rule.id)

    assert edited.stale_days == 5
    assert edited.action == "archive"
    assert disabled.enabled is False


def test_run_log_round_trips_through_persistence(session: Session) -> None:
    run_log = RunLog(
        trigger="schedule",
        status="completed",
        matched_count=4,
        actioned_count=3,
    )

    session.add(run_log)
    session.commit()
    session.refresh(run_log)

    persisted = session.get(RunLog, run_log.id)

    assert persisted is not None
    assert persisted.trigger == "schedule"
    assert persisted.status == "completed"
    assert persisted.matched_count == 4
    assert persisted.actioned_count == 3
    assert persisted.started_at is not None


def test_create_app_lifespan_creates_tables(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "lifespan.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_engine.cache_clear()

    app = create_app()

    with TestClient(app):
        with Session(get_engine()) as session:
            candidate = Candidate(
                sender_address="startup@example.com",
                sender_name="Startup Sender",
                recommended_stale_days=1,
                recommended_action="archive",
            )
            session.add(candidate)
            session.commit()
            session.refresh(candidate)

            assert candidate.id is not None

    get_engine.cache_clear()


def test_get_engine_reuses_cached_engine_for_process(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "cached-engine.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    get_engine.cache_clear()

    first_engine = get_engine()
    second_engine = get_engine()

    assert first_engine is second_engine

    init_db()

    with Session(get_engine()) as session:
        candidate = Candidate(
            sender_address="cached@example.com",
            sender_name="Cached Engine",
            recommended_stale_days=5,
            recommended_action="archive",
        )
        session.add(candidate)
        session.commit()
        session.refresh(candidate)

        assert candidate.id is not None
