import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.candidate import Candidate


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_sender_with_list_unsubscribe_is_classified_as_newsletter() -> None:
    from app.discovery.newsletters import classify_sender

    sender = classify_sender(
        sender_address="daily@example.com",
        sender_name="Daily Example",
        headers={"List-Unsubscribe": "<mailto:leave@example.com>"},
        subjects=["Top stories today", "Your Wednesday briefing", "Top picks for you"],
        category="promotions",
    )

    assert sender.is_newsletter is True
    assert sender.recommended_stale_days == 2
    assert sender.recommended_action == "trash"
    assert sender.observed_frequency == "weekly"
    assert sender.example_message_ids == []


def test_sender_with_two_subjects_gets_subject_pattern_signal() -> None:
    from app.discovery.newsletters import classify_sender

    sender = classify_sender(
        sender_address="digest@example.com",
        sender_name="Digest",
        headers={},
        subjects=["Issue 1", "Issue 2"],
        category="promotions",
    )

    assert sender.is_newsletter is True
    assert sender.recommended_stale_days == 2
    assert sender.recommended_action == "trash"
    assert sender.observed_frequency == "weekly"


def test_sender_without_enough_signals_is_not_classified_as_newsletter() -> None:
    from app.discovery.newsletters import classify_sender

    sender = classify_sender(
        sender_address="friend@example.com",
        sender_name="Friend",
        headers={},
        subjects=["Checking in"],
        category="personal",
    )

    assert sender.is_newsletter is False
    assert sender.recommended_stale_days == 7
    assert sender.recommended_action == "archive"
    assert sender.observed_frequency == "weekly"


@pytest.mark.asyncio
async def test_discovery_service_persists_pending_candidates(session: Session) -> None:
    from app.services.discovery import discover_newsletter_candidates

    class FakeGmailClient:
        async def list_message_ids(self, query: str) -> list[str]:
            assert query == "newer_than:7d"
            return ["m1", "m2", "m3"]

        async def get_message_metadata(self, message_id: str) -> dict:
            payloads = {
                "m1": {
                    "id": "m1",
                    "labelIds": ["CATEGORY_PROMOTIONS"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Daily Example <daily@example.com>"},
                            {"name": "Subject", "value": "Top stories today"},
                            {"name": "List-Unsubscribe", "value": "<mailto:leave@example.com>"},
                        ]
                    },
                },
                "m2": {
                    "id": "m2",
                    "labelIds": ["CATEGORY_PROMOTIONS"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Daily Example <daily@example.com>"},
                            {"name": "Subject", "value": "Your Wednesday briefing"},
                        ]
                    },
                },
                "m3": {
                    "id": "m3",
                    "labelIds": ["CATEGORY_PROMOTIONS"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Daily Example <daily@example.com>"},
                            {"name": "Subject", "value": "Top picks for you"},
                        ]
                    },
                },
            }
            return payloads[message_id]

    created = await discover_newsletter_candidates(session, FakeGmailClient())

    assert created == 1
    candidate = session.exec(select(Candidate)).one()
    assert candidate.sender_address == "daily@example.com"
    assert candidate.sender_name == "Daily Example"
    assert json.loads(candidate.sample_subjects_json) == [
        "Top stories today",
        "Your Wednesday briefing",
        "Top picks for you",
    ]
    assert json.loads(candidate.example_message_ids_json) == ["m1", "m2", "m3"]
    assert candidate.observed_frequency == "weekly"
    assert candidate.recommended_stale_days == 2
    assert candidate.recommended_action == "trash"
    assert candidate.risk_level == "low"
    assert candidate.status == "pending"


@pytest.mark.asyncio
async def test_discovery_service_updates_existing_pending_candidate(session: Session) -> None:
    from app.services.discovery import discover_newsletter_candidates

    existing = Candidate(
        sender_address="daily@example.com",
        sender_name="Old Name",
        sample_subjects_json=json.dumps(["Old subject"]),
        example_message_ids_json=json.dumps(["old-id"]),
        observed_frequency="unknown",
        recommended_stale_days=7,
        recommended_action="archive",
        risk_level="medium",
        status="pending",
    )
    session.add(existing)
    session.commit()

    class FakeGmailClient:
        async def list_message_ids(self, query: str) -> list[str]:
            return ["m1", "m2", "m3", "m4", "m5"]

        async def get_message_metadata(self, message_id: str) -> dict:
            return {
                "id": message_id,
                "labelIds": ["CATEGORY_PROMOTIONS"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Daily Example <daily@example.com>"},
                        {"name": "Subject", "value": f"Edition {message_id}"},
                        {"name": "List-Unsubscribe", "value": "<mailto:leave@example.com>"},
                    ]
                },
            }

    created = await discover_newsletter_candidates(session, FakeGmailClient())

    assert created == 1
    candidates = session.exec(select(Candidate)).all()
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.sender_name == "Daily Example"
    assert json.loads(candidate.example_message_ids_json) == ["m1", "m2", "m3"]
    assert candidate.observed_frequency == "daily"
    assert candidate.recommended_stale_days == 2
    assert candidate.recommended_action == "trash"
    assert candidate.risk_level == "low"


@pytest.mark.asyncio
async def test_discovery_service_normalizes_sender_address_case_before_grouping(session: Session) -> None:
    from app.services.discovery import discover_newsletter_candidates

    class FakeGmailClient:
        async def list_message_ids(self, query: str) -> list[str]:
            return ["m1", "m2"]

        async def get_message_metadata(self, message_id: str) -> dict:
            payloads = {
                "m1": {
                    "id": "m1",
                    "labelIds": ["CATEGORY_PROMOTIONS"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Daily Example <Daily@Example.com>"},
                            {"name": "Subject", "value": "Morning edition"},
                            {"name": "List-Unsubscribe", "value": "<mailto:leave@example.com>"},
                        ]
                    },
                },
                "m2": {
                    "id": "m2",
                    "labelIds": ["CATEGORY_PROMOTIONS"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Daily Example <daily@example.com>"},
                            {"name": "Subject", "value": "Evening edition"},
                        ]
                    },
                },
            }
            return payloads[message_id]

    created = await discover_newsletter_candidates(session, FakeGmailClient())

    assert created == 1
    candidates = session.exec(select(Candidate)).all()
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.sender_address == "daily@example.com"
    assert json.loads(candidate.sample_subjects_json) == ["Morning edition", "Evening edition"]
    assert json.loads(candidate.example_message_ids_json) == ["m1", "m2"]
