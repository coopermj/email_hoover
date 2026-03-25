import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app import create_app
from app.db import get_database_url
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


def test_approving_candidate_creates_rule(session: Session) -> None:
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

    assert isinstance(rule, CleanupRule)
    assert rule.sender_address == "newsletter@example.com"
    assert rule.stale_days == 3
    assert rule.action == "archive"
    session.refresh(candidate)
    assert candidate.status == "approved"


def test_approving_missing_candidate_raises_error(session: Session) -> None:
    with pytest.raises(ValueError, match="Candidate 999 does not exist"):
        approve_candidate(session, 999, stale_days=3, action="archive")


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

    app = create_app()

    with TestClient(app):
        with Session(create_engine(get_database_url())) as session:
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
