from sqlmodel import Field, SQLModel


class CleanupRule(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    sender_address: str = Field(index=True, unique=True)
    sender_name: str
    stale_days: int
    action: str
    enabled: bool = True
    schedule_enabled: bool = True
    pause_reason: str | None = None
