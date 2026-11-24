from __future__ import annotations

import os
from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for shop tables."""


def database_url() -> str:
    """Return DATABASE_URL, defaulting to a local SQLite file."""
    return os.environ.get("DATABASE_URL", "sqlite:///./app.db")


def make_engine(url: str | None = None) -> Engine:
    """Create an engine, with SQLite thread-check disabled."""
    resolved = url or database_url()
    connect_args = {"check_same_thread": False} if resolved.startswith("sqlite") else {}
    return create_engine(resolved, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Bind a sessionmaker to the given engine."""
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db(request: Request) -> Generator[Session]:
    """Yield a request-scoped session and commit or roll it back."""
    session_factory: sessionmaker[Session] = request.app.state.SessionLocal
    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
