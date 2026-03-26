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
    build_google_oauth_start,
    create_oauth_state_token,
    exchange_google_code,
    write_gmail_credentials,
)
from app.models.candidate import Candidate
from app.models.rule import CleanupRule
from app.models.run_log import RunLog
from app.services.discovery import discover_newsletter_candidates
from app.services.executor import is_auth_failure
from app.services.scheduler import (
    AUTH_RECONNECT_MESSAGE,
    build_gmail_client,
    pause_cleanup_job,
    resume_cleanup_job,
)
from app.services.executor import run_cleanup_once
from app.services.rules import (
    approve_candidate,
    create_rule,
    disable_rule,
    enable_rule,
    mark_candidate_postponed,
    mark_candidate_rejected,
    update_rule,
)


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
ALLOWED_ACTIONS = {"archive", "trash"}
OAUTH_STATE_COOKIE = "email_hoover_oauth_state"
OAUTH_CODE_VERIFIER_COOKIE = "email_hoover_oauth_code_verifier"
ALLOWED_TABS = {"candidates", "rules", "activity", "exceptions"}


@dataclass(slots=True)
class ActivityEntry:
    heading: str
    status: str
    detail: str


@dataclass(slots=True)
class WorkspaceRow:
    row_type: str
    sender_name: str
    sender_address: str
    stale_days: int
    action: str
    enabled: bool
    schedule_enabled: bool
    state_label: str
    state_tone: str
    recommendation: str
    editor_token: str
    rule_id: int | None = None
    candidate_id: int | None = None
    is_selected: bool = False


@dataclass(slots=True)
class RuleEditorState:
    title: str
    subtitle: str
    submit_url: str
    submit_label: str
    sender_name: str
    sender_address: str
    stale_days: int
    action: str
    enabled: bool
    schedule_enabled: bool
    candidate_id: int | None = None


async def get_gmail_client() -> AsyncGenerator[GmailClient, None]:
    gmail_client = build_gmail_client(Settings.from_env())
    try:
        yield gmail_client
    finally:
        await gmail_client.aclose()


def list_pending_candidates(session: Session) -> list[Candidate]:
    ruled_senders = select(CleanupRule.sender_address)
    statement = (
        select(Candidate)
        .where(
            Candidate.status == "pending",
            Candidate.sender_address.not_in(ruled_senders),
        )
        .order_by(Candidate.sender_name, Candidate.sender_address)
    )
    return list(session.exec(statement).all())


