from fastapi.testclient import TestClient
import pytest

from app import create_app
from app.db import get_engine


def test_healthcheck_returns_ok():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_starts_paused_scheduler_when_gmail_auth_is_disconnected(
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "health.db"
    token_path = tmp_path / "missing-token.json"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", str(token_path))
    get_engine.cache_clear()

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        cleanup_job = client.app.state.scheduler.get_job("cleanup")
        assert cleanup_job is not None
        assert cleanup_job.next_run_time is None

    get_engine.cache_clear()
