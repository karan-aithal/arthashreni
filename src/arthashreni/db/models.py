"""ORM models. Phase 1: only the shared `ref` tables for local auth."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LocalUser(Base):
    __tablename__ = "local_user"
    __table_args__ = (CheckConstraint("id = 1", name="single_user"), {"schema": "ref"})

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = ({"schema": "ref"},)

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
