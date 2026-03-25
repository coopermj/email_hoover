import json
from email.utils import parseaddr

from sqlmodel import Session, select

from app.discovery.newsletters import classify_sender
from app.models.candidate import Candidate


async def discover_newsletter_candidates(session: Session, gmail_client: object) -> int:
    message_ids = await gmail_client.list_message_ids("newer_than:7d")
    sender_groups: dict[str, dict[str, object]] = {}

    for message_id in message_ids:
        metadata = await gmail_client.get_message_metadata(message_id)
        headers = _extract_headers(metadata)
        sender_name, sender_address = parseaddr(headers.get("From", ""))
        if not sender_address:
            continue

        group = sender_groups.setdefault(
            sender_address,
            {
                "sender_name": sender_name or sender_address,
                "headers": {},
                "subjects": [],
                "message_ids": [],
                "categories": [],
            },
        )
        group["sender_name"] = sender_name or group["sender_name"]
        group["subjects"].append(headers.get("Subject", ""))
        group["message_ids"].append(metadata["id"])
        group["categories"].append(_normalize_category(metadata.get("labelIds", [])))
        if headers.get("List-Unsubscribe"):
            group["headers"]["List-Unsubscribe"] = headers["List-Unsubscribe"]

    persisted = 0
    for sender_address, group in sender_groups.items():
        category = _preferred_category(group["categories"])
        recommendation = classify_sender(
            sender_address=sender_address,
            sender_name=group["sender_name"],
            headers=group["headers"],
            subjects=group["subjects"],
            category=category,
            message_ids=group["message_ids"],
            message_count_last_7_days=len(group["message_ids"]),
        )
        if not recommendation.is_newsletter:
            continue

        candidate = session.exec(
            select(Candidate).where(Candidate.sender_address == recommendation.sender_address)
        ).first()
        if candidate is None:
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
        elif candidate.status != "pending":
            continue
        else:
            candidate.sender_name = recommendation.sender_name
            candidate.sample_subjects_json = json.dumps(recommendation.sample_subjects)
            candidate.example_message_ids_json = json.dumps(recommendation.example_message_ids)
            candidate.observed_frequency = recommendation.observed_frequency
            candidate.recommended_stale_days = recommendation.recommended_stale_days
            candidate.recommended_action = recommendation.recommended_action
            candidate.risk_level = recommendation.risk_level

        session.add(candidate)
        persisted += 1

    session.commit()
    return persisted


def _extract_headers(metadata: dict) -> dict[str, str]:
    payload = metadata.get("payload", {})
    return {
        header["name"]: header["value"]
        for header in payload.get("headers", [])
        if "name" in header and "value" in header
    }


def _normalize_category(label_ids: list[str]) -> str:
    for label in label_ids:
        if label == "CATEGORY_PROMOTIONS":
            return "promotions"
        if label == "CATEGORY_UPDATES":
            return "updates"
        if label == "CATEGORY_SOCIAL":
            return "social"
    return "unknown"


def _preferred_category(categories: list[str]) -> str:
    if "promotions" in categories:
        return "promotions"
    if "updates" in categories:
        return "updates"
    if "social" in categories:
        return "social"
    return "unknown"
