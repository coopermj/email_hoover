from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import os


APP_NAME = "email-hoover"
DEFAULT_GMAIL_BASE_URL = "https://gmail.googleapis.com"
DEFAULT_GMAIL_TOKEN_PATH = Path(__file__).resolve().parent.parent / ".gmail-token.json"


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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gmail_token_path=Path(os.getenv("GMAIL_TOKEN_PATH", DEFAULT_GMAIL_TOKEN_PATH)),
            gmail_base_url=os.getenv("GMAIL_BASE_URL", DEFAULT_GMAIL_BASE_URL),
        )
