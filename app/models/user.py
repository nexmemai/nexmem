"""User and API Key ORM models — Day 2."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class User(Base):
    """Represents a registered user (email/password or wallet)."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=True, index=True)
    wallet_address = Column(String, unique=True, nullable=True, index=True)
    hashed_password = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    tier = Column(String, default="free", nullable=False)  # free, starter, pro, enterprise
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    total_tokens_used = Column(Integer, default=0, nullable=False)


class APIKey(Base):
    """Named API key scoped to a user. Raw key is shown exactly once."""

    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    key_hash = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)          # e.g. "Telegram Bot", "VS Code"
    scopes = Column(String, default="read,write")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class TokenUsage(Base):
    """Track token usage per user/app for billing/cost tracking."""

    __tablename__ = "token_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    app_id = Column(String, nullable=True, index=True)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    model = Column(String, nullable=False)
    cost_cents = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
