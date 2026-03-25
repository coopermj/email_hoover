from sqlmodel import Field, SQLModel


class Candidate(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    sender_address: str = Field(index=True, unique=True)
    sender_name: str
    sample_subjects_json: str = "[]"
    example_message_ids_json: str = "[]"
    observed_frequency: str = "unknown"
    recommended_stale_days: int
    recommended_action: str
    risk_level: str = "low"
    status: str = "pending"
