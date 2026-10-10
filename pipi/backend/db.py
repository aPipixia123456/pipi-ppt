from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


def now():
    return datetime.now(timezone.utc)


def identifier():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)  # pipiapi identity
    name: Mapped[str] = mapped_column(String(120))
    stored_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class WebSession(Base):
    __tablename__ = "web_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    credential: Mapped[str] = mapped_column(Text)  # Fernet ciphertext only
    expires: Mapped[int] = mapped_column()


class LoginState(Base):
    __tablename__ = "login_states"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    verifier: Mapped[str] = mapped_column(Text)
    expires: Mapped[int] = mapped_column()


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column()
    purpose: Mapped[str] = mapped_column(String(24))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    data: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class Deck(Base):
    __tablename__ = "decks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    template_id: Mapped[str] = mapped_column(String(36))
    template: Mapped[dict] = mapped_column(JSON)  # Immutable template snapshot
    outline: Mapped[list] = mapped_column(JSON, default=list)
    slides: Mapped[list] = mapped_column(JSON, default=list)
    research: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    version: Mapped[int] = mapped_column(default=1)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("owner", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64))
    deck_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80))
    args: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(500))
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    cursor: Mapped[int] = mapped_column(default=0)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("job_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24))
    request_id: Mapped[str | None] = mapped_column(String(64))
    response: Mapped[dict | None] = mapped_column(JSON)


class Policy(Base):
    __tablename__ = "policy"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    data: Mapped[dict] = mapped_column(JSON)


DEFAULT_POLICY = {
    "enabled": False,
    "text_models": [],
    "image_models": [],
    "research_model": "",
    "generation_concurrency": 4,
    "export_concurrency": 2,
    "user_running": 1,
    "user_queued": 3,
    "upload_mb": 20,
    "storage_mb": 1024,
}


@lru_cache
def engine():
    url = settings().database_url
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )


@contextmanager
def session():
    with sessionmaker(engine(), expire_on_commit=False)() as db:
        yield db


def initialize():
    Base.metadata.create_all(engine())
    # ``create_all`` does not add columns to an existing deployment. Keep this
    # small migration portable across the SQLite/PostgreSQL/MySQL JSON dialects.
    if "research" not in {column["name"] for column in inspect(engine()).get_columns("decks")}:
        try:
            with engine().begin() as connection:
                connection.execute(text("ALTER TABLE decks ADD COLUMN research JSON"))
        except SQLAlchemyError:
            # API and Worker containers can initialize at the same time. A
            # concurrent migration is successful when the column now exists;
            # preserve any other database error for startup diagnostics.
            if "research" not in {
                column["name"] for column in inspect(engine()).get_columns("decks")
            }:
                raise
    with session() as db:
        if not db.get(Policy, 1):
            db.add(Policy(id=1, data=DEFAULT_POLICY.copy()))
            db.commit()
