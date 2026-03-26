from dataclasses import dataclass
import json
from pathlib import Path
import secrets

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from app.config import Settings


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class GoogleOAuthStart:
    authorization_url: str
    code_verifier: str


def load_google_oauth_config(settings: Settings) -> GoogleOAuthConfig:
    if settings.google_credentials_path is not None:
        try:
            payload = json.loads(settings.google_credentials_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            msg = f"Google OAuth credentials file not found: {settings.google_credentials_path}"
            raise ValueError(msg) from exc
        except json.JSONDecodeError as exc:
            msg = "Google OAuth credentials file is invalid JSON."
            raise ValueError(msg) from exc
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


def load_google_credentials(path: Path) -> Credentials:
    payload = read_gmail_credentials(path)
    scopes = payload.get("scopes")
    return Credentials.from_authorized_user_info(payload, scopes=scopes)


def read_gmail_access_token(path: Path) -> str:
    credentials = load_google_credentials(path)
    if not credentials.valid:
        credentials.refresh(GoogleAuthRequest())
        write_gmail_credentials(path, json.loads(credentials.to_json()))
    if not credentials.token:
        raise ValueError("Gmail OAuth credentials did not yield an access token.")
    return credentials.token


def create_oauth_state_token() -> str:
    return secrets.token_urlsafe(32)


def build_google_oauth_start(settings: Settings, state_token: str) -> GoogleOAuthStart:
    config = load_google_oauth_config(settings)
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=config.scopes,
        redirect_uri=config.redirect_uri,
    )
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state_token,
    )
    code_verifier = getattr(flow, "code_verifier", None)
    if not code_verifier:
        raise ValueError("Google OAuth flow did not produce a PKCE code verifier.")
    return GoogleOAuthStart(
        authorization_url=authorization_url,
        code_verifier=code_verifier,
    )


def exchange_google_code(settings: Settings, code: str, code_verifier: str) -> dict[str, object]:
    config = load_google_oauth_config(settings)
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=config.scopes,
        redirect_uri=config.redirect_uri,
    )
    flow.fetch_token(code=code, code_verifier=code_verifier)
    return json.loads(flow.credentials.to_json())
