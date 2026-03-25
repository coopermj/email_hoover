from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class AuthState:
    connected: bool
    reason: str | None = None

    @classmethod
    def from_disk(cls, settings: Settings) -> "AuthState":
        if not settings.gmail_token_path.exists():
            return cls(connected=False, reason="missing_token")
        return cls(connected=True)
