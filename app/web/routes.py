from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import Settings
from app.db import get_session
from app.gmail.auth import AuthState
from app.gmail.client import GmailClient
from app.gmail.oauth import (
    build_google_authorization_redirect,
    create_oauth_state_token,
    exchange_google_code,
    write_gmail_credentials,
)
from app.models.candidate import Candidate
from app.models.rule import CleanupRule
from app.models.run_log import RunLog
from app.services.executor import is_auth_failure
from app.services.scheduler import (
    AUTH_RECONNECT_MESSAGE,
    build_gmail_client,
    pause_cleanup_job,
    resume_cleanup_job,
)
from app.services.executor import run_cleanup_once
from app.services.rules import approve_candidate, mark_candidate_postponed, mark_candidate_rejected


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
ALLOWED_ACTIONS = {"archive", "trash"}
OAUTH_STATE_COOKIE = "email_hoover_oauth_state"


@dataclass(slots=True)
class ActivityEntry:
    heading: str
    status: str
    detail: str


async def get_gmail_client() -> AsyncGenerator[GmailClient, None]:
    gmail_client = build_gmail_client(Settings.from_env())
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


def list_recent_activity(session: Session) -> list[ActivityEntry]:
    return [_to_activity_entry(run) for run in list_recent_runs(session)]


def list_paused_rules(session: Session) -> list[CleanupRule]:
    statement = (
        select(CleanupRule)
        .where(CleanupRule.pause_reason.is_not(None))
        .order_by(CleanupRule.sender_name, CleanupRule.sender_address)
    )
    return list(session.exec(statement).all())


def _to_activity_entry(run: RunLog) -> ActivityEntry:
    if run.status == "completed":
        action_count = run.actioned_count or 1
        noun = "action" if action_count == 1 else "actions"
        message_suffix = f" for message {run.message_id}" if run.message_id else ""
        return ActivityEntry(
            heading=f"{run.triggered_by} cleanup event",
            status="completed",
            detail=f"{action_count} {noun} applied via {run.action}{message_suffix}.",
        )
    if run.status == "planned":
        match_count = run.matched_count or 1
        noun = "message" if match_count == 1 else "messages"
        return ActivityEntry(
            heading=f"{run.triggered_by} dry-run event",
            status="planned",
            detail=f"{match_count} {noun} queued for {run.action}.",
        )
    if run.status == "paused":
        reason = run.error_message or "pause requested"
        return ActivityEntry(
            heading=f"{run.triggered_by} pause event",
            status="paused",
            detail=f"Rule paused because {reason}.",
        )
    if run.status == "error":
        return ActivityEntry(
            heading=f"{run.triggered_by} error event",
            status="error",
            detail=run.error_message or "Cleanup failed before an action was applied.",
        )
    return ActivityEntry(
        heading=f"{run.triggered_by} cleanup event",
        status=run.status,
        detail=f"Recorded action {run.action}.",
    )


def _redirect_with_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/?{urlencode({'error': message})}", status_code=303)


def _validate_approval_inputs(stale_days: int, action: str) -> str | None:
    if stale_days < 1:
        return "Stale days must be at least 1."
    if action not in ALLOWED_ACTIONS:
        return "Action must be archive or trash."
    return None


def _scheduler_status_message(request: Request) -> str | None:
    explicit_error = request.query_params.get("error")
    if explicit_error is not None:
        return explicit_error

    settings = getattr(request.app.state, "settings", Settings.from_env())
    auth_state = AuthState.from_disk(settings)
    scheduler = getattr(request.app.state, "scheduler", None)
    cleanup_job = scheduler.get_job("cleanup") if scheduler is not None else None
    scheduler_paused = cleanup_job is not None and cleanup_job.next_run_time is None
    auth_failed = getattr(request.app.state, "cleanup_job_auth_failed", False)

    if not auth_state.connected:
        return AUTH_RECONNECT_MESSAGE
    if scheduler_paused and auth_failed:
        return AUTH_RECONNECT_MESSAGE
    if scheduler_paused:
        return "Scheduled cleanup is paused."
    return None


def _auth_reconnect_message() -> str:
    return AUTH_RECONNECT_MESSAGE


def _oauth_start_url(settings: Settings) -> str:
    redirect = urlsplit(settings.google_redirect_uri)
    if not redirect.scheme or not redirect.netloc:
        return "/auth/google/start"
    return f"{redirect.scheme}://{redirect.netloc}/auth/google/start"


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = getattr(request.app.state, "settings", Settings.from_env())
    auth_state = AuthState.from_disk(settings)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "candidates": list_pending_candidates(session),
            "rules": list_rules(session),
            "recent_activity": list_recent_activity(session),
            "exceptions": list_paused_rules(session),
            "auth_connected": auth_state.connected,
            "oauth_start_url": _oauth_start_url(settings),
            "error_message": _scheduler_status_message(request),
        },
    )


@router.get("/auth/google/start")
def start_google_oauth(request: Request) -> RedirectResponse:
    settings = getattr(request.app.state, "settings", Settings.from_env())
    state_token = create_oauth_state_token()
    try:
        redirect_url = build_google_authorization_redirect(settings, state_token)
    except (ValueError, FileNotFoundError) as exc:
        return _redirect_with_error(str(exc))
    response = RedirectResponse(redirect_url)
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state_token,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/auth/google/callback")
def complete_google_oauth(request: Request) -> RedirectResponse:
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    returned_state = request.query_params.get("state")
    if expected_state is None or returned_state != expected_state:
        return _redirect_with_error("Google OAuth state validation failed.")

    provider_error = request.query_params.get("error")
    if provider_error is not None:
        return _redirect_with_error(f"Google OAuth failed: {provider_error}")

    code = request.query_params.get("code")
    if not code:
        return _redirect_with_error("Google OAuth callback did not include an authorization code.")

    settings = getattr(request.app.state, "settings", Settings.from_env())
    try:
        credential_payload = exchange_google_code(settings, code)
        write_gmail_credentials(settings.gmail_token_path, credential_payload)
    except (ValueError, OSError) as exc:
        return _redirect_with_error(str(exc))

    resume_cleanup_job(request.app)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    return response


@router.post("/rules/approve")
def approve_rule(
    candidate_id: int = Form(...),
    stale_days: int = Form(...),
    action: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    validation_error = _validate_approval_inputs(stale_days, action)
    if validation_error is not None:
        return _redirect_with_error(validation_error)
    try:
        approve_candidate(session, candidate_id, stale_days=stale_days, action=action)
    except ValueError as exc:
        return _redirect_with_error(str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    try:
        mark_candidate_rejected(session, candidate_id)
    except ValueError as exc:
        return _redirect_with_error(str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/candidates/{candidate_id}/postpone")
def postpone_candidate(candidate_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    try:
        mark_candidate_postponed(session, candidate_id)
    except ValueError as exc:
        return _redirect_with_error(str(exc))
    return RedirectResponse("/", status_code=303)


@router.post("/runs/execute")
async def run_cleanup(
    request: Request,
    session: Session = Depends(get_session),
    gmail_client: GmailClient = Depends(get_gmail_client),
) -> RedirectResponse:
    try:
        await run_cleanup_once(session, gmail_client, triggered_by="manual", dry_run=False)
    except Exception as exc:
        if is_auth_failure(exc):
            pause_cleanup_job(request.app, auth_failed=True)
            return _redirect_with_error(_auth_reconnect_message())
        if not isinstance(exc, ValueError):
            raise
        return _redirect_with_error(str(exc))
    return RedirectResponse("/", status_code=303)
