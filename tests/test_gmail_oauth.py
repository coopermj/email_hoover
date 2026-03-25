from pathlib import Path

import pytest

from app.config import Settings
from app.gmail.auth import AuthState
from app.gmail.oauth import (
    GoogleOAuthConfig,
    load_google_oauth_config,
    read_gmail_credentials,
    write_gmail_credentials,
)


def test_google_oauth_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8765/auth/google/callback")
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)

    settings = Settings.from_env()

    config = load_google_oauth_config(settings)

    assert config == GoogleOAuthConfig(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://127.0.0.1:8765/auth/google/callback",
        scopes=("https://www.googleapis.com/auth/gmail.modify",),
    )


def test_credential_store_round_trips_refreshable_token_payload(tmp_path: Path) -> None:
    path = tmp_path / "gmail-token.json"
    payload = {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    }

    write_gmail_credentials(path, payload)

    assert read_gmail_credentials(path) == payload


def test_auth_state_reports_reconnect_for_invalid_stored_credentials(tmp_path: Path) -> None:
    settings = Settings(gmail_token_path=tmp_path / "gmail-token.json")
    settings.gmail_token_path.write_text('{"token": "missing-refresh"}', encoding="utf-8")

    state = AuthState.from_disk(settings)

    assert state.connected is False
    assert state.reason == "invalid_token"
