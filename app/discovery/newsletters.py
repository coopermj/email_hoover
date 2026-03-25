from dataclasses import dataclass


@dataclass(slots=True)
class CandidateRecommendation:
    sender_address: str
    sender_name: str
    is_newsletter: bool
    sample_subjects: list[str]
    example_message_ids: list[str]
    observed_frequency: str
    recommended_stale_days: int
    recommended_action: str
    risk_level: str


def classify_sender(
    *,
    sender_address: str,
    sender_name: str,
    headers: dict[str, str],
    subjects: list[str],
    category: str,
    message_ids: list[str] | None = None,
    message_count_last_7_days: int | None = None,
) -> CandidateRecommendation:
    if message_ids is None:
        message_ids = []
    if message_count_last_7_days is None:
        message_count_last_7_days = len(message_ids)

    signals = 0
    if headers.get("List-Unsubscribe"):
        signals += 2
    if category == "promotions":
        signals += 1
    if len(subjects) >= 2:
        signals += 1

    is_newsletter = signals >= 2
    return CandidateRecommendation(
        sender_address=sender_address,
        sender_name=sender_name,
        is_newsletter=is_newsletter,
        sample_subjects=subjects[:3],
        example_message_ids=message_ids[:3],
        observed_frequency="daily" if message_count_last_7_days >= 5 else "weekly",
        recommended_stale_days=2 if is_newsletter else 7,
        recommended_action="trash" if category == "promotions" else "archive",
        risk_level="low" if category == "promotions" else "medium",
    )
