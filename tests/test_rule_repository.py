import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.candidate import Candidate
from app.models.rule import CleanupRule
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
