from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import os


APP_NAME = "email-hoover"
DEFAULT_GMAIL_BASE_URL = "https://gmail.googleapis.com"
DEFAULT_GMAIL_TOKEN_PATH = Path.home() / ".local" / "state" / APP_NAME / "gmail-token.json"
DEFAULT_GOOGLE_REDIRECT_URI = "http://127.0.0.1:8765/auth/google/callback"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


def _package_version() -> str:
    try:
        return version(APP_NAME)
    except PackageNotFoundError:
        return "0.1.0"


APP_VERSION = _package_version()


@dataclass(frozen=True)
class Settings:
    gmail_token_path: Path = DEFAULT_GMAIL_TOKEN_PATH
    gmail_base_url: str = DEFAULT_GMAIL_BASE_URL
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_credentials_path: Path | None = None
    google_redirect_uri: str = DEFAULT_GOOGLE_REDIRECT_URI
    gmail_scopes: tuple[str, ...] = (GMAIL_MODIFY_SCOPE,)

    @classmethod
    def from_env(cls) -> "Settings":
        google_credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH")
        return cls(
            gmail_token_path=Path(os.getenv("GMAIL_TOKEN_PATH", DEFAULT_GMAIL_TOKEN_PATH)),
            gmail_base_url=os.getenv("GMAIL_BASE_URL", DEFAULT_GMAIL_BASE_URL),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID"),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            google_credentials_path=Path(google_credentials_path) if google_credentials_path else None,
            google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", DEFAULT_GOOGLE_REDIRECT_URI),
        )