def list_rules(session: Session) -> list[CleanupRule]:
    statement = (
        select(CleanupRule)
        .where(CleanupRule.pause_reason.is_(None))
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
    if run.status == "noop":
        return ActivityEntry(
            heading=f"{run.triggered_by} cleanup event",
            status="noop",
            detail=run.error_message or "Cleanup completed with no changes.",
        )
    if run.status == "completed":
        message_detail = run.error_message
        if message_detail:
            return ActivityEntry(
                heading=f"{run.triggered_by} cleanup event",
                status="completed",
                detail=f"{run.action} applied to {message_detail}.",
            )
        action_count = run.actioned_count or 1
        noun = "action" if action_count == 1 else "actions"
        message_suffix = f" for message {run.message_id}" if run.message_id else ""
        return ActivityEntry(
            heading=f"{run.triggered_by} cleanup event",
            status="completed",
            detail=f"{action_count} {noun} applied via {run.action}{message_suffix}.",
        )
    if run.status == "planned":
        if run.error_message:
            return ActivityEntry(
                heading=f"{run.triggered_by} dry-run event",
                status="planned",
                detail=f"{run.action} queued for {run.error_message}.",
            )
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


def _dashboard_location(
    *,
    tab: str | None = None,
    editor: str | None = None,
    message: str | None = None,
    error: str | None = None,
) -> str:
    query: dict[str, str] = {}
    if tab in ALLOWED_TABS:
        query["tab"] = tab
    if editor:
        query["editor"] = editor
    if message is not None:
        query["message"] = message
    if error is not None:
        query["error"] = error
    if not query:
        return "/"
    return f"/?{urlencode(query)}"


def _redirect_with_error(message: str, *, tab: str | None = None, editor: str | None = None) -> RedirectResponse:
    return RedirectResponse(
        _dashboard_location(tab=tab, editor=editor, error=message),
        status_code=303,
    )


def _redirect_with_message(message: str, *, tab: str | None = None, editor: str | None = None) -> RedirectResponse:
    return RedirectResponse(
        _dashboard_location(tab=tab, editor=editor, message=message),
        status_code=303,
    )


def _validate_rule_inputs(sender_address: str, stale_days: int, action: str) -> str | None:
    if not sender_address.strip():
        return "Sender email is required."
    if stale_days < 0:
        return "Stale days must be at least 0."
    if action not in ALLOWED_ACTIONS:
        return "Action must be archive or trash."
    return None


def _validate_approval_inputs(stale_days: int, action: str) -> str | None:
    return _validate_rule_inputs("candidate@example.com", stale_days, action)


def _checkbox_checked(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "off", "no"}


def _banner_context(request: Request) -> tuple[str | None, str]:
    explicit_message = request.query_params.get("message")
    if explicit_message is not None:
        return explicit_message, "info"

    explicit_error = request.query_params.get("error")
    if explicit_error is not None:
        return explicit_error, "error"

    settings = getattr(request.app.state, "settings", Settings.from_env())
    auth_state = AuthState.from_disk(settings)
    scheduler = getattr(request.app.state, "scheduler", None)
    cleanup_job = scheduler.get_job("cleanup") if scheduler is not None else None
    scheduler_paused = cleanup_job is not None and cleanup_job.next_run_time is None
    auth_failed = getattr(request.app.state, "cleanup_job_auth_failed", False)

    if not auth_state.connected:
        return AUTH_RECONNECT_MESSAGE, "error"
    if scheduler_paused and auth_failed:
        return AUTH_RECONNECT_MESSAGE, "error"
    if scheduler_paused:
        return "Scheduled cleanup is paused.", "info"
    return None, "info"


def _auth_reconnect_message() -> str:
    return AUTH_RECONNECT_MESSAGE


def _oauth_start_url(settings: Settings) -> str:
    redirect = urlsplit(settings.google_redirect_uri)
    if not redirect.scheme or not redirect.netloc:
        return "/auth/google/start"
    return f"{redirect.scheme}://{redirect.netloc}/auth/google/start"


def _active_tab(request: Request) -> str:
    requested = request.query_params.get("tab", "candidates")
    if requested in ALLOWED_TABS:
        return requested
    return "candidates"


def _editor_token_for_tab(active_tab: str, editor_token: str) -> str:
    if active_tab == "candidates" and editor_token.startswith("rule:"):
        return "create"
    if active_tab == "rules" and editor_token.startswith("candidate:"):
        return "create"
    if active_tab in {"activity", "exceptions"}:
        return "create"
    return editor_token


def _build_workspace_rows(
    session: Session,
    *,
    selected_editor: str,
    include_candidates: bool = True,
    include_rules: bool = True,
) -> list[WorkspaceRow]:
    rows: list[WorkspaceRow] = []
    if include_candidates:
        for candidate in list_pending_candidates(session):
            editor_token = f"candidate:{candidate.id}"
            rows.append(
                WorkspaceRow(
                    row_type="candidate",
                    sender_name=candidate.sender_name,
                    sender_address=candidate.sender_address,
                    stale_days=candidate.recommended_stale_days,
                    action=candidate.recommended_action,
                    enabled=True,
                    schedule_enabled=True,
                    state_label="candidate",
                    state_tone="candidate",
                    recommendation=(
                        f"Suggested: {candidate.recommended_action} after "
                        f"{candidate.recommended_stale_days} days."
                    ),
                    editor_token=editor_token,
                    candidate_id=candidate.id,
                    is_selected=selected_editor == editor_token,
                )
            )
    if include_rules:
        for rule in list_rules(session):
            editor_token = f"rule:{rule.id}"
            state_label = "enabled" if rule.enabled else "disabled"
            rows.append(
                WorkspaceRow(
                    row_type="rule",
                    sender_name=rule.sender_name,
                    sender_address=rule.sender_address,
                    stale_days=rule.stale_days,
                    action=rule.action,
                    enabled=rule.enabled,
                    schedule_enabled=rule.schedule_enabled,
                    state_label=state_label,
                    state_tone=state_label,
                    recommendation=f"{rule.action} messages older than {rule.stale_days} days.",
                    editor_token=editor_token,
                    rule_id=rule.id,
                    is_selected=selected_editor == editor_token,
                )
            )
    rows.sort(key=lambda row: (row.sender_name.lower(), row.sender_address.lower(), row.row_type))
    return rows


def _candidate_for_editor(session: Session, candidate_id: str) -> Candidate:
    candidate = session.get(Candidate, int(candidate_id))
    if candidate is None:
        msg = f"Candidate {candidate_id} does not exist"
        raise ValueError(msg)
    return candidate


def _rule_for_editor(session: Session, rule_id: str) -> CleanupRule:
    rule = session.get(CleanupRule, int(rule_id))
    if rule is None:
        msg = f"Rule {rule_id} does not exist"
        raise ValueError(msg)
    return rule


def _build_rule_editor(session: Session, editor_token: str) -> RuleEditorState:
    if editor_token.startswith("candidate:"):
        candidate = _candidate_for_editor(session, editor_token.removeprefix("candidate:"))
        return RuleEditorState(
            title="Create Rule From Candidate",
            subtitle="Start from the discovered sender and adjust it before saving.",
            submit_url="/rules/create",
            submit_label="Create Rule",
            sender_name=candidate.sender_name,
            sender_address=candidate.sender_address,
            stale_days=candidate.recommended_stale_days,
            action=candidate.recommended_action,
            enabled=True,
            schedule_enabled=True,
            candidate_id=candidate.id,
        )
    if editor_token.startswith("rule:"):
        rule = _rule_for_editor(session, editor_token.removeprefix("rule:"))
        return RuleEditorState(
            title="Edit Rule",
            subtitle="Adjust sender settings, automation state, and schedule behavior.",
            submit_url=f"/rules/{rule.id}/update",
            submit_label="Save Rule",
            sender_name=rule.sender_name,
            sender_address=rule.sender_address,
            stale_days=rule.stale_days,
            action=rule.action,
            enabled=rule.enabled,
            schedule_enabled=rule.schedule_enabled,
        )
    return RuleEditorState(
        title="Create Rule",
        subtitle="Add a sender rule directly when discovery has not surfaced it yet.",
        submit_url="/rules/create",
        submit_label="Create Rule",
        sender_name="",
        sender_address="",
        stale_days=2,
        action="trash",
        enabled=True,
        schedule_enabled=True,
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = getattr(request.app.state, "settings", Settings.from_env())
    auth_state = AuthState.from_disk(settings)
    active_tab = _active_tab(request)
    editor_token = _editor_token_for_tab(active_tab, request.query_params.get("editor", "create"))
    try:
        rule_editor = _build_rule_editor(session, editor_token)
    except ValueError as exc:
        rule_editor = _build_rule_editor(session, "create")
        editor_token = "create"
        banner_message = str(exc)
        banner_tone = "error"
    else:
        banner_message, banner_tone = _banner_context(request)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "candidates": list_pending_candidates(session),
            "workspace_rows": _build_workspace_rows(
                session,
                selected_editor=editor_token,
                include_candidates=active_tab == "candidates",
                include_rules=active_tab == "rules",
            ),
            "rules": list_rules(session),
            "rule_editor": rule_editor,
            "recent_activity": list_recent_activity(session),
            "exceptions": list_paused_rules(session),
            "active_tab": active_tab,
            "auth_connected": auth_state.connected,
            "oauth_start_url": _oauth_start_url(settings),
            "banner_message": banner_message,
            "banner_tone": banner_tone,
        },
    )


