from sqlmodel import Session, select

from app.gmail.client import GmailClient, RulePreviewMatch
from app.models.candidate import Candidate
from app.models.rule import CleanupRule


def _get_candidate(session: Session, candidate_id: int) -> Candidate:
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        msg = f"Candidate {candidate_id} does not exist"
        raise ValueError(msg)
    return candidate


def _get_rule(session: Session, rule_id: int) -> CleanupRule:
    rule = session.get(CleanupRule, rule_id)
    if rule is None:
        msg = f"Rule {rule_id} does not exist"
        raise ValueError(msg)
    return rule


def _find_existing_rule_by_sender(session: Session, sender_address: str) -> CleanupRule | None:
    return session.exec(
        select(CleanupRule).where(CleanupRule.sender_address == sender_address)
    ).first()


def _mark_matching_pending_candidates_approved(session: Session, sender_address: str) -> None:
    candidates = session.exec(
        select(Candidate).where(
            Candidate.sender_address == sender_address,
            Candidate.status == "pending",
        )
    ).all()
    for candidate in candidates:
        candidate.status = "approved"
        session.add(candidate)


def create_rule(
    session: Session,
    *,
    sender_address: str,
    sender_name: str,
    stale_days: int,
    action: str,
    enabled: bool = True,
    schedule_enabled: bool = True,
    candidate_id: int | None = None,
) -> CleanupRule:
    existing_rule = _find_existing_rule_by_sender(session, sender_address)
    if existing_rule is not None:
        msg = f"Sender {sender_address} already has a cleanup rule"
        raise ValueError(msg)

    candidate: Candidate | None = None
    if candidate_id is not None:
        candidate = _get_candidate(session, candidate_id)

    rule = CleanupRule(
        sender_address=sender_address,
        sender_name=sender_name,
        stale_days=stale_days,
        action=action,
        enabled=enabled,
        schedule_enabled=schedule_enabled,
    )
    session.add(rule)
    if candidate is not None:
        candidate.status = "approved"
        session.add(candidate)
    _mark_matching_pending_candidates_approved(session, sender_address)
    session.commit()
    session.refresh(rule)
    return rule


def approve_candidate(
    session: Session,
    candidate_id: int,
    *,
    stale_days: int,
    action: str,
) -> CleanupRule:
    candidate = _get_candidate(session, candidate_id)
    return create_rule(
        session,
        sender_address=candidate.sender_address,
        sender_name=candidate.sender_name,
        stale_days=stale_days,
        action=action,
        candidate_id=candidate.id,
    )


def mark_candidate_rejected(session: Session, candidate_id: int) -> Candidate:
    candidate = _get_candidate(session, candidate_id)
    candidate.status = "rejected"
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def mark_candidate_postponed(session: Session, candidate_id: int) -> Candidate:
    candidate = _get_candidate(session, candidate_id)
    candidate.status = "postponed"
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def update_rule(
    session: Session,
    rule_id: int,
    *,
    sender_address: str | None = None,
    sender_name: str | None = None,
    stale_days: int,
    action: str,
    enabled: bool | None = None,
    schedule_enabled: bool | None = None,
) -> CleanupRule:
    rule = _get_rule(session, rule_id)
    if sender_address is not None and sender_address != rule.sender_address:
        existing_rule = _find_existing_rule_by_sender(session, sender_address)
        if existing_rule is not None and existing_rule.id != rule.id:
            msg = f"Sender {sender_address} already has a cleanup rule"
            raise ValueError(msg)
        rule.sender_address = sender_address
    if sender_name is not None:
        rule.sender_name = sender_name
    rule.stale_days = stale_days
    rule.action = action
    if enabled is not None:
        rule.enabled = enabled
        if enabled:
            rule.pause_reason = None
    if schedule_enabled is not None:
        rule.schedule_enabled = schedule_enabled
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def disable_rule(session: Session, rule_id: int) -> CleanupRule:
    rule = _get_rule(session, rule_id)
    rule.enabled = False
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def enable_rule(session: Session, rule_id: int) -> CleanupRule:
    rule = _get_rule(session, rule_id)
    rule.enabled = True
    rule.pause_reason = None
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


async def preview_rule_matches(
    session: Session,
    gmail_client: GmailClient,
    rule_id: int,
) -> list[RulePreviewMatch]:
    rule = _get_rule(session, rule_id)
    query = f"from:{rule.sender_address}"
    if rule.stale_days > 0:
        query = f"{query} older_than:{rule.stale_days}d"
    matches = await gmail_client.preview_matches(query, action=rule.action)
    return [
        match
        if isinstance(match, RulePreviewMatch)
        else RulePreviewMatch(**match)
        for match in matches
    ]
