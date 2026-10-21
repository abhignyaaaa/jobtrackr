from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

STATUSES = ["Saved", "Applied", "Assessment", "Interview", "Offer Received", "Rejected", "Withdrawn"]
CLOSED_STATUSES = {"Offer Received", "Rejected", "Withdrawn"}
EMPLOYMENT_TYPES = ["Internship", "Full-time", "Internship + PPO", "Part-time", "Contract", "Other"]
INTERVIEW_TYPES = ["Online Assessment", "Technical", "HR", "Managerial", "Group Discussion", "System Design", "Other"]
INTERVIEW_MODES = ["Online", "On-site", "Phone"]
INTERVIEW_RESULTS = ["Pending", "Passed", "Failed", "Cancelled"]
COMP_PERIODS = ["year", "month"]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class AppSecret(Base):
    """Server-generated application secrets (e.g. the cookie-signing key)."""

    __tablename__ = "app_secrets"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class UserSession(Base):
    """Server-side session. Only the SHA-256 of the cookie token is stored,
    so a database leak does not hand out live sessions."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(default=False)
    attempted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_applications_user_status", "user_id", "status"),
        Index("ix_applications_user_applied", "user_id", "applied_on"),
        CheckConstraint("comp_min IS NULL OR comp_min >= 0", name="ck_comp_min_nonneg"),
        CheckConstraint("comp_max IS NULL OR comp_min IS NULL OR comp_max >= comp_min", name="ck_comp_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    employment_type: Mapped[str] = mapped_column(String(32), default="Internship")
    source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    applied_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    comp_min: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comp_max: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comp_currency: Mapped[str] = mapped_column(String(3), default="INR")
    comp_period: Mapped[str] = mapped_column(String(8), default="year")
    status: Mapped[str] = mapped_column(String(20), default="Saved")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    history: Mapped[list[StatusChange]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="StatusChange.changed_at.desc()"
    )
    interviews: Mapped[list[Interview]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="Interview.scheduled_at"
    )
    documents: Mapped[list[Document]] = relationship(back_populates="application")


class StatusChange(Base):
    __tablename__ = "status_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    application: Mapped[Application] = relationship(back_populates="history")


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    round_name: Mapped[str] = mapped_column(String(80))
    interview_type: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16), default="Online")
    meeting_link: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    prep_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="Pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    application: Mapped[Application] = relationship(back_populates="interviews")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    label: Mapped[str] = mapped_column(String(120))
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    application: Mapped[Application | None] = relationship(back_populates="documents")
