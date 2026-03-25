import asyncio
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.gmail.client import GmailClient
from app.models.rule import CleanupRule
from app.models.run_log import RunLog
from app.services.rules import preview_rule_matches


@dataclass(slots=True)
class RunSummary:
    rules_ran: int = 0
    failed_rules: int = 0
    paused_rules: int = 0
    messages_acted_on: int = 0
    planned_actions: int = 0
    errors: list[str] = field(default_factory=list)


async def run_cleanup_once(
    session: Session,
    gmail_client: GmailClient,
    triggered_by: str,
    dry_run: bool = False,
    max_matches_per_rule: int = 100,
) -> RunSummary:
    summary = RunSummary()
    rules = session.exec(select(CleanupRule)).all()

    for rule in rules:
        if not rule.can_run(triggered_by=triggered_by):
            continue

        summary.rules_ran += 1
        try:
            matches = await preview_rule_matches(session, gmail_client, rule.id)
            if len(matches) > max_matches_per_rule:
                _pause_rule(
                    session,
                    rule,
                    reason="volume_spike",
                    triggered_by=triggered_by,
                    matched_count=len(matches),
                )
                summary.paused_rules += 1
                summary.errors.append("volume_spike")
                continue

            for match in matches:
                if _already_processed(session, rule.id, match.message_id):
                    continue

                if dry_run:
                    session.add(
                        RunLog(
                            rule_id=rule.id,
                            message_id=match.message_id,
                            action=f"dry_run:{rule.action}",
                            trigger=triggered_by,
                            triggered_by=triggered_by,
                            status="planned",
                            matched_count=1,
                        )
                    )
                    summary.planned_actions += 1
                    continue

                await _apply_with_retry(gmail_client, match.message_id, rule.action)
                session.add(
                    RunLog(
                        rule_id=rule.id,
                        message_id=match.message_id,
                        action=rule.action,
                        trigger=triggered_by,
                        triggered_by=triggered_by,
                        status="completed",
                        matched_count=1,
                        actioned_count=1,
                    )
                )
                summary.messages_acted_on += 1
        except Exception as exc:
            _pause_rule(
                session,
                rule,
                reason="retry_exhausted",
                triggered_by=triggered_by,
                error_message=str(exc),
            )
            summary.failed_rules += 1
            summary.paused_rules += 1
            summary.errors.append(f"retry_exhausted:{exc}")

    session.commit()
    return summary


def _already_processed(session: Session, rule_id: int, message_id: str) -> bool:
    existing = session.exec(
        select(RunLog).where(
            RunLog.rule_id == rule_id,
            RunLog.message_id == message_id,
            RunLog.action.in_(("archive", "trash")),
        )
    ).first()
    return existing is not None


async def _apply_with_retry(
    gmail_client: GmailClient,
    message_id: str,
    action: str,
    *,
    attempts: int = 3,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await _dispatch_action(gmail_client, message_id, action)
            return
        except Exception as exc:  # pragma: no cover - covered via caller behavior
            last_error = exc
            if attempt == attempts:
                break
            await asyncio.sleep(0)

    assert last_error is not None
    raise last_error


async def _dispatch_action(gmail_client: GmailClient, message_id: str, action: str) -> None:
    if action == "archive":
        await gmail_client.archive_message(message_id)
        return
    if action == "trash":
        await gmail_client.trash_message(message_id)
        return
    msg = f"Unsupported Gmail cleanup action: {action}"
    raise ValueError(msg)


def _pause_rule(
    session: Session,
    rule: CleanupRule,
    *,
    reason: str,
    triggered_by: str,
    matched_count: int = 0,
    error_message: str | None = None,
) -> None:
    rule.pause_reason = reason
    session.add(rule)
    session.add(
        RunLog(
            rule_id=rule.id,
            action="paused",
            trigger=triggered_by,
            triggered_by=triggered_by,
            status="paused",
            matched_count=matched_count,
            error_message=reason if error_message is None else f"{reason}: {error_message}",
        )
    )
