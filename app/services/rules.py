from sqlmodel import Session

from app.models.candidate import Candidate
from app.models.rule import CleanupRule


def approve_candidate(
    session: Session,
    candidate_id: int,
    *,
    stale_days: int,
    action: str,
) -> CleanupRule:
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        msg = f"Candidate {candidate_id} does not exist"
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
