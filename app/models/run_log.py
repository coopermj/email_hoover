from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class RunLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    trigger: str = "manual"
    triggered_by: str = "manual"
    status: str = "started"
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    matched_count: int = 0
    actioned_count: int = 0
    rule_id: int | None = Field(default=None, index=True)
    message_id: str | None = Field(default=None, index=True)
    action: str = "started"
    error_message: str | None = None
