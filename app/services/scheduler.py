from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from sqlmodel import Session

from app.config import Settings
from app.gmail.auth import AuthState
from app.gmail.client import GmailClient
from app.gmail.oauth import read_gmail_access_token
from app.services.executor import RunSummary, is_auth_failure, run_cleanup_once


AUTH_RECONNECT_MESSAGE = "Reconnect Gmail to resume scheduled cleanup."


def build_gmail_client(settings: Settings) -> GmailClient:
    return GmailClient(
        settings,
        token_getter=lambda: read_gmail_access_token(settings.gmail_token_path),
    )


async def run_scheduled_cleanup(app: FastAPI) -> RunSummary:
    gmail_client = build_gmail_client(app.state.settings)
    try:
        with Session(app.state.engine) as session:
            return await run_cleanup_once(
                session,
                gmail_client,
                triggered_by="scheduled",
                dry_run=False,
            )
    except Exception as exc:
        if is_auth_failure(exc):
            pause_cleanup_job(app, auth_failed=True)
        raise
    finally:
        await gmail_client.aclose()


def pause_cleanup_job(app: FastAPI, *, auth_failed: bool = False) -> None:
    app.state.cleanup_job_auth_failed = auth_failed
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        return
    try:
        scheduler.pause_job("cleanup")
    except JobLookupError:
        return


def resume_cleanup_job(app: FastAPI) -> None:
    app.state.cleanup_job_auth_failed = False
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        return
    try:
        scheduler.resume_job("cleanup")
    except JobLookupError:
        return


def start_scheduler(app: FastAPI) -> AsyncIOScheduler:
    app.state.cleanup_job_auth_failed = False
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_scheduled_cleanup,
        "cron",
        hour=6,
        minute=0,
        id="cleanup",
        args=[app],
    )
    if not AuthState.from_disk(app.state.settings).connected:
        app.state.cleanup_job_auth_failed = True
        scheduler.pause_job("cleanup")
    scheduler.start()
    return scheduler
