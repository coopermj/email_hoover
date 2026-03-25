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


def approve_candidate(
    session: Session,
    candidate_id: int,
    *,
    stale_days: int,
    action: str,
) -> CleanupRule:
    candidate = _get_candidate(session, candidate_id)
    existing_rule = session.exec(
        select(CleanupRule).where(CleanupRule.sender_address == candidate.sender_address)
    ).first()
    if existing_rule is not None:
        msg = f"Sender {candidate.sender_address} already has a cleanup rule"
        raise ValueError(msg)

    rule = CleanupRule(
        sender_address=candidate.sender_address,
        sender_name=candidate.sender_name,
        stale_days=stale_days,
        action=action,
    )
    candidate.status = "approved"
    session.add(rule)
    session.add(candidate)
    session.commit()
    session.refresh(rule)
    return rule


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


def update_rule(session: Session, rule_id: int, *, stale_days: int, action: str) -> CleanupRule:
    rule = _get_rule(session, rule_id)
    rule.stale_days = stale_days
    rule.action = action
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


async def preview_rule_matches(
    session: Session,
    gmail_client: GmailClient,
    rule_id: int,
) -> list[RulePreviewMatch]:
    rule = _get_rule(session, rule_id)
    query = f"from:{rule.sender_address} older_than:{rule.stale_days}d"
    matches = await gmail_client.preview_matches(query, action=rule.action)
    return [
        match
        if isinstance(match, RulePreviewMatch)
        else RulePreviewMatch(**match)
        for match in matches
    ]
