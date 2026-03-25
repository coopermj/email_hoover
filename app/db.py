from collections.abc import Generator
from functools import lru_cache
from pathlib import Path
import os

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .models import Candidate, CleanupRule, RunLog


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "email_hoover.db"


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    database_url = get_database_url()
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def init_db() -> None:
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session


__all__ = [
    "Candidate",
    "CleanupRule",
    "RunLog",
    "DEFAULT_DB_PATH",
    "get_database_url",
    "get_engine",
    "get_session",
    "init_db",
]
