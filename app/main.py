from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import APP_NAME, APP_VERSION, Settings
from .db import get_engine, init_db
from .services.scheduler import start_scheduler
from .web import router as web_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.settings = getattr(app.state, "settings", Settings.from_env())
    app.state.engine = get_engine()
    init_db()
    app.state.scheduler = start_scheduler(app)
    try:
        yield
    finally:
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is not None:
            scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = Settings.from_env()
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
    app.state.settings = settings

    @app.get("/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(web_router)

    return app
