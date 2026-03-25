from dataclasses import dataclass
import json
from pathlib import Path

from app.config import Settings


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]


def load_google_oauth_config(settings: Settings) -> GoogleOAuthConfig:
    if settings.google_credentials_path is not None:
        payload = json.loads(settings.google_credentials_path.read_text(encoding="utf-8"))
        client_payload = payload.get("installed") or payload.get("web") or {}
        client_id = client_payload.get("client_id")
        client_secret = client_payload.get("client_secret")
    else:
        client_id = settings.google_client_id
        client_secret = settings.google_client_secret

    if not client_id or not client_secret or not settings.google_redirect_uri:
        raise ValueError("Google OAuth configuration is incomplete.")

    return GoogleOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=settings.google_redirect_uri,
        scopes=settings.gmail_scopes,
    )


def read_gmail_credentials(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_gmail_credentials(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def has_refreshable_credentials(path: Path) -> bool:
    try:
        payload = read_gmail_credentials(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False

    required_keys = {"refresh_token", "token_uri", "client_id", "client_secret", "scopes"}
    return required_keys.issubset(payload)
