from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from .models import Candidate, CleanupRule, RunLog


DATABASE_URL = "sqlite:///./email_hoover.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


__all__ = [
    "Candidate",
    "CleanupRule",
    "RunLog",
    "engine",
    "get_session",
    "init_db",
]
