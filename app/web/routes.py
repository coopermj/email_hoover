from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import Settings
from app.db import get_session
from app.gmail.client import GmailClient
from app.models.candidate import Candidate
from app.models.rule import CleanupRule
from app.models.run_log import RunLog
from app.services.executor import run_cleanup_once
from app.services.rules import approve_candidate, mark_candidate_postponed, mark_candidate_rejected


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _read_gmail_token() -> str:
    return Settings.from_env().gmail_token_path.read_text(encoding="utf-8").strip()


async def get_gmail_client() -> AsyncGenerator[GmailClient, None]:
    gmail_client = GmailClient(Settings.from_env(), token_getter=_read_gmail_token)
    try:
        yield gmail_client
    finally:
        await gmail_client.aclose()


def list_pending_candidates(session: Session) -> list[Candidate]:
    statement = (
        select(Candidate)
        .where(Candidate.status == "pending")
        .order_by(Candidate.sender_name, Candidate.sender_address)
    )
    return list(session.exec(statement).all())


def list_rules(session: Session) -> list[CleanupRule]:
    statement = (
        select(CleanupRule)
        .where(CleanupRule.enabled.is_(True), CleanupRule.pause_reason.is_(None))
        .order_by(CleanupRule.sender_name, CleanupRule.sender_address)
    )
    return list(session.exec(statement).all())


def list_recent_runs(session: Session) -> list[RunLog]:
    statement = select(RunLog).order_by(RunLog.started_at.desc(), RunLog.id.desc()).limit(10)
    return list(session.exec(statement).all())


def list_paused_rules(session: Session) -> list[CleanupRule]:
    statement = (
        select(CleanupRule)
        .where(CleanupRule.pause_reason.is_not(None))
        .order_by(CleanupRule.sender_name, CleanupRule.sender_address)
    )
    return list(session.exec(statement).all())


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "candidates": list_pending_candidates(session),
            "rules": list_rules(session),
            "recent_runs": list_recent_runs(session),
            "exceptions": list_paused_rules(session),
        },
    )


@router.post("/rules/approve")
def approve_rule(
    candidate_id: int = Form(...),
    stale_days: int = Form(...),
    action: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    approve_candidate(session, candidate_id, stale_days=stale_days, action=action)
    return RedirectResponse("/", status_code=303)


@router.post("/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    mark_candidate_rejected(session, candidate_id)
    return RedirectResponse("/", status_code=303)


@router.post("/candidates/{candidate_id}/postpone")
def postpone_candidate(candidate_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    mark_candidate_postponed(session, candidate_id)
    return RedirectResponse("/", status_code=303)


@router.post("/runs/execute")
async def run_cleanup(
    session: Session = Depends(get_session),
    gmail_client: GmailClient = Depends(get_gmail_client),
) -> RedirectResponse:
    await run_cleanup_once(session, gmail_client, triggered_by="manual", dry_run=False)
    return RedirectResponse("/", status_code=303)