@router.get("/auth/google/start")
def start_google_oauth(request: Request) -> RedirectResponse:
    settings = getattr(request.app.state, "settings", Settings.from_env())
    state_token = create_oauth_state_token()
    try:
        oauth_start = build_google_oauth_start(settings, state_token)
    except (ValueError, FileNotFoundError) as exc:
        return _redirect_with_error(str(exc))
    response = RedirectResponse(oauth_start.authorization_url)
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state_token,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        OAUTH_CODE_VERIFIER_COOKIE,
        oauth_start.code_verifier,
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

    code_verifier = request.cookies.get(OAUTH_CODE_VERIFIER_COOKIE)
    if not code_verifier:
        return _redirect_with_error("Google OAuth PKCE verifier is missing.")

    settings = getattr(request.app.state, "settings", Settings.from_env())
    try:
        credential_payload = exchange_google_code(settings, code, code_verifier)
        write_gmail_credentials(settings.gmail_token_path, credential_payload)
    except (ValueError, OSError) as exc:
        return _redirect_with_error(str(exc))

    resume_cleanup_job(request.app)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.delete_cookie(OAUTH_CODE_VERIFIER_COOKIE)
    return response


@router.post("/rules/approve")
def approve_rule(
    candidate_id: int = Form(...),
    stale_days: int = Form(...),
    action: str = Form(...),
    return_tab: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    validation_error = _validate_approval_inputs(stale_days, action)
    if validation_error is not None:
        return _redirect_with_error(validation_error, tab=return_tab)
    try:
        approve_candidate(session, candidate_id, stale_days=stale_days, action=action)
    except ValueError as exc:
        return _redirect_with_error(str(exc), tab=return_tab)
    return RedirectResponse(_dashboard_location(tab=return_tab), status_code=303)


@router.post("/rules/create")
def create_rule_action(
    sender_address: str = Form(""),
    sender_name: str = Form(""),
    stale_days: int = Form(...),
    action: str = Form(...),
    enabled: str | None = Form(default=None),
    schedule_enabled: str | None = Form(default=None),
    candidate_id: int | None = Form(default=None),
    return_tab: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    sender_address = sender_address.strip()
    sender_name = sender_name.strip() or sender_address
    validation_error = _validate_rule_inputs(sender_address, stale_days, action)
    if validation_error is not None:
        return _redirect_with_error(validation_error, tab=return_tab)
    try:
        create_rule(
            session,
            sender_address=sender_address,
            sender_name=sender_name,
            stale_days=stale_days,
            action=action,
            enabled=_checkbox_checked(enabled) if enabled is not None else True,
            schedule_enabled=(
                _checkbox_checked(schedule_enabled) if schedule_enabled is not None else True
            ),
            candidate_id=candidate_id,
        )
    except ValueError as exc:
        return _redirect_with_error(str(exc), tab=return_tab)
    return RedirectResponse(_dashboard_location(tab=return_tab), status_code=303)


@router.post("/rules/{rule_id}/update")
def update_rule_action(
    rule_id: int,
    sender_address: str = Form(""),
    sender_name: str = Form(""),
    stale_days: int = Form(...),
    action: str = Form(...),
    enabled: str | None = Form(default=None),
    schedule_enabled: str | None = Form(default=None),
    return_tab: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    sender_address = sender_address.strip()
    sender_name = sender_name.strip() or sender_address
    validation_error = _validate_rule_inputs(sender_address, stale_days, action)
    if validation_error is not None:
        return _redirect_with_error(validation_error, tab=return_tab, editor=f"rule:{rule_id}")
    try:
        update_rule(
            session,
            rule_id,
            sender_address=sender_address,
            sender_name=sender_name,
            stale_days=stale_days,
            action=action,
            enabled=_checkbox_checked(enabled),
            schedule_enabled=_checkbox_checked(schedule_enabled),
        )
    except ValueError as exc:
        return _redirect_with_error(str(exc), tab=return_tab, editor=f"rule:{rule_id}")
    return RedirectResponse(
        _dashboard_location(tab=return_tab, editor=f"rule:{rule_id}" if return_tab == "rules" else None),
        status_code=303,
    )


@router.post("/rules/{rule_id}/disable")
def disable_rule_action(
    rule_id: int,
    return_tab: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    try:
        disable_rule(session, rule_id)
    except ValueError as exc:
        return _redirect_with_error(str(exc), tab=return_tab)
    return RedirectResponse(_dashboard_location(tab=return_tab), status_code=303)


@router.post("/rules/{rule_id}/enable")
def enable_rule_action(
    rule_id: int,
    return_tab: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    try:
        enable_rule(session, rule_id)
    except ValueError as exc:
        return _redirect_with_error(str(exc), tab=return_tab)
    return RedirectResponse(_dashboard_location(tab=return_tab), status_code=303)


@router.post("/candidates/{candidate_id}/reject")
def reject_candidate(
    candidate_id: int,
    return_tab: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    try:
        mark_candidate_rejected(session, candidate_id)
    except ValueError as exc:
        return _redirect_with_error(str(exc), tab=return_tab)
    return RedirectResponse(_dashboard_location(tab=return_tab), status_code=303)


@router.post("/candidates/{candidate_id}/postpone")
def postpone_candidate(
    candidate_id: int,
    return_tab: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    try:
        mark_candidate_postponed(session, candidate_id)
    except ValueError as exc:
        return _redirect_with_error(str(exc), tab=return_tab)
    return RedirectResponse(_dashboard_location(tab=return_tab), status_code=303)


@router.post("/candidates/discover")
async def discover_candidates(
    request: Request,
    session: Session = Depends(get_session),
    gmail_client: GmailClient = Depends(get_gmail_client),
    return_tab: str | None = Form(default=None),
) -> RedirectResponse:
    try:
        created = await discover_newsletter_candidates(session, gmail_client)
    except Exception as exc:
        if is_auth_failure(exc):
            pause_cleanup_job(request.app, auth_failed=True)
            return _redirect_with_error(_auth_reconnect_message(), tab=return_tab)
        if not isinstance(exc, ValueError):
            raise
        return _redirect_with_error(str(exc), tab=return_tab)
    if created == 0:
        return _redirect_with_message(
            "No likely newsletters were found in the current scan window.",
            tab=return_tab,
        )
    noun = "candidate" if created == 1 else "candidates"
    return _redirect_with_message(f"Found {created} newsletter {noun} to review.", tab=return_tab)


@router.post("/runs/execute")
async def run_cleanup(
    request: Request,
    session: Session = Depends(get_session),
    gmail_client: GmailClient = Depends(get_gmail_client),
    return_tab: str | None = Form(default=None),
) -> RedirectResponse:
    try:
        summary = await run_cleanup_once(session, gmail_client, triggered_by="manual", dry_run=False)
    except Exception as exc:
        if is_auth_failure(exc):
            pause_cleanup_job(request.app, auth_failed=True)
            return _redirect_with_error(_auth_reconnect_message(), tab=return_tab)
        if not isinstance(exc, ValueError):
            raise
        return _redirect_with_error(str(exc), tab=return_tab)
    if summary is None:
        return RedirectResponse(_dashboard_location(tab=return_tab), status_code=303)
    if summary.rules_ran == 0:
        return _redirect_with_message("No active cleanup rules to run.", tab=return_tab)
    if (
        summary.messages_acted_on == 0
        and summary.planned_actions == 0
        and summary.failed_rules == 0
        and summary.paused_rules == 0
    ):
        return _redirect_with_message("No stale messages matched the active rules.", tab=return_tab)
    return RedirectResponse(_dashboard_location(tab=return_tab), status_code=303)
